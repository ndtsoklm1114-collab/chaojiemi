"""個人設定：`~/.config/chaojiemi/config.json`，沒有就全用預設。
可設的鍵：out_dir（去識別版資料夾）、restore_dir（還原版資料夾）、map_dir（對照表資料夾）、skip（常用不換的字串清單）。
例：{"out_dir": "~/Documents/去識別版", "skip": ["某某中心"]}"""
from __future__ import annotations

import json
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "chaojiemi" / "config.json"


def _load() -> dict:
    try:
        d = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


CFG = _load()


def path(key: str, default: str | Path) -> Path:
    return Path(CFG.get(key) or default).expanduser()
