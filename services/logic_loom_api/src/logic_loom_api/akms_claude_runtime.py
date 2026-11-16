"""Per-run claude-runtime workspace materializer.

This module is the write-side companion to :mod:`akms_claude_assets`. Each
sidecar run materializes a fresh ``.claude/`` tree into a per-user cache
directory; downstream dispatch sets that directory as the child
Claude process's working dir. Source of truth is:

1. The user-editable copy under :func:`akms_claude_assets.user_assets_dir`
   if it exists (so user edits are honored at runtime).
2. Otherwise the bundled defaults read via :mod:`importlib.resources`.

An optional ``effective_prompts`` overlay rewrites specific files after the
base copy; the run-workflow handler uses it to apply per-run prompt slots.

The runtime version marker (:data:`RUNTIME_VERSION`) gates future migrations.
The sentinel ``.logic-loom-runtime-ok`` is written last and contains the
version string so dispatchers can fail fast on a half-materialized workspace.
"""

from __future__ import annotations

import os
import shutil
import sys
from importlib import resources
from pathlib import Path
from typing import Mapping

from .akms_claude_assets import ASSET_ROOTS, safe_asset_path, user_assets_dir

RESOURCE_PACKAGE = "logic_loom_api.resources.akms_claude"
RUNTIME_VERSION = "v1"


