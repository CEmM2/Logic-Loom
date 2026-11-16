"""AKMS dispatch module.

Provides the AKMS node-generation helpers (binary checks, preflight, dispatch)
and wires them into the Logic-Loom run lifecycle.

Public surface:

* :class:`AkmsDispatchError` — raised by ``require_binary`` and the
  other dispatch helpers; routes map it to HTTP 503 / 400.
* :func:`require_binary` / :func:`run_command_capture` — small async
  helpers reused by the preflight route.
* :func:`preflight_node_generation` — returns the structured
  ``{ok, checks[]}`` shape. Missing tools **never
  raise** — they show up as ``{ok: false, detail: "not found on PATH"}``
  so the frontend can render a structured error list.
* :func:`dispatch_node_generation_to_claude` — async iterator that
  yields decoded stdout lines. ``cwd`` is the per-run claude-runtime/
  directory,
  resolved via :meth:`pathlib.Path.resolve()` before being passed to
  :func:`asyncio.create_subprocess_exec` so Windows long-paths and
  junctions work.

CLAUDE.md degrade-gracefully invariants:

* ``import akms_dispatch`` must not eager-import the AKMS sibling
  packages — the preflight check uses ``importlib.import_module`` lazily.
* ``preflight_node_generation`` must never raise: its agent-CLI PATH checks
  use the non-raising :func:`_check_binary_present` / :func:`shutil.which`
  (NOT :func:`require_binary`, which raises) and return a structured failed
  check. Host-LLM resolution (:func:`resolve_node_gen_runtime`)
  likewise degrades to ``None`` (claude fallback) on any error.

Run-log contract (`OutputStore.LOG_FILENAME == "logs.txt"`):

* The SSE tail polls ``RunRegistry.get(run_id).status`` for
  ``succeeded|failed|cancelled`` to close-on-EOF. The dispatcher
  surfaces an async iterator; the registry-side driver
  (:meth:`RunRegistry.attach_workflow_subprocess`) consumes that
  iterator, appends each line to ``<run_dir>/logs.txt`` (the file the
  SSE tails), and flips the registry record's status to a terminal
  value on exit.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Iterable

from ._reentry import module_argv
from .akms_claude_runtime import materialize_runtime_workspace, runtime_dir

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .config import Settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


class AkmsDispatchError(RuntimeError):
    """Raised by AKMS dispatch helpers on configuration / environment errors.

    Routes catch this and translate to HTTP 503 when the dispatcher cannot
    start because of a missing external tool (claude / nlm) or to HTTP 400
    when the request payload is otherwise valid but the runtime is wrong
    (missing plan path, AKMS extras not installed, etc.).
    """


# ---------------------------------------------------------------------------
# Host-LLM bridge: dispatch the chosen agent CLI, or set
# node-gen's LiteLLM env, instead of hardcoding ``claude``.
# ---------------------------------------------------------------------------
#
# The node-gen pipeline (vendored ``generate_nodes_pipeline.py``, NEVER edited)
# is steered through env vars:
#   * ``AKMS_LLM_PROVIDER`` — ``anthropic`` / ``openai`` / ``google``
#   * ``AKMS_MODEL``        — concrete model id (from host_llm.model_map)
#   * ``AKMS_LLM_API_BASE`` — base URL for an OpenAI-compatible endpoint
#   * ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` / ``GEMINI_API_KEY`` — the key
# and dispatched through an agent CLI (``claude -p`` by default) that runs the
# node-gen skill. BOTH are selectable from the unified host choice:
#   * CLI choice (codex-cli / a custom label) → dispatch THAT binary, not claude.
#   * API choice (akms completion) → keep dispatching via the claude agent CLI
#     but set the LiteLLM env so node-gen's pipeline calls the chosen API.
# ``claude`` stays the fallback when no choice resolves (back-compat is sacred).
#
# This mirrors ``adapters/zsum_adapter.py`` exactly: resolve with
# ``host_llm.routing.resolve_choice`` (NEVER ``route_for`` — its API bucket
# eagerly calls ``api_model_for("akms","nodegen")`` which raises
# ``UnknownProviderError`` since there is no ``("akms","nodegen")`` mapping), map
# the host label, look the model up with a GUARDED lookup, and wrap the whole
# resolution in a try/except returning ``None`` (never 500 into the
# node-gen request path / never block the sidecar).

#: Host completion family → the node-gen ``AKMS_LLM_PROVIDER`` value plus the
#: env var node-gen's LiteLLM lane reads the key from (``generate_nodes_pipeline``
#: ``PROVIDERS[...]["key_env"]``). The host ``akms`` completion choice does not
#: carry a family — :func:`_node_gen_api_family` derives it from whichever
#: completion key env is present (the same vars akms_learn's gate honours).
#: ``model_map`` family name == the node-gen provider value here, so one map
#: serves both the model lookup and the env value.
_NODE_GEN_API_KEY_ENV: dict[str, str] = {
    # node-gen reads ANTHROPIC_API_KEY for the anthropic lane.
    "anthropic": "ANTHROPIC_API_KEY",
    # node-gen reads OPENAI_API_KEY for the openai-compatible lane.
    "openai": "OPENAI_API_KEY",
    # node-gen reads GEMINI_API_KEY for the google lane (GOOGLE_API_KEY is an
    # accepted fallback in the pipeline, but we forward whichever the host has).
    "google": "GEMINI_API_KEY",
}

#: Host completion API-key env var → node-gen API family. Ordered: the first
#: present key wins, matching ``host_llm.routing._COMPLETION_ENV_VARS`` order
#: (anthropic > openai > google) so the akms completion choice maps to a single
#: concrete family deterministically.
_HOST_KEY_ENV_TO_API_FAMILY: tuple[tuple[str, str], ...] = (
    ("ANTHROPIC_API_KEY", "anthropic"),
    ("OPENAI_API_KEY", "openai"),
    # The host gate uses GOOGLE_API_KEY; node-gen's google lane reads
    # GEMINI_API_KEY (GOOGLE_API_KEY accepted as fallback) — accept either as
    # the host-side signal that the google family is configured.
    ("GOOGLE_API_KEY", "google"),
    ("GEMINI_API_KEY", "google"),
)


@dataclass(slots=True, frozen=True)
class NodeGenRuntime:
    """The host-derived node-gen dispatch runtime.

    ``binary`` is the agent-CLI binary to dispatch — the resolved CLI provider's
    binary (``codex`` for codex-cli, the first argv token for a custom label) or
    ``"claude"`` for the API path (node-gen still runs through the claude agent;
    the env vars steer its internal LiteLLM). ``provider`` is the host registry
    label that resolved (``codex-cli`` / ``akms`` / a custom label), carried for
    diagnostics + the "missing binary names the provider" 400. ``model`` is
    the concrete API model id for the API path (``None`` for CLI). ``env_overrides``
    is the ``AKMS_LLM_*`` + key env to apply to the dispatch subprocess for the
    API path (empty for CLI). It is scoped to the dispatch subprocess — NEVER
    written process-globally (the long-lived sidecar must not leak one request's
    provider into the next).
    """

    binary: str
    provider: str
    model: str | None = None
    env_overrides: dict[str, str] = field(default_factory=dict)
    # Full CLI command template for a host CLI provider (e.g. "codex exec {prompt}");
    # None for the API / claude-fallback path. Carried so the dispatcher builds the
    # agent's OWN argv ({prompt} substitution / stdin, per _run_cli_template) rather
    # than claude's --print/--output-format flags, which a non-claude agent rejects.
    argv_template: str | None = None


def _node_gen_api_family() -> str | None:
    """Map the present host completion key to a node-gen API family, or ``None``.

    The host ``akms`` completion choice is family-agnostic — it just means "use
    the API". node-gen's pipeline needs a concrete ``AKMS_LLM_PROVIDER``
    (``anthropic`` / ``openai`` / ``google``), so we derive it from whichever
    completion key env is present (the SAME signal ``resolve_choice``'s akms
    branch used to validate the choice was usable). No key present → ``None`` so
    resolution degrades to the claude fallback (no half-set LiteLLM env).
    """
    for env_var, family in _HOST_KEY_ENV_TO_API_FAMILY:
        if os.environ.get(env_var):
            return family
    return None


def _node_gen_model_for(family: str) -> str | None:
    """Look the concrete node-gen model id up for *family*, GUARDED.

    Wraps ``host_llm.model_map.api_model_for(family, "nodegen")`` in try/except —
    an ``UnknownProviderError`` (or any error) degrades to ``None`` (the provider
    env still switches node-gen's lane; node-gen falls back to its own
    provider-default model). Never raise into the dispatch path.
    """
    from .host_llm import model_map

    try:
        return model_map.api_model_for(family, "nodegen")
    except Exception as exc:  # noqa: BLE001 — degrade to no-model, never raise
        logger.debug("node-gen model lookup for %r skipped: %s", family, exc)
        return None


def resolve_node_gen_runtime(settings: Settings | None) -> NodeGenRuntime | None:
    """Resolve the host choice into a node-gen dispatch runtime, or ``None``.

    ``None`` means "no host override" — the caller uses the ``claude`` fallback
    binary with no env manipulation (the back-compat default).

    Resolution (mirrors ``adapters/zsum_adapter.resolve_zsum_runtime``):

    1. ``host_llm.routing.resolve_choice(settings, "nodegen")`` resolves the
       effective node-gen choice (``nodegen_llm_provider`` override → global
       ``llm_provider`` → legacy ``learn_llm_provider`` alias). ``None`` →
       fallback.
    2. CLI provider (in ``available_cli_providers``): the dispatch binary is the
       first token of the provider's argv template, parsed with the never-raise
       ``host_llm.discovery._safe_split`` (a malformed custom-CLI command →
       ``None`` → fallback, never a ``shlex`` ``ValueError``).
       Carries no env (the CLI authenticates via its own session).
    3. ``akms`` completion (API path): the binary stays ``claude`` (node-gen runs
       through the claude agent), and ``env_overrides`` carries
       ``AKMS_LLM_PROVIDER`` + a GUARDED ``AKMS_MODEL`` + the present key env +
       ``AKMS_LLM_API_BASE`` if configured. The concrete family is derived from
       the present completion key.
    4. ``nlm`` (grounded) / any unmapped label / any error → ``None`` → fallback
       (``route_for``/``api_model_for`` ``UnknownProviderError`` is dodged
       by resolving with ``resolve_choice`` + a guarded lookup, never reaching a
       500).

    ``resolve_choice`` is used over ``route_for`` deliberately — see the module
    note above. NEVER raises: the whole body is wrapped in a try/except that
    degrades to ``None`` (a belt over ``resolve_choice``'s own ``_safe_split``).
    """
    if settings is None:
        return None
    try:
        from .host_llm.discovery import _safe_split, available_cli_providers
        from .host_llm.routing import cli_argv_template, resolve_choice

        choice = resolve_choice(settings, "nodegen")
        if choice is None:
            return None

        provider = choice.provider

        # --- CLI agent path (codex-cli / a custom label) -------------------
        if provider in available_cli_providers(settings):
            # Shared sourcing rule (built-in agents first, then custom labels)
            # lives in host_llm.routing.cli_argv_template — reused here so the
            # node-gen path can never drift from route_for's CLI resolution.
            template = cli_argv_template(settings, provider)
            if not template:
                return None
            # Defense-in-depth: the PRIMARY malformed-command guard is
            # available_cli_providers' own _safe_split (a malformed label is
            # excluded above, so this branch normally only runs for a parseable
            # template). This in-branch _safe_split is kept so a future code path
            # that reaches the CLI branch with a malformed template still degrades
            # cleanly (the whole-body try/except belt would also catch a raw-split
            # ValueError, but degrading HERE keeps the warning quiet — a clean
            # debug-level fallback, not the belt's warning).
            argv = _safe_split(template)
            if not argv:
                # Malformed command (unbalanced quote) or empty — degrade to the
                # claude fallback, never let shlex ValueError escape.
                logger.debug(
                    "node-gen runtime: CLI provider %r has no parseable binary; "
                    "falling back to claude",
                    provider,
                )
                return None
            # Carry the FULL template (not just argv[0]) so the dispatcher builds
            # the agent's own argv ({prompt}/stdin), not claude's flags.
            return NodeGenRuntime(
                binary=argv[0], provider=provider, argv_template=template
            )

        # --- API / completion path (akms) ----------------------------------
        if provider == "akms":
            family = _node_gen_api_family()
            if family is None:
                # akms resolved but no concrete completion key present to pick a
                # family — degrade to claude fallback rather than set a partial
                # LiteLLM env.
                logger.debug(
                    "node-gen runtime: akms completion chosen but no completion "
                    "key present; falling back to claude"
                )
                return None
            env_overrides: dict[str, str] = {"AKMS_LLM_PROVIDER": family}
            model = _node_gen_model_for(family)
            if model:
                env_overrides["AKMS_MODEL"] = model
            key_env = _NODE_GEN_API_KEY_ENV.get(family)
            if key_env:
                value = os.environ.get(key_env)
                if value:
                    env_overrides[key_env] = value
            # AKMS_LLM_API_BASE: forwarded only when already configured in the
            # process env (an OpenAI-compatible / local endpoint). Logic-Loom has
            # no dedicated Settings field for it (YAGNI); if a future field is
            # added, slot it in ahead of this env read.
            api_base = os.environ.get("AKMS_LLM_API_BASE")
            if api_base:
                env_overrides["AKMS_LLM_API_BASE"] = api_base
            return NodeGenRuntime(
                binary="claude",
                provider=provider,
                model=model,
                env_overrides=env_overrides,
            )

        # --- nlm (grounded) / anything unmapped ----------------------------
        logger.debug(
            "node-gen runtime: host provider %r has no node-gen dispatch mapping; "
            "falling back to claude",
            provider,
        )
        return None
    except Exception as exc:  # noqa: BLE001 — degrade to claude, never raise
        logger.warning(
            "node-gen runtime resolution failed (%s); falling back to claude",
            exc,
        )
        return None


def require_node_gen_binary(settings: Settings | None) -> str:
    """Validate the node-gen dispatch binary is on PATH; return it, else raise.

    The *actionable-400* gate. Run at run-creation time (before a
    run record is minted) so a chosen-but-uninstalled agent CLI fails fast with a
    message naming the missing binary + the chosen provider, instead of silently
    falling back to ``claude`` (a confusing "why did it use claude?") or minting a
    doomed run that 500s later when the spawn fails.

    The subtlety this gate exists to handle: a CLI choice whose
    binary is MISSING is filtered out of ``host_llm.discovery.available_cli_providers``
    upstream, so :func:`resolve_node_gen_runtime` returns ``None`` and degrades to
    ``claude`` — losing the "install the agent" signal. This gate therefore reads
    the **RAW** effective choice (BEFORE that PATH filter) and classifies it:

    1. **Explicit built-in CLI** (``claude-cli`` / ``codex-cli``) whose binary is
       not on PATH → ``AkmsDispatchError`` naming the binary + provider (→ HTTP
       400). This is the actionable "install <binary>" case.
    2. **Explicit valid custom-CLI label** (command parses via ``_safe_split``)
       whose first-token binary is not on PATH → same ``AkmsDispatchError``.
    3. **Malformed custom-CLI** (``_safe_split`` → ``None``, e.g. an unbalanced
       quote) → NO raise; degrade to the resolver's effective binary (``claude``
       fallback). We cannot name a binary to install, and a user typo must never
       500.
    4. **Unmapped / unknown provider label** (not a built-in CLI, not a custom
       label, not ``akms``/``nlm``) → NO raise; degrade to the resolver fallback
       too.
    5. **No choice / ``auto`` / ``None`` / ``akms`` API path** → NO raise; return
       the resolver's effective binary (``claude`` for the API + no-choice
       paths).

    Returns the binary that WILL be dispatched (``resolve_node_gen_runtime``'s
    result, ``"claude"`` when it degrades to ``None``). NEVER raises for anything
    but cases 1-2 — every other path degrades gracefully so the sidecar stays up
    and a doomed-but-recoverable config never 500s.
    """
    # The effective binary the dispatch path would actually use (claude when the
    # resolver degrades to None). This is the value we return on every non-raising
    # path, so the gate and the dispatcher agree on the binary by construction.
    runtime = resolve_node_gen_runtime(settings)
    effective_binary = runtime.binary if runtime is not None else "claude"

    if settings is None:
        return effective_binary

    # Read the RAW effective choice BEFORE available_cli_providers' PATH filter —
    # this is the only place a chosen-but-missing CLI is still distinguishable
    # from a degrade. effective_llm_provider mirrors resolve_choice's step-1
    # precedence (per-consumer override → global → legacy alias); fall back to the
    # defensive getattr chain when a stub Settings lacks the method (tests).
    try:
        if hasattr(settings, "effective_llm_provider"):
            raw_choice = settings.effective_llm_provider("nodegen")
        else:  # SimpleNamespace / stub settings
            raw_choice = (
                getattr(settings, "nodegen_llm_provider", None)
                or getattr(settings, "llm_provider", None)
                or getattr(settings, "learn_llm_provider", None)
            )
    except Exception as exc:  # noqa: BLE001 — a bad Settings must not 500
        logger.debug("node-gen raw choice read failed (%s); using fallback", exc)
        return effective_binary

    choice = (raw_choice or "").strip()
    # No choice / auto / API-or-grounded labels → not an explicit-CLI install
    # case; the resolver already handled them (claude fallback / API env path).
    if not choice or choice in {"auto", "akms", "nlm"}:
        return effective_binary

    # The discovery import + the entire Case 1/2/3 classification are wrapped in
    # ONE belt: the intentional AkmsDispatchError (the actionable 400) MUST still
    # propagate, but EVERY other exception degrades to the claude fallback. This
    # closes a theoretical 500-hole — e.g. a non-dict-coercible
    # learn_llm_custom_clis (a string on a stub Settings) makes _custom_clis()
    # raise TypeError; Pydantic validates the field as a dict in production so
    # it's unreachable for real Settings, but the rule is "ANY bad config never
    # 500s" (mirrors resolve_node_gen_runtime's whole-body belt for the same class).
    try:
        from .host_llm.discovery import _BUILTIN_CLIS, _custom_clis, _safe_split

        # Case 1: explicit built-in CLI label (claude-cli / codex-cli).
        builtin = _BUILTIN_CLIS.get(choice)
        if builtin is not None:
            binary = builtin[0]
            if shutil.which(binary) is None:
                raise AkmsDispatchError(
                    f"Required command not found on PATH: {binary} "
                    f"(node-gen provider {choice!r})"
                )
            return binary

        # Case 2/3: explicit custom-CLI label.
        command = _custom_clis(settings).get(choice)
        if command is not None:
            argv = _safe_split((command or "").strip())
            if not argv:
                # Case 3 — malformed (unbalanced quote → _safe_split None) or
                # empty: cannot name a binary to install; degrade, never 400/500.
                logger.debug(
                    "node-gen gate: custom CLI %r has no parseable binary; "
                    "falling back to %s",
                    choice,
                    effective_binary,
                )
                return effective_binary
            binary = argv[0]
            if shutil.which(binary) is None:
                raise AkmsDispatchError(
                    f"Required command not found on PATH: {binary} "
                    f"(node-gen provider {choice!r})"
                )
            return binary
    except AkmsDispatchError:
        raise  # intentional actionable 400 — must propagate to the 400 handler
    except Exception as exc:  # noqa: BLE001 — classification must never 500
        logger.debug(
            "node-gen gate classification failed (%s); using fallback %s",
            exc,
            effective_binary,
        )
        return effective_binary

    # Case 4: unmapped / unknown label (not built-in CLI, not custom, not
    # akms/nlm) — the resolver degraded it to claude already; mirror that, never
    # 500 on a stray provider string.
    logger.debug(
        "node-gen gate: provider %r is unmapped; falling back to %s",
        choice,
        effective_binary,
    )
    return effective_binary


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def require_binary(name: str) -> str:
    """Resolve ``name`` on PATH or raise :class:`AkmsDispatchError`.

    Kept as a thin wrapper around
    :func:`shutil.which` so tests can monkeypatch.
    """
    found = shutil.which(name)
    if not found:
        raise AkmsDispatchError(f"Required command not found on PATH: {name}")
    return found


async def run_command_capture(*argv: str, timeout: int = 30) -> tuple[int, str]:
    """Run ``argv`` and capture its merged stdout/stderr as UTF-8 text.

    Returns ``(exit_code, text)``. ``timeout`` is enforced via
    :func:`asyncio.wait_for`; on timeout the child is killed and the
    function returns ``(124, "timed out")`` (POSIX-style timeout code).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError as exc:  # pragma: no cover — caller pre-checks
        return 127, f"binary not found: {exc}"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "timed out"
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def _check(name: str, ok: bool, detail: str) -> dict:
    """Helper to build a single check entry in the canonical shape."""
    return {"name": name, "ok": bool(ok), "detail": detail}


