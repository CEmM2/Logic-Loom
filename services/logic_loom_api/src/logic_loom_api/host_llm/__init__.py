"""host_llm — unified host-LLM wiring (discovery, routing, model map).

Public surface re-exported here so callers can use either:
    from logic_loom_api.host_llm import discover_providers
or the fully-qualified path:
    from logic_loom_api.host_llm.discovery import discover_providers
"""

from .discovery import (
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
    register_host_llm_providers,
    rewire_host_llm,
)
from .model_map import UnknownProviderError, api_model_for
from .routing import (
    HostLLMProvider,
    Transport,
    resolve_choice,
    resolve_host_llm_provider,
    route_for,
)

__all__ = [
    "discover_providers",
    "inject_api_keys_into_env",
    "register_host_llm_providers",
    "rewire_host_llm",
    "available_cli_providers",
    "_make_provider",
    "_run_cli_template",
    "_normalize_cli_output",
    "_build_prompt",
    "_custom_clis",
    "_api_key_set",
    "_CLI_TIMEOUT_S",
    "_BUILTIN_CLIS",
    "_KEY_ENV_TO_FIELD",
    "_FULL_FENCE_RE",
    # routing
    "HostLLMProvider",
    "Transport",
    "resolve_choice",
    "route_for",
    "resolve_host_llm_provider",
    # model_map
    "api_model_for",
    "UnknownProviderError",
]
