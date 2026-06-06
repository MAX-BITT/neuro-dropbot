"""Слой данных: каталог и выдача ключей из Google Sheets.

Структура таблицы (можно поменять под реальную, когда придёт):
  Лист «Каталог»: id | товар | описание | цена | image_url
  Лист «Ключи»:   товар | ключ | статус | дата | покупатель
                  статус: free / sold

Пока таблица не подключена (нет SHEET_ID / json-ключа) — работает заглушка,
чтобы бот можно было запустить и проверить интерфейс.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass

from config import config

# Блокировка, чтобы один и тот же ключ не выдался двум покупателям одновременно
_key_lock = asyncio.Lock()


@dataclass
class Product:
    id: str
    title: str
    description: str
    price: int  # в рублях, целое
    image_url: str = ""
    stock: int = 0


# ---------- заглушка (без таблицы) ----------
_STUB = [
    Product("win11pro", "Windows 11 Pro", "Лицензионный ключ активации, онлайн.", 599,
            "", 5),
    Product("office2021", "Office 2021 Pro Plus", "Бессрочная лицензия, привязка к аккаунту.",
            899, "", 3),
]


def _client():
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(
        config.google_credentials_file, scopes=scopes
    )
    return gspread.authorize(creds)


def _catalog_sync() -> list[Product]:
    if not config.sheets_enabled:
        return list(_STUB)
    gc = _client()
    sh = gc.open_by_key(config.sheet_id)
    cat = sh.worksheet(config.sheet_catalog).get_all_records()
    keys = sh.worksheet(config.sheet_keys).get_all_records()

    # считаем свободные ключи по каждому товару
    free: dict[str, int] = {}
    for row in keys:
        if str(row.get("статус", "")).strip().lower() in ("free", "свободен", ""):
            free[str(row.get("товар", "")).strip()] = free.get(
                str(row.get("товар", "")).strip(), 0
            ) + 1

    products: list[Product] = []
    for row in cat:
        title = str(row.get("товар", "")).strip()
        if not title:
            continue
        products.append(
            Product(
                id=str(row.get("id") or title),
                title=title,
                description=str(row.get("описание", "")).strip(),
                price=int(float(row.get("цена", 0) or 0)),
                image_url=str(row.get("image_url", "")).strip(),
                stock=free.get(title, 0),
            )
        )
    return products


def _reserve_sync(product_title: str, buyer: str) -> str | None:
    """Находит первый свободный ключ, помечает sold, возвращает его. None — если нет."""
    if not config.sheets_enabled:
        return f"STUB-KEY-{product_title}-{dt.datetime.utcnow():%H%M%S}"
    gc = _client()
    ws = gc.open_by_key(config.sheet_id).worksheet(config.sheet_keys)
    rows = ws.get_all_records()
    header = ws.row_values(1)
    col_status = header.index("статус") + 1
    col_date = header.index("дата") + 1 if "дата" in header else None
    col_buyer = header.index("покупатель") + 1 if "покупатель" in header else None

    for i, row in enumerate(rows, start=2):  # строка 1 — заголовок
        if str(row.get("товар", "")).strip() != product_title:
            continue
        if str(row.get("статус", "")).strip().lower() not in ("free", "свободен", ""):
            continue
        key = str(row.get("ключ", "")).strip()
        ws.update_cell(i, col_status, "sold")
        if col_date:
            ws.update_cell(i, col_date, dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M"))
        if col_buyer:
            ws.update_cell(i, col_buyer, buyer)
        return key
    return None


# ---------- async-обёртки (gspread синхронный) ----------
async def get_catalog() -> list[Product]:
    return await asyncio.to_thread(_catalog_sync)


async def get_product(product_id: str) -> Product | None:
    for p in await get_catalog():
        if p.id == product_id:
            return p
    return None


async def reserve_key(product_title: str, buyer: str) -> str | None:
    async with _key_lock:
        return await asyncio.to_thread(_reserve_sync, product_title, buyer)
