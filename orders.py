"""Лёгкое хранилище заказов (SQLite) — связка label -> покупатель + товар.

`label` уходит в ЮMoney QuickPay и возвращается в HTTP-уведомлении, поэтому по
нему вебхук находит, кому и какой ключ выдать. Идемпотентность — по operation_id.
sqlite3 синхронный — оборачиваем в asyncio.to_thread.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3

from config import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    label        TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL,
    product_id   TEXT NOT NULL,
    product_title TEXT NOT NULL,
    amount       INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    key_issued   TEXT NOT NULL DEFAULT '',
    operation_id TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Слой переопределения над Google Sheets: храним только ЗАНЯТЫЕ ключи
-- (reserved/sold). Лист = что вообще существует; эта таблица = что уже занято.
-- Так остаток считается корректно, даже если оператор правит таблицу руками,
-- и при гонке двух покупателей PRIMARY KEY (game,key_text) не даст забронировать
-- один ключ дважды.
CREATE TABLE IF NOT EXISTS inventory_keys (
    game           TEXT NOT NULL,
    key_text       TEXT NOT NULL,
    qty            INTEGER NOT NULL,
    price          INTEGER NOT NULL,
    row            INTEGER NOT NULL,   -- координаты ячейки в листе (1-based)
    col            INTEGER NOT NULL,   -- для пометки ячейки после продажи
    status         TEXT NOT NULL,      -- reserved / sold
    buyer          TEXT NOT NULL DEFAULT '',
    order_label    TEXT NOT NULL DEFAULT '',
    reserved_until TEXT NOT NULL DEFAULT '',  -- UTC 'YYYY-MM-DD HH:MM:SS'
    sold_at        TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (game, key_text)
);
CREATE INDEX IF NOT EXISTS idx_inv_label ON inventory_keys(order_label);
"""


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.db_path), exist_ok=True)
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _init() -> None:
    with _connect() as c:
        c.executescript(_SCHEMA)


def _create(label: str, user_id: int, product_id: str, title: str, amount: int) -> None:
    with _connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO orders(label,user_id,product_id,product_title,amount) "
            "VALUES (?,?,?,?,?)",
            (label, user_id, product_id, title, amount),
        )


def _get(label: str) -> dict | None:
    with _connect() as c:
        row = c.execute("SELECT * FROM orders WHERE label=?", (label,)).fetchone()
        return dict(row) if row else None


def _mark_issued(label: str, key: str, operation_id: str) -> None:
    with _connect() as c:
        c.execute(
            "UPDATE orders SET key_issued=?, operation_id=?, status='paid' WHERE label=?",
            (key, operation_id, label),
        )


def _set_status(label: str, status: str) -> None:
    with _connect() as c:
        c.execute("UPDATE orders SET status=? WHERE label=?", (status, label))


def _user_keys(user_id: int, limit: int, offset: int) -> list[dict]:
    """Выданные ключи пользователя (для раздела «Мои ключи»), новые сверху."""
    with _connect() as c:
        rows = c.execute(
            "SELECT product_title, key_issued, amount, created_at FROM orders "
            "WHERE user_id=? AND key_issued <> '' "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


def _user_keys_count(user_id: int) -> int:
    with _connect() as c:
        r = c.execute(
            "SELECT COUNT(*) AS n FROM orders WHERE user_id=? AND key_issued <> ''",
            (user_id,),
        ).fetchone()
        return int(r["n"])


# ---------- инвентарь ключей (бронь / продажа) ----------
def _inv_expire() -> int:
    """Снимает просроченные брони — ключ снова считается свободным. Возвращает число."""
    with _connect() as c:
        cur = c.execute(
            "DELETE FROM inventory_keys "
            "WHERE status='reserved' AND reserved_until <> '' "
            "AND reserved_until < datetime('now')"
        )
        return cur.rowcount


def _inv_blocked() -> set[tuple[str, str]]:
    """(game, key_text) всех занятых ключей: проданы или активно забронированы.

    Вызывать после _inv_expire(), чтобы просроченные брони уже были сняты.
    """
    with _connect() as c:
        rows = c.execute("SELECT game, key_text FROM inventory_keys").fetchall()
    return {(r["game"], r["key_text"]) for r in rows}


def _inv_reserve(game: str, key_text: str, qty: int, price: int,
                 row: int, col: int, buyer: str, order_label: str,
                 reserved_until: str) -> bool:
    """Бронирует конкретный ключ. False, если он уже занят (гонка/дубль)."""
    with _connect() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO inventory_keys"
            "(game,key_text,qty,price,row,col,status,buyer,order_label,reserved_until)"
            " VALUES (?,?,?,?,?,?, 'reserved', ?,?,?)",
            (game, key_text, qty, price, row, col, buyer, order_label, reserved_until),
        )
        return cur.rowcount == 1


