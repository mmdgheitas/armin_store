from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app import create_app
from app.db import get_db
from app.services.smsir import process_sms_queue


def main() -> None:
    app = create_app()
    with app.app_context():
        conn = get_db()
        sent = process_sms_queue(conn, limit=100)
        print(f"Processed {sent} queued SMS items.")


if __name__ == "__main__":
    main()