def _check_import(module_name: str) -> dict:
    try:
        importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 — surface as structured failure
        return _check(module_name, False, f"import failed: {exc}")
    return _check(module_name, True, "import ok")


def _check_bundled_assets() -> dict:
    """Check the bundled .claude / scripts resources are present."""
    try:
        from importlib import resources

        root = resources.files("logic_loom_api.resources.akms_claude")
        names = [c.name for c in root.iterdir() if c.is_dir()] if root else []
    except (ModuleNotFoundError, FileNotFoundError, OSError) as exc:
        return _check("bundled_assets", False, f"resources unavailable: {exc}")
    if not names:
        return _check("bundled_assets", False, "resources package empty")
    return _check("bundled_assets", True, f"present: {sorted(names)}")


def _check_runtime_materialisable() -> dict:
    """Probe runtime materialisation without forcing a destructive rewrite.

    We don't call ``materialize_runtime_workspace(force=True)`` here —
    that would wipe and re-copy on every preflight which is expensive
    and racey. Instead we check that the runtime parent directory is
    writable and the bundled root resolves.
    """
    try:
        target = runtime_dir()
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _check(
            "runtime_materialisable",
            False,
            f"cannot prepare runtime dir: {exc}",
        )
    return _check(
        "runtime_materialisable", True, f"writable parent: {target.parent}"
    )


