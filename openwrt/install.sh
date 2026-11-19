#!/bin/sh
# Установка бота MAX на роутер OpenWrt
# Установка и обновление одной командой (на роутере, по ssh):
#   wget -O /tmp/i.sh https://raw.githubusercontent.com/Daniluk-90/systeme-max-bot/main/openwrt/install.sh && sh /tmp/i.sh

DIR=/root/max-bot
SRC=$(cd "$(dirname "$0")" 2>/dev/null && pwd)

REPO=https://raw.githubusercontent.com/Daniluk-90/systeme-max-bot/main

echo "== 1/4 Файлы бота -> $DIR"
mkdir -p "$DIR"
if [ -f "$SRC/bot.py" ] && [ "$SRC" != "$DIR" ]; then
    cp "$SRC/bot.py" "$SRC/russian_trusted_root_ca.pem" "$DIR/"
    echo "   скопированы из $SRC"
else
    for f in bot.py russian_trusted_root_ca.pem; do
        wget -q -O "$DIR/$f.new" "$REPO/$f" && mv "$DIR/$f.new" "$DIR/$f" \
            || { rm -f "$DIR/$f.new"; echo "!! Не удалось скачать $f с GitHub"; exit 1; }
    done
    echo "   скачаны с GitHub"
fi

echo "== 2/4 Устанавливаю Python (несколько минут)"
if command -v apk >/dev/null 2>&1; then
    apk update; INST="apk add"
else
    opkg update; INST="opkg install"
fi
TEST="import json,logging,re,ssl,html,urllib.request,urllib.parse,xml.etree.ElementTree"
if ! python3 -c "$TEST" 2>/dev/null; then
    $INST python3-light python3-urllib python3-xml python3-openssl python3-logging \
          python3-email python3-codecs ca-bundle 2>/dev/null
fi
if ! python3 -c "$TEST" 2>/dev/null; then
    echo "   Части не хватает — ставлю полный python3"
    $INST python3 ca-bundle
fi
python3 -c "$TEST" || { echo "!! Python не установился. Проверьте интернет и место: df -h /overlay"; exit 1; }
echo "   $(python3 --version) — OK"

echo "== 3/4 Токен бота"
if ! grep -qs '^MAX_BOT_TOKEN=.' "$DIR/.env"; then
    printf "Вставьте токен бота MAX (business.max.ru -> Чат-боты -> ... -> Настройки): "
    read -r TOKEN </dev/tty
    [ -n "$TOKEN" ] || { echo "Токен пустой"; exit 1; }
    printf 'MAX_BOT_TOKEN=%s\nSYSTEME_TOKEN=\n' "$TOKEN" > "$DIR/.env"
    chmod 600 "$DIR/.env"
else
    echo "   Токен уже есть в $DIR/.env"
fi

echo "== 4/4 Служба автозапуска"
cat > /etc/init.d/maxbot <<'INIT'
#!/bin/sh /etc/rc.common
# Бот MAX: автозапуск и перезапуск при падении
START=99
STOP=10
USE_PROCD=1

start_service() {
    procd_open_instance
    procd_set_param command /usr/bin/python3 -u /root/max-bot/bot.py
    procd_set_param respawn 3600 10 0
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_close_instance
}
INIT
chmod +x /etc/init.d/maxbot

# Перезапуск бота, когда снова поднялся интернет (wan / PPPoE)
mkdir -p /etc/hotplug.d/iface
cat > /etc/hotplug.d/iface/99-maxbot <<'HOT'
[ "$ACTION" = "ifup" ] || exit 0
[ "$INTERFACE" = "wan" ] || exit 0
logger -t maxbot "Интернет снова есть — перезапускаю бота"
/etc/init.d/maxbot restart
HOT

# сохранять файлы бота при обновлении прошивки
grep -qs "/root/max-bot" /etc/sysupgrade.conf || echo "/root/max-bot/" >> /etc/sysupgrade.conf

/etc/init.d/maxbot enable
/etc/init.d/maxbot restart
sleep 8
echo
echo "Готово! Последние строки лога бота:"
logread -e python3 | tail -n 5
echo
echo "Лог:          logread -e python3 | tail -n 30"
echo "Перезапуск:   /etc/init.d/maxbot restart"
echo "Остановить:   /etc/init.d/maxbot stop && /etc/init.d/maxbot disable"
