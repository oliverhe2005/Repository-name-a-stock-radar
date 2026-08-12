import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_config() -> dict:
    path = Path(os.environ.get("WATCHLIST_CONFIG", ROOT / "config.json"))
    if not path.is_absolute():
        path = ROOT / path
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("database_path", "report_dir"):
        p = Path(data[key])
        if not p.is_absolute():
            data[key] = str(ROOT / p)
    return data
