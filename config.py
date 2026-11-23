"""Config persistence and autostart helpers."""
from __future__ import annotations

import json
import os
import subprocess
import sys

APP_NAME = "LiquidGlassSensors"
TASK_NAME = "LiquidGlassSensors"
VERSION = "1.0.8"
ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(os.environ["APPDATA"], APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULTS = {
    "x": None,
    "y": None,
    "fan_sensor": "CPU Fan",
    "theme": "auto",
    "autostart": False,
    "visible": True,
    "locked": False,
}


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data.update(json.load(f))
    except Exception:
        pass
    return data


def save(data: dict) -> None:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CONFIG_PATH)
    except Exception as exc:  # noqa: BLE001
        print("config save failed:", exc)


def autostart_enabled() -> bool:
    try:
        out = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME, "/XML"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=0x08000000,
        )
        if out.returncode != 0:
            return False
        # A task can exist but be switched off; that is stored on its trigger.
        return "<Enabled>false</Enabled>" not in (out.stdout or "")
    except Exception:
        return False


def _elevated_powershell(script: str) -> bool:
    try:
        import ctypes

        params = f'-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command "{script}"'
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", params, None, 0)
        return rc > 32
    except Exception:
        return False


def set_autostart(enabled: bool) -> bool:
    try:
        import ctypes

        script = os.path.join(ROOT, "autostart.ps1")
        mode = "enable" if enabled else "disable"
        if _is_admin():
            # Pass the path as a single argument: quoting it by hand breaks
            # PowerShell's -File parsing and the toggle silently did nothing.
            done = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-WindowStyle",
                    "Hidden",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    script,
                    "-Mode",
                    mode,
                ],
                capture_output=True,
                creationflags=0x08000000,
            )
            return done.returncode == 0
        args = (
            f'-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass '
            f'-File "{script}" -Mode {mode}'
        )
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "powershell.exe", args, ROOT, 0
        )
        return rc > 32
    except Exception:
        return False


def _is_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def restart_elevated() -> None:
    try:
        import ctypes

        params = f'"{os.path.join(ROOT, "main.py")}"'
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe"), params, ROOT, 1
        )
    except Exception:
        pass
