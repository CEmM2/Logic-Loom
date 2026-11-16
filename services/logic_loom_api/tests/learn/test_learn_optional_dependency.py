"""Tests for akms-learn as an optional dependency (vendored + declared as an extra).

Covers: import akms_learn / compile_learning_source succeed with the extra
installed; without it, the import fails cleanly.
"""

from __future__ import annotations

import importlib.util

import pytest


class TestLearnOptionalDependency:
    """Integration tests: akms_learn vendored + declared as an optional extra."""

    @pytest.mark.integration
    def test_import_akms_learn_succeeds_when_extra_installed(self):
        """Verifies: `import akms_learn` resolves when the akms extra is installed.

        Requirement: 'With the akms extra installed, import akms_learn succeeds.'
        Passes when: importlib can locate and import the akms_learn package.
        """
        # The akms extra (which now declares akms-learn) is installed in this
        # dev env, so the package must be discoverable on sys.path.
        assert importlib.util.find_spec("akms_learn") is not None
        # And actually importable, not just discoverable.
        module = importlib.import_module("akms_learn")
        assert module.__name__ == "akms_learn"

    @pytest.mark.integration
    def test_compile_learning_source_importable(self):
        """Verifies: the public compile entrypoint is importable.

        Requirement: 'from akms_learn import compile_learning_source succeeds.'
        Passes when: the symbol imports and is callable.
        """
        from akms_learn import compile_learning_source

        assert callable(compile_learning_source)

    @pytest.mark.integration
    def test_unavailable_import_is_clean_when_missing(self):
        """Verifies: without the extra, the absence is detectable cleanly.

        Requirement: 'Without the extra, import fails cleanly (ModuleNotFoundError),
        enabling the unavailable status path.'
        Passes when: importlib.util.find_spec semantics let the adapter detect absence
        without raising at module-load time.

        We cannot uninstall the package in-process, so we exercise the
        *detection idiom* the adapter relies on: find_spec for a missing module
        returns None rather than raising, giving a clean unavailable path.
        """
        assert (
            importlib.util.find_spec("a_module_that_does_not_exist_xyz") is None
        )
