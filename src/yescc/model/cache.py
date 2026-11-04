"""本地模型缓存：内容寻址、7 天 TTL、不跨用户。"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import platformdirs

CACHE_TTL_S = 7 * 24 * 60 * 60


def _cache_dir() -> Path:
    override = os.environ.get("YESCC_CACHE_DIR")
    root = Path(override) if override else Path(platformdirs.user_cache_dir("yescc"))
    return root / "model-v1"


def cache_key(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load(key: str) -> dict[str, Any] | None:
    path = _cache_dir() / f"{key}.json"
    try:
        if time.time() - path.stat().st_mtime > CACHE_TTL_S:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("value")
    return value if isinstance(value, dict) else None


def store(key: str, value: dict[str, Any]) -> None:
    directory = _cache_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / f"{key}.tmp"
        temporary.write_text(
            json.dumps({"value": value}, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(directory / f"{key}.json")
    except OSError:
        return
