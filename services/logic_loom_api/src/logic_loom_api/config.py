"""Resolved LogicLoom settings.

Priority (highest first):
  1. Constructor kwargs (e.g. CLI overrides).
    2. Environment variables prefixed ``LOGIC_LOOM_``.
    3. User TOML at ``~/.config/logic-loom/config.toml`` (or whatever ``--config`` points at).
  4. Defaults.
"""

from __future__ import annotations

import os
import shlex
import sys
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, EnvSettingsSource, SettingsConfigDict

from ._reentry import is_frozen, module_cmd

PLACEHOLDER_PATTERN = "<"
ZSUM_LAUNCHER_MODULE = "logic_loom_api._zsum_launcher"
TEMPLATE_NAME = "config.toml.template"
DEFAULT_AKMS_GLOBAL_VAULT = Path("~/.claude/akms/nodes")


def _default_akms_workspace() -> Path:
    """Platform data dir for the AKMS workspace (avoids macOS Documents TCC).

    ``~/Documents/...`` is protected by macOS privacy prompts. Sidecars
    launched from restricted hosts (sandboxed terminals, some IDE runners,
    unsigned Tauri shells without Full Disk Access) hit
    ``PermissionError: [Errno 1] Operation not permitted`` on every
    batches/workflows read and the Assistant UI looks "dead". Prefer the
    standard per-user application data location instead.
    """
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Logic-Loom" / "AKMS-Workspace"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (home / "AppData" / "Local"))
        return base / "Logic-Loom" / "AKMS-Workspace"
    return home / ".local" / "share" / "logic-loom" / "AKMS-Workspace"


def _default_zsum_python_cmd() -> str:
    """Return the canonical "spawn zsum through the sidecar's own runtime" cmd.

    Resolved lazily so the value tracks the actual runtime — in dev that's
    ``<venv python> -m logic_loom_api._zsum_launcher``; in packaged builds
    ``sys.executable`` is the frozen binary, which has no ``-m``, so the
    launcher is addressed through the ``--run-module`` re-entry flag instead
    (see :mod:`logic_loom_api._reentry`). Either way the embedded zsum
    package is importable, so the bundled launcher always resolves.

    Using ``sys.executable`` rather than the bare string ``"python"`` is
    deliberate: ``python`` on a user's PATH may be Python 2 (macOS pre-3.6),
    a Conda env, or otherwise unrelated to the sidecar's runtime.
    """
    return module_cmd(ZSUM_LAUNCHER_MODULE)


def template_path() -> Path:
    return Path(__file__).parent / "templates" / TEMPLATE_NAME


def ensure_user_config_exists() -> Path:
    """Copy the shipped template to the user config path if absent. Returns the target path."""
    target = _user_config_path()
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    src = template_path()
    if src.exists():
        target.write_text(src.read_text())
    else:
        target.write_text("")
    return target


def _user_config_path() -> Path:
    return Path(os.environ.get("LOGIC_LOOM_CONFIG", str(Path.home() / ".config" / "logic-loom" / "config.toml")))


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def _filter_placeholders(data: dict[str, Any]) -> dict[str, Any]:
    """Drop values that look like ``<PLACEHOLDER>`` so the placeholder ships through to first-run UX."""
    return {k: v for k, v in data.items() if not (isinstance(v, str) and v.startswith(PLACEHOLDER_PATTERN) and v.endswith(">"))}


