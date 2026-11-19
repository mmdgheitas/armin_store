from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.utils.persian import now_utc_iso


def log_audit(
    conn: sqlite3.Connection,
    *,
    admin_id: int | None,
    admin_name: str,
    action: str,
    entity_type: str,
    entity_id: str | int = "",
    before: Any = None,
    after: Any = None,
    ip_address: str = "",
    user_agent: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO audit_logs
            (admin_id, admin_name, action, entity_type, entity_id, before_json, after_json, ip_address, user_agent, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            admin_id,
            admin_name,
            action,
            entity_type,
            str(entity_id),
            json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
            json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
            ip_address,
            user_agent[:250],
            now_utc_iso(),
        ),
    )


def list_audit_logs(
    conn: sqlite3.Connection,
    *,
    entity_type: str = "",
    action: str = "",
    page: int = 1,
    per_page: int = 30,
) -> tuple[list[sqlite3.Row], int]:
    where = []
    params: list[Any] = []
    if entity_type:
        where.append("entity_type = ?")
        params.append(entity_type)
    if action:
        where.append("action = ?")
        params.append(action)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM audit_logs {where_sql}", params).fetchone()[0]
    offset = max(0, (page - 1) * per_page)
    rows = conn.execute(
        f"""
        SELECT * FROM audit_logs
        {where_sql}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, per_page, offset],
    ).fetchall()
    return rows, total