async def _check_binary_present(name: str, label: str | None = None) -> dict:
    """Structured PATH check that never raises."""
    found = shutil.which(name)
    if not found:
        return _check(label or name, False, "not found on PATH")
    return _check(label or name, True, f"found at {found}")


async def _check_claude_version(claude_path: str | None) -> dict:
    if not claude_path:
        return _check("claude_version", False, "claude not on PATH")
    rc, text = await run_command_capture(claude_path, "--version", timeout=15)
    if rc != 0:
        return _check("claude_version", False, f"claude --version failed: {text[-400:]}")
    return _check("claude_version", True, text.strip()[:200])


async def _check_nlm_notebook_list(nlm_path: str | None) -> dict:
    if not nlm_path:
        return _check("nlm_notebook_list", False, "nlm not on PATH")
    rc, text = await run_command_capture(
        nlm_path, "notebook", "list", timeout=30
    )
    if rc != 0:
        return _check(
            "nlm_notebook_list",
            False,
            f"nlm notebook list failed (run `nlm login`?): {text[-400:]}",
        )
    return _check("nlm_notebook_list", True, "ok")


def _check_nlm_id_nonempty(nlm_id: str | None) -> dict:
    """Notebook id is optional: missing means create a new notebook at run time.

    Product rule (nlm_batch_fix): an empty ``nlm_id`` is not a preflight
    failure — the node-gen launch path creates/opens a notebook via
    :func:`create_nlm_notebook` when no id is supplied. Preflight only
    reports the *intent* so the UI can show what will happen.
    """
    if nlm_id is None or not str(nlm_id).strip():
        return _check(
            "nlm_id",
            True,
            "no notebook id selected; a new notebook will be created at run time",
        )
    return _check("nlm_id", True, f"id={nlm_id}")


