from __future__ import annotations

import json
import logging
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import requests
from flask import current_app

logger = logging.getLogger("armin_store.payment")


@dataclass
class GatewaySendResult:
    success: bool
    id_get: str = ""
    redirect_url: str = ""
    error_code: str = ""
    error_message: str = ""
    raw_request: str = ""
    raw_response: str = ""


@dataclass
class GatewayVerifyResult:
    success: bool
    already_verified: bool = False
    amount_rial: int = 0
    factor_id: str = ""
    card_num: str = ""
    trans_id: str = ""
    error_code: str = ""
    error_message: str = ""
    raw_request: str = ""
    raw_response: str = ""


class PaymentGateway(ABC):
    name: str = "abstract"

    @abstractmethod
    def send(
        self,
        *,
        amount_rial: int,
        callback_url: str,
        factor_id: str,
        description: str,
        customer_name: str = "",
        customer_email: str = "",
    ) -> GatewaySendResult:
        raise NotImplementedError

    @abstractmethod
    def verify(self, *, id_get: str, trans_id: str) -> GatewayVerifyResult:
        raise NotImplementedError


_BITPAY_SEND_ERRORS: dict[str, str] = {
    "-1": "کلید API درگاه بیت‌پی معتبر نیست.",
    "-2": "مبلغ پرداخت باید عددی و حداقل ۱۰۰۰ ریال باشد.",
    "-3": "آدرس بازگشت (callback) معتبر نیست.",
    "-4": "درگاهی با این مشخصات یافت نشد یا در انتظار تایید است.",
    "-5": "خطا در اتصال به سرور درگاه بیت‌پی.",
}

_BITPAY_VERIFY_ERRORS: dict[str, str] = {
    "-1": "کلید API درگاه بیت‌پی معتبر نیست.",
    "-2": "شناسه تراکنش (trans_id) معتبر نیست.",
    "-3": "شناسه دریافتی (id_get) معتبر نیست.",
    "-4": "تراکنش یافت نشد یا ناموفق بوده است.",
    "11": "تراکنش قبلاً تایید شده است.",
}


def _sanitize_payload(data: dict[str, Any]) -> str:
    masked = dict(data)
    if "api" in masked and masked["api"]:
        key = str(masked["api"])
        masked["api"] = (key[:4] + "***" + key[-2:]) if len(key) > 6 else "***"
    return json.dumps(masked, ensure_ascii=False)


