"""Assert the shared failure-memory consumer-contract pack.

Every consumer of ``akms-failure-memory`` (Logic-Loom among them) must
consume the IDENTICAL fixture pack from the public AKMS repo
(``packages/akms_failure_memory/tests/fixtures/consumer_contract/v1/``) and forbids
accepting any copied/vendored fixture without a checksum assertion.

Access-path note (read before touching this file): the pack lives under
``tests/fixtures/`` in the AKMS source tree. The ``akms-failure-memory``
DISTRIBUTION (the wheel Logic-Loom actually installs, see
``[tool.setuptools.data-files]`` in ``packages/akms_failure_memory/pyproject.toml``)
ships only ``src/akms_failure_memory/**``, ``schemas/*.json``, and two
specific NumerixWeave fixture files — never
``tests/fixtures/consumer_contract/v1/**``. There is therefore no way to
reach this pack from an *installed* ``akms-failure-memory``; it only exists
in a git checkout of the AKMS repository. Logic-Loom does not vendor a copy
of the pack (a vendored copy could silently drift from the pinned commit).
Instead, this test does a `git sparse-checkout` fetch of ONLY the pack
subtree, fresh, from the exact pinned commit, into a pytest tmp_path — never
into this repository. That fetched copy is then verified against
``EXPECTED_PACK_SHA256`` using the pack's own bundled ``verify_pack.py`` and a
manifest field assertion, exactly as
``dev/docs/failure-memory.md`` documents.

Requires network access to ``github.com`` (the same access CI already needs
to install ``akms``/``akms-failure-memory`` themselves via
``uv sync --extra failure-memory``).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

# Pinned AKMS release and consumer-contract pack digest.
#
# Logic-Loom installs ``akms`` / ``akms-failure-memory`` from PyPI (locked in
# ``uv.lock``): version 0.4.0 of both. The pack, however, lives only under
# ``tests/fixtures/`` in the AKMS source tree and is not part of any wheel, so
# it is fetched from the PUBLIC https://github.com/CEmM2/AKMS repository (no
# token) at the commit that the ``v0.4.0`` tag points to -- the exact source the
# PyPI 0.4.0 distributions were built from. The commit, not the tag name, is the
# immutable identity used for the fetch; the tag is recorded so a reader can
# verify it with:
#
#   git ls-remote https://github.com/CEmM2/AKMS.git 'refs/tags/v0.4.0^{}'
#
# The pack digest bd1c2dc3... is pack revision "amendment-2": AKMS re-froze the
# pack (CEmM2/AKMS #42) after the public repository scrubbed internal-plan
# references from it, which had left the v0.3.2 pack (90ad2801...) failing its
# own verify_pack.py. The akms release version no longer feeds the provider
# fingerprint, so a plain AKMS version bump does not by itself invalidate the
# pinned expected/*.json. These values still do NOT track AKMS's historical v1
# contracts/failure-memory-integration/v1/integration-lock.json (0.1.0 @
# 72494ac), which is deliberately left recording that artifact.
#
# Bumping these is a deliberate re-pin, never a routine edit: when the locked
# ``akms-failure-memory`` version changes, point PINNED_AKMS_COMMIT at the new
# release tag's commit, re-read the pack digest from its fixture-manifest.json,
# and keep all three call sites in step:
#   services/logic_loom_api/tests/test_failure_memory_conformance.py
#   services/logic_loom_api/tests/test_failure_memory_operator.py
#   tests/failure_memory/test_consumer_contract_pack.py
PINNED_AKMS_COMMIT = "937f7aa062dee10837e0dbb9b98cd80a4bd20495"  # tag v0.4.0
PACK_SUBPATH = "packages/akms_failure_memory/tests/fixtures/consumer_contract/v1"
EXPECTED_PACK_SHA256 = "bd1c2dc3a1d35ab41d7203aecce1bd1d39a494dde1532c9360b9e3518f2f8735"
AKMS_REPOSITORY = "https://github.com/CEmM2/AKMS.git"


def _sparse_fetch_pack(destination: Path) -> Path:
    """Fetch ONLY the pack subtree at the pinned commit into `destination`.

    A `git sparse-checkout` (cone mode) + single-commit `fetch` — not a full
    clone, not a vendored copy committed to this repository. `destination` is
    always a pytest tmp_path; nothing here writes inside the Logic-Loom repo.
    """
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args], cwd=destination, check=True, capture_output=True, text=True
    )
    destination.mkdir(parents=True, exist_ok=True)
    run("init", "-q")
    run("remote", "add", "origin", AKMS_REPOSITORY)
    # The pack is verified by hashing the bytes git puts on disk, so the
    # checkout must be byte-exact. On Windows git defaults to
    # core.autocrlf=true and rewrites LF -> CRLF, which changes every file's
    # digest and makes verify_pack.py report "digest mismatch" for all of
    # expected/*.json while the manifest's own pack_sha256 still matches.
    # Disable the conversion for this throwaway clone.
    run("config", "core.autocrlf", "false")
    run("config", "core.eol", "lf")
    run("sparse-checkout", "init", "--cone")
    run("sparse-checkout", "set", PACK_SUBPATH)
    run("fetch", "--depth", "1", "origin", PINNED_AKMS_COMMIT)
    run("checkout", "-q", "FETCH_HEAD")
    return destination / PACK_SUBPATH


@pytest.fixture(scope="module")
def fetched_pack(tmp_path_factory: pytest.TempPathFactory) -> Path:
    destination = tmp_path_factory.mktemp("consumer-contract-pack")
    return _sparse_fetch_pack(destination)


def test_pack_is_reachable_only_via_git_not_the_installed_package() -> None:
    """Documents (and pins) the access-path finding from the module docstring."""
    try:
        import akms_failure_memory
    except ImportError:
        pytest.skip("akms_failure_memory not installed; run `uv sync --extra failure-memory`")
    package_root = Path(akms_failure_memory.__file__).resolve().parent
    # The installed package tree has no `tests/` directory at all -- the
    # wheel packages `src/akms_failure_memory/**` only.
    assert not (package_root / "tests").exists()
    assert not (package_root.parent / "tests" / "fixtures" / "consumer_contract").exists()


def test_pack_checksum_matches_the_pinned_lock(fetched_pack: Path) -> None:
    manifest = json.loads((fetched_pack / "fixture-manifest.json").read_text(encoding="utf-8"))
    assert manifest["checksum"]["pack_sha256"] == EXPECTED_PACK_SHA256

    # Run the pack's own bundled verifier exactly as documented -- it
    # recomputes every file's digest plus the whole-pack digest and fails
    # closed on any drift, extra file, or missing file.
    completed = subprocess.run(
        [sys.executable, str(fetched_pack / "verify_pack.py"), str(fetched_pack)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert f"PASS consumer-contract pack verified: {fetched_pack}" in completed.stdout


def test_pack_identity_matches_this_repos_pinned_commit(fetched_pack: Path) -> None:
    """project.toml [toolchain] values the pack was built against must match
    what Logic-Loom's own .failure-memory/config.toml pins -- both are the
    same integration-lock.json identity tuple."""
    import tomllib

    manifest = json.loads((fetched_pack / "fixture-manifest.json").read_text(encoding="utf-8"))
    config_path = Path(__file__).resolve().parents[2] / ".failure-memory" / "config.toml"
    our_toolchain = tomllib.loads(config_path.read_text(encoding="utf-8"))["toolchain"]

    assert our_toolchain["akms_public_api_sha256"] == manifest["identity"]["akms_public_api_sha256"]
    # The repo2md fixture digest only identifies the external exporter. Since
    # AKMS 0.4 this project uses the exporter shipped in akms
    # (`mirror_provider = "builtin"`), which needs none of the repo2md_* keys;
    # a stale repo2md pin left beside it would describe a tool nothing runs.
    if our_toolchain.get("mirror_provider", "repo2md") == "repo2md":
        assert our_toolchain["repo2md_fixture_sha256"] == manifest["identity"]["repo2md_fixture_sha256"]
    else:
        assert our_toolchain["mirror_provider"] == "builtin"
        assert not [key for key in our_toolchain if key.startswith("repo2md_")]