def _check_plan_json_exists(plan_path: Path | None) -> dict:
    if plan_path is None:
        return _check("plan_json", False, "no plan path supplied")
    p = Path(plan_path)
    if not p.exists():
        return _check("plan_json", False, f"plan file missing: {p}")
    if not p.is_file():
        return _check("plan_json", False, f"plan path is not a file: {p}")
    return _check("plan_json", True, f"exists: {p}")


def _check_output_dir_writable(out_dir: Path | None) -> dict:
    if out_dir is None:
        return _check("output_dir_writable", False, "no output dir supplied")
    p = Path(out_dir)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _check(
            "output_dir_writable",
            False,
            f"cannot create output dir {p}: {exc}",
        )
    # Probe writability with a tempfile pattern (no os.access on weird FS).
    probe = p / ".logic-loom-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        return _check(
            "output_dir_writable", False, f"output dir not writable: {exc}"
        )
    return _check("output_dir_writable", True, f"writable: {p}")


def _check_akms_vault_configured(vault_path: Path | str | None) -> dict:
    if vault_path is None or not str(vault_path).strip():
        return _check(
            "akms_vault",
            False,
            "AKMS vault path not configured (graph review workflows will not work)",
        )
    p = Path(vault_path)
    if not p.exists():
        return _check("akms_vault", False, f"vault path missing: {p}")
    return _check("akms_vault", True, f"configured: {p}")