def cache_base_dir() -> Path:
    """Return the per-user cache base directory.

    Cross-platform per the reference:

    * Windows: ``%LOCALAPPDATA%/Logic-Loom``
    * macOS: ``~/Library/Caches/Logic-Loom``
    * Linux: ``$XDG_CACHE_HOME/logic-loom`` (or ``~/.cache/logic-loom``)
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "Logic-Loom"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "Logic-Loom"
    base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    return base / "logic-loom"


def runtime_dir() -> Path:
    """Return the versioned runtime workspace path."""
    return cache_base_dir() / "claude-runtime" / RUNTIME_VERSION


def codex_runtime_dir() -> Path:
    """Return the versioned codex-runtime workspace path (issue #103 A1).

    A host CLI agent (``codex`` / a custom label) discovers skills under
    ``.agents/skills/`` (its native convention), NOT the claude ``.claude/`` tree.
    So a codex node-gen dispatch needs its OWN cwd holding the node-gen skill at
    ``.agents/skills/node-gen/`` — see :func:`materialize_codex_runtime`.
    """
    return cache_base_dir() / "codex-runtime" / RUNTIME_VERSION


#: Minimal AGENTS.md laid into the codex runtime so the agent has accurate cwd
#: context (the node-gen skill is auto-discovered under .agents/skills/, but a
#: short pointer keeps a pathless "use the node-gen skill" prompt unambiguous).
_CODEX_AGENTS_MD = """\
# AGENTS.md — AKMS node-gen (Logic-Loom codex runtime)

This workspace runs the AKMS **node-gen** skill. The skill is at
`.agents/skills/node-gen/SKILL.md` — read it and its `references/` before starting.

Use the skill's `extract` command with the `NLM_ID` / `NODE_SELECTION` / `PLAN_PATH`
/ `OUT_DIR` provided in the prompt. The `nlm` CLI (authenticated) and file tools are
required; generated YAML/markdown nodes are written to the prompt's `OUT_DIR`.
"""


class AkmsAssetsUnavailable(RuntimeError):
    """Raised when neither user-installed nor bundled AKMS Claude assets exist.

    Surfaced by :func:`materialize_runtime_workspace` so route handlers can
    return a precise 400 ("AKMS assets not vendored in this Logic-Loom build")
    instead of a generic 500.
    """


def bundled_root():
    """Return the :class:`importlib.resources.abc.Traversable` for the bundle, or ``None``.

    The AKMS Claude bundled defaults are only present when the wheel was
    built after ``scripts/sync_akms_claude_assets.py`` copied them out of the
    installed ``akms`` package (see ``services/logic_loom_api/pyproject.toml``
    force-include). Dev builds without that step return ``None``; callers must handle
    the absence (see :func:`materialize_runtime_workspace`).
    """
    try:
        return resources.files(RESOURCE_PACKAGE)
    except (ModuleNotFoundError, FileNotFoundError):
        return None


def materialize_runtime_workspace(
    *,
    force: bool = True,
    effective_prompts: Mapping[str, str] | None = None,
    target: Path | None = None,
) -> Path:
    """Lay down a fresh ``.claude/`` runtime tree for a single Claude run.

    ``target`` overrides the destination (default :func:`runtime_dir`). The live
    node-gen launch passes a PER-RUN path (``runtime_dir()/<run_id>``) so two
    concurrently-dispatched runs never share a cwd — otherwise one run's
    ``force=True`` rmtree could wipe the tree another run's agent is executing
    from (issue #103). Returns the materialized path.

    Behaviour:

    1. If ``force=True`` and the target already exists, it is removed.
       This is the default — every dispatch starts from a clean slate.
    2. The base copy uses ``user_assets_dir()`` when it exists (so user edits
       are reflected at runtime); otherwise it falls back to the bundled
       resources package.
    3. Both ``.claude/`` and ``scripts/`` are copied (only those names are in
       :data:`logic_loom_api.akms_claude_assets.ASSET_ROOTS`).
    4. ``effective_prompts`` (optional) maps runtime-relative paths to file
       content. Each entry is validated through
       :func:`logic_loom_api.akms_claude_assets.safe_asset_path` before being
       written, so a malicious overlay cannot escape the runtime root.
    5. ``inputs/`` and ``outputs/`` sibling directories are created
       unconditionally — downstream dispatch and output stores rely on them.
    6. A ``.logic-loom-runtime-ok`` sentinel containing
       :data:`RUNTIME_VERSION` is written last; dispatchers should refuse to
       launch if it is missing or mismatched.
    """
    target = target if target is not None else runtime_dir()
    if target.exists():
        if not force:
            # Reuse the existing tree as-is. Callers that need a fresh tree
            # must pass force=True (the default).
            return target
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    # Base copy: prefer user-edited tree, else bundled defaults.
    user_dir = user_assets_dir()
    if user_dir.exists():
        for name in ASSET_ROOTS:
            src = user_dir / name
            if src.is_dir():
                shutil.copytree(src, target / name)
    else:
        root = bundled_root()
        if root is None:
            raise AkmsAssetsUnavailable(
                "AKMS Claude assets are not vendored in this build of Logic-Loom. "
                "Install editable copies first (Settings → AKMS → Create editable "
                "copies), or rebuild the sidecar with the akms extra installed."
            )
        for name in ASSET_ROOTS:
            src = root.joinpath(name)
            try:
                is_dir = src.is_dir()
            except (FileNotFoundError, NotADirectoryError):
                is_dir = False
            if is_dir:
                shutil.copytree(str(src), target / name)

    # Per-slot overlay (optional; the run-workflow handler supplies it).
    if effective_prompts:
        for rel_path, content in effective_prompts.items():
            rel = safe_asset_path(rel_path)
            out = target / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")

    # Sibling working dirs and the version sentinel — written last so a
    # half-materialized workspace is detectable.
    (target / "inputs").mkdir(exist_ok=True)
    (target / "outputs").mkdir(exist_ok=True)
    (target / ".logic-loom-runtime-ok").write_text(
        f"{RUNTIME_VERSION}\n", encoding="utf-8"
    )
    return target


def _node_gen_skill_source():
    """Locate the node-gen skill dir to seed the codex runtime, or ``None``.

    Same source precedence as :func:`materialize_runtime_workspace`: the user-edited
    ``.claude/skills/node-gen`` if present (so user skill edits are honored), else the
    bundled defaults. Returns a :class:`pathlib.Path` or an ``importlib.resources``
    Traversable (both accepted by ``shutil.copytree(str(...))``), or ``None`` when the
    node-gen skill is not vendored in this build.
    """
    user = user_assets_dir() / ".claude" / "skills" / "node-gen"
    if user.is_dir():
        return user
    root = bundled_root()
    if root is None:
        return None
    src = root.joinpath(".claude").joinpath("skills").joinpath("node-gen")
    try:
        return src if src.is_dir() else None
    except (FileNotFoundError, NotADirectoryError):
        return None


def materialize_codex_runtime(*, force: bool = True, target: Path | None = None) -> Path:
    """Lay down a codex agent workspace for a node-gen dispatch (issue #103 A1).

    Builds a workspace (default :func:`codex_runtime_dir`) containing the node-gen
    skill at ``.agents/skills/node-gen/`` (codex's native skill location) + a short
    ``AGENTS.md``, replicating the workspace shape that a live codex run was verified
    to drive end-to-end. The skill content is the SAME one bundled for the claude
    runtime (the upstream codex ``.agents`` variant is cosmetically identical), so
    there is no second skill copy to keep in sync.

    ``target`` overrides the destination; the live launch passes a PER-RUN path
    (``codex_runtime_dir()/<run_id>``) so two concurrent codex runs never share a cwd
    — otherwise one run's ``force=True`` rmtree could wipe the skill tree another run's
    codex is executing from (issue #103). ``force=True`` (default)
    re-materializes from a clean slate, mirroring :func:`materialize_runtime_workspace`.
    Raises :class:`AkmsAssetsUnavailable` when the node-gen skill is not vendored — the
    caller degrades gracefully to the claude runtime cwd.
    """
    target = target if target is not None else codex_runtime_dir()
    if target.exists():
        if not force:
            return target
        shutil.rmtree(target)

    skill_src = _node_gen_skill_source()
    if skill_src is None:
        raise AkmsAssetsUnavailable(
            "AKMS node-gen skill is not vendored in this build of Logic-Loom; "
            "cannot materialize a codex runtime."
        )

    target.mkdir(parents=True, exist_ok=True)
    skill_dst = target / ".agents" / "skills" / "node-gen"
    skill_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(skill_src), skill_dst)
    (target / "AGENTS.md").write_text(_CODEX_AGENTS_MD, encoding="utf-8")
    (target / "inputs").mkdir(exist_ok=True)
    (target / "outputs").mkdir(exist_ok=True)
    (target / ".logic-loom-runtime-ok").write_text(
        f"{RUNTIME_VERSION}\n", encoding="utf-8"
    )
    return target
