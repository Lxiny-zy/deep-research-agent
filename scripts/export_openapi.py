"""Emit the HTTP contract without starting the app or opening runtime data."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    from deep_research.api import app

    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(app.openapi(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
