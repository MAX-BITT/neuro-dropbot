"""Neuro_dropbot — Telegram-магазин цифровых ключей.

Каталог и ключи — в Google Sheets (sheets.py).
Оплата — ЮMoney (QuickPay): бот даёт ссылку на оплату на кошелёк, а ключ
выдаётся автоматически после HTTP-уведомления о зачислении (webapp.py).
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiohttp import web

import orders
import sheets
import yoomoney_api as ym
from config import config
from webapp import build_app

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("neuro_dropbot")

dp = Dispatcher()


# ---------- клавиатуры ----------
def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Каталог", callback_data="catalog")],
        [InlineKeyboardButton(text="❓ Поддержка", callback_data="support")],
    ])


async def catalog_kb() -> InlineKeyboardMarkup:
    rows = []
    for p in await sheets.get_catalog():
        mark = "" if p.stock > 0 else " (нет в наличии)"
        rows.append([InlineKeyboardButton(
            text=f"{p.title} — {p.price}₽{mark}", callback_data=f"p:{p.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Меню", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def product_kb(product_id: str, in_stock: bool) -> InlineKeyboardMarkup:
    rows = []
    if in_stock:
        rows.append([InlineKeyboardButton(text="💳 Купить", callback_data=f"buy:{product_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ В каталог", callback_data="catalog")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- меню/каталог ----------
@dp.message(CommandStart())
async def start(m: Message):
    await m.answer(
        "👋 Добро пожаловать в магазин цифровых ключей!\n\n"
        "Выбирай товар в каталоге — оплата картой через ЮMoney, "
        "ключ приходит сразу после оплаты.",
        reply_markup=main_menu(),
    )


@dp.callback_query(F.data == "menu")
async def cb_menu(c: CallbackQuery):
    await c.message.edit_text("Главное меню:", reply_markup=main_menu())
    await c.answer()


@dp.callback_query(F.data == "support")
async def cb_support(c: CallbackQuery):
    await c.answer()
    await c.message.answer("По вопросам пишите: @your_support_username")


@dp.callback_query(F.data == "catalog")
async def cb_catalog(c: CallbackQuery):
    await c.message.edit_text("Каталог товаров:", reply_markup=await catalog_kb())
    await c.answer()


@dp.callback_query(F.data.startswith("p:"))
async def cb_product(c: CallbackQuery):
    pid = c.data.split(":", 1)[1]
    p = await sheets.get_product(pid)
    if not p:
        await c.answer("Товар не найден", show_alert=True)
        return
    text = (f"<b>{p.title}</b>\n\n{p.description}\n\n"
            f"Цена: <b>{p.price}₽</b>\nВ наличии: {p.stock} шт.")
    kb = product_kb(p.id, p.stock > 0)
    if p.image_url:
        await c.message.answer_photo(p.image_url, caption=text, reply_markup=kb)
    else:
        await c.message.answer(text, reply_markup=kb)
    await c.answer()


# ---------- покупка (ЮMoney QuickPay) ----------
@dp.callback_query(F.data.startswith("buy:"))
async def cb_buy(c: CallbackQuery, bot: Bot):
    pid = c.data.split(":", 1)[1]
    p = await sheets.get_product(pid)
    if not p or p.stock <= 0:
        await c.answer("Товар закончился", show_alert=True)
        return
    if not config.ym_enabled:
        await c.answer("Оплата ещё не настроена (нет кошелька ЮMoney)", show_alert=True)
        return
    await c.answer()

    label = f"{c.from_user.id}-{p.id}-{uuid.uuid4().hex[:8]}"
    who = f"@{c.from_user.username}" if c.from_user.username else str(c.from_user.id)

    # Бронируем ключ ДО оплаты, чтобы при одновременных покупках один и тот же
    # ключ не ушёл двум людям. Бронь держится config.reserve_ttl_min минут.
    key = await sheets.reserve_for_order(p.id, label, who)
    if not key:
        await bot.send_message(
            c.from_user.id,
            "😔 Похоже, этот номинал только что закончился. "
            "Загляните в каталог — возможно, есть другие.",
        )
        return

    pay_url = ym.build_quickpay_url(
        label=label,
        amount_rub=float(p.price),
        target_text=f"{p.title} — заказ {who}",
        success_url=config.return_url,
    )
    await orders.create_order(label, c.from_user.id, p.id, p.title, p.price)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=pay_url)]])
    await bot.send_message(
        c.from_user.id,
        f"🧾 Заказ: <b>{p.title}</b> — {p.price}₽\n\n"
        f"Ключ забронирован за вами на {config.reserve_ttl_min} мин. "
        "Нажмите «Оплатить» — после оплаты ключ придёт сюда автоматически.",
        reply_markup=kb,
    )


# ---------- запуск ----------
async def main():
    if not config.bot_token or config.bot_token.startswith("000000"):
        raise SystemExit("BOT_TOKEN не задан в .env")

    await orders.init()
    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    runner = web.AppRunner(build_app(bot))
    await runner.setup()
    site = web.TCPSite(runner, config.web_host, config.web_port)
    await site.start()
    log.info("Web server on http://%s:%s (webhook %s)",
             config.web_host, config.web_port, config.webhook_path)
    log.info("Bot starting. ym_enabled=%s sheets_enabled=%s webhook_url=%s",
             config.ym_enabled, config.sheets_enabled,
             config.webhook_url or "(no PUBLIC_BASE_URL)")

    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
