"""BBT-live indicator. Mirrors view.ts isBbtLive(): reads the zsum config,
finds bbt_json, and checks whether it was modified within the last hour.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import yaml


def _expand(p: str) -> Path:
    return Path(p).expanduser()


def bbt_status(zsum_config_path: Path) -> dict[str, Any]:
    """Return ``{is_live, mtime, bbt_json}`` for the BBT JSON path.

    "live" = file exists and was modified within the last 60 minutes.
    """
    try:
        raw = zsum_config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"is_live": False, "mtime": None, "bbt_json": None, "reason": "config-missing"}
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        return {"is_live": False, "mtime": None, "bbt_json": None, "reason": f"yaml: {e}"}
    if not isinstance(parsed, dict):
        return {"is_live": False, "mtime": None, "bbt_json": None, "reason": "config-not-mapping"}
    bbt_value = parsed.get("bbt_json")
    if not isinstance(bbt_value, str) or not bbt_value:
        return {"is_live": False, "mtime": None, "bbt_json": None, "reason": "bbt_json-unset"}
    bbt_path = _expand(bbt_value)
    try:
        mtime = bbt_path.stat().st_mtime
    except FileNotFoundError:
        return {
            "is_live": False,
            "mtime": None,
            "bbt_json": str(bbt_path),
            "reason": "bbt_json-missing",
        }
    age = time.time() - mtime
    return {
        "is_live": age < 60 * 60,
        "mtime": mtime,
        "bbt_json": str(bbt_path),
        "age_seconds": age,
    }