def is_placeholder(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(PLACEHOLDER_PATTERN) and value.endswith(">")


def _existing_dir(value: Path | None) -> Path | None:
    """Tilde-expanded ``value`` when it names an existing directory, else None."""
    if value is None:
        return None
    path = Path(value).expanduser()
    return path if path.is_dir() else None


#: Legacy ``zsum_python_cmd`` values that predate the bundled launcher. Users with these in
#: their persisted ``config.toml`` get a "spawn failed [Errno 2]" because the
#: Tauri-spawned sidecar inherits a stripped PATH on macOS — ``uv`` lives in
#: ``~/.local/bin`` (or wherever the user installed it) and that is rarely
#: on the GUI-app PATH. The current default factory routes through the
#: sidecar's own ``sys.executable`` + the bundled launcher, which always
#: resolves, so silently dropping the legacy value here causes the field's
#: ``default_factory`` to fire on the next ``Settings`` construction.
_LEGACY_ZSUM_PYTHON_CMD_VALUES: frozenset[str] = frozenset({"uv run zsum"})


def _is_broken_frozen_zsum_cmd(cmd: str) -> bool:
    """True for a persisted ``<this frozen binary> -m <module>`` command.

    Written by any packaged build that saved Settings before the
    ``--run-module`` pivot. ``-m`` is an *interpreter* flag; on the frozen
    binary it falls through to argparse and dies with "unrecognized
    arguments", so the value can never work and is dropped to let the
    default factory re-fire.

    Deliberately narrow: only the frozen binary matches. A user pointing at
    a real external interpreter (``/opt/my-python/bin/python -m …``) still
    works and passes through untouched.
    """
    if not is_frozen():
        return False
    try:
        parts = shlex.split(cmd)
    except ValueError:  # unbalanced quotes — leave it for the caller to fail on
        return False
    return len(parts) >= 2 and parts[0] == sys.executable and parts[1] == "-m"


def _migrate_legacy_values(data: dict[str, Any]) -> dict[str, Any]:
    """Quietly drop fields whose persisted value is a known-broken default.

    Pattern: the user's ``config.toml`` was written when ``Settings``
    persisted the literal default value, then the default itself changed
    in code (e.g. the switch to the bundled zsum launcher). On every subsequent load the
    stale literal wins over the new default factory, leaving the user
    stuck on the broken behaviour. Dropping the field here lets the new
    ``default_factory`` re-fire on construction.

    Only matches values we know are broken; arbitrary user customisations
    (e.g. ``zsum_python_cmd = "/opt/my-python/bin/python -m zsum.cli"``)
    pass through untouched.
    """
    out = dict(data)
    cmd = out.get("zsum_python_cmd")
    if isinstance(cmd, str) and (
        cmd.strip() in _LEGACY_ZSUM_PYTHON_CMD_VALUES or _is_broken_frozen_zsum_cmd(cmd)
    ):
        out.pop("zsum_python_cmd", None)
    return out


#: Failure-memory permission modes.
#:
#: ``read-only`` is the DEFAULT: inspection,
#: preview and diagnostics, with every package call made on a non-writing code
#: path. ``operator`` additionally unlocks the record/refresh/compile endpoints;
#: it must be turned on deliberately in ``config.toml`` (or via
#: ``LOGIC_LOOM_FAILURE_MEMORY_MODE``) and is intentionally NOT settable through
#: ``PATCH /api/app/config`` — a write-permission escalation should never be one
#: unauthenticated HTTP call away. ``disabled`` turns the whole surface off.
FailureMemoryMode = Literal["disabled", "read-only", "operator"]

#: How far ``FailureMemoryProject.resolved_root`` will ascend from a project
#: config file while looking for the repository root that the config's own
#: relative ``[paths]`` are anchored to. Bounded so a misconfigured entry fails
#: fast instead of walking to ``/``.
FAILURE_MEMORY_ROOT_SEARCH_DEPTH = 8


class FailureMemoryProject(BaseModel):
    """One EXPLICITLY configured failure-memory project.

    Logic-Loom never discovers ``.failure-memory`` directories by walking the
    filesystem: a project exists
    for Logic-Loom only because a human wrote it into ``config.toml``::

        [[failure_memory_projects]]
        id = "logic-loom"
        config = "/path/to/Logic-Loom/.failure-memory/config.toml"

    ``root`` (the repository root the project's relative ``[paths]`` resolve
    against) is optional because for the common layout it is derivable, but the
    derivation only ever ASCENDS from the explicitly configured config file — it
    never scans sibling or child directories. Set ``root`` explicitly for
    layouts where the config does not sit directly under the repository root
    (e.g. a config kept at ``dev/knowledge/failure-memory.toml``).
    """

    id: str = Field(min_length=1, description="Stable project identifier used in API paths and overlay node ids.")
    config: Path = Field(description="Absolute path to the project's failure-memory config TOML.")
    root: Path | None = Field(
        default=None,
        description=(
            "Repository root the project's relative [paths] resolve against. "
            "None = derive by ascending from the config file (never by scanning)."
        ),
    )

    def config_path(self) -> Path:
        return self.config.expanduser()

    def declared_root(self) -> Path | None:
        return self.root.expanduser() if self.root is not None else None

    def resolved_root(self, registry_relative: str | None = None) -> Path:
        """Return the repository root for this project.

        Precedence:
          1. Explicit ``root``.
          2. The nearest ancestor ``A`` of the config file (starting at its own
             directory) for which ``A/<registry_relative>`` exists — this is the
             only derivation, and it strictly ascends.
          3. The config file's grandparent (``<repo>/.failure-memory/config.toml``
             → ``<repo>``), which is what ``failure-memory init`` produces.

        ``registry_relative`` is the project config's own ``paths.registry``
        value; callers that have not loaded the config yet may omit it and get
        rule 3.
        """
        explicit = self.declared_root()
        if explicit is not None:
            return explicit.resolve()
        config = self.config_path().resolve()
        if registry_relative:
            candidate = config.parent
            for _ in range(FAILURE_MEMORY_ROOT_SEARCH_DEPTH):
                if (candidate / registry_relative).exists():
                    return candidate
                if candidate.parent == candidate:
                    break
                candidate = candidate.parent
        return config.parent.parent


class RecordsStore(BaseModel):
    """One EXPLICITLY configured akms-records store.

    Like failure-memory projects, Logic-Loom never discovers record stores by
    scanning: a store is visible only because a human listed it::

        [[records_stores]]
        id = "my-project"
        scope = "project:my-project"
        repo = "/path/to/my-project"      # project scope: <repo>/.knowledge/records

        [[records_stores]]
        id = "scout"
        scope = "agent:scout"             # agent/shared: ~/.knowledge/records/...
        # home = "/custom/records-home"   # optional, replaces ~/.knowledge/records

    ``scope`` is akms-records' own ``kind:id`` notation. The store root is
    resolved by ``akms_records.store_root`` — Logic-Loom does not restate the
    layout rules.
    """

    id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
        description="Stable store identifier used in API paths.",
    )
    scope: str = Field(
        pattern=r"^(project|agent|shared):[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
        description="akms-records scope, as kind:id (project, agent or shared).",
    )
    repo: Path | None = Field(
        default=None,
        description="Repository root for a project scope (records live in <repo>/.knowledge/records).",
    )
    home: Path | None = Field(
        default=None,
        description="Records home for agent/shared scopes. None = AKMS_RECORDS_HOME or ~/.knowledge/records.",
    )


