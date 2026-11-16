# AKMS assets kept in-tree

Files here are bundled into the sidecar by `scripts/sync_akms_claude_assets.py`
(see `packaging/akms_claude_assets.toml`) because no installed AKMS wheel ships
them. Everything else the manifest lists is read from the installed `akms`
package.

| File | Origin | License |
|---|---|---|
| `docs/reference/akms/conformance-invariants.md` | [CEmM2/AKMS](https://github.com/CEmM2/AKMS) `v0.4.0`, `docs/reference/akms/conformance-invariants.md`, unmodified | CC BY 4.0 (AKMS documentation), © Shmuel Osovski |

When the AKMS floor in `services/logic_loom_api/pyproject.toml` moves, replace
the copy with the one from the matching AKMS tag.
