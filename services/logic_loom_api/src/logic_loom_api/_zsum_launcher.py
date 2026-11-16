"""In-process launcher for the zsum CLI used by ``RunRegistry``.

Why this exists: ``RunRegistry.submit`` spawns the zsum pipeline as a
subprocess so log streaming, cancellation, and the run-history index
can reuse the existing zsum machinery. The original argv hardcoded
``["uv", "run", "zsum", …]`` which only works in dev where the ``uv``
binary is on ``PATH``. In packaged (PyInstaller-frozen) sidecar builds
``uv`` is **not** on ``PATH``, so every pipeline click failed with
``spawn failed: [Errno 2] No such file or directory``.

The fix is to invoke the zsum CLI through the same runtime that is running
the sidecar — ``sys.executable`` — addressing this launcher module. Because
the sidecar wheel bundles the zsum package via the path-dep, ``zsum.cli.main``
is importable in both dev and packaged builds. Click's :func:`main` reads
from ``sys.argv``, so the launcher does nothing more than call ``main()``.

How the module is addressed differs by build, and this matters: ``-m`` is an
*interpreter* flag, and a PyInstaller-frozen ``sys.executable`` is not an
interpreter — it forwards ``sys.argv[1:]`` to our own ``__main__``. So dev
uses ``-m`` and frozen builds use the ``--run-module`` re-entry flag. Build
the prefix with :func:`logic_loom_api._reentry.module_argv` rather than
hardcoding either form.

The launcher is also referenced by :data:`Settings.zsum_python_cmd`'s
default factory (see :mod:`logic_loom_api.config`), which keeps the
"Test" button in Settings → Zsum and the "Run" buttons on the Pipeline
page on the same code path.
"""

from __future__ import annotations

from zsum.cli import main


def _entrypoint() -> None:  # pragma: no cover - thin shim
    """Spawned-subprocess entrypoint. ``main()`` reads from ``sys.argv``."""
    main()


if __name__ == "__main__":  # pragma: no cover - subprocess only
    _entrypoint()