class Settings(BaseSettings):
    """LogicLoom runtime settings."""

    model_config = SettingsConfigDict(env_prefix="LOGIC_LOOM_", extra="ignore", arbitrary_types_allowed=True)

    # Paths
    # Source checkouts are optional, development-only overrides. None (the
    # default) means "no checkout": every feature resolves from the installed
    # packages, the Logic-Loom workspace or a documented fallback. Use
    # akms_repo_dir() / zsum_repo_dir() to read them.
    logic_loom_root: Path | None = Field(
        default=None,
        description="Optional Logic-Loom source checkout (display only). None = unset.",
    )
    akms_repo: Path | None = Field(
        default=None,
        description=(
            "Optional, development-only AKMS source checkout. None = unset; nothing "
            "requires it, in packaged builds or otherwise. When present it adds "
            "the legacy batch plan, "
            "Packages/Nodes_Vault, local graph nodes and the /api/nlm developer scripts."
        ),
    )
    akms_workspace: Path = Field(
        default_factory=_default_akms_workspace,
        description=(
            "Logic-Loom-owned AKMS workspace root. Holds runtime state (prompts, "
            "workflows, batches, runs, generated nodes, NLM inputs/outputs). "
            "Bootstrapped on first sidecar startup; safe in packaged builds. "
            "Default is under Application Support / XDG data (not Documents) "
            "so macOS TCC does not block the sidecar."
        ),
    )
    akms_global_vault: Path | None = Field(
        default=None,
        description=(
            "Source vault for AKMS graph compilation. None = resolve via "
            "<akms_repo>/Packages/Nodes_Vault when that optional checkout is "
            "present, then AKMS_GLOBAL_VAULT, then the AKMS default."
        ),
    )
    learn_domain_packs_path: Path | None = Field(
        default=None,
        description=(
            "Root directory scanned (recursively) for AKMS Learn domain-pack "
            "descriptor YAMLs (files named 'domain_pack.yaml'). Read as DATA "
            "via akms_learn's descriptor loader to drive the Learn tab's "
            "domain-pack status UI. None = resolve via "
            "learn_domain_packs_search_root() under akms_repo. An empty or "
            "missing directory yields domain_packs: []."
        ),
    )
    zsum_repo: Path | None = Field(
        default=None,
        description=(
            "Optional, development-only zotero-summarizer checkout. None = unset; "
            "zsum runs from the installed package."
        ),
    )

    @field_validator("logic_loom_root", "akms_repo", "zsum_repo", mode="before")
    @classmethod
    def _blank_checkout_is_unset(cls, value: Any) -> Any:
        """An empty / whitespace value (e.g. ``LOGIC_LOOM_AKMS_REPO=``) means
        unset. Without this, ``Path("")`` is ``.``, i.e. the process cwd."""
        if isinstance(value, str) and not value.strip():
            return None
        return value
    vault_path: Path | None = Field(
        default=None,
        description="Published Obsidian vault root. None = unset → first-run UX.",
    )
    cache_path: Path = Field(
        default_factory=lambda: Path.home() / ".cache" / "logic-loom",
        description="LogicLoom-owned cache (run history, port file, etc.).",
    )

    # NotebookLM
    nlm_mcp_url: str | None = Field(
        default=None,
        description="NotebookLM MCP server URL for document-grounded node generation (e.g. http://localhost:3000/sse).",
    )

    # Nodes Review
    nodes_folders: list[Path] = Field(
        default_factory=list,
        description="Staging directories scanned for AKMS node .md files in the Nodes Review tab.",
    )
    nodes_editor: str | None = Field(
        default=None,
        description=(
            "Editor command for opening nodes (e.g. 'code'). None = system default (macOS: open). "
            "The Settings page accepts known editors with plain flags only; set any other "
            "program here in config.toml."
        ),
    )

    # Networking
    port: int | None = Field(default=None, description="Fixed port, or None for --auto-port.")

    # Search
    search_backend: Literal["ripgrep", "qmd"] = Field(
        default="ripgrep",
        description="Default backend for /api/vault/search.",
    )
    vault_qmd_collection: str | None = Field(
        default=None,
        description="QMD collection name registered for the vault. Required when search_backend=qmd.",
    )

    # Zotero Summarizer (mirrors the Obsidian "Zotero Summarizer" plugin
    # settings — see plugin_dev/src/settings.ts for the source of truth.)
    zsum_config_path: Path | None = Field(
        default=None,
        description="Absolute path to zsum.config.yaml. None → defaults to <zsum_repo>/zsum.config.yaml at use time.",
    )
    zsum_python_cmd: str = Field(
        default_factory=lambda: _default_zsum_python_cmd(),
        description=(
            "Command used to invoke the zsum pipeline. Defaults to running "
            "the bundled launcher through the sidecar's own Python interpreter, "
            "which works in both dev and packaged builds. Override (e.g. "
            "'uv run zsum') only if you want to point at a different zsum install."
        ),
    )
    zsum_cost_per_mtoken_in: float = Field(
        default=3.0,
        ge=0.0,
        description="USD cost per million input tokens (for cost estimation only).",
    )
    zsum_cost_per_mtoken_out: float = Field(
        default=15.0,
        ge=0.0,
        description="USD cost per million output tokens (for cost estimation only).",
    )
    zsum_tokens_per_card_in: int = Field(
        default=4000,
        ge=0,
        description="Estimated input tokens per card.",
    )
    zsum_tokens_per_card_out: int = Field(
        default=500,
        ge=0,
        description="Estimated output tokens per card.",
    )
    local_models_custom: list[str] = Field(
        default_factory=list,
        description="User-added local model names shown in Home → Models.",
    )
    local_model_last_installed: str | None = Field(
        default=None,
        description="Most recent local model successfully installed/configured from LogicLoom.",
    )

    # ── Learn LLM provider ──────────────────────────────────────────────────
    # Drives the AKMS Learn `llm_expanded` mode. When an API key is set the
    # akms completion (LiteLLM) route is used; otherwise an explicitly-chosen
    # agent CLI (claude/codex/custom) drives expansion. Keys are injected into
    # the sidecar env at startup, mirroring the openalex/semantic-scholar
    # plaintext-in-config precedent below.
    anthropic_api_key: str | None = Field(
        default=None,
        description="Anthropic API key (Claude). Enables the API route for Learn LLM expansion.",
    )
    openai_api_key: str | None = Field(
        default=None,
        description="OpenAI API key (Codex/GPT). Enables the API route for Learn LLM expansion.",
    )
    google_api_key: str | None = Field(
        default=None,
        description="Google API key (Gemini). Enables the API route for Learn LLM expansion.",
    )
    llm_provider: str | None = Field(
        default=None,
        description=(
            "Global host LLM provider used by every consumer (Learn/zsum/node-gen) "
            "unless a per-consumer override is set. Same vocabulary as the legacy "
            "learn_llm_provider: 'akms' (API/LiteLLM), 'claude-cli', 'codex-cli', "
            "'nlm', or a custom label. None/'auto' = auto-resolve (nlm or API key "
            "only; CLI agents require an explicit choice). When unset, the legacy "
            "learn_llm_provider value is read as the back-compat alias."
        ),
    )
    learn_llm_provider: str | None = Field(
        default=None,
        description=(
            "Legacy/back-compat alias for the global llm_provider. Kept so "
            "existing config.toml files keep working untouched: when llm_provider "
            "is unset, learn_llm_provider is read as the effective global provider "
            "(effective = llm_provider or learn_llm_provider). Same vocabulary: "
            "'akms' (API/LiteLLM), 'claude-cli', 'codex-cli', 'nlm', or a custom "
            "label. None/'auto' = auto-resolve."
        ),
    )
    zsum_llm_provider: str | None = Field(
        default=None,
        description=(
            "Per-consumer override for the zsum (Zotero Summarizer) host LLM "
            "provider. None = fall back to the global llm_provider (then the "
            "learn_llm_provider alias). Same provider vocabulary as llm_provider."
        ),
    )
    nodegen_llm_provider: str | None = Field(
        default=None,
        description=(
            "Per-consumer override for the AKMS node-generation host LLM provider. "
            "None = fall back to the global llm_provider (then the "
            "learn_llm_provider alias). Same provider vocabulary as llm_provider."
        ),
    )
    learn_llm_custom_clis: dict[str, str] = Field(
        default_factory=lambda: {"copilot": "", "antigravity": ""},
        description=(
            "User-defined CLI providers: label → command template. '{prompt}' is "
            "argv-substituted (no shell); absent → prompt piped to stdin. Empty "
            "command = not registered."
        ),
    )

    # ── Assistant mode ──────────────────────────────────────────────────────
    # API credentials. `<` placeholders in the template ensure first-run UX
    # flags them as needed (see Settings.load → _filter_placeholders).
    openalex_user_email: str | None = Field(
        default=None,
        description=(
            "OpenAlex polite-pool email used as the User-Agent contact. "
            "Required for the OpenAlex client; optional only when the "
            "Assistant Discovery feature is disabled."
        ),
    )
    openalex_api_key: str | None = Field(
        default=None,
        description="Optional OpenAlex API key (premium tier).",
    )
    semantic_scholar_api_key: str | None = Field(
        default=None,
        description=(
            "Optional Semantic Scholar API key. The Assistant client uses "
            "the public endpoint when this is unset, with stricter rate limits."
        ),
    )
    assistant_workspace: Path | None = Field(
        default=None,
        description=(
            "Assistant data tree root. None = resolve via "
            "assistant_workspace_path() — either <akms_workspace>/../"
            "Assistant-Workspace, or <akms_workspace>/assistant/ when "
            "co-located with the AKMS workspace."
        ),
    )
    assistant_workspace_colocate: bool = Field(
        default=False,
        description=(
            "When True and assistant_workspace is unset, the resolver "
            "returns <akms_workspace>/assistant/ instead of the sibling "
            "Assistant-Workspace directory. Lets users keep both trees "
            "under a single root."
        ),
    )

    # ── Failure memory ──────────────────────────────────────────────────────
    failure_memory_mode: FailureMemoryMode = Field(
        default="read-only",
        description=(
            "Permission mode for the Failure Memory workspace. 'read-only' "
            "(default) exposes inspection/preview/diagnostics only and cannot "
            "mutate any configured repository. 'operator' additionally unlocks "
            "record/refresh/compile and must be set deliberately in "
            "config.toml or LOGIC_LOOM_FAILURE_MEMORY_MODE. 'disabled' turns the "
            "surface off entirely."
        ),
    )
    failure_memory_projects: list[FailureMemoryProject] = Field(
        default_factory=list,
        description=(
            "EXPLICITLY configured failure-memory projects, as [[failure_memory_projects]] "
            "array-of-tables entries with `id` + `config` (+ optional `root`). "
            "Logic-Loom never discovers .failure-memory directories by walking the "
            "filesystem — an unlisted project is invisible to the app, by design."
        ),
    )

    # ── akms-records ────────────────────────────────────────────────────────
    records_stores: list[RecordsStore] = Field(
        default_factory=list,
        description=(
            "EXPLICITLY configured akms-records stores, as [[records_stores]] "
            "array-of-tables entries with `id` + `scope` (+ `repo` for a project "
            "scope, optional `home` for agent/shared). Logic-Loom only reads "
            "them and never discovers stores by scanning."
        ),
    )

    # ── First-run seeding ───────────────────────────────────────────────────
    seed_starter_batches: bool = Field(
        default=True,
        description=(
            "Seed the AKMS batch store with shipped starter batches on first "
            "sidecar startup. Seeding runs only when the store is empty; "
            "existing batches (including any whose id collides with a seed id) "
            "are never overwritten. Set False to skip seeding entirely — "
            "useful for clean-room testing or users who want a blank workspace."
        ),
    )

    def failure_memory_project(self, project_id: str) -> FailureMemoryProject | None:
        """Return the EXPLICITLY configured project with this id, or None.

        A linear scan of the configured list — the only lookup path. There is
        deliberately no fallback that probes the filesystem for an unlisted id.
        """
        for project in self.failure_memory_projects:
            if project.id == project_id:
                return project
        return None

    def records_store(self, store_id: str) -> RecordsStore | None:
        """Return the explicitly configured records store with this id, or None."""
        for store in self.records_stores:
            if store.id == store_id:
                return store
        return None

    def failure_memory_enabled(self) -> bool:
        return self.failure_memory_mode != "disabled"

    def failure_memory_operator(self) -> bool:
        return self.failure_memory_mode == "operator"

    def effective_zsum_config_path(self) -> Path:
        """Resolve the zsum config file.

        Precedence:
          1. Explicit ``zsum_config_path``.
          2. ``<zsum_repo>/zsum.config.yaml`` when the optional zotero-summarizer
             checkout is present (dev-only override).
          3. ``<akms_workspace>/zsum-runtime/zsum.config.yaml`` — Logic-Loom's
             own workspace (``zsum-runtime/`` is created by the workspace
             bootstrap), so no source checkout is needed.
        """
        if self.zsum_config_path:
            return self.zsum_config_path
        repo = self.zsum_repo_dir()
        if repo is not None:
            return repo / "zsum.config.yaml"
        return self.akms_workspace.expanduser() / "zsum-runtime" / "zsum.config.yaml"

    def effective_llm_provider(self, consumer: str = "learn") -> str | None:
        """Effective explicit provider label for *consumer* — the input to
        host_llm.routing.resolve_choice's explicit branch. Per-consumer override
        (`<consumer>_llm_provider`) wins, then the global `llm_provider`, then the
        legacy `learn_llm_provider` name (back-compat alias). Equal-by-construction
        with resolve_choice's step-1 precedence; for consumer='learn' it reduces to
        `learn_llm_provider or llm_provider` (byte-for-byte with the original
        Learn-only resolution)."""
        override = getattr(self, f"{consumer}_llm_provider", None)
        return override or self.llm_provider or self.learn_llm_provider

    def effective_akms_global_vault(self) -> Path:
        """Resolve the vault used for AKMS graph compilation.

        Precedence:
          1. Explicit ``akms_global_vault`` config
          2. ``<akms_repo>/Packages/Nodes_Vault`` when an ``akms_repo``
             checkout is configured and present (dev-only override)
          3. ``AKMS_GLOBAL_VAULT`` env var
          4. Default ``~/.claude/akms/nodes``
        """
        if self.akms_global_vault is not None:
            return self.akms_global_vault.expanduser()
        repo = self.akms_repo_dir()
        if repo is not None:
            repo_default = repo / "Packages" / "Nodes_Vault"
            if repo_default.exists():
                return repo_default
        env = os.environ.get("AKMS_GLOBAL_VAULT")
        if env:
            return Path(env).expanduser()
        return DEFAULT_AKMS_GLOBAL_VAULT.expanduser()

    def learn_domain_packs_search_root(self) -> Path | None:
        """Resolve the directory scanned for domain-pack descriptor YAMLs.

        Precedence:
          1. Explicit ``learn_domain_packs_path`` config (used verbatim,
             tilde-expanded). Callers that set this (e.g. tests) get exactly
             the directory they asked for.
          2. ``<akms_repo>/Packages`` when it exists — all published domain
             packs ship inside sub-trees of the AKMS ``Packages/`` directory
             (e.g. ``Packages/<domain>/…/domain_pack.yaml``).
             Narrowing here rather than falling back to the bare ``akms_repo``
             root avoids a full-tree walk: ``akms_repo`` can contain large
             source trees (``Sources_Evals/``, ``artifacts/``, NLM outputs,
             etc.) that are never relevant to descriptor discovery. The
             ``Packages/`` sub-tree is the canonical home for all domain
             packs; descriptors placed outside it are not supported by default
             (use the explicit ``learn_domain_packs_path`` override for
             non-standard layouts).

        Returns ``None`` when neither resolves to an existing directory, so the
        caller emits ``domain_packs: []`` rather than scanning a bogus path.
        This resolver is descriptor-location-agnostic: it never names a domain
        or companion — discovery is purely "find domain_pack.yaml files".
        """
        if self.learn_domain_packs_path is not None:
            return self.learn_domain_packs_path.expanduser()
        repo = self.akms_repo_dir()
        if repo is None:
            return None
        packages_dir = repo / "Packages"
        if packages_dir.exists():
            return packages_dir
        return None

    def akms_repo_dir(self) -> Path | None:
        """The optional AKMS source checkout, or ``None`` when absent.

        ``akms_repo`` is a development-only override. An unset value and a
        path that is not an existing directory both mean "no checkout"; every
        feature then resolves from the installed packages, the Logic-Loom
        workspace, or its documented fallback.
        """
        return _existing_dir(self.akms_repo)

    def zsum_repo_dir(self) -> Path | None:
        """The optional zotero-summarizer source checkout, or ``None``.

        Same contract as :meth:`akms_repo_dir`: zsum runs from the installed
        ``zotero-summarizer`` package, so the checkout is a dev-only override.
        """
        return _existing_dir(self.zsum_repo)

    def graph_cache_path(self) -> Path:
        return self.cache_path / "graph" / "graph.json"

    # --- AKMS workspace resolvers ------------------------------------------------
    # All resolvers return paths under ``akms_workspace``. They do NOT touch the
    # filesystem — bootstrap_akms_workspace() is responsible for creating the
    # tree. Tilde expansion is performed so the returned path is concrete.
    def _ws(self) -> Path:
        return self.akms_workspace.expanduser()

    def akms_batches_path(self) -> Path:
        """Path to the on-disk ``batches.json`` (workspace root)."""
        return self._ws() / "batches.json"

    def akms_batch_state_path(self) -> Path:
        """Path to ``batch_assignments.json`` holding picker-side batch state."""
        return self._ws() / "batch_assignments.json"

    def akms_saved_queries_path(self) -> Path:
        """Path to ``saved_queries.json`` (workspace root)."""
        return self._ws() / "saved_queries.json"

    def akms_nlm_inputs_dir(self) -> Path:
        """NotebookLM inputs directory (``Sources_Evals/NLM/Inputs``)."""
        return self._ws() / "Sources_Evals" / "NLM" / "Inputs"

    def akms_nlm_outputs_dir(self) -> Path:
        """NotebookLM outputs directory (``Sources_Evals/NLM/Outputs``)."""
        return self._ws() / "Sources_Evals" / "NLM" / "Outputs"

    def akms_generated_nodes_dir(self) -> Path:
        """Generated nodes staging directory (``generated_nodes``)."""
        return self._ws() / "generated_nodes"

    # --- Assistant workspace resolvers -----------------------------------------
    # Mirror the AKMS resolvers above. The resolver returns a concrete path
    # with tildes expanded; bootstrap_assistant_workspace is
    # responsible for materialising the tree.
    def assistant_workspace_path(self) -> Path:
        """Resolve the Assistant workspace root.

        Precedence:
          1. Explicit ``assistant_workspace`` config (highest).
          2. ``<akms_workspace>/assistant`` when ``assistant_workspace_colocate=True``.
          3. ``<akms_workspace>/../Assistant-Workspace`` (default sibling layout).

        Tilde expansion is performed so the returned path is concrete.
        """
        if self.assistant_workspace is not None:
            return self.assistant_workspace.expanduser()
        akms_root = self._ws()
        if self.assistant_workspace_colocate:
            return akms_root / "assistant"
        return akms_root.parent / "Assistant-Workspace"

    def assistant_batches_path(self) -> Path:
        """Path to the Assistant ``batches.json`` (created by the bootstrap)."""
        return self.assistant_workspace_path() / "batches.json"

    def assistant_workflows_path(self) -> Path:
        """Path to the Assistant ``workflows.json`` (created by the bootstrap)."""
        return self.assistant_workspace_path() / "workflows.json"

    def assistant_prompt_profiles_path(self) -> Path:
        """Path to the Assistant ``prompt_profiles.json`` (created by the bootstrap)."""
        return self.assistant_workspace_path() / "prompt_profiles.json"

    @classmethod
    def load(cls, *, config_path: Path | None = None, **overrides: Any) -> "Settings":
        """Layered constructor: overrides > ``LOGIC_LOOM_*`` env > TOML > defaults.

        The TOML layer is fed to ``BaseSettings`` as constructor kwargs, and
        pydantic-settings ranks init kwargs *above* environment variables. So a
        key that is present in both the TOML file and the environment must be
        dropped from the TOML layer here, or the file would silently shadow the
        env var and invert the documented precedence (the module docstring).
        ``EnvSettingsSource`` is the library's own env reader (prefix- and
        case-aware), so "which fields does the environment provide" is answered
        by the same code that will consume them.
        """
        path = config_path or _user_config_path()
        toml_data = _filter_placeholders(_load_toml(path))
        toml_data = _migrate_legacy_values(toml_data)
        env_provided = EnvSettingsSource(cls)()
        toml_data = {k: v for k, v in toml_data.items() if k not in env_provided}
        merged: dict[str, Any] = {**toml_data, **overrides}
        return cls(**merged)


