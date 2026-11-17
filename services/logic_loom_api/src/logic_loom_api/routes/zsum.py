"""``/api/zsum/*`` — pipeline runner + index/config readers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tomllib
import uuid

import tomli_w
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ..config import Settings, _user_config_path
from ..deps import get_settings, reload_settings
from ..errors import envelope
from ..runs.registry import get_registry
from ..safe_paths import split_command
from ..zsum_pipeline.bbt import bbt_status
from ..zsum_pipeline.config_doc import Update, read_doc, write_doc
from ..zsum_pipeline.index_reader import compute_stats, read_index_detailed

# Dotted paths whose blank values get backfilled with the last-installed
# local model so any pipeline stage that sends data to a local model will
# pick it up without the user having to visit the LLM settings tab.
_LOCAL_MODEL_BACKFILL_PATHS: tuple[str, ...] = (
    "llm.openai_compat.model",
    "llm.openai_compat.base_url",
    "llm.openai_compat.api_key",
    "llm.routing.cards",
    "llm.routing.digests",
    "llm.routing.redigest",
)

router = APIRouter()

logger = logging.getLogger(__name__)

LOCAL_MODEL_INSTALL_TIMEOUT_SECONDS = 60 * 60

ZsumStage = Literal[
    "skeleton",
    "index",
    "extract",
    "cards",
    "digests",
    "redigest",
    "publish",
    "all",
    "stats",
    "citations",
    "reviews",
    "restructure",
]


class RunRequest(BaseModel):
    stage: ZsumStage
    config_overrides: dict[str, str] = Field(default_factory=dict)
    # Extra CLI args appended after the stage (e.g. ["--force"] from the
    # Force toggle in the Pipeline page).
    extra_args: list[str] = Field(default_factory=list)


class LocalModelInstallRequest(BaseModel):
    model: str = Field(min_length=1)


@router.get("/status")
def status(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return {
        # Optional dev-only source checkout: null when unset.
        "zsum_repo": str(settings.zsum_repo) if settings.zsum_repo is not None else None,
        "vault_path": str(settings.vault_path) if settings.vault_path else None,
        "cache_path": str(settings.cache_path),
    }


@router.get("/runs")
def list_runs() -> dict[str, Any]:
    return {"runs": [r.model_dump() for r in get_registry().list()]}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    rec = get_registry().get(run_id)
    if rec is None:
        raise HTTPException(404, detail=f"run {run_id} not found")
    return rec.model_dump()


def _apply_host_llm_runtime(
    settings: Settings, config_overrides: dict[str, str]
) -> tuple[Path, dict[str, str]]:
    """Resolve the host LLM choice to a per-run zsum ``--config`` path + env.

    Bridge: translate the resolved host choice
    (``llm_provider`` / ``zsum_llm_provider`` via ``host_llm.routing``) into
    zsum's own provider/model/key surface so a user's Settings ▸ LLM choice
    drives the summariser without re-parsing ``zsum.config.yaml`` by hand and
    without editing vendored code (the vendored tree stays untouched).

    Returns ``(config_path, env_overrides)``:

      * ``config_path`` is the path the run's ``--config`` should point at. When
        a host choice resolves, it is a **transient effective-config file** under
        ``<cache_path>`` — a comment-preserving copy of the user's real
        ``zsum.config.yaml`` with ``llm.provider`` switched to the resolved
        backend (and the per-provider ``model`` set only when the user left it
        blank). The user's committed ``zsum.config.yaml`` is **never mutated** —
        Gate-B fix: the override is applied transiently for that one run, so a
        later cleared choice leaves the committed file at baseline (back-compat
        is sacred, symmetrically).
      * ``env_overrides`` carries the matching key env var (merged into the
        registry's ``config_overrides``, which it passes as subprocess env).

    Degrade-gracefully (back-compat no-op): when no host choice resolves,
    ``resolve_zsum_runtime`` returns ``None`` and this returns the ORIGINAL
    ``effective_zsum_config_path()`` with no env injection — zsum reads its own
    ``zsum.config.yaml`` exactly as today, byte-for-byte. The whole body is
    best-effort: a resolution/materialisation failure must never block a run, so
    it logs and falls back to the original path rather than raising.
    """
    from ..adapters.zsum_adapter import resolve_zsum_runtime

    original_path = settings.effective_zsum_config_path()
    try:
        runtime = resolve_zsum_runtime(settings)
    except Exception as exc:  # pragma: no cover — resolve_zsum_runtime never raises
        logger.warning("host LLM runtime resolution skipped: %s", exc)
        return original_path, dict(config_overrides)
    if runtime is None:
        # No host choice → original committed config, nothing injected (no-op).
        return original_path, dict(config_overrides)

    try:
        effective_path = _materialize_effective_zsum_config(settings, original_path, runtime)
    except Exception as exc:  # best-effort — a materialise hiccup must not block runs
        logger.warning(
            "host LLM transient zsum config materialisation skipped (%s); "
            "using committed config unchanged",
            exc,
        )
        effective_path = original_path

    # Forward the key env var; an explicit caller override of the same var wins.
    merged = dict(runtime.env_overrides)
    merged.update(config_overrides)
    return effective_path, merged


def _effective_zsum_config_dir(settings: Settings) -> Path:
    """Directory holding transient host-effective zsum configs (under cache_path)."""
    return settings.cache_path.expanduser() / "zsum" / "effective"


def _materialize_effective_zsum_config(
    settings: Settings, source_path: Path, runtime: Any
) -> Path:
    """Write a host-effective copy of ``source_path`` and return its path.

    Copies the user's real ``zsum.config.yaml`` verbatim (comments/ordering
    preserved by ruamel) into a transient file under :func:`_effective_zsum_config_dir`,
    then applies the runtime's overrides to THAT copy — never the committed file:

      * ``llm.provider`` is host-owned and always applied (the Settings ▸ LLM
        selector), and
      * the per-provider ``model`` is applied only when the user left it blank,
        so a pinned model survives.

    The transient file is **content-addressed per (source path + source text +
    applied overrides)** — the filename is a digest of all three, so two
    concurrent runs with DIFFERENT host choices (e.g. global ``codex-cli`` vs a
    request overriding ``zsum_llm_provider`` to anthropic) materialise to
    DISTINCT files and never read each other's provider/model (this
    closes a last-writer-wins race). Identical fingerprints still
    reuse one file, so tmp files stay bounded. The seed+apply is done into a
    uniquely-named temp file and ``os.replace``-d into place, so a concurrent run
    with the same fingerprint can never read a half-written file.

    If the source YAML is malformed (won't parse) the override is skipped and the
    SOURCE path is returned unchanged — never a silent rewrite, never a raise.

    Returns the transient path on success, or ``source_path`` when there is
    nothing to override (e.g. malformed source).
    """
    from ..adapters.zsum_adapter import runtime_config_doc_updates

    state = read_doc(source_path)
    if state.parse_error is not None:
        return source_path
    parsed = state.parsed if isinstance(state.parsed, dict) else {}

    def _get_at(path: str) -> Any:
        cur: Any = parsed
        for token in path.split("."):
            if isinstance(cur, dict) and token in cur:
                cur = cur[token]
            else:
                return None
        return cur

    updates: list[Update] = []
    for update in runtime_config_doc_updates(runtime):
        # Provider selector is host-owned and always applied; per-provider model
        # is blank-only so a user's pinned model survives into the effective copy.
        if update.path != "llm.provider" and not _is_blank_config_value(_get_at(update.path)):
            continue
        updates.append(update)

    if not updates:
        return source_path

    out_dir = _effective_zsum_config_dir(settings)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Content-address over the FULL run fingerprint: source path + source text +
    # the exact applied (path, value) overrides. Different choice/model/overrides
    # ⇒ different filename ⇒ no cross-run contamination; identical ⇒ same file.
    fingerprint = hashlib.sha256()
    fingerprint.update(str(source_path.resolve()).encode("utf-8"))
    fingerprint.update(b"\0")
    fingerprint.update(state.raw.encode("utf-8"))
    for u in updates:
        fingerprint.update(b"\0")
        fingerprint.update(f"{u.path}={u.value!r}".encode("utf-8"))
    digest = fingerprint.hexdigest()[:32]
    effective_path = out_dir / f"{digest}.zsum.config.yaml"

    # Seed + apply into a uniquely-named temp file, then atomically move it into
    # the content-addressed slot. Two-step write into the shared path would let a
    # same-fingerprint concurrent run read a half-written file; os.replace is
    # atomic on POSIX/Windows so a reader sees either the old file or the complete
    # new one. write_doc applies the host overrides via the comment-preserving
    # writer (read_doc returns raw text even for a non-existent committed file).
    tmp_path = effective_path.with_name(f"{effective_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp_path.write_text(state.raw, encoding="utf-8")
        write_doc(tmp_path, updates)
        os.replace(tmp_path, effective_path)
    finally:
        # Clean up the temp file if the move didn't consume it (e.g. write_doc raised).
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:  # pragma: no cover - best-effort cleanup
                pass
    return effective_path


@router.post("/runs")
async def submit_run(
    req: RunRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    config_path, config_overrides = _apply_host_llm_runtime(
        settings, dict(req.config_overrides)
    )
    # zsum runs from the installed package; the zsum_repo checkout is only an
    # optional dev cwd. Anchor on the user's own config dir (not the derived
    # host-LLM copy) so zsum's relative defaults resolve next to it.
    cwd = _zsum_cwd(settings, settings.effective_zsum_config_path())
    if not cwd.is_dir():
        raise HTTPException(
            412,
            detail=(
                f"zsum working directory not found: {cwd}. Set zsum_config_path "
                "to your zsum.config.yaml."
            ),
        )
    try:
        rec = await get_registry().submit(
            req.stage,
            config_overrides=config_overrides,
            extra_args=req.extra_args,
            config_path=config_path,
            python_cmd=settings.zsum_python_cmd,
            cwd_override=cwd,
        )
    except RuntimeError as e:
        raise HTTPException(412, detail=str(e))
    return rec.model_dump()


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict[str, str]:
    ok = get_registry().cancel(run_id)
    if not ok:
        raise HTTPException(409, detail="run not running or already terminated")
    return {"status": "cancelling"}


def _resolve_relative_to(raw: str, base: Path) -> Path:
    """Expand ~ and resolve relative paths against ``base``. Mirrors the
    Obsidian plugin's resolveConfiguredPath()."""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (base / p).resolve()
    return p


def _read_zsum_config_yaml(config_path: Path) -> dict[str, Any] | None:
    if not config_path.exists():
        return None
    try:
        import yaml as _y  # type: ignore[import-untyped]

        data = _y.safe_load(config_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _zsum_cwd(settings: Settings, config_path: Path) -> Path:
    """Working directory for zsum subprocesses.

    The optional ``zsum_repo`` checkout when present, else the directory of
    the effective config — zsum runs from the installed package, and its
    relative defaults (``./cache``) then land next to the config.
    """
    return settings.zsum_repo_dir() or config_path.expanduser().parent


def _resolve_cache_dir(settings: Settings) -> Path:
    """cache_dir from zsum config (relative → resolved against config dir).
    Falls back to zsum's own ``./cache`` default under :func:`_zsum_cwd`."""
    config_path = settings.effective_zsum_config_path()
    data = _read_zsum_config_yaml(config_path)
    if isinstance(data, dict) and isinstance(data.get("cache_dir"), str):
        return _resolve_relative_to(data["cache_dir"], config_path.parent)
    return _zsum_cwd(settings, config_path) / "cache"


def _samefile(a: Path, b: Path) -> bool:
    """True if two paths point to the same file/dir on disk after symlink
    resolution. Survives the case where one side doesn't exist."""
    try:
        return a.exists() and b.exists() and a.resolve() == b.resolve()
    except OSError:
        return False


def _maybe_load_setup_model_catalog() -> dict[str, Any] | None:
    try:
        from zsum.setup_model import (  # type: ignore[import-not-found]
            MODEL_TIERS,
            OLLAMA_API_KEY,
            OLLAMA_ENDPOINT,
            TIER_ORDER,
        )
    except ImportError:
        return None

    tiers: list[dict[str, Any]] = []
    for tier_id in TIER_ORDER:
        info = MODEL_TIERS.get(tier_id, {})
        tiers.append(
            {
                "id": tier_id,
                "model": str(info.get("model", "")).strip(),
                "ram": str(info.get("ram", "")).strip(),
                "params": str(info.get("params", "")).strip(),
                "note": str(info.get("note", "")).strip(),
                "is_default": tier_id == "medium",
            }
        )

    return {
        "tiers": tiers,
        "ollama_endpoint": OLLAMA_ENDPOINT,
        "ollama_api_key": OLLAMA_API_KEY,
    }


def _load_setup_model_catalog_or_raise() -> dict[str, Any]:
    catalog = _maybe_load_setup_model_catalog()
    if catalog is None:
        raise HTTPException(
            412,
            detail="zsum setup-model support is unavailable in this build.",
        )
    return catalog


def _nested_string(mapping: dict[str, Any] | None, *tokens: str) -> str | None:
    cur: Any = mapping
    for token in tokens:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(token)
    if isinstance(cur, str) and cur.strip():
        return cur.strip()
    return None


def _local_model_defaults(
    settings: Settings,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    model = (settings.local_model_last_installed or "").strip()
    if not model:
        return None
    catalog = catalog or _maybe_load_setup_model_catalog()
    return {
        "model": model,
        "base_url": catalog.get("ollama_endpoint") if catalog else None,
        "api_key": catalog.get("ollama_api_key") if catalog else None,
        "routing_provider": "openai_compat",
    }


def _build_local_models_state(
    settings: Settings,
    catalog: dict[str, Any],
) -> dict[str, Any]:
    config_path = settings.effective_zsum_config_path()
    config_data = _read_zsum_config_yaml(config_path)
    current_config_model = _nested_string(config_data, "llm", "openai_compat", "model")

    predefined_models = {
        tier["model"]
        for tier in catalog["tiers"]
        if isinstance(tier.get("model"), str) and tier["model"].strip()
    }
    custom_models: list[str] = []
    seen: set[str] = set()
    for model in settings.local_models_custom:
        normalized = model.strip()
        if not normalized or normalized in predefined_models or normalized in seen:
            continue
        custom_models.append(normalized)
        seen.add(normalized)

    return {
        "tiers": catalog["tiers"],
        "custom_models": custom_models,
        "last_installed_model": (settings.local_model_last_installed or "").strip() or None,
        "current_config_model": current_config_model,
        "requires_openai_compatible": True,
        "ollama_endpoint": catalog.get("ollama_endpoint"),
        "config_path": str(config_path),
        "local_model_defaults": _local_model_defaults(settings, catalog),
    }


def _config_doc_payload(
    settings: Settings,
    *,
    state: Any,
) -> dict[str, Any]:
    payload = state.model_dump()
    payload["local_model_defaults"] = _local_model_defaults(settings)
    return payload


def build_setup_model_argv(settings: Settings, model: str) -> tuple[list[str], Path]:
    cmd_parts = split_command(settings.zsum_python_cmd)
    if not cmd_parts:
        raise ValueError("zsum_python_cmd is empty")

    config_path = settings.effective_zsum_config_path()
    cwd = _zsum_cwd(settings, config_path)
    argv = [*cmd_parts, "--config", str(config_path), "setup-model", "--model", model]
    return argv, cwd


async def _run_setup_model_command(settings: Settings, model: str) -> dict[str, Any]:
    try:
        argv, cwd = build_setup_model_argv(settings, model)
    except ValueError as exc:
        return {
            "cmd": [],
            "cwd": str(_zsum_cwd(settings, settings.effective_zsum_config_path())),
            "exit_code": None,
            "output": str(exc),
        }

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=LOCAL_MODEL_INSTALL_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "cmd": argv,
                "cwd": str(cwd),
                "exit_code": None,
                "output": f"setup-model timed out after {LOCAL_MODEL_INSTALL_TIMEOUT_SECONDS}s",
            }
    except FileNotFoundError as exc:
        return {
            "cmd": argv,
            "cwd": str(cwd),
            "exit_code": None,
            "output": f"command not found: {argv[0]} ({exc})",
        }

    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    output = "\n".join(part for part in (stdout_text, stderr_text) if part).strip() or "(no output)"
    return {
        "cmd": argv,
        "cwd": str(cwd),
        "exit_code": proc.returncode,
        "output": output,
    }


def _is_blank_config_value(raw: Any) -> bool:
    """Mirror of the frontend ``isBlankConfigValue`` — treat ``None``,
    missing keys, and whitespace-only strings as "not yet set"."""
    return raw is None or (isinstance(raw, str) and raw.strip() == "")


def _backfill_value_for_path(
    path: str, defaults: dict[str, Any]
) -> Any | None:
    """Return the local-model default for ``path``, or ``None`` if the
    defaults don't cover it."""
    if path == "llm.openai_compat.model":
        return defaults.get("model")
    if path == "llm.openai_compat.base_url":
        return defaults.get("base_url")
    if path == "llm.openai_compat.api_key":
        return defaults.get("api_key")
    if path in ("llm.routing.cards", "llm.routing.digests", "llm.routing.redigest"):
        return defaults.get("routing_provider")
    return None


def _apply_local_model_to_zsum_config(
    config_path: Path, defaults: dict[str, Any]
) -> list[Update]:
    """Write the installed local model into ``zsum.config.yaml`` for fields
    that are currently blank — never overwrite an explicit user value.

    Returns the list of updates actually applied so callers can decide what
    to log/surface; an empty list means the user already has values set for
    every backfill path.
    """
    if not defaults.get("model"):
        return []
    state = read_doc(config_path)
    if state.parse_error is not None:
        # If the YAML doesn't parse we don't want to silently rewrite it.
        return []
    parsed = state.parsed if isinstance(state.parsed, dict) else {}

    def _get_at(path: str) -> Any:
        cur: Any = parsed
        for token in path.split("."):
            if isinstance(cur, dict) and token in cur:
                cur = cur[token]
            else:
                return None
        return cur

    updates: list[Update] = []
    for dotted in _LOCAL_MODEL_BACKFILL_PATHS:
        candidate = _backfill_value_for_path(dotted, defaults)
        if candidate is None or _is_blank_config_value(candidate):
            continue
        if not _is_blank_config_value(_get_at(dotted)):
            continue
        updates.append(Update(path=dotted, value=candidate))

    if not updates:
        return []
    write_doc(config_path, updates)
    return updates


def _persist_local_model_preferences(
    settings: Settings,
    model: str,
    *,
    predefined_models: set[str],
) -> Path:
    target = _user_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, Any] = {}
    if target.exists():
        with target.open("rb") as handle:
            existing = tomllib.load(handle)

    custom_models = [
        candidate.strip()
        for candidate in settings.local_models_custom
        if candidate.strip() and candidate.strip() != model
    ]
    if model not in predefined_models:
        custom_models.append(model)
    custom_models = list(dict.fromkeys(custom_models))

    existing["local_model_last_installed"] = model
    if custom_models:
        existing["local_models_custom"] = custom_models
    else:
        existing.pop("local_models_custom", None)

    tmp = target.with_suffix(".toml.tmp")
    with tmp.open("wb") as handle:
        tomli_w.dump(existing, handle)
    tmp.replace(target)
    return target


@router.get("/effective-config")
def effective_config(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Show the user *exactly* which zsum.config.yaml is in use, where it
    will write, where its cache lives, and whether any of those paths
    disagree with LogicLoom's own settings.

    This is the answer to "why doesn't the pipeline output match what I
    expected?" — the Settings page lets you change ``zsum_config_path``
    independently of zsum's own ``vault_output``, and a mismatch silently
    splits state between the two views. The banner in the Pipeline page
    surfaces the disagreement and offers a one-click fix.
    """
    config_path = settings.effective_zsum_config_path()
    config_source = "override" if settings.zsum_config_path else "default"
    data = _read_zsum_config_yaml(config_path)
    config_dir = config_path.parent

    vault_output_raw: str | None = None
    vault_output_resolved: str | None = None
    cache_dir_raw: str | None = None
    cache_dir_resolved: str | None = None
    if isinstance(data, dict):
        vault_output_value = data.get("vault_output")
        if isinstance(vault_output_value, str):
            vault_output_raw = vault_output_value
            vault_output_resolved = str(_resolve_relative_to(vault_output_value, config_dir))
        cache_dir_value = data.get("cache_dir")
        if isinstance(cache_dir_value, str):
            cache_dir_raw = cache_dir_value
            cache_dir_resolved = str(_resolve_relative_to(cache_dir_value, config_dir))

    cache_dir_path = (
        Path(cache_dir_resolved)
        if cache_dir_resolved
        else (_zsum_cwd(settings, config_path) / "cache")
    )
    index_json_path = cache_dir_path / "index.json"

    logic_loom_vault = str(settings.vault_path) if settings.vault_path else None
    vault_mismatch = bool(
        logic_loom_vault
        and vault_output_resolved
        and not _samefile(Path(logic_loom_vault), Path(vault_output_resolved))
    )

    # Bonus: if the user's vault_path contains its own zsum.config.yaml,
    # surface it as a candidate so they can switch with one click.
    vault_candidate: str | None = None
    if settings.vault_path:
        cand = settings.vault_path / "zsum.config.yaml"
        if cand.exists() and not _samefile(cand, config_path):
            vault_candidate = str(cand)

    return {
        "config_path": str(config_path),
        "config_exists": config_path.exists(),
        "config_source": config_source,  # "override" | "default"
        "vault_output_raw": vault_output_raw,
        "vault_output_resolved": vault_output_resolved,
        "cache_dir_raw": cache_dir_raw,
        "cache_dir_resolved": str(cache_dir_path),
        "index_json_path": str(index_json_path),
        "index_exists": index_json_path.exists(),
        "logic_loom_vault_path": logic_loom_vault,
        "vault_mismatch": vault_mismatch,
        "vault_candidate": vault_candidate,
    }


@router.get("/index")
def get_index(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Return the parsed ``index.json`` plus computed library stats.

    Mirrors the data the Obsidian plugin's ``readIndexDetailed`` +
    ``computeStats`` feed into the Library status bar / tree / papers.
    """
    cache_dir = _resolve_cache_dir(settings)
    res = read_index_detailed(cache_dir)
    payload: dict[str, Any] = {
        "kind": res.kind,
        "message": res.message,
        "cache_dir": str(cache_dir),
    }
    if res.index is None:
        return payload
    payload["index"] = res.index.model_dump()
    payload["stats"] = compute_stats(res.index).model_dump()
    return payload


@router.get("/bbt-status")
def get_bbt_status(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return bbt_status(settings.effective_zsum_config_path())


@router.get("/config-doc")
def get_config_doc(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return _config_doc_payload(
        settings,
        state=read_doc(settings.effective_zsum_config_path()),
    )


class ConfigDocUpdates(BaseModel):
    updates: list[Update]


@router.post("/config-doc")
def post_config_doc(
    payload: ConfigDocUpdates,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Apply a flat list of dotted-path updates to zsum.config.yaml,
    preserving comments / ordering / blank lines around untouched keys.
    """
    if not payload.updates:
        return _config_doc_payload(
            settings,
            state=read_doc(settings.effective_zsum_config_path()),
        )
    try:
        state = write_doc(settings.effective_zsum_config_path(), payload.updates)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    return _config_doc_payload(settings, state=state)


@router.get("/local-models")
def get_local_models(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    catalog = _load_setup_model_catalog_or_raise()
    return _build_local_models_state(settings, catalog)


@router.post("/local-models/install")
async def install_local_model(
    payload: LocalModelInstallRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    catalog = _load_setup_model_catalog_or_raise()
    model = payload.model.strip()
    if not model:
        raise HTTPException(400, detail="model is required")

    result = await _run_setup_model_command(settings, model)
    updated_settings = settings
    backfilled_paths: list[str] = []
    if result.get("exit_code") == 0:
        predefined_models = {
            tier["model"]
            for tier in catalog["tiers"]
            if isinstance(tier.get("model"), str) and tier["model"].strip()
        }
        _persist_local_model_preferences(
            settings,
            model,
            predefined_models=predefined_models,
        )
        reload_settings()
        updated_settings = get_settings()
        defaults = _local_model_defaults(updated_settings, catalog)
        if defaults is not None:
            applied = _apply_local_model_to_zsum_config(
                updated_settings.effective_zsum_config_path(),
                defaults,
            )
            backfilled_paths = [u.path for u in applied]

    return {
        "status": "configured" if result.get("exit_code") == 0 else "failed",
        "installed_model": model,
        "backfilled_paths": backfilled_paths,
        **result,
        **_build_local_models_state(updated_settings, catalog),
    }


@router.post("/test")
async def test_environment(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Run ``<zsum_python_cmd> --config <path> stats`` and return its output.

    Mirrors the "Test" button in the Obsidian "Zotero Summarizer" plugin —
    a quick reachability check for the configured Python environment.

    A non-zero exit is a failed test: the answer is a 502 ``zsum_test_failed``
    envelope whose ``detail`` carries the same fields as a success (it used to
    be a 200 with ``exit_code`` 1, which clients read as success).
    """
    cmd_parts = split_command(settings.zsum_python_cmd)
    if not cmd_parts:
        raise HTTPException(400, detail="zsum_python_cmd is empty")
    config_path = settings.effective_zsum_config_path()
    cwd = _zsum_cwd(settings, config_path)
    argv = [*cmd_parts, "--config", str(config_path), "stats"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise HTTPException(504, detail="zsum stats timed out after 60s")
    except FileNotFoundError as e:
        # Bubble up as 4xx so the UI can show "command not found" rather than 500.
        raise HTTPException(400, detail=f"command not found: {cmd_parts[0]} ({e})")
    result = {
        "exit_code": proc.returncode,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "cmd": argv,
        "cwd": str(cwd),
    }
    if proc.returncode != 0:
        return JSONResponse(
            envelope(
                "zsum_test_failed",
                f"zsum stats exited with code {proc.returncode}",
                result,
            ),
            status_code=502,
        )
    return result


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str) -> EventSourceResponse:
    reg = get_registry()
    if reg.get(run_id) is None:
        raise HTTPException(404, detail=f"run {run_id} not found")

    async def gen():
        async for evt in reg.stream(run_id):
            yield {"event": "log", "data": json.dumps(evt.model_dump())}
        yield {"event": "done", "data": json.dumps(reg.get(run_id).model_dump() if reg.get(run_id) else {})}

    return EventSourceResponse(gen())
