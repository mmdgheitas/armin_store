from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app import create_app
from app.db import get_db
from app.services.inventory import expire_unpaid_orders


def main() -> None:
    app = create_app()
    with app.app_context():
        conn = get_db()
        expired = expire_unpaid_orders(conn)
        print(f"Expired {expired} unpaid orders.")


if __name__ == "__main__":
    main()
