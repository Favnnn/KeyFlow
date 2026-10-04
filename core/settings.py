# -*- coding: utf-8 -*-
"""Application settings (settings.json in the app folder)."""
import json
import os
import threading

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")

DEFAULTS = {
    "language": "ru",          # ru | en
    "theme": "dark",           # dark | light
    "link_style": "circuit",   # circuit (прямоугольные «платы» по сетке) | spline (кривые)
    "snap_grid": True,         # выравнивать ноды по сетке при перетаскивании
    "active_workflow": "main",
    "port": 8760,
    "open_browser": True,
}

_lock = threading.Lock()
_cache = None


def load():
    """Load settings once, return a copy."""
    global _cache
    with _lock:
        if _cache is None:
            data = {}
            try:
                with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass
            merged = dict(DEFAULTS)
            for k, v in data.items():
                if k in DEFAULTS:
                    merged[k] = v
            _cache = merged
        return dict(_cache)


def update(patch):
    """Update whitelisted keys and persist."""
    global _cache
    with _lock:
        cur = dict(_cache if _cache is not None else DEFAULTS)
        for k, v in (patch or {}).items():
            if k in DEFAULTS:
                cur[k] = v
        _cache = cur
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_PATH)
        return dict(_cache)
