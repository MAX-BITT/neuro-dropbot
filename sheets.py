"""Слой данных: каталог и выдача ключей из Google Sheets.

Новая структура таблицы:
  Каждый лист = игра (например, ROBLOX)
  Заголовки: количество_валюты цена (например, "100 100", "296 200")
  Строки: ключи
  
  Добавляем колонки справа: Статус | Покупатель | Дата
  Статус: free / sold
"""
from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass

from config import config

_key_lock = asyncio.Lock()


@dataclass
class Product:
    id: str
    title: str
    description: str
    price: int
    image_url: str = ""
    stock: int = 0


def _client():
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(
        config.google_credentials_file, scopes=scopes
    )
    return gspread.authorize(creds)


def _parse_header(header: str) -> tuple[int, int] | None:
    """Парсит заголовок типа 100 100 -> (количество=100, цена=100)"""
    parts = header.strip().split()
    if len(parts) >= 2:
        try:
            qty = int(parts[0])
            price = int(parts[1])
            return qty, price
        except ValueError:
            pass
    return None


def _ensure_status_columns(ws, header_row):
    """Добавляет колонки Статус, Покупатель, Дата если их нет"""
    required = ["Статус", "Покупатель", "Дата"]
    existing = [h.strip() for h in header_row]
    cols_to_add = []
    for col in required:
        if col not in existing:
            cols_to_add.append(col)
    if cols_to_add:
        next_col = len(existing) + 1
        for i, col in enumerate(cols_to_add):
            ws.update_cell(1, next_col + i, col)
        return ws.row_values(1)
    return header_row


def _catalog_sync() -> list[Product]:
    if not config.sheets_enabled:
        return []
    
    gc = _client()
    sh = gc.open_by_key(config.sheet_id)
    
    products = []
    for ws in sh.worksheets():
        sheet_name = ws.title
        if sheet_name in ("Логи", "Настройки"):
            continue
            
        all_values = ws.get_all_values()
        if not all_values:
            continue
            
        header_row = all_values[0]
        header_row = _ensure_status_columns(ws, header_row)
        
        # Индексы колонок
        status_idx = None
        buyer_idx = None
        for i, h in enumerate(header_row):
            h_stripped = h.strip()
            if h_stripped == "Статус":
                status_idx = i
            elif h_stripped == "Покупатель":
                buyer_idx = i
        
        # Считаем свободные ключи по каждому заголовку
        for col_idx, header in enumerate(header_row):
            parsed = _parse_header(header)
            if not parsed:
                continue
                
            qty, price = parsed
            free_count = 0
            
            for row_idx in range(1, len(all_values)):
                row = all_values[row_idx]
                if col_idx >= len(row):
                    continue
                key_val = row[col_idx].strip()
                if not key_val:
                    continue
                    
                # Проверяем статус если есть колонка
                if status_idx is not None and status_idx < len(row):
                    status = row[status_idx].strip().lower()
                    if status and status not in ("free", "свободен", ""):
                        continue
                
                free_count += 1
            
            if free_count > 0:
                product_id = f"{sheet_name}_{qty}"
                products.append(Product(
                    id=product_id,
                    title=f"{sheet_name} {qty} единиц",
                    description=f"Ключ для {sheet_name} — {qty} единиц валюты",
                    price=price,
                    image_url="",
                    stock=free_count,
                ))
    
    return products


def _reserve_sync(product_title: str, buyer: str) -> str | None:
    """Находит первый свободный ключ, помечает sold, возвращает его."""
    if not config.sheets_enabled:
        return None
        
    gc = _client()
    sh = gc.open_by_key(config.sheet_id)
    
    # Парсим product_title: "ROBLOX 100 единиц" -> sheet_name="ROBLOX", qty=100
    parts = product_title.split()
    if len(parts) < 2:
        return None
    
    sheet_name = parts[0]
    try:
        target_qty = int(parts[1])
    except ValueError:
        return None
    
    ws = sh.worksheet(sheet_name)
    all_values = ws.get_all_values()
    if not all_values:
        return None
        
    header_row = all_values[0]
    header_row = _ensure_status_columns(ws, header_row)
    
    # Находим колонку с нужным количеством
    target_col = None
    for i, h in enumerate(header_row):
        parsed = _parse_header(h)
        if parsed and parsed[0] == target_qty:
            target_col = i
            break
    
    if target_col is None:
        return None
    
    # Индексы колонок статуса
    status_idx = None
    buyer_idx = None
    date_idx = None
    for i, h in enumerate(header_row):
        h_stripped = h.strip()
        if h_stripped == "Статус":
            status_idx = i
        elif h_stripped == "Покупатель":
            buyer_idx = i
        elif h_stripped == "Дата":
            date_idx = i
    
    # Ищем первый свободный ключ
    for row_idx in range(1, len(all_values)):
        row = all_values[row_idx]
        if target_col >= len(row):
            continue
        key_val = row[target_col].strip()
        if not key_val:
            continue
            
        # Проверяем статус
        if status_idx is not None and status_idx < len(row):
            status = row[status_idx].strip().lower()
            if status and status not in ("free", "свободен", ""):
                continue
        
        # Помечаем как sold
        import gspread
        now_str = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        updates = []
        if status_idx is not None:
            updates.append(gspread.Cell(row_idx + 1, status_idx + 1, "sold"))
        if buyer_idx is not None:
            updates.append(gspread.Cell(row_idx + 1, buyer_idx + 1, buyer))
        if date_idx is not None:
            updates.append(gspread.Cell(row_idx + 1, date_idx + 1, now_str))
        
        if updates:
            ws.update_cells(updates)
        
        return key_val
    
    return None


# --- async-обёртки ---
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
