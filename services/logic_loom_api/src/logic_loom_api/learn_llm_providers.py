"""Re-export shim — provider discovery now lives in host_llm/discovery.py.

Existing import sites continue to resolve from here unchanged (keep it that way).
Do not add logic here; modify host_llm/discovery.py instead.
"""

from .host_llm.discovery import *  # noqa: F401, F403
from .host_llm.discovery import (
    _api_key_set,
    _build_prompt,
    _BUILTIN_CLIS,
    _CLI_TIMEOUT_S,
    _custom_clis,
    _FULL_FENCE_RE,
    _KEY_ENV_TO_FIELD,
    _make_provider,
    _normalize_cli_output,
    _run_cli_template,
    available_cli_providers,
    discover_providers,
    inject_api_keys_into_env,
    logger,
    register_host_llm_providers,
    rewire_host_llm,
    shutil,
)

__all__ = [
    # Public API
    "discover_providers",
    "inject_api_keys_into_env",
    "register_host_llm_providers",
    "rewire_host_llm",
    "available_cli_providers",
    # Supporting surface (used by test suite via `p.<name>`)
    "_make_provider",
    "_run_cli_template",
    "_normalize_cli_output",
    "_build_prompt",
    "_custom_clis",
    "_api_key_set",
    # Module-level objects tests access as attributes on this module
    "shutil",
    "logger",
    # Constants
    "_CLI_TIMEOUT_S",
    "_BUILTIN_CLIS",
    "_KEY_ENV_TO_FIELD",
    "_FULL_FENCE_RE",
]
