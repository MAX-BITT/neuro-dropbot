# Neuro_dropbot — Telegram-магазин цифровых ключей

Витрина в Telegram (`@Neuro_dropbot`). Каталог и ключи — в Google Sheets,
оплата — ЮKassa через Telegram Payments. Каталог можно наполнять парсером с сайта.

## Состав
- `bot.py` — бот (aiogram 3): меню, каталог, карточка товара, оплата, выдача ключа.
- `sheets.py` — чтение каталога и выдача ключей из Google Sheets (с блокировкой от двойной выдачи).
- `parser.py` — парсер каталога с сайта-источника (Shopify `/products.json` + HTML-фолбэк).
- `config.py` — настройки из `.env`.
- `neuro-dropbot.service` — systemd-юнит.

## Структура таблицы (черновик, поправим под реальную)
**Лист «Каталог»:** `id | товар | описание | цена | image_url`
**Лист «Ключи»:** `товар | ключ | статус | дата | покупатель`  (статус: `free`/`sold`)

## Что нужно подключить
1. **BOT_TOKEN** — перевыпустить в @BotFather (старый засветился).
2. **PROVIDER_TOKEN** — @BotFather → Payments → ЮKassa.
3. **Google Sheets** — сервисный аккаунт (JSON-ключ на сервер как `service_account.json`),
   включить Sheets API, расшарить таблицу на email сервисного аккаунта, указать `SHEET_ID`.

## Деплой
```bash
cd /opt/neuro-dropbot
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # заполнить токены
systemctl enable --now neuro-dropbot
journalctl -u neuro-dropbot -f
```

## Наполнить каталог из сайта
```bash
.venv/bin/python parser.py --csv > catalog.csv   # вставить в лист «Каталог»
```

> ⚠️ Закупку ключей надёжнее вести у оптовика с официальным API, а не авто-скупкой
> с розничного сайта (брак/чарджбеки). Бот при этом не меняется — меняется источник ключей.
