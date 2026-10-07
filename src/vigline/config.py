"""Application settings. Secrets and paths come from the environment; the
authorized promo-terms source list defaults to empty (see config/sources.example.json).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

TIMEZONE = "America/New_York"
DEFAULT_EXPIRY_WINDOW_HOURS = 72


@dataclass
class Settings:
    db_path: Path
    sources_file: Path
    timezone: str = TIMEZONE
    expiry_window_hours: int = DEFAULT_EXPIRY_WINDOW_HOURS


def load_settings() -> Settings:
    db_path = Path(os.environ.get("VIGLINE_DB_PATH", PROJECT_ROOT / "data" / "vigline.db"))
    sources_file = Path(
        os.environ.get("VIGLINE_SOURCES_FILE", PROJECT_ROOT / "config" / "sources.json")
    )
    return Settings(db_path=db_path, sources_file=sources_file)


def load_authorized_sources(settings: Settings) -> list[str]:
    """Return the configured list of authorized official promo-terms URLs.

    Default is the empty list: with no configured sources, terms capture must
    report 'Unavailable — no authorized source configured.'
    """
    path = settings.sources_file
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    urls = data.get("official_urls", []) if isinstance(data, dict) else []
    return [u for u in urls if isinstance(u, str) and u.startswith(("http://", "https://"))]
