"""FastAPI dependency providers."""

from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, HTTPException, status

from .config import Settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached, process-wide settings. Use ``reload_settings`` to invalidate."""
    return Settings.load()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


def get_vault_root(settings: Settings = Depends(get_settings)) -> Path:
    if settings.vault_path is None:
        raise HTTPException(status.HTTP_412_PRECONDITION_FAILED, detail="vault_path is not configured")
    root = settings.vault_path.expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(status.HTTP_412_PRECONDITION_FAILED, detail=f"vault_path does not exist: {root}")
    return root


# A single process-wide lock for state-file writes (batch_assignments.json, etc.).
# Wraps a re-entrant lock so handlers that recursively touch state don't deadlock.
_state_lock = threading.RLock()


def get_state_lock() -> threading.RLock:
    return _state_lock
