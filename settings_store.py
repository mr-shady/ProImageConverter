from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

SETTINGS_SCHEMA_VERSION = 2

DEFAULT_SETTINGS: dict[str, Any] = {
    "schema_version": SETTINGS_SCHEMA_VERSION,
    "input_folder": "",
    "output_folder": "",
    "output_format": "WEBP",
    "quality": 90,
    "include_subfolders": True,
    "preserve_structure": True,
    "overwrite": False,
    "preserve_metadata": True,
    "strip_metadata": False,
    "preserve_timestamps": True,
    "preserve_animation": True,
    "resize_enabled": False,
    "allow_upscale": False,
    "max_width": "1920",
    "max_height": "",
    "background_color": "#FFFFFF",
    "theme": "تیره",
    "settings_tab": "خروجی",
    "window_geometry": "",
}


def settings_file_path() -> Path:
    """Return a per-user settings path that also works for a packaged EXE."""
    override = os.environ.get("PRO_IMAGE_CONVERTER_CONFIG_DIR")
    if override:
        base = Path(override).expanduser()
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))

    return base / "ProImageConverter" / "settings.json"


def load_settings() -> dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    path = settings_file_path()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return settings

    if not isinstance(raw, dict):
        return settings

    # Only accept known keys. This keeps stale or corrupt settings harmless.
    for key, default in DEFAULT_SETTINGS.items():
        if key not in raw:
            continue

        value = raw[key]
        if isinstance(default, bool):
            if isinstance(value, bool):
                settings[key] = value
        elif isinstance(default, int):
            if isinstance(value, int) and not isinstance(value, bool):
                settings[key] = value
        elif isinstance(default, str):
            if isinstance(value, str):
                settings[key] = value
        else:
            settings[key] = value

    settings["schema_version"] = SETTINGS_SCHEMA_VERSION
    return settings


def save_settings(settings: dict[str, Any]) -> Path:
    path = settings_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = dict(DEFAULT_SETTINGS)
    payload.update({key: settings[key] for key in DEFAULT_SETTINGS if key in settings})
    payload["schema_version"] = SETTINGS_SCHEMA_VERSION

    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path