async def preflight_node_generation(
    *,
    nlm_id: str | None = None,
    plan_path: Path | str | None = None,
    out_dir: Path | str | None = None,
    akms_vault: Path | str | None = None,
    claude_binary: str = "claude",
    nlm_binary: str = "nlm",
    settings: Settings | None = None,
) -> dict:
    """Structured node-generation preflight (12 checks).

    Returns ``{"ok": bool, "checks": list[Check]}`` where each
    ``Check`` is ``{name: str, ok: bool, detail: str}``. The top-level
    ``ok`` is ``all(check["ok"] for check in checks)``.

    Missing external tools (``claude``, ``nlm``) DO NOT raise — they
    show up as structured failed checks so the frontend can render a
    one-shot diagnostic.

    When *settings* resolves a host CLI choice (e.g. ``codex-cli``), the
    agent-CLI PATH/version checks target the RESOLVED binary (``codex``)
    instead of hardcoded ``claude``. The API path (``akms``) and the no-choice
    path keep dispatching via the ``claude`` agent, so the check stays on
    ``claude`` there (back-compat). Resolution never raises —
    :func:`resolve_node_gen_runtime` degrades to ``None`` (claude fallback) on
    any error.
    """
    checks: list[dict] = []

    # Resolve the host-chosen dispatch binary. CLI choices (codex-cli / a custom
    # label) swap the agent binary; the API + no-choice paths stay on claude.
    # Never raises (resolver degrades to None on any error → claude_binary).
    runtime = resolve_node_gen_runtime(settings)
    effective_binary = runtime.binary if runtime is not None else claude_binary

    # 1-2: AKMS python package imports (dispatch needs both).
    checks.append(_check_import("akms"))
    checks.append(_check_import("akms_nodes_gen"))

    # 3-4: bundled assets + runtime materialisation.
    checks.append(_check_bundled_assets())
    checks.append(_check_runtime_materialisable())

    # 5: chosen agent CLI on PATH (resolved binary, claude by default).
    claude_check = await _check_binary_present(effective_binary, "claude_cli")
    checks.append(claude_check)
    claude_path = shutil.which(effective_binary)

    # 6: chosen agent CLI --version succeeds.
    checks.append(await _check_claude_version(claude_path))

    # 7: nlm on PATH.
    nlm_check = await _check_binary_present(nlm_binary, "nlm_cli")
    checks.append(nlm_check)
    nlm_path = shutil.which(nlm_binary)

    # 8: nlm notebook list succeeds.
    checks.append(await _check_nlm_notebook_list(nlm_path))

    # 9: NLM ID non-empty.
    checks.append(_check_nlm_id_nonempty(nlm_id))

    # 10: plan JSON exists.
    checks.append(
        _check_plan_json_exists(Path(plan_path) if plan_path else None)
    )

    # 11: output dir writable.
    checks.append(
        _check_output_dir_writable(Path(out_dir) if out_dir else None)
    )

    # 12: AKMS node vault configured.
    checks.append(_check_akms_vault_configured(akms_vault))

    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "checks": checks}


# ---------------------------------------------------------------------------
# Notebook resolve / create (empty nlm_id → open a new notebook)
# ---------------------------------------------------------------------------


def parse_nlm_notebook_id(text: str) -> str:
    """Extract a notebook id from ``nlm notebook create`` stdout/stderr.

    Mirrors the batch-picker parser (``exporters._extract_id_from_output``)
    so Logic-Loom and AKMS accept the same CLI output shapes. Returns ``""``
    when nothing parseable is found.
    """
    import re

    if not text:
        return ""
    m = re.search(r'"(?:notebook_)?id"\s*:\s*"([^"]+)"', text)
    if m:
        return m.group(1)
    m = re.search(r"\b(nb_[A-Za-z0-9_-]+)\b", text)
    if m:
        return m.group(1)
    # Drive-style opaque ids (20+ alnum / underscore / hyphen).
    m = re.search(r"\b([A-Za-z0-9_-]{20,})\b", text)
    if m:
        return m.group(1)
    return ""