def _inv_get_by_label(order_label: str) -> dict | None:
    with _connect() as c:
        r = c.execute(
            "SELECT * FROM inventory_keys WHERE order_label=? LIMIT 1", (order_label,)
        ).fetchone()
        return dict(r) if r else None


def _inv_confirm(order_label: str) -> dict | None:
    """Помечает забронированный по заказу ключ как sold. Возвращает запись или None."""
    with _connect() as c:
        r = c.execute(
            "SELECT * FROM inventory_keys WHERE order_label=? AND status='reserved'",
            (order_label,),
        ).fetchone()
        if not r:
            return None
        c.execute(
            "UPDATE inventory_keys SET status='sold', reserved_until='', "
            "sold_at=datetime('now') WHERE game=? AND key_text=?",
            (r["game"], r["key_text"]),
        )
        return dict(r)


def _inv_mark_sold_direct(game: str, key_text: str, qty: int, price: int,
                          row: int, col: int, buyer: str, order_label: str) -> bool:
    """Фолбэк: продать ключ напрямую (если бронь истекла к моменту оплаты)."""
    with _connect() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO inventory_keys"
            "(game,key_text,qty,price,row,col,status,buyer,order_label,sold_at)"
            " VALUES (?,?,?,?,?,?, 'sold', ?,?, datetime('now'))",
            (game, key_text, qty, price, row, col, buyer, order_label),
        )
        return cur.rowcount == 1


def _inv_release(order_label: str) -> None:
    """Снимает бронь по заказу (например, отмена)."""
    with _connect() as c:
        c.execute(
            "DELETE FROM inventory_keys WHERE order_label=? AND status='reserved'",
            (order_label,),
        )


# --- async-обёртки ---
async def init() -> None:
    await asyncio.to_thread(_init)


async def create_order(label: str, user_id: int, product_id: str, title: str, amount: int):
    await asyncio.to_thread(_create, label, user_id, product_id, title, amount)


async def get_order(label: str) -> dict | None:
    return await asyncio.to_thread(_get, label)


async def mark_issued(label: str, key: str, operation_id: str):
    await asyncio.to_thread(_mark_issued, label, key, operation_id)


async def set_status(label: str, status: str):
    await asyncio.to_thread(_set_status, label, status)


async def user_keys(user_id: int, limit: int, offset: int) -> list[dict]:
    return await asyncio.to_thread(_user_keys, user_id, limit, offset)


async def user_keys_count(user_id: int) -> int:
    return await asyncio.to_thread(_user_keys_count, user_id)


# --- инвентарь: async-обёртки ---
async def inv_expire() -> int:
    return await asyncio.to_thread(_inv_expire)


async def inv_blocked() -> set[tuple[str, str]]:
    return await asyncio.to_thread(_inv_blocked)


async def inv_reserve(game, key_text, qty, price, row, col, buyer,
                      order_label, reserved_until) -> bool:
    return await asyncio.to_thread(
        _inv_reserve, game, key_text, qty, price, row, col,
        buyer, order_label, reserved_until,
    )


async def inv_get_by_label(order_label: str) -> dict | None:
    return await asyncio.to_thread(_inv_get_by_label, order_label)


async def inv_confirm(order_label: str) -> dict | None:
    return await asyncio.to_thread(_inv_confirm, order_label)


async def inv_mark_sold_direct(game, key_text, qty, price, row, col,
                               buyer, order_label) -> bool:
    return await asyncio.to_thread(
        _inv_mark_sold_direct, game, key_text, qty, price, row, col,
        buyer, order_label,
    )


async def inv_release(order_label: str) -> None:
    await asyncio.to_thread(_inv_release, order_label)
