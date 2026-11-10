from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG: dict[str, Any] = {
    "db": "./data/bidradar.db",
    "keywords": ["信息化", "人工智能", "网络安全"],
    "sources": {
        "cebpub": True,
        "ggzy": True,
        "ccgp": False,
        "ted": True,
        "sam": False,
        "searxng": False,
        "gd": True,
        "js": True,
        "zj": True,
    },
    "filters": {
        "regions": [],
        "exclude": ["废标", "终止公告"],
    },
    "notify": {
        "webhook": "",
        "min_amount": 0,
    },
    "schedule": {
        "interval_seconds": 600,
    },
}

CONFIG_FILENAME = "config.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def find_config(path: str | Path | None = None) -> Path:
    if path:
        return Path(path).expanduser()
    import os

    env_path = os.getenv("BIDRADAR_CONFIG")
    if env_path:
        return Path(env_path).expanduser()
    return Path.cwd() / CONFIG_FILENAME


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = find_config(path)
    if not config_path.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件格式错误: {config_path}")
    config = _deep_merge(DEFAULT_CONFIG, data)
    config["_config_path"] = str(config_path)
    config["_base_dir"] = str(config_path.parent)
    return config


def db_path(config: dict[str, Any]) -> Path:
    raw = Path(str(config.get("db") or "./data/bidradar.db")).expanduser()
    if raw.is_absolute():
        return raw
    return Path(config.get("_base_dir") or Path.cwd()) / raw


def write_default_config(path: str | Path | None = None) -> Path:
    target = find_config(path)
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(DEFAULT_CONFIG, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return target