def create_nlm_notebook(
    title: str,
    *,
    nlm_binary: str = "nlm",
) -> str:
    """Create a new NotebookLM notebook via ``nlm notebook create``.

    Returns the new notebook id. Raises :class:`AkmsDispatchError` when the
    CLI is missing, exits non-zero, or the id cannot be parsed from output.

    Blocking I/O — call from a worker thread (``run_in_threadpool``), never on
    the FastAPI event loop.
    """
    import subprocess

    binary = shutil.which(nlm_binary) or nlm_binary
    safe_title = (title or "Logic-Loom batch").strip() or "Logic-Loom batch"
    try:
        cp = subprocess.run(
            [binary, "notebook", "create", safe_title],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AkmsDispatchError(
            f"nlm CLI not found ({nlm_binary!r}); install notebooklm-mcp-cli "
            "or put `nlm` on PATH before running node extraction without a "
            "notebook id"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise AkmsDispatchError(
            f"`nlm notebook create` timed out after {exc.timeout}s"
        ) from exc

    combined = (cp.stdout or "") + "\n" + (cp.stderr or "")
    if cp.returncode != 0:
        raise AkmsDispatchError(
            f"`nlm notebook create` exited {cp.returncode}: {combined.strip()[-400:]}"
        )
    notebook_id = parse_nlm_notebook_id(combined)
    if not notebook_id:
        raise AkmsDispatchError(
            "could not parse notebook id from `nlm notebook create` output: "
            f"{combined.strip()[:400]!r}"
        )
    return notebook_id


def resolve_nlm_id_for_batch(
    *,
    explicit_nlm_id: str | None = None,
    batch_notebook_id: str | None = None,
    batch_id: str | None = None,
    batch_title: str | None = None,
    workspace: Path | None = None,
    persist: bool = True,
    nlm_binary: str = "nlm",
) -> tuple[str, dict[str, Any]]:
    """Resolve the NotebookLM id for a node-gen run.

    Precedence:

    1. Explicit request ``nlm_id`` / ``notebook_id`` (non-empty).
    2. Existing ``batch.notebook_id``.
    3. Create a **new** notebook via :func:`create_nlm_notebook` and, when
       ``persist`` + ``workspace`` + ``batch_id`` are set, write the id back
       onto the batch so subsequent runs reuse it.

    Returns ``(notebook_id, meta)`` where ``meta`` is
    ``{"source": "explicit"|"batch"|"created", "created": bool}``.
    Raises :class:`AkmsDispatchError` only when creation is required and fails.
    """
    # BatchStore is imported lazily so a missing batches package never
    # blocks import of this module (only the persist path needs it).
    explicit = (explicit_nlm_id or "").strip()
    if explicit:
        return explicit, {"source": "explicit", "created": False}

    existing = (batch_notebook_id or "").strip()
    if existing:
        return existing, {"source": "batch", "created": False}

    title_bits = ["Logic-Loom"]
    if batch_id:
        title_bits.append(str(batch_id))
    if batch_title:
        title_bits.append("—")
        title_bits.append(str(batch_title))
    title = " ".join(title_bits)

    notebook_id = create_nlm_notebook(title, nlm_binary=nlm_binary)
    meta: dict[str, Any] = {"source": "created", "created": True}

    if persist and workspace is not None and batch_id:
        try:
            from .batches import BatchStore

            store = BatchStore.load(Path(workspace))
            batch = store.get(batch_id)
            if batch is not None:
                batch.notebook_id = notebook_id
                store.replace(batch)
                store.save(Path(workspace))
                meta["persisted"] = True
            else:
                meta["persisted"] = False
        except Exception as exc:  # noqa: BLE001 — creation succeeded
            logger.warning(
                "created notebook %s but failed to persist on batch %s: %s",
                notebook_id,
                batch_id,
                exc,
            )
            meta["persisted"] = False
            meta["persist_error"] = str(exc)

    return notebook_id, meta


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _build_node_gen_prompt(
    *, nlm_id: str, node_selection: str, plan_path: Path, out_dir: Path
) -> str:
    """The node-gen instruction prompt (shared by the claude argv + CLI templates)."""
    return (
        "Use the node-gen skill.\n\n"
        f"NLM_ID: {nlm_id}\n"
        f"NODE_SELECTION: {node_selection}\n"
        f"PLAN_PATH: {plan_path}\n"
        f"OUT_DIR: {out_dir}\n\n"
        "Run the extract workflow. Convert and validate each generated node.\n"
        "Report OK / MANUAL / SKIP at the end."
    )


def build_dispatch_argv(
    *,
    claude_binary: str,
    nlm_id: str,
    node_selection: str,
    plan_path: Path,
    out_dir: Path,
) -> list[str]:
    """Build the Claude argv for node generation.

    The canonical claude shape (``--print --output-format json`` + the prompt as the
    final arg) — node-gen parses claude's JSON output, so this is load-bearing for the
    claude (fallback / API) path. Host CLI providers (codex-cli / custom) dispatch via
    their OWN transport template instead — see :func:`_build_cli_dispatch`.

    Pure helper so the routes layer can record ``args`` on the run
    record before launch (mirroring the zsum ``build_zsum_argv`` pattern).
    """
    return [
        claude_binary,
        "--print",
        "--output-format",
        "json",
        _build_node_gen_prompt(
            nlm_id=nlm_id,
            node_selection=node_selection,
            plan_path=plan_path,
            out_dir=out_dir,
        ),
    ]


def _build_cli_dispatch(
    template: str, prompt: str
) -> tuple[list[str], str | None] | None:
    """Build ``(argv, stdin)`` for a host CLI provider's command *template*.

    Mirrors :func:`host_llm.discovery._run_cli_template`'s convention so a host CLI
    provider dispatches with ITS transport, not claude's flags:
    ``shlex``-split the template via the never-raising
    :func:`host_llm.discovery._safe_split`; if any token contains ``{prompt}`` it is
    substituted (the prompt becomes one argv element, no stdin), otherwise the prompt
    is piped to the agent's stdin. Returns ``None`` if the template is unparseable
    (the caller degrades to the claude shape; this never raises).
    """
    from .host_llm.discovery import _safe_split

    parts = _safe_split(template)
    if not parts:
        return None
    if any("{prompt}" in p for p in parts):
        return [p.replace("{prompt}", prompt) for p in parts], None
    return parts, prompt


@dataclass(slots=True, frozen=True)
class NodeGenDispatch:
    """The concrete ``(argv, env, stdin)`` for ONE node-gen subprocess launch.

    Pure data built by :func:`build_node_gen_dispatch` from the host-resolved
    :class:`NodeGenRuntime` (or the grounded ``nlm`` choice). It is the single
    source of truth for "what command does a node-gen run actually launch", shared
    by the live registry path (``routes/akms_dispatch.create_run`` →
    :meth:`RunRegistry.attach_workflow_subprocess`) and the legacy streaming helper
    (:func:`dispatch_node_generation_to_claude`) so the gate, the resolver, and the
    executor can never drift.

    ``env_overrides`` is the per-subprocess ``AKMS_LLM_*`` + key env to LAYER ON TOP
    of ``os.environ`` at spawn time — the registry driver and the streaming helper
    both merge ``{**os.environ, **env_overrides}``; the overrides are NEVER written
    process-globally (a long-lived sidecar would otherwise leak one run's
    provider env into the next). ``stdin`` is the text to feed the
    agent on stdin for a stdin-mode CLI template (``None`` for ``{prompt}``-substituted
    / claude / nlm shapes). ``binary``/``provider`` are carried for diagnostics + tests.
    """

    binary: str
    provider: str
    argv: list[str]
    env_overrides: dict[str, str] = field(default_factory=dict)
    stdin: str | None = None
    #: True for a host CLI agent (codex-cli / a custom label) dispatched via its own
    #: template. Such an agent discovers skills under ``.agents/skills/`` (not claude's
    #: ``.claude/`` tree), so the caller launches it in the codex runtime workspace
    #: (:func:`akms_claude_runtime.materialize_codex_runtime`) rather than the claude
    #: ``runtime_dir`` (issue #103). False for claude / akms / nlm / fallback.
    is_cli_agent: bool = False


def _nlm_node_prompt_path() -> str | None:
    """Filesystem path to the bundled NotebookLM per-node prompt, or ``None``.

    ``akms_nodes_gen.nlm_batch --prompt-file`` needs a real path; in dev the bundled
    resource is a real file. Returns ``None`` if it is missing (caller degrades to
    the claude fallback). TEMP-FIX follow-ups (issue #103): this resource dir is
    not yet in the wheel force-include rule (scoped to ``resources/akms_claude/``), so
    a packaged sidecar may not ship it — dev-only for now.
    """
    try:
        p = (
            Path(__file__).resolve().parent
            / "resources"
            / "akms_nlm"
            / "notebooklm_node_prompt.md"
        )
        return str(p) if p.is_file() else None
    except Exception as exc:  # noqa: BLE001 — degrade, never raise
        logger.debug("nlm node prompt resource unavailable: %s", exc)
        return None


def _build_nlm_dispatch(
    settings: Settings | None, *, plan_path: Path, out_dir: Path, nlm_id: str
) -> NodeGenDispatch | None:
    """Build the grounded-nlm dispatch when the resolved node-gen provider is ``nlm``.

    The NotebookLM-only node-gen path is the vendored ``akms_nodes_gen.nlm_batch``
    module (Python orchestrates; NotebookLM synthesizes — **no agent LLM**), NOT an
    agent CLI. The real ``nlm`` CLI has no ``run`` subcommand; ``nlm_batch`` drives
    ``nlm notebook query`` per node internally. Dispatch shape::

        <python> -m akms_nodes_gen.nlm_batch --plan <plan> --out-dir <out>
                 --prompt-file <bundled node prompt> --notebook-id <id>
                 --timeout 180 --output-format yaml

    The notebook id is the resolved provider's grounding notebook (its ``sources``),
    falling back to the request's ``nlm_id``. Returns ``None`` (→ claude fallback)
    when the resolved provider is not ``nlm``, no notebook id resolves, or the bundled
    prompt is unavailable. NEVER raises.

    Runner: ``sys.executable`` — the venv python in dev, the frozen binary in a
    packaged build. The module is addressed via :func:`module_argv`, which emits
    ``-m`` in dev and the ``--run-module`` re-entry flag when frozen (a frozen
    binary has no ``-m``; see :mod:`logic_loom_api._reentry`).
    """
    if settings is None:
        return None
    try:
        from .host_llm.routing import resolve_choice

        choice = resolve_choice(settings, "nodegen")
        if choice is None or choice.provider != "nlm":
            return None
        notebook_id = (choice.sources or {}).get("notebook_id") or (nlm_id or "").strip()
        if not notebook_id:
            # nlm resolved but no notebook id to ground on — degrade to claude
            # rather than dispatch nlm_batch with no notebook.
            logger.debug(
                "node-gen dispatch: nlm chosen but no notebook id; falling back to claude"
            )
            return None
        prompt_path = _nlm_node_prompt_path()
        if not prompt_path:
            logger.warning(
                "node-gen nlm dispatch: bundled NotebookLM prompt unavailable; "
                "falling back to claude"
            )
            return None
        argv = [
            *module_argv("akms_nodes_gen.nlm_batch"),
            "--plan",
            str(plan_path),
            "--out-dir",
            str(out_dir),
            "--prompt-file",
            prompt_path,
            "--notebook-id",
            str(notebook_id),
            "--timeout",
            "180",
            "--output-format",
            "yaml",
            # A node-gen run generates a SUBSET of a batch, so the generated edges
            # legitimately reference the wider knowledge graph (ids outside this run's
            # node set). Allow them instead of failing otherwise-valid nodes — the
            # default strict mode rejects every partial-batch run (issue #103 E2E).
            "--allow-invented-edges",
            # No in-run auto-retry: NotebookLM's query API throttles rapid back-to-back
            # calls (INVALID_ARGUMENT), and the auto-retry's repair queries compound it.
            # max-retries 0 → a transient failure is reported and re-running is a
            # human-gated action (the run resumes from the per-run cache), rather than
            # a rapid-fire retry storm (issue #103 E2E). A true inter-query backoff
            # would need an AKMS nlm_batch change (no delay flag exists) — out of the
            # adapter boundary (the installed AKMS packages are never patched).
            "--max-retries",
            "0",
        ]
        return NodeGenDispatch(binary=sys.executable, provider="nlm", argv=argv)
    except Exception as exc:  # noqa: BLE001 — degrade to claude, never raise
        logger.warning(
            "node-gen nlm dispatch build failed (%s); falling back to claude", exc
        )
        return None


def build_node_gen_dispatch(
    *,
    settings: Settings | None,
    nlm_id: str,
    node_selection: str,
    plan_path: Path,
    out_dir: Path,
    claude_binary: str = "claude",
    workflow_id: str | None = None,
) -> NodeGenDispatch:
    """Build the ``(argv, env, stdin)`` for one node-gen subprocess from the host choice.

    The single source of truth for the node-gen launch command — full dispatch:
    EVERY resolvable provider is dispatched (not merely validated by the gate), so a
    provider choice has a real end-to-end effect on the run (issue #103). Resolution:

    1. grounded **nlm** (``resolve_choice`` → ``"nlm"`` with a notebook id) — only
       when a *workflow_id* is supplied (the live run path): the ``nlm run`` shape via
       :func:`_build_nlm_dispatch`. The streaming helper passes no *workflow_id*, so an
       nlm choice degrades to the claude shape there (its long-standing behaviour).
    2. **CLI agent** (``codex-cli`` / a custom label): the provider's OWN template argv
       (``{prompt}`` substitution or stdin) via :func:`_build_cli_dispatch`, NEVER
       claude's ``--print``/``--output-format`` flags.
    3. **akms API** path: the canonical claude argv (node-gen parses claude's JSON
       output) + the resolved ``AKMS_LLM_*`` + key env overrides that steer node-gen's
       internal LiteLLM lane.
    4. **no choice / fallback**: the canonical claude argv with no env (back-compat).

    NEVER raises: :func:`resolve_node_gen_runtime` already degrades to ``None``
    (claude) on any error, and the nlm builder degrades to ``None`` likewise.
    """
    prompt = _build_node_gen_prompt(
        nlm_id=nlm_id,
        node_selection=node_selection,
        plan_path=plan_path,
        out_dir=out_dir,
    )

    # 1. Grounded nlm (live run path only — gated on workflow_id, which the
    #    streaming helper does not pass, so an nlm CHOICE degrades to the claude
    #    shape there, its long-standing behaviour).
    if workflow_id:
        nlm = _build_nlm_dispatch(
            settings, plan_path=plan_path, out_dir=out_dir, nlm_id=nlm_id
        )
        if nlm is not None:
            return nlm

    runtime = resolve_node_gen_runtime(settings)

    # 2. CLI agent (codex-cli / custom label): dispatch ITS template argv (+stdin).
    if runtime is not None and runtime.argv_template:
        cli = _build_cli_dispatch(runtime.argv_template, prompt)
        if cli is not None:
            argv, stdin_text = cli
            return NodeGenDispatch(
                binary=runtime.binary,
                provider=runtime.provider,
                argv=argv,
                env_overrides=dict(runtime.env_overrides),
                stdin=stdin_text,
                is_cli_agent=True,
            )
        # Unparseable template (defensive — resolve_node_gen_runtime's in-branch
        # _safe_split already guards this) → fall through to the claude shape.

    # 3-4. akms API path keeps the resolved claude binary + env; the no-choice /
    # CLI-template-failed fallback uses the canonical claude shape with no env.
    if runtime is not None and not runtime.argv_template:
        binary = runtime.binary  # "claude" on the API path
        env_overrides = dict(runtime.env_overrides)
        provider = runtime.provider
    else:
        binary = claude_binary
        env_overrides = {}
        provider = runtime.provider if runtime is not None else "claude"

    argv = build_dispatch_argv(
        claude_binary=binary,
        nlm_id=nlm_id,
        node_selection=node_selection,
        plan_path=plan_path,
        out_dir=out_dir,
    )
    return NodeGenDispatch(
        binary=binary,
        provider=provider,
        argv=argv,
        env_overrides=env_overrides,
        stdin=None,
    )


async def dispatch_node_generation_to_claude(
    *,
    nlm_id: str,
    node_selection: str,
    plan_path: Path,
    out_dir: Path,
    claude_binary: str = "claude",
    effective_prompts: dict[str, str] | None = None,
    cwd: Path | None = None,
    settings: Settings | None = None,
) -> AsyncIterator[str]:
    """Spawn the chosen agent CLI with the per-run runtime as ``cwd`` and stream lines.

    Yields each decoded stdout line (no trailing newline) in order. On
    non-zero exit a final JSON-encoded ``{"event":"error","message":...}``
    line is yielded so consumers see the failure at end-of-stream.

    ``cwd``:
      * If supplied, used as-is (resolved via :meth:`Path.resolve`).
        Routes pass the materialised per-run runtime path here.
      * If omitted, :func:`materialize_runtime_workspace` is called
        with the supplied ``effective_prompts`` overlay and its
        return value is used.

    Host-LLM bridge: when *settings* resolves a host choice
    (:func:`resolve_node_gen_runtime`), a CLI choice (``codex-cli`` / a custom
    label) swaps the dispatched agent binary; an API choice (``akms``)
    keeps ``claude`` but sets ``AKMS_LLM_PROVIDER`` / ``AKMS_MODEL`` /
    ``AKMS_LLM_API_BASE`` + key in the SUBPROCESS env. No choice → the
    ``claude`` fallback with no env changes. The env is applied via an
    explicit ``env=`` dict passed to the spawn — it is scoped to this one
    subprocess and NEVER written into ``os.environ``, so one request's provider
    cannot leak into the next in the long-lived sidecar.
    Resolution never raises (it degrades to the claude fallback).

    The Path.resolve() call is load-bearing on Windows — long paths and
    junctions otherwise fail to launch under
    :func:`asyncio.create_subprocess_exec`.
    """
    if cwd is None:
        runtime = materialize_runtime_workspace(
            force=True, effective_prompts=effective_prompts
        )
    else:
        runtime = Path(cwd)
        # If overlays were supplied alongside an explicit cwd, materialise
        # them into the supplied runtime (no force=True so we don't wipe a
        # caller-managed dir).
        if effective_prompts:
            materialize_runtime_workspace(
                force=False, effective_prompts=effective_prompts
            )

    runtime = runtime.resolve()

    if not plan_path.exists():
        raise AkmsDispatchError(f"PLAN_PATH does not exist: {plan_path}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build the dispatch (argv + stdin + env) via the shared builder so this
    # streaming helper and the live registry path can never drift. A host CLI
    # provider (codex-cli / custom) dispatches via its OWN template ({prompt}/stdin),
    # NOT claude's --print/--output-format flags; the claude
    # fallback / API path keeps the canonical Claude shape. No workflow_id is passed
    # here — this helper has no `nlm run` shape, so an nlm CHOICE degrades to the
    # claude shape (its long-standing behaviour). Never raises.
    dispatch = build_node_gen_dispatch(
        settings=settings,
        nlm_id=nlm_id,
        node_selection=node_selection,
        plan_path=plan_path,
        out_dir=out_dir,
        claude_binary=claude_binary,
    )
    effective_binary = dispatch.binary
    argv = dispatch.argv
    stdin_text = dispatch.stdin

    # Build the subprocess env: inherit os.environ, then layer the host-resolved
    # AKMS_LLM_* / key overrides ON A COPY (never mutate os.environ). When there
    # are no overrides (CLI / no-choice path) the env is os.environ unchanged.
    subprocess_env: dict[str, str] | None = None
    if dispatch.env_overrides:
        subprocess_env = {**os.environ, **dispatch.env_overrides}

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(runtime),
            env=subprocess_env,
            stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError as exc:
        raise AkmsDispatchError(
            f"failed to spawn agent CLI binary {effective_binary!r}: {exc}"
        ) from exc

    # Stdin-mode CLI template (no {prompt} token): feed the prompt to the agent's
    # stdin then close so it sees EOF before we stream stdout.
    if stdin_text is not None and proc.stdin is not None:
        proc.stdin.write(stdin_text.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

    assert proc.stdout is not None
    async for raw in proc.stdout:
        # rstrip("\r\n") catches both LF (POSIX) and CRLF (Windows) line
        # endings — required for the SSE stream to render uniformly on
        # every platform and for tests that grep yielded lines.
        yield raw.decode("utf-8", errors="replace").rstrip("\r\n")

    rc = await proc.wait()
    if rc != 0:
        yield json.dumps(
            {"event": "error", "message": f"agent CLI exited with code {rc}"}
        )


__all__ = [
    "AkmsDispatchError",
    "NodeGenDispatch",
    "NodeGenRuntime",
    "build_dispatch_argv",
    "build_node_gen_dispatch",
    "dispatch_node_generation_to_claude",
    "preflight_node_generation",
    "require_binary",
    "require_node_gen_binary",
    "resolve_node_gen_runtime",
    "run_command_capture",
]
