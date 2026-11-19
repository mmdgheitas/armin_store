from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
from typing import Any

import requests
from flask import current_app

from app.repositories.settings import get_bool_setting, get_int_setting, get_setting
from app.utils.persian import normalize_iran_mobile, now_utc_iso

logger = logging.getLogger("armin_store.sms")


class SmsService:
    """SMS.ir v1 client + DB-backed queue so SMS failures never disrupt order flows."""

    BASE_URL = "https://api.sms.ir/v1"

    def __init__(
        self,
        *,
        api_key: str,
        line_number: str,
        mode: str = "mock",
        timeout: int = 10,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.line_number = (line_number or "").strip()
        self.mode = (mode or "mock").strip().lower()
        self.timeout = max(3, int(timeout))

    @classmethod
    def from_conn(cls, conn: sqlite3.Connection) -> "SmsService":
        api_key = get_setting(conn, "smsir_api_key", "") or current_app.config.get(
            "SMSIR_API_KEY", ""
        )
        line = get_setting(conn, "smsir_line_number", "") or current_app.config.get(
            "SMSIR_LINE_NUMBER", ""
        )
        mode = current_app.config.get("SMS_MODE", "mock")
        timeout = current_app.config.get("SMSIR_TIMEOUT_SECONDS", 10)
        return cls(api_key=api_key, line_number=line, mode=mode, timeout=timeout)

    def send_verify(
        self, *, mobile: str, template_id: int, parameters: dict[str, Any]
    ) -> tuple[bool, str, str]:
        """Send template-based verification/transactional SMS via POST /send/verify."""
        norm_mobile = normalize_iran_mobile(mobile) or mobile
        # sms.ir enforces max 25 chars per parameter value
        param_list = [
            {"name": str(k)[:25], "value": str(v)[:25]} for k, v in parameters.items()
        ]
        payload = {
            "mobile": norm_mobile,
            "templateId": int(template_id),
            "parameters": param_list,
        }

        if self.mode == "mock" or not self.api_key:
            logger.info("[SMS MOCK verify] mobile=%s tpl=%s params=%s", norm_mobile, template_id, param_list)
            return True, "mock-msg-id", json.dumps({"mock": True, "payload": payload}, ensure_ascii=False)

        try:
            resp = requests.post(
                f"{self.BASE_URL}/send/verify",
                json=payload,
                headers={"x-api-key": self.api_key, "Accept": "application/json"},
                timeout=self.timeout,
            )
            data = resp.json() if resp.content else {}
            if resp.status_code == 200 and int(data.get("status", 0)) == 1:
                msg_id = str((data.get("data") or {}).get("messageId", ""))
                return True, msg_id, json.dumps(data, ensure_ascii=False)[:1000]
            return False, "", json.dumps(data or {"http": resp.status_code}, ensure_ascii=False)[:1000]
        except Exception as exc:
            logger.warning("SMS.ir verify failed: %s", exc)
            return False, "", str(exc)[:500]

    def send_bulk(self, *, mobile: str, message_text: str) -> tuple[bool, str, str]:
        """Send free-form message via POST /send/bulk when no template is specified."""
        norm_mobile = normalize_iran_mobile(mobile) or mobile
        if self.mode == "mock" or not self.api_key or not self.line_number:
            logger.info("[SMS MOCK bulk] mobile=%s text=%s", norm_mobile, message_text)
            return True, "mock-bulk-id", json.dumps({"mock": True, "mobile": norm_mobile}, ensure_ascii=False)
        try:
            line_int = int(self.line_number)
        except ValueError:
            return False, "", "شماره خط پیامک معتبر نیست."
        payload = {
            "lineNumber": line_int,
            "messageText": message_text,
            "mobiles": [norm_mobile],
        }
        try:
            resp = requests.post(
                f"{self.BASE_URL}/send/bulk",
                json=payload,
                headers={"x-api-key": self.api_key, "Accept": "application/json"},
                timeout=self.timeout,
            )
            data = resp.json() if resp.content else {}
            if resp.status_code == 200 and int(data.get("status", 0)) == 1:
                ids = (data.get("data") or {}).get("messageIds") or []
                return True, str(ids[0] if ids else ""), json.dumps(data, ensure_ascii=False)[:1000]
            return False, "", json.dumps(data or {"http": resp.status_code}, ensure_ascii=False)[:1000]
        except Exception as exc:
            logger.warning("SMS.ir bulk failed: %s", exc)
            return False, "", str(exc)[:500]


def enqueue_sms(
    conn: sqlite3.Connection,
    *,
    recipient: str,
    event_type: str,
    template_id: int | None = None,
    params: dict[str, Any] | None = None,
    message_text: str = "",
    deliver_now: bool = True,
) -> int:
    norm = normalize_iran_mobile(recipient)
    if not norm:
        return 0
    ts = now_utc_iso()
    cur = conn.execute(
        """
        INSERT INTO sms_queue (recipient, template_id, params_json, message_text, event_type, status, attempts, max_attempts, next_retry_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'pending', 0, 3, ?, ?, ?)
        """,
        (
            norm,
            template_id,
            json.dumps(params or {}, ensure_ascii=False),
            message_text,
            event_type,
            ts,
            ts,
            ts,
        ),
    )
    queue_id = int(cur.lastrowid)
    if deliver_now:
        try:
            process_sms_queue(conn, limit=5, only_id=queue_id)
        except Exception as exc:
            logger.warning("Immediate SMS delivery failed (kept in queue): %s", exc)
    return queue_id


def process_sms_queue(
    conn: sqlite3.Connection, *, limit: int = 20, only_id: int | None = None
) -> int:
    now = now_utc_iso()
    if only_id:
        rows = conn.execute(
            "SELECT * FROM sms_queue WHERE id = ? AND status = 'pending'", (only_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM sms_queue
            WHERE status = 'pending' AND (next_retry_at IS NULL OR next_retry_at <= ?)
            ORDER BY id ASC
            LIMIT ?
            """,
            (now, limit),
        ).fetchall()

    if not rows:
        return 0

    svc = SmsService.from_conn(conn)
    processed = 0
    for row in rows:
        params = {}
        try:
            params = json.loads(row["params_json"] or "{}")
        except ValueError:
            params = {}

        if row["template_id"]:
            ok, msg_id, resp_text = svc.send_verify(
                mobile=row["recipient"],
                template_id=int(row["template_id"]),
                parameters=params,
            )
        else:
            ok, msg_id, resp_text = svc.send_bulk(
                mobile=row["recipient"],
                message_text=row["message_text"] or row["event_type"],
            )

        ts = now_utc_iso()
        attempts = int(row["attempts"]) + 1
        conn.execute(
            """
            INSERT INTO sms_logs (recipient, template_id, message_text, event_type, status, provider_message_id, provider_response, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["recipient"],
                row["template_id"],
                row["message_text"] or json.dumps(params, ensure_ascii=False),
                row["event_type"],
                "sent" if ok else "failed",
                msg_id,
                resp_text,
                ts,
            ),
        )
        if ok:
            conn.execute(
                "UPDATE sms_queue SET status = 'sent', attempts = ?, updated_at = ? WHERE id = ?",
                (attempts, ts, row["id"]),
            )
        else:
            new_status = "failed" if attempts >= int(row["max_attempts"]) else "pending"
            retry_at = (
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=2 * attempts)
            ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            conn.execute(
                """
                UPDATE sms_queue
                SET status = ?, attempts = ?, next_retry_at = ?, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_status, attempts, retry_at, resp_text[:300], ts, row["id"]),
            )
        processed += 1
    return processed


def send_event_sms(
    conn: sqlite3.Connection,
    *,
    toggle_key: str | None,
    template_setting_key: str,
    recipient: str,
    event_type: str,
    params: dict[str, Any],
    fallback_text: str,
) -> int:
    if toggle_key and not get_bool_setting(conn, toggle_key, True):
        return 0
    tpl_id = get_int_setting(conn, template_setting_key, 0)
    return enqueue_sms(
        conn,
        recipient=recipient,
        event_type=event_type,
        template_id=tpl_id if tpl_id > 0 else None,
        params=params,
        message_text=fallback_text,
        deliver_now=True,
    )


def send_admin_alert_sms(
    conn: sqlite3.Connection,
    *,
    toggle_key: str,
    template_setting_key: str,
    event_type: str,
    params: dict[str, Any],
    fallback_text: str,
) -> None:
    if not get_bool_setting(conn, toggle_key, True):
        return
    raw_mobiles = get_setting(conn, "admin_alert_mobiles", "")
    for part in raw_mobiles.replace("،", ",").split(","):
        mobile = normalize_iran_mobile(part)
        if mobile:
            send_event_sms(
                conn,
                toggle_key=None,
                template_setting_key=template_setting_key,
                recipient=mobile,
                event_type=event_type,
                params=params,
                fallback_text=fallback_text,
            )