class BitpayGateway(PaymentGateway):
    name = "bitpay"

    def __init__(
        self,
        *,
        api_key: str,
        mode: str = "mock",
        timeout: int = 15,
        retries: int = 2,
        mock_pay_base_url: str = "",
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.mode = (mode or "mock").strip().lower()
        self.timeout = max(3, int(timeout))
        self.retries = max(0, int(retries))
        self.mock_pay_base_url = mock_pay_base_url.rstrip("/")

        if self.mode == "test":
            self.send_url = "https://bitpay.ir/payment-test/gateway-send"
            self.gate_url_tpl = "https://bitpay.ir/payment-test/gateway-{id_get}-get"
            self.verify_url = "https://bitpay.ir/payment-test/gateway-result-second"
        else:
            self.send_url = "https://bitpay.ir/payment/gateway-send"
            self.gate_url_tpl = "https://bitpay.ir/payment/gateway-{id_get}-get"
            self.verify_url = "https://bitpay.ir/payment/gateway-result-second"

    def send(
        self,
        *,
        amount_rial: int,
        callback_url: str,
        factor_id: str,
        description: str,
        customer_name: str = "",
        customer_email: str = "",
    ) -> GatewaySendResult:
        payload = {
            "api": self.api_key,
            "amount": int(amount_rial),
            "redirect": callback_url,
            "factorId": factor_id,
            "name": customer_name[:100],
            "email": customer_email[:100],
            "description": description[:250],
        }
        raw_req = _sanitize_payload(payload)

        if self.mode == "mock":
            fake_id_get = str(100000 + secrets.randbelow(899999))
            redirect_url = f"{self.mock_pay_base_url}/payment/mock-gateway/{fake_id_get}"
            return GatewaySendResult(
                success=True,
                id_get=fake_id_get,
                redirect_url=redirect_url,
                raw_request=raw_req,
                raw_response=json.dumps({"mock": True, "id_get": fake_id_get}),
            )

        if not self.api_key:
            return GatewaySendResult(
                success=False,
                error_code="-1",
                error_message="کلید API درگاه بیت‌پی تنظیم نشده است.",
                raw_request=raw_req,
            )

        last_err = ""
        for attempt in range(self.retries + 1):
            try:
                resp = requests.post(self.send_url, data=payload, timeout=self.timeout)
                body = resp.text.strip()
                if resp.status_code >= 500:
                    last_err = f"HTTP {resp.status_code}"
                    continue
                try:
                    num = int(body)
                except ValueError:
                    num = None
                if num is not None and num > 0:
                    id_get = str(num)
                    return GatewaySendResult(
                        success=True,
                        id_get=id_get,
                        redirect_url=self.gate_url_tpl.format(id_get=id_get),
                        raw_request=raw_req,
                        raw_response=body[:500],
                    )
                code = str(num if num is not None else body)
                return GatewaySendResult(
                    success=False,
                    error_code=code,
                    error_message=_BITPAY_SEND_ERRORS.get(
                        code, "خطا در ایجاد تراکنش در درگاه بیت‌پی."
                    ),
                    raw_request=raw_req,
                    raw_response=body[:500],
                )
            except requests.RequestException as exc:
                last_err = str(exc)
                logger.warning("Bitpay send attempt %d failed: %s", attempt + 1, exc)

        return GatewaySendResult(
            success=False,
            error_code="-5",
            error_message="خطا در برقراری ارتباط با درگاه پرداخت. لطفاً دوباره تلاش کنید.",
            raw_request=raw_req,
            raw_response=last_err[:500],
        )

    def verify(self, *, id_get: str, trans_id: str) -> GatewayVerifyResult:
        payload = {
            "api": self.api_key,
            "id_get": str(id_get).strip(),
            "trans_id": str(trans_id).strip(),
            "json": 1,
        }
        raw_req = _sanitize_payload(payload)

        if self.mode == "mock":
            # In mock mode, verification succeeds for valid numeric ids unless trans_id == "FAIL"
            if str(trans_id).upper() == "FAIL":
                return GatewayVerifyResult(
                    success=False,
                    error_code="-4",
                    error_message="پرداخت توسط کاربر لغو یا ناموفق بود.",
                    raw_request=raw_req,
                    raw_response='{"status": -4}',
                )
            return GatewayVerifyResult(
                success=True,
                trans_id=str(trans_id),
                card_num="6037-99**-****-1234",
                raw_request=raw_req,
                raw_response='{"status": 1, "mock": true}',
            )

        if not self.api_key:
            return GatewayVerifyResult(
                success=False,
                error_code="-1",
                error_message="کلید API درگاه بیت‌پی تنظیم نشده است.",
                raw_request=raw_req,
            )

        last_err = ""
        for attempt in range(self.retries + 1):
            try:
                resp = requests.post(self.verify_url, data=payload, timeout=self.timeout)
                raw_body = resp.text.strip()
                if resp.status_code >= 500:
                    last_err = f"HTTP {resp.status_code}"
                    continue
                data = resp.json()
                status_str = str(data.get("status", ""))
                if status_str == "1":
                    return GatewayVerifyResult(
                        success=True,
                        amount_rial=int(data.get("amount", 0) or 0),
                        factor_id=str(data.get("factorId", "") or ""),
                        card_num=str(data.get("cardNum", "") or ""),
                        trans_id=str(trans_id),
                        raw_request=raw_req,
                        raw_response=raw_body[:1000],
                    )
                if status_str == "11":
                    return GatewayVerifyResult(
                        success=True,
                        already_verified=True,
                        amount_rial=int(data.get("amount", 0) or 0),
                        factor_id=str(data.get("factorId", "") or ""),
                        card_num=str(data.get("cardNum", "") or ""),
                        trans_id=str(trans_id),
                        error_code="11",
                        error_message=_BITPAY_VERIFY_ERRORS["11"],
                        raw_request=raw_req,
                        raw_response=raw_body[:1000],
                    )
                return GatewayVerifyResult(
                    success=False,
                    error_code=status_str,
                    error_message=_BITPAY_VERIFY_ERRORS.get(
                        status_str, "تراکنش توسط درگاه تایید نشد."
                    ),
                    raw_request=raw_req,
                    raw_response=raw_body[:1000],
                )
            except (requests.RequestException, ValueError) as exc:
                last_err = str(exc)
                logger.warning("Bitpay verify attempt %d failed: %s", attempt + 1, exc)

        return GatewayVerifyResult(
            success=False,
            error_code="NETWORK_ERROR",
            error_message="خطا در استعلام وضعیت پرداخت از درگاه.",
            raw_request=raw_req,
            raw_response=last_err[:500],
        )


def get_payment_gateway(conn, *, base_url: str = "") -> PaymentGateway:
    from app.repositories.settings import get_bool_setting, get_setting

    cfg_mode = current_app.config.get("PAYMENT_MODE", "mock")
    db_api_key = get_setting(conn, "bitpay_api_key", "")
    api_key = db_api_key or current_app.config.get("BITPAY_API_KEY", "")
    db_test_mode = get_bool_setting(conn, "bitpay_test_mode", True)

    if cfg_mode == "mock":
        mode = "mock"
    elif cfg_mode == "test" or db_test_mode:
        mode = "test"
    else:
        mode = "production"

    return BitpayGateway(
        api_key=api_key,
        mode=mode,
        timeout=current_app.config.get("BITPAY_TIMEOUT_SECONDS", 15),
        retries=current_app.config.get("BITPAY_RETRIES", 2),
        mock_pay_base_url=base_url,
    )
