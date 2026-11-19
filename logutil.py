"""Tiny debug log in %APPDATA%\\LiquidGlassSensors\\debug.log."""
from __future__ import annotations

import os
import time

import config

MAX_BYTES = 200_000


def path() -> str:
    return os.path.join(config.CONFIG_DIR, "debug.log")


def trim() -> None:
    try:
        if os.path.exists(path()) and os.path.getsize(path()) > MAX_BYTES:
            os.remove(path())
    except OSError:
        pass


def log(message: str) -> None:
    try:
        os.makedirs(config.CONFIG_DIR, exist_ok=True)
        with open(path(), "a", encoding="utf-8") as handle:
            handle.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
    except Exception:
        pass
