# Деплой neuro-dropbot (ЮMoney QuickPay + вебхук)

Сервер: `root@213.171.8.240` (Ubuntu 24.04), код в `/opt/neuro-dropbot`,
заход по ключу `D:\OpenClaw\.deploy-keys\neuro_dropbot_ed25519`.

## 1. Домен (duckdns)
`neurodropbot.duckdns.org` → `213.171.8.240`. В `.env`:
`PUBLIC_BASE_URL=https://neurodropbot.duckdns.org`.

## 2. nginx + HTTPS
```bash
apt-get install -y nginx certbot python3-certbot-nginx
sed -i 's/ДОМЕН/neurodropbot.duckdns.org/' /opt/neuro-dropbot/deploy/nginx/neuro-dropbot.conf
cp /opt/neuro-dropbot/deploy/nginx/neuro-dropbot.conf /etc/nginx/sites-available/neuro-dropbot.conf
ln -sf /etc/nginx/sites-available/neuro-dropbot.conf /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot --nginx -d neurodropbot.duckdns.org --non-interactive --agree-tos -m kriptnik10@gmail.com --redirect
```

## 3. .env
```bash
cd /opt/neuro-dropbot
cp .env.example .env && nano .env
#   BOT_TOKEN, YOOMONEY_WALLET, YOOMONEY_NOTIFICATION_SECRET,
#   PUBLIC_BASE_URL, ADMIN_IDS
chmod 600 .env
```

## 4. Запуск
```bash
.venv/bin/pip install -r requirements.txt
cp neuro-dropbot.service /etc/systemd/system/ && systemctl daemon-reload
systemctl enable --now neuro-dropbot
journalctl -u neuro-dropbot -f
```

## 5. В кабинете ЮMoney (делает заказчик)
yoomoney.ru/transfer/myservices/http-notification →
URL: `https://neurodropbot.duckdns.org/yoomoney/webhook` →
галка «Отправлять HTTP-уведомления» → Готово.
(Секрет оттуда — в `YOOMONEY_NOTIFICATION_SECRET`.)

## Проверка
- `curl https://neurodropbot.duckdns.org/health` → `{"ok": true}`
- Бот: Каталог → товар → Купить → «Оплатить» → оплата на кошелёк
  → ключ приходит в чат. В логах `ym webhook: paid label=…`.
