# Бот наличия Systeme Electric для MAX

Бот [@se14733049_bot](https://max.ru/se14733049_bot) в мессенджере MAX: пришлите артикул
(`SB5AD35` или несколько: `GSL000545, 11001DEK`) — бот ответит остатками на складах
**Лобня** и **Екатеринбург** с сайта [api.systeme.ru](https://api.systeme.ru/stock).

Подбор аналога Systeme Electric для оборудования других брендов (Schneider, ABB, Siemens, IEK, EKF…):
напишите слово «аналог» и артикул, например `аналог LC1D09M7`. Данные — с [analogs.tools.systeme.ru](https://analogs.tools.systeme.ru),
к каждому аналогу бот добавляет остаток на складах.

## Установка на роутер OpenWrt (одна команда)

Подключитесь к роутеру по ssh (`ssh root@192.168.1.1`) и выполните:

```sh
wget -O /tmp/i.sh https://raw.githubusercontent.com/Daniluk-90/systeme-max-bot/main/openwrt/install.sh && sh /tmp/i.sh
```

Скрипт поставит Python, спросит токен бота и запустит бота как службу: он стартует при включении роутера,
поднимается после падения и перезапускается, когда после обрыва снова появляется интернет (wan/PPPoE).
Обновление — та же команда.

- Лог: `logread -e python3 | tail -n 30`
- Перезапуск: `/etc/init.d/maxbot restart`
- Токен: `/root/max-bot/.env`

## Установка на Windows

Скачайте репозиторий (Code → Download ZIP), распакуйте, установите Python с python.org и запустите
`windows\install.cmd` (попросит права администратора и токен). Бот запускается при включении ПК без входа
в систему, перезапускается после падения и при восстановлении интернета.
Логи: `bot.log` и `server.log`; управление: `windows\restart.cmd`, `windows\uninstall.cmd`.

## Установка на сервер Linux (одна команда)

На VPS с Ubuntu/Debian, под root:

```bash
curl -fsSL https://raw.githubusercontent.com/Daniluk-90/systeme-max-bot/main/install.sh | sudo bash
```

Скрипт попросит токен бота MAX (business.max.ru → Чат-боты → ⋮ → Настройки),
поставит бота как службу и запустит. После перезагрузки сервера бот запускается сам.

- Лог: `journalctl -u max-stock-bot -f`
- Перезапуск: `systemctl restart max-stock-bot`
- Обновление: запустить команду установки ещё раз.
- Сменить токен: `nano /opt/max-stock-bot/.env`, затем перезапуск.

## Как устроено

- `bot.py` — бот на Python 3 без зависимостей, получает сообщения через long polling `platform-api2.max.ru`.
- `openwrt/install.sh` — установщик для роутера OpenWrt, `windows/` — скрипты для Windows, `install.sh` — для Linux VPS.
- Остатки берутся с открытой страницы `api.systeme.ru/stock`. Если указать `SYSTEME_TOKEN`
  (партнёрский токен API), бот использует официальный API и дополнительно показывает цену.
- `russian_trusted_root_ca.pem` — сертификат Минцифры, без него MAX API не открывается.
- `netlify/` — вариант бота в виде webhook-функции для Netlify (переменные `MAX_BOT_TOKEN`, `WEBHOOK_SECRET`).

## Если не работает

| В логе | Причина |
|---|---|
| `CERTIFICATE_VERIFY_FAILED` | нет файла сертификата — запустите установку ещё раз |
| `Неверный MAX_BOT_TOKEN` / `HTTP 401` | неверный токен в `/opt/max-stock-bot/.env` |
| «Сайт Systeme не отвечает» | api.systeme.ru временно недоступен или блокирует IP сервера |

Остатки на стороне Systeme обновляются в 8:00, 12:00, 16:00, 22:00 МСК; данные ориентировочные.
