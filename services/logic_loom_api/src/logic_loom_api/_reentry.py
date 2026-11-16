"""Run a bundled module as a subprocess, in dev *and* frozen builds.

Why this exists: several features spawn a Python module as a child process
(the zsum pipeline via :mod:`logic_loom_api._zsum_launcher`, nlm node-gen via
``akms_nodes_gen.nlm_batch``). The obvious argv — ``sys.executable -m <mod>``
— works in dev, where ``sys.executable`` is the uv-managed ``.venv/bin/python``.

It does **not** work in a packaged build. PyInstaller's bootloader does not
implement Python's command-line interface: ``sys.executable`` is the frozen
``logic-loom-api`` binary, and everything in ``sys.argv[1:]`` is handed
straight to our own :func:`logic_loom_api.__main__.main`. So ``-m <mod>``
never executes a module — it reaches argparse as stray positionals and dies
with::

    logic-loom-api: error: unrecognized arguments: -m logic_loom_api._zsum_launcher cards

The fix mirrors the existing ``--install-runtime-pkg`` escape hatch: frozen
builds re-enter the binary with a hidden ``--run-module`` flag, which
``__main__`` intercepts *before* argparse and dispatches through
:func:`runpy.run_module` (reproducing ``python -m`` semantics exactly).

Kept in its own dependency-free module so ``__main__``, ``config``, and
``akms_dispatch`` can all import it without an import cycle.
"""

from __future__ import annotations

import shlex
import sys

#: Hidden re-entry flag consumed by ``__main__.main`` before argparse runs.
#: Frozen-build counterpart of the interpreter's ``-m``.
RUN_MODULE_FLAG = "--run-module"


def is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def module_argv(module: str) -> list[str]:
    """argv prefix that runs ``module`` as ``__main__`` through this runtime.

    Dev returns the plain ``<python> -m <module>``; frozen returns
    ``<binary> --run-module <module>``. Callers append their own args to
    either form — both leave the module's own CLI parsing untouched.
    """
    if is_frozen():
        return [sys.executable, RUN_MODULE_FLAG, module]
    return [sys.executable, "-m", module]


def module_cmd(module: str) -> str:
    """:func:`module_argv` as a single shell-quoted string.

    For settings that are stored as text and later re-split with
    :func:`shlex.split` (e.g. ``Settings.zsum_python_cmd``).
    """
    return shlex.join(module_argv(module))
