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


# ---------- пагинация ----------
GAMES_PER_PAGE = 8     # игр на странице
TIERS_PER_PAGE = 8     # номиналов на странице
KEYS_PER_PAGE = 5      # «моих ключей» на странице


def _page_bounds(total: int, page: int, per_page: int) -> tuple[int, int, int]:
    """Возвращает (page, total_pages, offset) с зажатием page в допустимый диапазон."""
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    return page, total_pages, page * per_page


def _nav_row(callbacks: list[tuple[str, str]]) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=t, callback_data=d) for t, d in callbacks]


# ---------- клавиатуры ----------
def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Каталог", callback_data="g:0")],
        [InlineKeyboardButton(text="🔑 Мои ключи", callback_data="mk:0")],
        [InlineKeyboardButton(text="❓ Поддержка", callback_data="support")],
    ])


async def _games() -> list[tuple[str, int, int]]:
    """[(game, суммарный остаток, число номиналов)] по тем, у кого есть наличие."""
    agg: dict[str, list[int]] = {}
    for p in await sheets.get_catalog():
        g = agg.setdefault(p.game, [0, 0])
        g[0] += p.stock
        g[1] += 1
    return [(name, s, t) for name, (s, t) in sorted(agg.items())]


async def games_view(page: int) -> tuple[str, InlineKeyboardMarkup]:
    games = await _games()
    page, total_pages, off = _page_bounds(len(games), page, GAMES_PER_PAGE)
    rows = []
    for name, stock, tiers in games[off:off + GAMES_PER_PAGE]:
        rows.append([InlineKeyboardButton(
            text=f"🎮 {name} · {stock} шт.", callback_data=f"t:0:{name}")])
    nav = []
    if page > 0:
        nav.append(("◀️", f"g:{page - 1}"))
    if total_pages > 1:
        nav.append((f"{page + 1}/{total_pages}", "noop"))
    if page < total_pages - 1:
        nav.append(("▶️", f"g:{page + 1}"))
    if nav:
        rows.append(_nav_row(nav))
    rows.append([InlineKeyboardButton(text="⬅️ Меню", callback_data="menu")])
    title = "🛒 Выберите игру:" if games else "🛒 Каталог пока пуст — загляните позже."
    return title, InlineKeyboardMarkup(inline_keyboard=rows)


async def tiers_view(game: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    tiers = [p for p in await sheets.get_catalog() if p.game == game]
    tiers.sort(key=lambda p: p.qty)
    page, total_pages, off = _page_bounds(len(tiers), page, TIERS_PER_PAGE)
    rows = []
    for p in tiers[off:off + TIERS_PER_PAGE]:
        rows.append([InlineKeyboardButton(
            text=f"{p.qty} — {p.price}₽ · {p.stock} шт.", callback_data=f"p:{p.id}")])
    nav = []
    if page > 0:
        nav.append(("◀️", f"t:{page - 1}:{game}"))
    if total_pages > 1:
        nav.append((f"{page + 1}/{total_pages}", "noop"))
    if page < total_pages - 1:
        nav.append(("▶️", f"t:{page + 1}:{game}"))
    if nav:
        rows.append(_nav_row(nav))
    rows.append([InlineKeyboardButton(text="⬅️ Игры", callback_data="g:0")])
    title = (f"🎮 <b>{game}</b> — выберите номинал:"
             if tiers else f"🎮 <b>{game}</b>: сейчас нет в наличии.")
    return title, InlineKeyboardMarkup(inline_keyboard=rows)


async def mykeys_view(user_id: int, page: int) -> tuple[str, InlineKeyboardMarkup]:
    total = await orders.user_keys_count(user_id)
    page, total_pages, off = _page_bounds(total, page, KEYS_PER_PAGE)
    items = await orders.user_keys(user_id, KEYS_PER_PAGE, off)
    if not items:
        text = "🔑 У вас пока нет купленных ключей."
    else:
        lines = ["🔑 <b>Ваши ключи:</b>\n"]
        for it in items:
            lines.append(
                f"• <b>{it['product_title']}</b> — {it['amount']}₽\n"
                f"<code>{it['key_issued']}</code>\n"
                f"<i>{it['created_at']}</i>\n")
        text = "\n".join(lines)
    rows = []
    nav = []
    if page > 0:
        nav.append(("◀️", f"mk:{page - 1}"))
    if total_pages > 1:
        nav.append((f"{page + 1}/{total_pages}", "noop"))
    if page < total_pages - 1:
        nav.append(("▶️", f"mk:{page + 1}"))
    if nav:
        rows.append(_nav_row(nav))
    rows.append([InlineKeyboardButton(text="⬅️ Меню", callback_data="menu")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def product_kb(product_id: str, game: str, in_stock: bool) -> InlineKeyboardMarkup:
    rows = []
    if in_stock:
        rows.append([InlineKeyboardButton(text="💳 Купить", callback_data=f"buy:{product_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"t:0:{game}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- меню/каталог ----------
@dp.message(CommandStart())
async def start(m: Message):
    await m.answer(
        "👋 Добро пожаловать в магазин цифровых ключей!\n\n"
        "Выбирайте игру в каталоге — оплата картой через ЮMoney, "
        "ключ приходит сразу после оплаты.",
        reply_markup=main_menu(),
    )


@dp.callback_query(F.data == "menu")
async def cb_menu(c: CallbackQuery):
    await c.message.edit_text("Главное меню:", reply_markup=main_menu())
    await c.answer()


@dp.callback_query(F.data == "noop")
async def cb_noop(c: CallbackQuery):
    await c.answer()


@dp.callback_query(F.data == "support")
async def cb_support(c: CallbackQuery):
    await c.answer()
    await c.message.answer("По вопросам пишите: @your_support_username")


@dp.callback_query(F.data.startswith("g:"))
async def cb_games(c: CallbackQuery):
    page = int(c.data.split(":", 1)[1] or 0)
    text, kb = await games_view(page)
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()


@dp.callback_query(F.data.startswith("t:"))
async def cb_tiers(c: CallbackQuery):
    _, page_s, game = c.data.split(":", 2)
    text, kb = await tiers_view(game, int(page_s or 0))
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()


@dp.callback_query(F.data.startswith("mk:"))
async def cb_mykeys(c: CallbackQuery):
    page = int(c.data.split(":", 1)[1] or 0)
    text, kb = await mykeys_view(c.from_user.id, page)
    await c.message.edit_text(text, reply_markup=kb)
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
    kb = product_kb(p.id, p.game, p.stock > 0)
    if p.image_url:
        await c.message.answer_photo(p.image_url, caption=text, reply_markup=kb)
    else:
        await c.message.edit_text(text, reply_markup=kb)
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
    log.info("Bot starting. ym_enabled=%s sheets_read=%s sheets_write=%s webhook_url=%s",
             config.ym_enabled, config.sheets_readable, config.sheets_writable,
             config.webhook_url or "(no PUBLIC_BASE_URL)")

    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
