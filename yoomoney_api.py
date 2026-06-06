"""Интеграция с ЮMoney (кошелёк) — паттерн из max-payment-bot.

Две примитивы:
  - build_quickpay_url(): ссылка на yoomoney.ru/quickpay/confirm для оплаты;
  - verify_notification(): проверка SHA-1 подписи HTTP-уведомления.

OAuth и регистрация магазина не нужны: QuickPay-форма позволяет принимать
деньги на кошелёк, а HTTP-уведомления дают подписанный колбэк о зачислении.
"""
from __future__ import annotations

import hashlib
import hmac
from urllib.parse import quote, urlencode

from config import config

# Классические поля (старый формат с sha1_hash) — для обратной совместимости
_NOTIFICATION_FIELDS: tuple[str, ...] = (
    "notification_type",
    "operation_id",
    "amount",
    "currency",
    "datetime",
    "sender",
    "codepro",
)


def build_quickpay_url(
    label: str,
    amount_rub: float,
    target_text: str,
    success_url: str | None = None,
) -> str:
    """Ссылка на оплату. `label` возвращается в уведомлении — это наш id заказа."""
    params = {
        "receiver": config.ym_wallet,
        "quickpay-form": "shop",
        "targets": target_text[:150],
        "paymentType": "AC",  # оплата картой
        "sum": f"{amount_rub:.2f}",
        "label": label,
    }
    if success_url:
        params["successURL"] = success_url
    return "https://yoomoney.ru/quickpay/confirm?" + urlencode(params)


def verify_notification(form: dict[str, str]) -> bool:
    """Проверка подписи уведомления ЮMoney.

    Новый формат (поле `sign`): HMAC-SHA256(secret) по строке всех полей кроме
    `sign`, отсортированных по алфавиту, в виде key=URLencode(value) через '&'.
    Старый формат (поле `sha1_hash`): SHA-1 по фиксированному набору полей.
    """
    secret = config.ym_secret
    if not secret:
        return False

    sign = form.get("sign")
    if sign:
        params = {k: v for k, v in form.items() if k != "sign"}
        msg = "&".join(f"{k}={quote(str(params[k]), safe='')}" for k in sorted(params))
        expected = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"),
                            hashlib.sha256).hexdigest()
        return _consteq(expected.lower(), sign.lower())

    received = form.get("sha1_hash", "")
    if not received:
        return False
    parts = [form.get(k, "") for k in _NOTIFICATION_FIELDS]
    parts.append(secret)
    parts.append(form.get("label", ""))
    expected = hashlib.sha1("&".join(parts).encode("utf-8")).hexdigest()
    return _consteq(expected, received.lower())


def _consteq(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b, strict=True):
        diff |= ord(x) ^ ord(y)
    return diff == 0
