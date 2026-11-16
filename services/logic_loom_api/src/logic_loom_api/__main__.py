"""Console entry point for ``logic-loom-api``.

Two modes:
  - ``--auto-port``  bind to a random port, write the port-file, announce via
                     stdout, hand the socket to uvicorn. Used by the Tauri shell.
  - ``--port N``     bind to a fixed port (dev / standalone debugging).
"""

from __future__ import annotations

import argparse
import os
import runpy
import sys

import uvicorn

from ._reentry import RUN_MODULE_FLAG
from .handshake import (
    announce_ready,
    bind_random_port,
    install_shutdown_handlers,
    write_port_file,
)


def _run_module(argv: list[str]) -> int:
    """Execute ``argv[0]`` as ``__main__``, passing ``argv[1:]`` as its args.

    The frozen-build stand-in for ``python -m`` — PyInstaller's bootloader
    has no ``-m``, so subprocess callers re-enter this binary with
    ``--run-module`` instead (see :mod:`logic_loom_api._reentry`).
    ``runpy`` reproduces ``-m`` semantics, including running the module's
    ``if __name__ == "__main__"`` guard.
    """
    if not argv:
        sys.stderr.write(f"{RUN_MODULE_FLAG} requires a module name\n")
        return 2
    module, rest = argv[0], argv[1:]
    # Hand the module the argv IT expects: its own name in slot 0 (run_module
    # rewrites that to the module's __file__, exactly as -m does) followed by
    # its args, so click/argparse inside the module parse normally.
    sys.argv = [module, *rest]
    # A module that calls sys.exit raises SystemExit through this frame — that
    # is the intended exit path, so don't swallow it.
    runpy.run_module(module, run_name="__main__", alter_sys=True)
    return 0


def main() -> None:
    # Hidden `python -m` equivalent for frozen builds. Must run BEFORE argparse,
    # which would otherwise reject the module name as an unrecognized argument.
    if len(sys.argv) > 1 and sys.argv[1] == RUN_MODULE_FLAG:
        raise SystemExit(_run_module(sys.argv[2:]))


    # Hidden runtime-package install mode. Frozen onefile builds have no
    # `python -m pip`, so the in-app "Install taichi" action re-enters this
    # binary with --install-runtime-pkg, which runs pip in-process and exits
    # (never starts uvicorn). See runtime_install._install_cmd.
    if "--install-runtime-pkg" in sys.argv:
        from .runtime_install import _cli_install_runtime_pkg

        raise SystemExit(_cli_install_runtime_pkg(sys.argv[1:]))

    parser = argparse.ArgumentParser(prog="logic-loom-api")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--auto-port",
        action="store_true",
        help="Bind to a random localhost port and announce it on stdout.",
    )
    mode.add_argument("--port", type=int, default=None, help="Fixed port (dev mode).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--config", default=None, help="Path to Logic-Loom config.toml.")
    args = parser.parse_args()

    if args.config:
        os.environ["LOGIC_LOOM_CONFIG"] = args.config

    # Make any runtime-installed optional packages (e.g. a side-installed Taichi
    # for MechDSL verify) importable before the app touches them. Appended to
    # sys.path so bundled deps still win; guarded so a bad dir never blocks boot.
    # Must run after LOGIC_LOOM_CONFIG is set so the cache_path resolves right.
    from .runtime_install import ensure_runtime_site_on_path

    ensure_runtime_site_on_path()

    from .main import api

    install_shutdown_handlers()

    if args.auto_port:
        sock, port = bind_random_port(args.host)
        write_port_file(port)
        announce_ready(port)
        config = uvicorn.Config(api, fd=sock.fileno(), log_level="info", access_log=False)
        uvicorn.Server(config).run()
    else:
        port = args.port if args.port is not None else 17321
        write_port_file(port)
        announce_ready(port)
        uvicorn.run(api, host=args.host, port=port, log_level="info")


if __name__ == "__main__":
    main()
