"""aiohttp-сервер для приёма HTTP-уведомлений ЮMoney (за nginx, 127.0.0.1:WEB_PORT).

Безопасность: проверяем SHA-1 подпись уведомления секретом из .env.
ЮMoney повторяет уведомление при не-200 ответе, поэтому ВСЕГДА отвечаем 200.
"""
from __future__ import annotations

import logging

from aiohttp import web

import orders
import sheets
import yoomoney_api as ym
from config import config

log = logging.getLogger("neuro_dropbot.web")


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def handle_paid(request: web.Request) -> web.Response:
    return web.Response(
        text=("<h2>Оплата обрабатывается</h2>"
              f"<p>Вернитесь в бота <b>@{config.bot_username}</b> — "
              "ключ придёт в чат автоматически после подтверждения оплаты.</p>"),
        content_type="text/html",
    )


async def handle_webhook(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    try:
        form_data = await request.post()
        form = {str(k): str(v) for k, v in form_data.items()}
    except Exception:  # noqa: BLE001
        return web.json_response({"ok": False, "reason": "bad_form"})

    if not ym.verify_notification(form):
        # --- временная диагностика подписи ---
        import hashlib
        _fields = ("notification_type", "operation_id", "amount", "currency",
                   "datetime", "sender", "codepro")
        _base = "&".join([form.get(k, "") for k in _fields] + [config.ym_secret, form.get("label", "")])
        _exp = hashlib.sha1(_base.encode("utf-8")).hexdigest()
        log.warning("ym SIGFAIL form=%s", dict(form))
        log.warning("ym SIGFAIL expected=%s received=%s", _exp, form.get("sha1_hash", ""))
        # --- конец диагностики ---
        return web.json_response({"ok": False, "reason": "signature"})

    label = form.get("label", "") or form.get("operation_label", "")
    operation_id = form.get("operation_id", "")
    if not label:
        return web.json_response({"ok": False, "reason": "no_label"})

    if form.get("codepro", "").lower() == "true":
        log.warning("ym webhook: codepro=true label=%r — отказ", label)
        return web.json_response({"ok": False, "reason": "codepro"})

    order = await orders.get_order(label)
    if not order:
        log.warning("ym webhook: no order for label=%r", label)
        return web.json_response({"ok": True})
    if order["key_issued"] or order["status"] == "paid":
        return web.json_response({"ok": True, "duplicate": True})

    # сумма зачисления (за вычетом комиссии ЮMoney) — логируем для контроля
    try:
        paid = float(form.get("amount", "0"))
        if paid + 0.01 < order["amount"] * 0.9:  # пришло заметно меньше — подозрительно
            log.warning("ym webhook: underpaid label=%r paid=%s expected=%s",
                        label, paid, order["amount"])
    except ValueError:
        pass

    buyer = str(order["user_id"])
    # Подтверждаем продажу забронированного ключа: помечаем sold в БД и в листе.
    key = await sheets.confirm_sale(label, order["product_id"], buyer)
    await orders.mark_issued(label, key or "", operation_id)
    if key:
        await bot.send_message(
            order["user_id"],
            f"✅ Оплачено! Ваш ключ для <b>{order['product_title']}</b>:\n\n"
            f"<code>{key}</code>\n\nСпасибо за покупку!",
        )
    else:
        await bot.send_message(
            order["user_id"],
            "⚠️ Оплата прошла, но ключ временно не выдался. Напишите в поддержку.",
        )
    for admin in config.admin_ids:
        try:
            await bot.send_message(
                admin, f"💰 Оплачен заказ: {order['product_title']}\n"
                       f"Покупатель: {buyer}\nКлюч выдан: {'да' if key else 'НЕТ — проверь!'}")
        except Exception:  # noqa: BLE001
            pass

    log.info("ym webhook: paid label=%s op=%s key=%s", label, operation_id, bool(key))
    return web.json_response({"ok": True})


def build_app(bot) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app.router.add_post(config.webhook_path, handle_webhook)
    app.router.add_get("/paid", handle_paid)
    app.router.add_get("/health", handle_health)
    return app
