from __future__ import annotations

import json
import os
from pathlib import Path

from .models import AppConfig


def load_config() -> AppConfig:
    path = Path(os.getenv("FOMO_FOLLOWER_CONFIG", "config.json"))
    if not path.exists():
        example = Path("config.example.json")
        if not example.exists():
            return AppConfig()
        path = example

    return AppConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
