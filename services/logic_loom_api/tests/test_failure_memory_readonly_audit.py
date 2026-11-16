"""Mechanical audit of the failure-memory backend's read-only boundary.

Docstrings promise; this file checks. Every rule below is enforced by parsing
the four failure-memory modules' ASTs, so a future edit that quietly introduces
a write, a private AKMS import, or a hand-rolled registry update fails here
rather than in review.

The four rules, and why each exists:

1. **No filesystem write.** ``read-only`` must be structurally unable to mutate,
   not merely intended not to. Anything that could create, truncate, rename or
   delete a path is banned outright.
2. **No direct registry edit.** "Open ``lessons.json``, append a dictionary, save
   the file" would bypass the package's atomic writer, schema validation, ID
   allocation and generated-artifact checks in one move. Banned by rule 1 plus
   an explicit ban on JSON serialisation to a file.
3. **Public package API only.** No private ``akms_failure_memory`` name, no
   direct ``akms`` import, no ``repo2md``. The package's own boundary document
   treats private AKMS orchestration internals as forbidden dependencies for
   downstream consumers.
4. **No writing package mode.** ``resolve_provider`` is only ever called with a
   LITERAL ``write_artifacts=False``, and ``run_compiler`` only with
   ``mode="validate"`` or ``mode="check"``.

   The literal is load-bearing and replaced an earlier module constant
   ``WRITE_ARTIFACTS = False``. A reviewer rebound that constant at runtime and
   the preview published all three invocation artifacts while this audit still
   passed, because an AST can prove which NAME a call passes but never what the
   name holds. Rule 5 exists to stop the constant coming back.

5. **No rebindable toggle.** Neither failure-memory module may expose a
   module-level boolean, or a string equal to a writing package mode, that a
   caller could flip to change what the surface does.

Operator note: operator publication lives in ``adapters/failure_memory_operator.py``
and is audited by ``test_failure_memory_operator.py`` against the opposite rule
set. The two modules are kept apart precisely so the statements this file makes
about ``adapters/failure_memory.py`` stay total.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SRC = Path(__file__).resolve().parents[1] / "src" / "logic_loom_api"

AUDITED = {
    "adapters/failure_memory.py",
    "services/failure_memory.py",
    "routes/failure_memory.py",
    "models/failure_memory.py",
}

#: Attribute names that create, truncate, move or delete something. Checked by
#: NAME, not by inferred receiver type — a deliberately blunt instrument, since
#: a read-only module has no legitimate use for any of them.
FORBIDDEN_CALLS = frozenset(
    {
        "write_text",
        "write_bytes",
        "mkdir",
        "makedirs",
        "unlink",
        "rmdir",
        "rmtree",
        "remove",
        "rename",
        "replace",
        "touch",
        "chmod",
        "symlink_to",
        "hardlink_to",
        "link",
        "copy",
        "copy2",
        "copyfile",
        "copytree",
        "move",
        "dump",  # json.dump / yaml.dump into a file object
        "write",
        "writelines",
        "truncate",
        "mkstemp",
        "mkdtemp",
        "NamedTemporaryFile",
        "TemporaryDirectory",
    }
)

FORBIDDEN_IMPORT_ROOTS = frozenset({"repo2md", "akms"})

#: The complete public surface this integration is allowed to touch. Every name
#: is exported in its module's ``__all__``.
ALLOWED_PACKAGE_ATTRS = {
    "config": {"load_project_config", "ProjectConfig"},
    "compiler": {"run_compiler", "validate_registry_bytes"},
    "provider": {"resolve_provider", "validate_publication"},
    "refresh": {"preflight", "status"},
    "errors": {"FailureMemoryError"},
    # ProjectLock is public (locks.__all__) and used for exactly one thing: a
    # SHORT bounded read-side acquisition so `GET /status` cannot inherit the
    # 120-second write timeout under contention. Always constructed with
    # create_parent_directories=False, so it can never create repository
    # structure — the no-mutation tree-digest test covers the /status path.
    "locks": {"ProjectLock"},
}


def _tree(relative: str) -> ast.Module:
    return ast.parse((_SRC / relative).read_text(encoding="utf-8"), filename=relative)


@pytest.mark.parametrize("relative", sorted(AUDITED))
def test_no_module_can_write_to_the_filesystem(relative: str) -> None:
    offenders = []
    for node in ast.walk(_tree(relative)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name in FORBIDDEN_CALLS:
            offenders.append(f"{relative}:{node.lineno} {name}(...)")
        if name == "open":
            mode = _literal_mode(node)
            if mode is None or any(flag in mode for flag in "wax+"):
                offenders.append(f"{relative}:{node.lineno} open(mode={mode!r})")
    assert not offenders, "read-only modules must contain no write:\n" + "\n".join(offenders)


def _literal_mode(call: ast.Call) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        return str(call.args[1].value)
    return "r" if call.args else None


@pytest.mark.parametrize("relative", sorted(AUDITED))
def test_no_module_imports_a_forbidden_dependency(relative: str) -> None:
    """No ``repo2md``, and no direct ``akms`` import.

    Logic-Loom reaches AKMS exclusively THROUGH ``akms_failure_memory``. The
    graph adapter imports ``akms`` for the AKMS knowledge graph, which is a
    different feature with a different contract; the failure-memory modules must
    not blur the two.
    """
    offenders = []
    for node in ast.walk(_tree(relative)):
        roots: list[str] = []
        if isinstance(node, ast.Import):
            roots = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots = [node.module.split(".")[0]]
        for root in roots:
            if root in FORBIDDEN_IMPORT_ROOTS:
                offenders.append(f"{relative}:{node.lineno} imports {root}")
    assert not offenders, "\n".join(offenders)


def test_only_the_adapter_touches_the_package_at_all() -> None:
    """Service, routes and models must go through the adapter.

    A single import site is what makes the audit above sufficient: if the
    service layer could import ``akms_failure_memory`` itself, an unaudited
    write could appear behind the adapter's back.
    """
    offenders = [
        relative
        for relative in sorted(AUDITED - {"adapters/failure_memory.py"})
        if "akms_failure_memory" in (_SRC / relative).read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"{offenders} reference akms_failure_memory directly; route them through the adapter"
    )


def test_the_adapter_only_uses_public_package_names() -> None:
    """Every ``_module("x").y`` access must name a public, ``__all__``-exported y."""
    tree = _tree("adapters/failure_memory.py")
    seen: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "_module"
            and value.args
            and isinstance(value.args[0], ast.Constant)
        ):
            continue
        submodule = str(value.args[0].value)
        seen.setdefault(submodule, set()).add(node.attr)

    assert seen, "the audit found no package accesses at all — has the adapter changed shape?"
    for submodule, attrs in seen.items():
        allowed = ALLOWED_PACKAGE_ATTRS.get(submodule)
        assert allowed is not None, f"undeclared submodule akms_failure_memory.{submodule}"
        extra = attrs - allowed
        assert not extra, f"akms_failure_memory.{submodule} non-public access: {sorted(extra)}"
        assert not any(attr.startswith("_") for attr in attrs), submodule


def test_every_allowed_package_name_is_actually_public() -> None:
    """The allowlist above is only meaningful if it is true of the package."""
    importlib_util = pytest.importorskip("importlib.util")
    if importlib_util.find_spec("akms_failure_memory") is None:  # pragma: no cover
        pytest.skip("akms_failure_memory not installed")
    import importlib

    for submodule, names in ALLOWED_PACKAGE_ATTRS.items():
        module = importlib.import_module(f"akms_failure_memory.{submodule}")
        exported = set(getattr(module, "__all__", ()) or ())
        for name in names:
            assert hasattr(module, name), f"{submodule}.{name} does not exist"
            if exported:
                assert name in exported, f"{submodule}.{name} is not in __all__"


def test_the_only_write_artifacts_argument_is_a_false_literal() -> None:
    """``write_artifacts`` must be a literal, never a name.

    An earlier revision passed a module constant ``WRITE_ARTIFACTS = False``.
    The AST could prove the source always passed that NAME; it could not
    constrain the name's VALUE, and a reviewer rebound it at runtime and got all
    three provider artifacts written into the target repository. A literal has
    no name to rebind. This test therefore asserts the opposite of what it used
    to: an ``ast.Name`` here is now the defect.
    """
    tree = _tree("adapters/failure_memory.py")
    literals = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "write_artifacts":
                continue
            assert isinstance(keyword.value, ast.Constant), (
                "write_artifacts must be a literal — a name, attribute or parameter can "
                "be rebound at runtime and the static audit cannot see the value"
            )
            literals.append(keyword.value.value)
    assert literals == [False], f"expected exactly one `write_artifacts=False`, got {literals}"


def test_no_module_level_write_toggle_survives() -> None:
    """The rebindable constant is gone, and no equivalent has replaced it.

    Sweeps the module for any attribute a caller could flip to change what the
    surface does: booleans, and the strings that name a package mode. Both
    ``WRITE_ARTIFACTS`` and the service layer's ``REFRESH_POLICY`` were exactly
    this shape and are now literals at their call sites.
    """
    from logic_loom_api.adapters import failure_memory as adapter_module
    from logic_loom_api.services import failure_memory as service_module

    assert not hasattr(adapter_module, "WRITE_ARTIFACTS")
    assert not hasattr(service_module, "REFRESH_POLICY")

    for module in (adapter_module, service_module):
        toggles = [
            name
            for name in vars(module)
            if not name.startswith("__")
            and isinstance(vars(module)[name], bool)
            and name != "TYPE_CHECKING"
        ]
        assert not toggles, f"{module.__name__} exposes rebindable boolean(s): {toggles}"
        modes = [
            name
            for name, value in vars(module).items()
            if isinstance(value, str)
            and value in {"write", "dry-run", "require-current"}
        ]
        assert not modes, f"{module.__name__} exposes rebindable package mode(s): {modes}"


def test_the_compiler_is_only_run_in_non_writing_modes() -> None:
    tree = _tree("adapters/failure_memory.py")
    modes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                modes.append(str(keyword.value.value))
    assert modes, "no compiler mode found — has the adapter changed shape?"
    assert set(modes) <= {"validate", "check"}, f"writing compiler mode reachable: {modes}"


def test_no_module_names_a_recorder_or_refresh_entry_point() -> None:
    """Nothing in the read-only surface may REFERENCE the mutating package API.

    Checked as identifiers, not as text: ``routes/failure_memory.py`` documents
    in prose that recording goes through ``record.add_lesson``, and a raw text
    scan would flag the very docstring that states the rule. What matters is
    whether the name is ever used in an executable position.
    """
    banned = {
        "add_lesson",
        "record_lesson",
        "refresh_project",
        "init_project",
        "compile_project",
        "_publish_transaction",
    }
    offenders = []
    for relative in sorted(AUDITED):
        tree = _tree(relative)
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # A string literal naming one of these would be an indirect
                # getattr away from calling it.
                name = node.value if node.value in banned else None
            if name in banned:
                offenders.append(f"{relative}:{node.lineno} references {name}")
    assert not offenders, "\n".join(offenders)


def test_the_operator_module_is_audited_separately_and_is_not_in_this_set() -> None:
    """This file's statements are total ONLY over the read-only modules.

    Keeping the write-capable module out of ``AUDITED`` is deliberate, and
    keeping it audited elsewhere is what stops that being a loophole.
    """
    assert "adapters/failure_memory_operator.py" not in AUDITED
    assert (_SRC / "adapters" / "failure_memory_operator.py").is_file()
    operator_tests = Path(__file__).with_name("test_failure_memory_operator.py")
    assert operator_tests.is_file(), "the write-capable module must have its own audit"
