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