def first_run_status(settings: Settings) -> dict[str, Any]:
    """Report whether the user needs to walk through first-run setup.

    A path is "needed" when its TOML value is unset or a placeholder (the file
    was copied from the template and never edited). Only ``vault_path`` (the
    user's own Obsidian vault) is needed: zsum and AKMS run from installed
    packages, so the ``zsum_repo`` / ``akms_repo`` source checkouts are
    optional developer overrides and never make setup "needed".

    A value supplied another way (``LOGIC_LOOM_VAULT_PATH`` or a constructor
    kwarg) also counts: ``settings`` already holds it, with placeholders
    filtered out, so setup is not asked for a path the user already gave.
    """
    raw = _load_toml(_user_config_path())
    needs: list[str] = []
    for key in FIRST_RUN_REQUIRED_KEYS:
        if getattr(settings, key, None) is not None:
            continue
        v = raw.get(key)
        if v is None or is_placeholder(v):
            needs.append(key)
    return {
        "needs_setup": bool(needs),
        "missing_or_placeholder": needs,
        "configured": {
            "akms_repo": _opt_str(settings.akms_repo),
            # The explicit setting, else the AKMS_GLOBAL_VAULT env var the
            # graph also honours (see effective_akms_global_vault).
            "akms_global_vault": _opt_str(settings.akms_global_vault)
            or (os.environ.get("AKMS_GLOBAL_VAULT") or None),
            "zsum_repo": _opt_str(settings.zsum_repo),
            "vault_path": _opt_str(settings.vault_path),
        },
    }


