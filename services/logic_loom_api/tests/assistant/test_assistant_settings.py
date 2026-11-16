"""Tests for the Assistant Settings page — backend half.

Backend portion covers config.py Pydantic field additions and the
assistant_workspace_path() resolver. UI tests live alongside in
apps/desktop/src/__tests__/assistant/assistant_settings.test.tsx.

Covers: settings persist via TOML; the assistant_workspace_path resolver
returns correct paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomli_w

from logic_loom_api.config import Settings


class TestAssistantSettings:
    """Unit tests for the Assistant config fields + workspace resolver."""

    @pytest.mark.unit
    def test_assistant_fields_round_trip_through_toml(self, tmp_path: Path) -> None:
        """Verifies: openalex_user_email, openalex_api_key,
        semantic_scholar_api_key, and assistant_workspace persist through
        a Settings.load() round-trip.

        Requirement: 'Settings persist via TOML'.
        """
        toml_path = tmp_path / "config.toml"
        data = {
            "openalex_user_email": "research@example.org",
            "openalex_api_key": "oa-key-123",
            "semantic_scholar_api_key": "s2-key-456",
            "assistant_workspace": str(tmp_path / "MyAssistantWS"),
            "assistant_workspace_colocate": False,
        }
        with toml_path.open("wb") as f:
            tomli_w.dump(data, f)

        s = Settings.load(config_path=toml_path)
        assert s.openalex_user_email == "research@example.org"
        assert s.openalex_api_key == "oa-key-123"
        assert s.semantic_scholar_api_key == "s2-key-456"
        assert s.assistant_workspace == Path(str(tmp_path / "MyAssistantWS"))
        assert s.assistant_workspace_colocate is False

    @pytest.mark.unit
    def test_placeholder_values_are_filtered(self, tmp_path: Path) -> None:
        """Verifies: '<...>' placeholders in TOML are filtered out so the
        field falls back to its default (None / False), which is what the
        first-run UX detects.
        """
        toml_path = tmp_path / "config.toml"
        data = {
            "openalex_user_email": "<OPENALEX_USER_EMAIL>",
            "openalex_api_key": "<OPENALEX_API_KEY>",
            "semantic_scholar_api_key": "<SEMANTIC_SCHOLAR_API_KEY>",
        }
        with toml_path.open("wb") as f:
            tomli_w.dump(data, f)

        s = Settings.load(config_path=toml_path)
        assert s.openalex_user_email is None
        assert s.openalex_api_key is None
        assert s.semantic_scholar_api_key is None

    @pytest.mark.unit
    def test_assistant_workspace_path_default_layout(self, tmp_path: Path) -> None:
        """Verifies: assistant_workspace_path() defaults to
        <akms_workspace>/../Assistant-Workspace when assistant_workspace
        is unset and akms_workspace is set.

        Requirement: 'assistant_workspace_path() resolver returns correct paths
        for both layouts'.
        """
        akms_ws = tmp_path / "Documents" / "Logic-Loom" / "AKMS-Workspace"
        s = Settings(
            akms_workspace=akms_ws,
            assistant_workspace=None,
            assistant_workspace_colocate=False,
        )
        got = s.assistant_workspace_path()
        expected = akms_ws.parent / "Assistant-Workspace"
        assert got == expected

    @pytest.mark.unit
    def test_assistant_workspace_path_colocation_layout(self, tmp_path: Path) -> None:
        """Verifies: assistant_workspace_path() returns
        <akms_workspace>/assistant when explicit colocation is configured.

        Requirement: 'assistant_workspace_path() resolver returns correct paths
        for both layouts'.
        """
        akms_ws = tmp_path / "Documents" / "Logic-Loom" / "AKMS-Workspace"
        s = Settings(
            akms_workspace=akms_ws,
            assistant_workspace=None,
            assistant_workspace_colocate=True,
        )
        got = s.assistant_workspace_path()
        expected = akms_ws / "assistant"
        assert got == expected

    @pytest.mark.unit
    def test_assistant_workspace_path_explicit_override(self, tmp_path: Path) -> None:
        """Verifies: an explicit `assistant_workspace` value wins over both
        the default and the colocation flag. Tilde expansion is performed.
        """
        explicit = tmp_path / "custom" / "ws"
        s = Settings(
            akms_workspace=tmp_path / "ignored",
            assistant_workspace=explicit,
            assistant_workspace_colocate=True,  # ignored when explicit is set
        )
        assert s.assistant_workspace_path() == explicit

    @pytest.mark.unit
    def test_assistant_batches_path_uses_resolver(self, tmp_path: Path) -> None:
        """Verifies: assistant_batches_path()/assistant_workflows_path()/
        assistant_prompt_profiles_path() all route through the resolver
        so they react to colocation flag changes.
        """
        akms_ws = tmp_path / "ws"
        s_default = Settings(
            akms_workspace=akms_ws,
            assistant_workspace=None,
            assistant_workspace_colocate=False,
        )
        s_coloc = Settings(
            akms_workspace=akms_ws,
            assistant_workspace=None,
            assistant_workspace_colocate=True,
        )
        assert s_default.assistant_batches_path() == akms_ws.parent / "Assistant-Workspace" / "batches.json"
        assert s_coloc.assistant_batches_path() == akms_ws / "assistant" / "batches.json"
        assert s_default.assistant_workflows_path().name == "workflows.json"
        assert s_default.assistant_prompt_profiles_path().name == "prompt_profiles.json"
