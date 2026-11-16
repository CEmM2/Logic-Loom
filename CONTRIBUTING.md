# Contributing

Thanks for considering a contribution. Logic-Loom is research software with a
small maintainer team; focused, well-tested changes land fastest.

## Development setup

Python 3.12, [uv](https://docs.astral.sh/uv/), Node 22 or newer, pnpm 11 and a
Rust toolchain (for the Tauri shell) are required.

```bash
git clone <repository>
cd Logic-Loom
uv sync --all-packages --all-extras
pnpm install
pnpm sync:resources     # agent assets and prompts, read from the installed packages
pnpm logic-loom:dev     # sidecar + Vite + Tauri
```

Every Python dependency, including AKMS, MechDSL and zotero-summarizer, comes
from PyPI at the version `uv.lock` records. No sibling source checkout is
needed.

## Tests

```bash
uv run pytest services/logic_loom_api/tests -q     # backend
uv run pytest tests -q                             # repository-level checks
pnpm --filter logic-loom-desktop test              # frontend (vitest)
pnpm --filter logic-loom-desktop build             # tsc + vite build
uv run python scripts/check_docs.py                # documentation links
python scripts/check_versions.py                   # every version field agrees
python scripts/check_public_tree.py                # no secrets, local paths or private references
```

The default suites need **no network and no credentials**.

## Invariants worth knowing before you change things

- **The sidecar must always come up.** Optional routers, the picker mount and
  both workspace bootstraps are guarded: a failure is logged and skipped. A new
  router follows the same pattern.
- **Optional features are gated, not assumed.** The AKMS, MechDSL, Failure
  Memory and Records modes each sit behind an extra and a gate component. The
  app must work without any of them.
- **The handshake line `LOGIC_LOOM_API_READY:<port>` is a contract** between the
  Python sidecar, the Rust shell and the frontend client. Change all three or
  none.
- **Failure Memory writes live in one module**
  (`adapters/failure_memory_operator.py`), and the Records browser is read-only.
  Both are enforced by tests.
- **Never hardcode the API port**; it is random per launch.

## Review standards

- Tests accompany behaviour changes; comments explain current behaviour, not
  development history.
- A change to `uv.lock` is a dependency change: say which package moved and why.
- `THIRD_PARTY_NOTICES.md` is generated. After a dependency change, regenerate
  it with `uv run python scripts/gen_third_party_notices.py` (needs network,
  `pnpm install` and `cargo`); CI warns while it is stale and a release tag is
  refused.
- Version fields move together (`scripts/check_versions.py`); maintainers handle
  release tagging.

## License

By submitting a contribution you agree that it is licensed under the MIT
license of this repository.