#: TOML keys first-run setup requires. Source-checkout paths are deliberately
#: absent — see :func:`first_run_status`.
FIRST_RUN_REQUIRED_KEYS: tuple[str, ...] = ("vault_path",)


def _opt_str(value: Path | None) -> str | None:
    return str(value) if value is not None else None


def detect_paths() -> dict[str, str | None]:
    """Best-effort sibling-repo detection for first-run UX. Pure read-only filesystem peek.

    An optional developer convenience: it pre-fills the first-run dialog when
    AKMS / zotero-summarizer checkouts sit at ``~/Github/Personal/<name>``.
    Nothing requires it — on any other machine every value is ``None`` and the
    optional ``akms_repo`` / ``zsum_repo`` fields simply stay unset.
    """
    base = Path.home() / "Github" / "Personal"
    akms = base / "AKMS"
    zsum = base / "zotero-summarizer"
    akms_global_vault = akms / "Packages" / "Nodes_Vault"
    return {
        "akms_repo": str(akms) if (akms / "pyproject.toml").exists() or (akms / "Packages").exists() else None,
        "akms_global_vault": str(akms_global_vault) if akms_global_vault.exists() else None,
        "zsum_repo": str(zsum) if (zsum / "pyproject.toml").exists() else None,
        "vault_path": _detect_vault_from_zsum(zsum),
    }


def _detect_vault_from_zsum(zsum_root: Path) -> str | None:
    """Read zsum's config.yaml and return ``publisher.vault_path`` if set."""
    config = zsum_root / "config.yaml"
    if not config.exists():
        return None
    try:
        import yaml  # type: ignore[import-untyped]  # local import; PyYAML is a transitive dep via logic_loom_api → zsum
    except ImportError:
        return None
    try:
        data = yaml.safe_load(config.read_text())
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    pub = data.get("publisher") or {}
    vault = pub.get("vault_path") if isinstance(pub, dict) else None
    if isinstance(vault, str):
        # zsum expands tildes / relatives at use time; for detection we expand here.
        return str(Path(vault).expanduser())
    return None
