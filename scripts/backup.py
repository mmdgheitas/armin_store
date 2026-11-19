from __future__ import annotations

import datetime as dt
import sqlite3
import sys
import tarfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.config import Config


def main() -> None:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = Path(Config.BACKUP_FOLDER)
    backup_dir.mkdir(parents=True, exist_ok=True)

    db_src_path = Path(Config.DATABASE_PATH)
    db_dst_path = backup_dir / f"armin_store_{stamp}.db"
    if db_src_path.exists():
        src_conn = sqlite3.connect(str(db_src_path))
        dst_conn = sqlite3.connect(str(db_dst_path))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()
        print(f"SQLite backup created: {db_dst_path}")

    uploads_dir = Path(Config.UPLOAD_FOLDER)
    if uploads_dir.exists():
        tar_path = backup_dir / f"uploads_{stamp}.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(uploads_dir, arcname="uploads")
        print(f"Media backup created: {tar_path}")


if __name__ == "__main__":
    main()
