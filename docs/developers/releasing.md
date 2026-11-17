# Releasing

A release is a version tag, `v<major>.<minor>.<patch>` (with `-alpha.N`, `-beta.N` or
`-rc.N` for pre-releases). Pushing it starts three things:

| Workflow | What it does on a `v*` tag |
| --- | --- |
| `release.yml` | Creates a GitHub Release with the source archives and `SHA256SUMS.txt` |
| The binary build | Builds the macOS, Linux and Windows packages and attaches them with `SHA256SUMS.txt` |
| `docs.yml` | Builds this site strictly and publishes it to GitHub Pages (public repository only, not for pre-release tags) |

Pre-release tags are marked as pre-releases automatically.

!!! note "Where binaries are built"
    Installers are built in the separate
    [CEmM2/Logic-Loom-releases](https://github.com/CEmM2/Logic-Loom-releases) repository,
    which checks out the public source at the same tag and publishes the binaries on its own
    Releases page. The tag must exist in both repositories.

## Before tagging

1. Add a `CHANGELOG.md` section for the version; it becomes part of the published docs.
2. Write `docs/release_notes/<tag>.md`. The release workflows use it as the GitHub Release
   body, and it appears under [Release notes](../release_notes/index.md) on this site.
3. Bump every version field and check that they agree:
   `python scripts/check_versions.py --tag <tag>`. The release workflow runs the same check
   and refuses a tag that does not match the manifests or has no changelog section.
4. Run the backend and frontend tests and the strict docs build
   (see [Developer setup](index.md)).

## Cut the release

```bash
bash scripts/release.sh --tag v0.3.0 --dry-run   # print the plan, change nothing
bash scripts/release.sh --tag v0.3.0             # tag and push
```

`scripts/release.sh --help` lists every option. Building the packages locally is
described in [Build from source](build-from-source.md).

## Docs-only fixes

To fix the published docs without cutting a release, run the **docs** workflow by hand
(`workflow_dispatch`) from `master` with `tag` set to the latest release tag. That builds
the tag's docs with the current workflow and publishes them.
