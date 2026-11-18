#!/usr/bin/env bash
# Установка бота наличия Systeme Electric для MAX на сервер Ubuntu/Debian.
# Запуск:  curl -fsSL https://raw.githubusercontent.com/Daniluk-90/systeme-max-bot/main/install.sh | sudo bash
set -euo pipefail

REPO="https://raw.githubusercontent.com/Daniluk-90/systeme-max-bot/main"
DIR=/opt/max-stock-bot

command -v python3 >/dev/null || { apt-get update && apt-get install -y python3; }
mkdir -p "$DIR"
curl -fsSL "$REPO/bot.py" -o "$DIR/bot.py"
curl -fsSL "$REPO/russian_trusted_root_ca.pem" -o "$DIR/russian_trusted_root_ca.pem"

if [ ! -s "$DIR/.env" ] || ! grep -q '^MAX_BOT_TOKEN=.\+' "$DIR/.env"; then
  read -r -p "Вставьте токен бота MAX и нажмите Enter: " TOKEN < /dev/tty
  printf 'MAX_BOT_TOKEN=%s\nSYSTEME_TOKEN=\n' "$TOKEN" > "$DIR/.env"
  chmod 600 "$DIR/.env"
fi

cat > /etc/systemd/system/max-stock-bot.service <<EOF
[Unit]
Description=MAX stock bot (Systeme Electric)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStart=/usr/bin/python3 $DIR/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now max-stock-bot
sleep 3
systemctl --no-pager --lines=5 status max-stock-bot || true
echo
echo "Готово. Лог: journalctl -u max-stock-bot -f"
echo "Обновить бота: запустите эту же команду ещё раз."
