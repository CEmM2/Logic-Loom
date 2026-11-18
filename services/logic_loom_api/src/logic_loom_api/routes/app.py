"""``/api/app/*`` routes — meta, version, config."""

from __future__ import annotations

import datetime as dt
import logging
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Literal

import httpx
import tomli_w
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from .. import __version__
from ..config import (
    Settings,
    _opt_str,
    _user_config_path,
    detect_paths,
    ensure_user_config_exists,
    first_run_status,
    is_placeholder,
)
from ..deps import get_settings, reload_settings
from ..safe_paths import InvalidIdentifier, split_command

router = APIRouter()

# API keys never leave the sidecar. GET /config reports a set key as this mask
# (and an unset one as null); POST /config treats the mask as "unchanged", so a
# Settings form that posts back what it loaded leaves the stored key alone.
SECRET_MASK = "••••••••"
SECRET_FIELDS: tuple[str, ...] = (
    "openalex_api_key",
    "semantic_scholar_api_key",
    "anthropic_api_key",
    "openai_api_key",
    "google_api_key",
)


def _mask(value: str | None) -> str | None:
    return SECRET_MASK if value else None

_STARTED_AT = dt.datetime.now(dt.UTC).isoformat()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__, "started_at": _STARTED_AT}


@router.get("/version")
def version() -> dict[str, str | None]:
    pkg_versions: dict[str, str | None] = {"logic_loom_api": __version__}
    for name in ("akms_nodes_gen", "zsum"):
        try:
            module = __import__(name)
            pkg_versions[name] = getattr(module, "__version__", None)
        except ImportError:
            pkg_versions[name] = None
    return pkg_versions


