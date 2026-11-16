import pytest

from logic_loom_api.host_llm.model_map import api_model_for, UnknownProviderError


class TestHostLlmModelMap:
    """Tests for host_llm/model_map.py — provider -> model per consumer."""

    @pytest.mark.unit
    def test_table_lookup_per_consumer_default(self):
        """Verifies: Table lookups return the documented per-consumer default. Passes when: api_model_for('anthropic','nodegen') and api_model_for('anthropic','zsum') return their respective documented defaults, non-empty."""
        nodegen_model = api_model_for("anthropic", "nodegen")
        zsum_model = api_model_for("anthropic", "zsum")

        # Both must be non-empty strings
        assert nodegen_model, "nodegen model must not be empty"
        assert zsum_model, "zsum model must not be empty"

        # Pin the documented defaults (sourced from upstream anchors)
        assert nodegen_model == "claude-sonnet-4-20250514"
        assert zsum_model == "claude-sonnet-4-6"

        # The whole point of this module: same provider, different consumers → different models
        assert nodegen_model != zsum_model, (
            "anthropic/nodegen and anthropic/zsum must diverge — "
            "if they're equal, the per-consumer table is not working"
        )

    @pytest.mark.unit
    def test_fallback_to_provider_default(self):
        """Verifies: Provider with no per-consumer entry falls back to provider default. Passes when: Unknown consumer with known provider returns the per-provider default."""
        # "newconsumer" has no entry in _CONSUMER_MODEL_TABLE, so the per-provider
        # default from _PROVIDER_DEFAULTS must be returned.
        result = api_model_for("anthropic", "newconsumer")
        assert result == "claude-sonnet-4-20250514", (
            f"Expected per-provider default 'claude-sonnet-4-20250514', got {result!r}"
        )

        # Same for other known providers
        assert api_model_for("openai", "newconsumer") == "gpt-4o"
        assert api_model_for("google", "newconsumer") == "gemini-2.0-flash"

        # task_kind is a no-op seed — must not change the result
        assert api_model_for("anthropic", "newconsumer", task_kind="summary") == result

    @pytest.mark.unit
    def test_unknown_provider_error_or_marked_fallback(self):
        """Verifies: Unknown provider behavior — typed error or marked fallback, never empty string. Passes when: Unknown provider raises TypeError or returns a clearly-marked fallback sentinel (not silent empty string)."""
        with pytest.raises(UnknownProviderError):
            api_model_for("nonexistent-provider", "zsum")

        # Verify it also works for a consumer+provider both unknown
        with pytest.raises(UnknownProviderError):
            api_model_for("totally-made-up", "also-made-up")

        # UnknownProviderError must be a subclass of KeyError (documented contract)
        assert issubclass(UnknownProviderError, KeyError)
