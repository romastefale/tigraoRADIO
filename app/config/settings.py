from __future__ import annotations

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
DATABASE_URL = f"sqlite:///{(DATA_DIR / 'app.db').resolve()}"
