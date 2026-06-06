"""Конфигурация бота: читается из переменных окружения (.env)."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _ids(raw: str) -> list[int]:
    return [int(x) for x in raw.replace(" ", "").split(",") if x]


@dataclass
class Config:
    # --- Telegram ---
    bot_token: str = os.getenv("BOT_TOKEN", "")
    bot_username: str = os.getenv("BOT_USERNAME", "Neuro_dropbot")
    admin_ids: list[int] = field(default_factory=lambda: _ids(os.getenv("ADMIN_IDS", "")))

    # --- ЮMoney (кошелёк + QuickPay + HTTP-уведомления) ---
    ym_wallet: str = os.getenv("YOOMONEY_WALLET", "")            # 16 цифр, 4100...
    ym_secret: str = os.getenv("YOOMONEY_NOTIFICATION_SECRET", "")  # секрет подписи
    currency: str = os.getenv("CURRENCY", "RUB")

    # --- Веб-сервер для вебхука (за nginx) ---
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "")  # https://neurodropbot.duckdns.org
    webhook_path: str = os.getenv("WEBHOOK_PATH", "/yoomoney/webhook")
    web_host: str = os.getenv("WEB_HOST", "127.0.0.1")
    web_port: int = int(os.getenv("WEB_PORT", "8080"))
    _return_url: str = os.getenv("RETURN_URL", "")

    # --- Google Sheets ---
    sheet_id: str = os.getenv("SHEET_ID", "")
    google_credentials_file: str = os.getenv(
        "GOOGLE_CREDENTIALS_FILE", "/opt/neuro-dropbot/service_account.json"
    )
    sheet_catalog: str = os.getenv("SHEET_CATALOG", "Каталог")
    sheet_keys: str = os.getenv("SHEET_KEYS", "Ключи")
    # Листы, которые не являются играми и не парсятся в каталог
    sheet_skip: list[str] = field(
        default_factory=lambda: [
            s.strip() for s in os.getenv("SHEET_SKIP", "Логи,Настройки,Продажи").split(",")
            if s.strip()
        ]
    )
    # Сколько минут держать бронь ключа после нажатия «Купить» до оплаты
    reserve_ttl_min: int = int(os.getenv("RESERVE_TTL_MIN", "15"))
    # Сколько секунд кэшировать прочитанную таблицу (чтобы не дёргать её на каждый клик)
    sheet_cache_sec: int = int(os.getenv("SHEET_CACHE_SEC", "20"))

    # --- Хранилище заказов ---
    db_path: str = os.getenv("DB_PATH", "/opt/neuro-dropbot/data/orders.db")

    # --- Парсер каталога ---
    source_url: str = os.getenv("SOURCE_URL", "https://winkey.info")

    @property
    def return_url(self) -> str:
        if self._return_url:
            return self._return_url
        if self.public_base_url:
            return f"{self.public_base_url.rstrip('/')}/paid"
        return f"https://t.me/{self.bot_username}"

    @property
    def webhook_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}{self.webhook_path}"

    @property
    def ym_enabled(self) -> bool:
        return bool(self.ym_wallet and self.ym_secret)

    @property
    def sheets_readable(self) -> bool:
        """Можно читать каталог: либо по публичной ссылке, либо через креды."""
        return bool(self.sheet_id)

    @property
    def sheets_writable(self) -> bool:
        """Можно писать в таблицу (помечать проданные ячейки) — нужен сервисный аккаунт."""
        return bool(self.sheet_id and os.path.exists(self.google_credentials_file))

    # Обратная совместимость
    @property
    def sheets_enabled(self) -> bool:
        return self.sheets_readable

    @property
    def sheet_export_xlsx_url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/export?format=xlsx"


config = Config()