@router.get("/config")
def config_get(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return {
        # Optional dev-only source-checkout overrides: null when unset.
        "logic_loom_root": _opt_str(settings.logic_loom_root),
        "akms_repo": _opt_str(settings.akms_repo),
        "akms_workspace": str(settings.akms_workspace),
        "akms_global_vault": str(settings.akms_global_vault) if settings.akms_global_vault else None,
        "akms_global_vault_effective": str(settings.effective_akms_global_vault()),
        "zsum_repo": _opt_str(settings.zsum_repo),
        "vault_path": str(settings.vault_path) if settings.vault_path else None,
        "cache_path": str(settings.cache_path),
        "graph_cache_path": str(settings.graph_cache_path()),
        "search_backend": settings.search_backend,
        "vault_qmd_collection": settings.vault_qmd_collection,
        # Zotero Summarizer mirror — see config.py for field semantics.
        "zsum_config_path": str(settings.zsum_config_path) if settings.zsum_config_path else None,
        "zsum_config_path_effective": str(settings.effective_zsum_config_path()),
        "zsum_python_cmd": settings.zsum_python_cmd,
        "zsum_cost_per_mtoken_in": settings.zsum_cost_per_mtoken_in,
        "zsum_cost_per_mtoken_out": settings.zsum_cost_per_mtoken_out,
        "zsum_tokens_per_card_in": settings.zsum_tokens_per_card_in,
        "zsum_tokens_per_card_out": settings.zsum_tokens_per_card_out,
        # Nodes Review
        "nodes_folders": [str(f) for f in settings.nodes_folders],
        "nodes_editor": settings.nodes_editor,
        # Assistant mode. API keys come back masked (SECRET_MASK) or null. assistant_workspace defaults
        # to the resolver result when unset so the UI can show the effective
        # path without forcing the user to pick one.
        "openalex_user_email": settings.openalex_user_email,
        "openalex_api_key": _mask(settings.openalex_api_key),
        "semantic_scholar_api_key": _mask(settings.semantic_scholar_api_key),
        "assistant_workspace": str(settings.assistant_workspace) if settings.assistant_workspace else None,
        "assistant_workspace_colocate": settings.assistant_workspace_colocate,
        "assistant_workspace_effective": str(settings.assistant_workspace_path()),
        # Host LLM provider. Keys come back masked (SECRET_MASK) or null. llm_provider is the global; zsum_/nodegen_ are per-consumer
        # overrides; learn_llm_provider is the legacy back-compat alias.
        "anthropic_api_key": _mask(settings.anthropic_api_key),
        "openai_api_key": _mask(settings.openai_api_key),
        "google_api_key": _mask(settings.google_api_key),
        "llm_provider": settings.llm_provider,
        "learn_llm_provider": settings.learn_llm_provider,
        "zsum_llm_provider": settings.zsum_llm_provider,
        "nodegen_llm_provider": settings.nodegen_llm_provider,
        "learn_llm_custom_clis": settings.learn_llm_custom_clis,
        # Failure memory. READ-ONLY here
        # and deliberately absent from ConfigUpdate: flipping the workspace into
        # `operator` grants write access to every configured repository, so it is
        # an explicit config.toml / LOGIC_LOOM_FAILURE_MEMORY_MODE decision, not
        # something an unauthenticated PATCH can do.
        "failure_memory_mode": settings.failure_memory_mode,
        "failure_memory_projects": [
            {
                "id": project.id,
                "config": str(project.config_path()),
                "root": str(project.declared_root()) if project.declared_root() else None,
            }
            for project in settings.failure_memory_projects
        ],
        # akms-records stores. READ-ONLY here and absent from ConfigUpdate,
        # like failure_memory_projects: stores are listed in config.toml.
        "records_stores": [
            {
                "id": store.id,
                "scope": store.scope,
                "repo": str(store.repo.expanduser()) if store.repo else None,
                "home": str(store.home.expanduser()) if store.home else None,
            }
            for store in settings.records_stores
        ],
    }


#: File suffixes ``path-exists`` answers for anywhere under the home folder.
#: The zsum Settings page probes its "Config path" box (a zsum.config.yaml)
#: while the user types, before the path is saved, so it cannot be limited to
#: the configured folders alone.
PROBE_CONFIG_SUFFIXES: frozenset[str] = frozenset({".yaml", ".yml"})


def _probe_roots(settings: Settings) -> list[Path]:
    """Folders ``path-exists`` may answer for: the ones the user configured."""
    roots: list[Path] = [*settings.nodes_folders, settings.akms_workspace, settings.cache_path]
    if settings.vault_path:
        roots.append(settings.vault_path)
    for resolver in (
        settings.effective_akms_global_vault,
        settings.assistant_workspace_path,
        settings.zsum_repo_dir,
        lambda: settings.effective_zsum_config_path().parent,
        lambda: _user_config_path().parent,
    ):
        try:
            root = resolver()
        except Exception:  # a broken setting must not break the probe
            continue
        if root is not None:
            roots.append(root)
    return roots


def _is_under(path: Path, root: Path) -> bool:
    try:
        r = root.expanduser().resolve()
    except (OSError, ValueError):
        return False
    return path == r or r in path.parents


@router.get("/path-exists")
def path_exists(path: str, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Cheap fs probe used by the Settings page (config-path warning).

    It only answers for paths inside the configured folders (vault, node
    folders, workspaces, cache, config folders) and for ``.yaml`` / ``.yml``
    files under the home folder. Anything else is a 422, so the endpoint cannot
    be used to map the filesystem.
    """
    if "\x00" in path:
        raise InvalidIdentifier("invalid path: contains a NUL byte")
    p = Path(path).expanduser()
    try:
        resolved = p.resolve()
    except (OSError, ValueError) as exc:
        raise InvalidIdentifier(f"invalid path: {exc}") from exc
    allowed = any(_is_under(resolved, root) for root in _probe_roots(settings)) or (
        resolved.suffix.lower() in PROBE_CONFIG_SUFFIXES and _is_under(resolved, Path.home())
    )
    if not allowed:
        raise InvalidIdentifier(
            "path is outside the configured folders; path-exists only answers for "
            "those and for .yaml config files under your home folder"
        )
    return {
        "path": str(p),
        "exists": p.exists(),
        "is_file": p.is_file(),
        "is_dir": p.is_dir(),
    }


@router.get("/akms-status")
def akms_status(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Report whether AKMS integration is available."""
    try:
        import akms_nodes_gen  # noqa: F401
        picker_pkg_installed = True
    except ImportError:
        picker_pkg_installed = False
    try:
        import akms  # noqa: F401
        graph_pkg_installed = True
    except ImportError:
        graph_pkg_installed = False
    # The AKMS source checkout is an optional dev override: nothing here
    # requires it. ``repo_exists`` / ``akms_repo`` are reported for the
    # Settings UI only.
    repo = settings.akms_repo_dir()
    repo_exists = repo is not None
    global_vault = settings.effective_akms_global_vault().expanduser()
    global_vault_exists = global_vault.is_dir()
    available = picker_pkg_installed
    return {
        "available": available,
        "package_installed": picker_pkg_installed,
        "picker_package_installed": picker_pkg_installed,
        "graph_package_installed": graph_pkg_installed,
        "graph_available": graph_pkg_installed and global_vault_exists,
        "repo_exists": repo_exists,
        "akms_global_vault_exists": global_vault_exists,
        "akms_repo": _opt_str(settings.akms_repo),
        "akms_global_vault": str(global_vault),
    }


@router.get("/config/first-run")
def config_first_run(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    # Make sure a config file exists on disk (copied from template if missing)
    # so the user has a file to edit even before they confirm.
    ensure_user_config_exists()
    status = first_run_status(settings)
    return {**status, "detected": detect_paths(), "config_path": str(_user_config_path())}


class FirstRunPayload(BaseModel):
    # Source-checkout paths are optional dev overrides; only the vault is
    # required (see config.FIRST_RUN_REQUIRED_KEYS).
    akms_repo: str | None = None
    akms_global_vault: str | None = None
    zsum_repo: str | None = None
    vault_path: str
    search_backend: Literal["ripgrep", "qmd"] = "ripgrep"
    vault_qmd_collection: str | None = None


@router.post("/config/first-run")
def config_first_run_save(payload: FirstRunPayload) -> dict[str, Any]:
    """Persist confirmed first-run paths and reload settings."""
    target = _user_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Blank optional fields (e.g. an empty "AKMS repo" box) mean "unset".
    data = {k: v for k, v in payload.model_dump().items() if v is not None and v != ""}
    tmp = target.with_suffix(".toml.tmp")
    with tmp.open("wb") as f:
        tomli_w.dump(data, f)
    tmp.replace(target)
    reload_settings()
    return {"status": "saved", "path": str(target)}


@router.post("/config/reload")
def config_reload() -> dict[str, str]:
    """Reload settings from disk + env. Used after the user edits ``config.toml``."""
    reload_settings()
    return {"status": "reloaded"}


class ConfigUpdate(BaseModel):
    akms_repo: str | None = None
    akms_workspace: str | None = None
    akms_global_vault: str | None = None
    zsum_repo: str | None = None
    vault_path: str | None = None
    search_backend: Literal["ripgrep", "qmd"] | None = None
    vault_qmd_collection: str | None = None
    # Zotero Summarizer mirror.
    zsum_config_path: str | None = None
    zsum_python_cmd: str | None = None
    zsum_cost_per_mtoken_in: float | None = Field(default=None, ge=0.0)
    zsum_cost_per_mtoken_out: float | None = Field(default=None, ge=0.0)
    zsum_tokens_per_card_in: int | None = Field(default=None, ge=0)
    zsum_tokens_per_card_out: int | None = Field(default=None, ge=0)
    # Nodes Review
    nodes_folders: list[str] | None = None
    nodes_editor: str | None = None
    # Assistant mode.
    openalex_user_email: str | None = None
    openalex_api_key: str | None = None
    semantic_scholar_api_key: str | None = None
    assistant_workspace: str | None = None
    assistant_workspace_colocate: bool | None = None
    # Host LLM provider. llm_provider = global; zsum_/nodegen_ =
    # per-consumer overrides; learn_llm_provider = legacy back-compat alias.
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    google_api_key: str | None = None
    llm_provider: str | None = None
    learn_llm_provider: str | None = None
    zsum_llm_provider: str | None = None
    nodegen_llm_provider: str | None = None
    learn_llm_custom_clis: dict[str, str] | None = None

    @field_validator("nodes_editor", "zsum_python_cmd")
    @classmethod
    def _command_must_resolve(cls, v: str | None) -> str | None:
        """A command is run as an argv list (never through a shell), so it must
        split cleanly and its program must exist. Empty clears the override."""
        if v is None or v == "":
            return v
        argv = _split_command(v)
        program = argv[0]
        if shutil.which(program) is None and not Path(program).expanduser().is_file():
            raise ValueError(f"program not found: {program!r}")
        return v

    @field_validator("nodes_editor")
    @classmethod
    def _editor_must_be_known(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return v
        check_editor_command(_split_command(v))
        return v

    @field_validator("zsum_python_cmd")
    @classmethod
    def _zsum_cmd_must_be_python(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return v
        check_zsum_command(_split_command(v))
        return v


# ── Commands settable over HTTP ─────────────────────────────────────────────
# nodes_editor and zsum_python_cmd are run as programs, so anyone holding the
# API token could otherwise turn POST /config into "run this program". Over
# HTTP they are limited to known editors / Python launchers with plain flags.
# Someone who needs another program sets it in config.toml by hand, which the
# sidecar trusts (the file is the user's own).

#: Editor programs the Settings form may set (compared by base name, lower
#: case, without ``.exe`` / ``.app``).
KNOWN_EDITORS: frozenset[str] = frozenset(
    {
        # system openers
        "open", "xdg-open", "gio",
        # GUI editors and IDEs
        "code", "code-insiders", "codium", "vscodium", "cursor", "windsurf", "zed",
        "subl", "sublime_text", "mate", "bbedit", "textedit", "gedit",
        "gnome-text-editor", "kate", "kwrite", "mousepad", "pluma", "xed", "geany",
        "featherpad", "notepad", "notepad++", "gvim", "mvim", "emacsclient",
        "typora", "obsidian", "marktext", "ghostwriter", "idea", "pycharm",
        "webstorm", "charm", "fleet", "lapce", "nova",
    }
)

#: Flags that make an editor run commands (vim -c, emacs --eval, ...).
_EXEC_FLAGS: frozenset[str] = frozenset(
    {"-c", "--cmd", "-s", "-u", "-x", "--eval", "--exec", "--execute", "--command",
     "-l", "--load", "-f", "--funcall", "--remote-send", "--remote-expr"}
)

#: ``open -a <App>`` / ``open -b <bundle id>`` take one value.
_OPEN_VALUE_FLAGS: frozenset[str] = frozenset({"-a", "-b"})

_PLAIN_FLAG = re.compile(r"^--?[A-Za-z][A-Za-z0-9-]*$")
_PYTHON_NAME = re.compile(r"^python(3(\.\d+)?)?$")


def _program_name(program: str) -> str:
    name = Path(program).name.lower()
    for suffix in (".exe", ".app", ".cmd", ".bat"):
        name = name.removesuffix(suffix)
    return name


def check_editor_command(argv: list[str]) -> None:
    """Raise ``ValueError`` unless ``argv`` is a known editor with plain flags."""
    name = _program_name(argv[0])
    if name not in KNOWN_EDITORS:
        raise ValueError(
            f"{argv[0]!r} is not a known editor. Pick one such as code, cursor, zed, "
            "subl, gedit, kate or open, or set nodes_editor in config.toml yourself."
        )
    args = argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if name == "open" and arg in _OPEN_VALUE_FLAGS and i + 1 < len(args):
            i += 2
            continue
        if not _PLAIN_FLAG.match(arg) or (arg.lower() in _EXEC_FLAGS and name != "open"):
            raise ValueError(
                f"editor argument {arg!r} is not allowed here; only plain flags such as "
                "--wait or -n. Set nodes_editor in config.toml to use other arguments."
            )
        i += 1


def check_zsum_command(argv: list[str]) -> None:
    """Raise ``ValueError`` unless ``argv`` runs zsum through Python, uv or zsum."""
    if any(a == "-c" or (a.startswith("-c") and not a.startswith("--")) for a in argv[1:]):
        raise ValueError("zsum command must not use -c; run a module or script instead")
    if argv[0] == sys.executable:  # the default: the sidecar's own runtime
        return
    name = _program_name(argv[0])
    if not (_PYTHON_NAME.match(name) or name in {"uv", "zsum"}):
        raise ValueError(
            f"{argv[0]!r} is not a Python, uv or zsum command. "
            "Set zsum_python_cmd in config.toml yourself to use another program."
        )


# learn_llm_custom_clis is deliberately not validated here: a custom CLI may be
# installed later, and discovery already degrades a malformed one to
# "unavailable" (host_llm.discovery._safe_split).
def _split_command(command: str, *, label: str | None = None) -> list[str]:
    where = f" for {label!r}" if label else ""
    try:
        argv = split_command(command)
    except ValueError as exc:
        raise ValueError(f"command{where} does not parse: {exc}") from None
    if not argv:
        raise ValueError(f"command{where} is empty")
    if any("\x00" in a for a in argv):
        raise ValueError(f"command{where} contains a NUL byte")
    return argv


logger = logging.getLogger(__name__)


# ── Assistant Test-Connection ───────────────────────────────────────────────
# Lightweight probe of each discovery provider, independent of the full
# OpenAlex/Semantic-Scholar clients, so the Settings page can give the user
# immediate "configured correctly?" feedback.
#
# Response shape: {ok, latency_ms, quota_remaining}.
# `quota_remaining` is `None` when the provider does not report it.

_OPENALEX_PROBE_URL = "https://api.openalex.org/works?per-page=1"
_SEMANTIC_SCHOLAR_PROBE_URL = (
    "https://api.semanticscholar.org/graph/v1/paper/search"
    "?query=test&limit=1&fields=title"
)
_PROBE_TIMEOUT_SECONDS = 5.0


class TestConnectionResult(BaseModel):
    """Canonical shape of the test-connection response."""

    ok: bool
    latency_ms: int
    quota_remaining: int | None = None
    detail: str | None = None


def _probe_openalex(settings: Settings) -> TestConnectionResult:
    headers: dict[str, str] = {}
    email = settings.openalex_user_email
    if email and not is_placeholder(email):
        headers["User-Agent"] = f"logic-loom (mailto:{email})"
    elif email:
        # Placeholder still in TOML — let the user know.
        return TestConnectionResult(
            ok=False,
            latency_ms=0,
            quota_remaining=None,
            detail="OpenAlex polite-pool email is a placeholder; replace it in Settings.",
        )
    api_key = settings.openalex_api_key
    if api_key and not is_placeholder(api_key):
        headers["api_key"] = api_key
    start = time.perf_counter()
    try:
        resp = httpx.get(_OPENALEX_PROBE_URL, headers=headers, timeout=_PROBE_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        return TestConnectionResult(
            ok=False,
            latency_ms=int((time.perf_counter() - start) * 1000),
            quota_remaining=None,
            detail=f"network error: {exc.__class__.__name__}",
        )
    latency_ms = int((time.perf_counter() - start) * 1000)
    # OpenAlex doesn't expose a stable rate-limit header, but the polite-pool
    # documentation references X-RateLimit-Remaining when present.
    remaining_raw = resp.headers.get("x-ratelimit-remaining")
    quota: int | None
    try:
        quota = int(remaining_raw) if remaining_raw is not None else None
    except ValueError:
        quota = None
    return TestConnectionResult(
        ok=resp.status_code == 200,
        latency_ms=latency_ms,
        quota_remaining=quota,
        detail=None if resp.status_code == 200 else f"HTTP {resp.status_code}",
    )


def _probe_semantic_scholar(settings: Settings) -> TestConnectionResult:
    headers: dict[str, str] = {}
    api_key = settings.semantic_scholar_api_key
    if api_key and not is_placeholder(api_key):
        headers["x-api-key"] = api_key
    start = time.perf_counter()
    try:
        resp = httpx.get(_SEMANTIC_SCHOLAR_PROBE_URL, headers=headers, timeout=_PROBE_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        return TestConnectionResult(
            ok=False,
            latency_ms=int((time.perf_counter() - start) * 1000),
            quota_remaining=None,
            detail=f"network error: {exc.__class__.__name__}",
        )
    latency_ms = int((time.perf_counter() - start) * 1000)
    remaining_raw = resp.headers.get("x-ratelimit-remaining")
    quota: int | None
    try:
        quota = int(remaining_raw) if remaining_raw is not None else None
    except ValueError:
        quota = None
    return TestConnectionResult(
        ok=resp.status_code == 200,
        latency_ms=latency_ms,
        quota_remaining=quota,
        detail=None if resp.status_code == 200 else f"HTTP {resp.status_code}",
    )


@router.get("/assistant/test-connection/{provider}", response_model=TestConnectionResult)
def assistant_test_connection(
    provider: Literal["openalex", "semantic_scholar"],
    settings: Settings = Depends(get_settings),
) -> TestConnectionResult:
    """Probe a discovery provider and return ``{ok, latency_ms, quota_remaining}``.

    A lightweight probe independent of the full provider clients, so the
    Settings page can give immediate feedback.
    """
    if provider == "openalex":
        return _probe_openalex(settings)
    if provider == "semantic_scholar":
        return _probe_semantic_scholar(settings)
    raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"unknown provider: {provider}")


@router.post("/config")
def config_post(payload: ConfigUpdate) -> dict[str, Any]:
    """Persist mutable settings to ``~/.config/logic-loom/config.toml``, then reload.

    The TOML file is written atomically (tmp + replace). Unknown keys are ignored.
    Empty strings are coerced to None (= "unset" in TOML) so the Settings UI can
    clear an override and fall back to the field's default.
    """
    target = _user_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if target.exists():
        import tomllib

        with target.open("rb") as f:
            existing = tomllib.load(f)
    raw = payload.model_dump(exclude_unset=True)
    for key in SECRET_FIELDS:
        if raw.get(key) == SECRET_MASK:
            raw.pop(key)  # the form echoed the mask back: keep the stored key
    delta: dict[str, Any] = {}
    cleared: list[str] = []
    for k, v in raw.items():
        if v is None or (isinstance(v, str) and v == ""):
            cleared.append(k)
            existing.pop(k, None)
        else:
            delta[k] = v
    merged = {**existing, **delta}
    tmp = target.with_suffix(".toml.tmp")
    with tmp.open("wb") as f:
        tomli_w.dump(merged, f)
    tmp.replace(target)
    reload_settings()
    # Report what changed, but never echo a key back: a saved key comes back
    # as the same mask GET /config uses.
    applied = {k: (SECRET_MASK if k in SECRET_FIELDS else v) for k, v in delta.items()}
    return {"status": "saved", "path": str(target), "applied": applied, "cleared": cleared}
