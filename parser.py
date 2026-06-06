"""Парсер каталога с сайта-источника.

Многие такие магазины на Shopify — у них есть /products.json со всем каталогом
(название, описание, цена, картинки). Сначала пробуем его, потом — обычный HTML.

Запуск отдельно:  python parser.py            -> печатает найденные товары
                  python parser.py --csv      -> CSV в stdout (для вставки в таблицу)
"""
from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, asdict

import requests
from bs4 import BeautifulSoup

from config import config

HEADERS = {"User-Agent": "Mozilla/5.0 (catalog-sync)"}


@dataclass
class SourceItem:
    title: str
    description: str
    price: float
    image_url: str
    url: str


def _from_shopify(base: str) -> list[SourceItem]:
    items: list[SourceItem] = []
    page = 1
    while True:
        r = requests.get(f"{base}/products.json", params={"limit": 250, "page": page},
                         headers=HEADERS, timeout=20)
        if r.status_code != 200:
            break
        data = r.json().get("products", [])
        if not data:
            break
        for p in data:
            variant = (p.get("variants") or [{}])[0]
            img = (p.get("images") or [{}])
            soup = BeautifulSoup(p.get("body_html") or "", "html.parser")
            items.append(SourceItem(
                title=p.get("title", "").strip(),
                description=soup.get_text(" ", strip=True)[:500],
                price=float(variant.get("price") or 0),
                image_url=(img[0].get("src") if img and img[0] else "") or "",
                url=f"{base}/products/{p.get('handle','')}",
            ))
        page += 1
    return items


def _from_html(base: str) -> list[SourceItem]:
    """Грубый фолбэк, если /products.json недоступен."""
    r = requests.get(base, headers=HEADERS, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")
    items: list[SourceItem] = []
    for card in soup.select("[class*=product]"):
        title = card.find(["h2", "h3", "a"])
        if not title:
            continue
        img = card.find("img")
        items.append(SourceItem(
            title=title.get_text(strip=True),
            description="",
            price=0.0,
            image_url=(img.get("src") if img else "") or "",
            url=base,
        ))
    return items


def fetch_catalog(base: str | None = None) -> list[SourceItem]:
    base = (base or config.source_url).rstrip("/")
    try:
        items = _from_shopify(base)
        if items:
            return items
    except Exception as e:  # noqa: BLE001
        print(f"shopify json failed: {e}", file=sys.stderr)
    return _from_html(base)


if __name__ == "__main__":
    rows = fetch_catalog()
    if "--csv" in sys.argv:
        w = csv.DictWriter(sys.stdout, fieldnames=["title", "price", "image_url",
                                                   "description", "url"])
        w.writeheader()
        for it in rows:
            w.writerow(asdict(it))
    else:
        for it in rows:
            print(f"- {it.title} | {it.price} | {it.image_url}")
        print(f"\nВсего товаров: {len(rows)}", file=sys.stderr)
