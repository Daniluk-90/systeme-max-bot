#!/usr/bin/env python3
"""
Бот для мессенджера MAX: проверка наличия оборудования Systeme Electric
через API https://api.systeme.ru (PCSP).

Пользователь пишет артикул (или несколько через пробел/запятую) —
бот отвечает остатками по складам Лобня и Екатеринбург.

Без внешних зависимостей: нужен только Python 3.8+.
Настройки — в файле .env рядом со скриптом (см. .env.example).
"""

import json
import logging
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------- настройки ----------

def load_env(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


import sys

for _p in (os.path.join(BASE_DIR, ".env"), os.path.join(BASE_DIR, "env.txt"), ".env", "env.txt"):
    load_env(_p)
# Токен можно передать аргументом: python3 bot.py ТОКЕН
if len(sys.argv) > 1 and sys.argv[1].strip():
    os.environ["MAX_BOT_TOKEN"] = sys.argv[1].strip()

MAX_TOKEN = os.environ.get("MAX_BOT_TOKEN", "")
SYSTEME_TOKEN = os.environ.get("SYSTEME_TOKEN", "")
MAX_API = os.environ.get("MAX_API", "https://platform-api2.max.ru")
SYSTEME_API = "https://api.systeme.ru/new-api/XML"
CA_FILE = os.environ.get("CA_FILE", os.path.join(BASE_DIR, "russian_trusted_root_ca.pem"))
CACHE_TTL = int(os.environ.get("CACHE_TTL", "1800"))  # сек; остатки обновляются 4 раза в день
MAX_REFS = 20  # сколько артикулов за одно сообщение

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

SSL_CTX = ssl.create_default_context()
if os.path.exists(CA_FILE):
    # Сертификат Минцифры нужен для platform-api2.max.ru
    SSL_CTX.load_verify_locations(CA_FILE)

STATUS = {
    "S": "✅ в наличии",
    "N": "🕓 под заказ",
    "D": "⛔ снят с производства",
}


# ---------- HTTP ----------

def http(method, url, headers=None, body=None, timeout=40):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        return r.read()


# ---------- Systeme Electric API ----------

_cache = {}


def systeme(method, **params):
    params = {"accessCode": SYSTEME_TOKEN, **params}
    url = f"{SYSTEME_API}/{method}?" + urllib.parse.urlencode(params, safe=",")
    raw = http("GET", url, timeout=30)
    return ET.fromstring(raw)


def text_of(el, tag):
    x = el.find(tag)
    return (x.text or "").strip() if x is not None and x.text else ""


def get_stock(ref):
    """Остатки по одному артикулу -> список складов.
    В ответе теги id/warehouse/count/... повторяются внутри одного <reference>,
    поэтому каждый <id> начинает новый склад."""
    root = systeme("getstock", commercialRef=ref)
    result = []
    for r in root.iter("reference"):
        if text_of(r, "commercialRef").upper() != ref.upper():
            continue
        cur = None
        for child in r:
            tag, val = child.tag, (child.text or "").strip()
            if tag == "id":
                cur = {"id": val}
                result.append(cur)
            elif cur is not None and tag != "commercialRef":
                cur[tag] = val
    return result


def get_info(ref):
    """Наименование, бренд, статус в ассортименте, цена по тарифу."""
    root = systeme("getdata", commercialRef=ref)
    for r in root.iter("reference"):
        if text_of(r, "commercialRef").upper() == ref.upper():
            tariff = r.find("tariff")
            return {
                "description": text_of(r, "description"),
                "brand": text_of(r, "brand"),
                "status": text_of(r, "StatusRef"),
                "price": text_of(tariff, "value") if tariff is not None else "",
                "min_qty": text_of(tariff, "min_order_quantity") if tariff is not None else "",
            }
    return None


def web_stock(ref):
    """Без токена: открытая страница остатков api.systeme.ru/stock."""
    import html as H
    q = urllib.parse.urlencode({"StockDataSearch[query]": ref, "StockDataSearch[stocks][55994]": 1,
                                "StockDataSearch[stocks][55995]": 1, "per-page": 100})
    req = urllib.request.Request("https://api.systeme.ru/stock?" + q, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as r:
        page = r.read().decode("utf-8", "replace")
    rows = []
    for tr in re.findall(r'<tr class="item_table" data-key.*?</tr>', page, re.S):
        td = [re.sub(r"\s+", " ", H.unescape(re.sub("<[^>]+>", "", c))).strip()
              for c in re.findall(r"<td.*?</td>", tr, re.S)]
        if len(td) >= 5 and td[0].upper() == ref.upper():
            desc = td[1]
            rows.append({"id": "", "warehouse": re.sub(r"\s*\(stockID\d+\)", "", td[4]),
                         "count": td[3], "unit": td[2], "status": "S" if td[3] not in ("0", "") else "N"})
    info = {"description": desc, "brand": "", "status": "", "price": "", "min_qty": ""} if rows else None
    return info, rows


def check(ref):
    now = time.time()
    hit = _cache.get(ref.upper())
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    if not SYSTEME_TOKEN:
        info, stock = web_stock(ref)
        answer = format_answer(ref, info, stock)
        _cache[ref.upper()] = (now, answer)
        return answer
    try:
        info = get_info(ref)
    except Exception as e:  # карточка не критична
        log.warning("getdata %s: %s", ref, e)
        info = None
    stock = get_stock(ref)
    answer = format_answer(ref, info, stock)
    _cache[ref.upper()] = (now, answer)
    return answer


def fmt_price(v):
    try:
        return f"{float(v):,.2f}".replace(",", " ") + " ₽"
    except ValueError:
        return v


def format_answer(ref, info, stock):
    lines = [f"📦 **{ref.upper()}**"]
    if info:
        if info["description"]:
            lines.append(info["description"])
        meta = [x for x in (info["brand"], info["status"]) if x]
        if meta:
            lines.append(" · ".join(meta))
        if info["price"]:
            p = f"Цена по тарифу: {fmt_price(info['price'])}"
            if info["min_qty"]:
                p += f" (мин. партия {info['min_qty']} шт.)"
            lines.append(p)
    if not stock and not info:
        return f"❓ Артикул **{ref}** не найден. Проверьте написание."
    if not stock:
        lines.append("Остатков на складах нет (или данные не передаются — уточните у менеджера).")
    else:
        lines.append("")
        total = 0
        last = ""
        for s in stock:
            cnt = s.get("count", "0")
            try:
                total += int(float(cnt))
            except ValueError:
                pass
            st = STATUS.get(s.get("status", ""), s.get("status", ""))
            line = f"🏬 {s.get('warehouse', s['id'])}: **{cnt}** {unit(s.get('unit'))} — {st}"
            if s.get("count_min_date"):
                line += f", мин. к заказу {s['count_min_date']}"
            lines.append(line)
            last = s.get("last_update") or last
        lines.append(f"\nИтого свободно: **{total}** шт.")
        if last:
            lines.append(f"_Обновлено: {last} (данные ориентировочные)_")
    return "\n".join(lines)


def unit(u):
    return {"PCE": "шт.", "M": "м", "MTR": "м", "KGM": "кг"}.get((u or "").upper(), u or "")


# ---------- MAX Bot API ----------

def max_api(method, path, params=None, body=None, timeout=40):
    url = MAX_API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    raw = http(method, url, headers={"Authorization": MAX_TOKEN}, body=body, timeout=timeout)
    return json.loads(raw or b"{}")


def send(text, user_id=None, chat_id=None):
    params = {"user_id": user_id} if user_id else {"chat_id": chat_id}
    for chunk in split(text, 3900):
        try:
            max_api("POST", "/messages", params=params, body={"text": chunk, "format": "markdown"})
        except urllib.error.HTTPError as e:
            log.error("send: %s %s", e.code, e.read()[:300])


def split(text, n):
    parts, buf = [], ""
    for block in text.split("\n\n"):
        if len(buf) + len(block) + 2 > n and buf:
            parts.append(buf)
            buf = ""
        buf = (buf + "\n\n" + block) if buf else block
    if buf:
        parts.append(buf)
    return parts


HELP = (
    "👋 Я проверяю наличие оборудования **Systeme Electric** на складах "
    "(Лобня, Екатеринбург).\n\n"
    "Пришлите артикул, например: `ZENL1121`\n"
    "Можно несколько через пробел или запятую: `A9C62225, 11001DEK`\n\n"
    "Остатки обновляются 4 раза в день (8:00, 12:00, 16:00, 22:00 МСК)."
)

REF_RE = re.compile(r"[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9._/\-]{2,39}")


def handle_text(text):
    t = text.strip()
    if t.lower() in ("/start", "/help", "start", "помощь", "старт"):
        return HELP
    refs = list(dict.fromkeys(REF_RE.findall(t)))[:MAX_REFS]
    refs = [r for r in refs if any(c.isdigit() for c in r)]  # артикул почти всегда с цифрами
    if not refs:
        return "Не вижу артикула 🤔\n\n" + HELP
    answers = []
    for ref in refs:
        try:
            answers.append(check(ref))
        except Exception as e:
            log.exception("check %s", ref)
            answers.append(f"⚠️ {ref}: не удалось получить данные ({type(e).__name__}). Попробуйте позже.")
    return "\n\n———\n\n".join(answers)


def process(update):
    kind = update.get("update_type")
    if kind == "bot_started":
        uid = (update.get("user") or {}).get("user_id")
        send(HELP, user_id=uid) if uid else send(HELP, chat_id=update.get("chat_id"))
    elif kind == "message_created":
        msg = update.get("message") or {}
        text = (msg.get("body") or {}).get("text") or ""
        sender = msg.get("sender") or {}
        rcp = msg.get("recipient") or {}
        if sender.get("is_bot") or not text:
            return
        log.info("from %s: %s", sender.get("user_id"), text[:100])
        reply = handle_text(text)
        if rcp.get("chat_type") == "dialog":
            send(reply, user_id=sender.get("user_id"))
        else:
            send(reply, chat_id=rcp.get("chat_id"))


def main():
    if not MAX_TOKEN:
        raise SystemExit("Заполните MAX_BOT_TOKEN в файле .env")
    me = max_api("GET", "/me")
    log.info("Бот запущен: %s (@%s)", me.get("name") or me.get("first_name"), me.get("username"))
    marker = None
    while True:
        try:
            params = {"timeout": 30, "limit": 100, "types": "message_created,bot_started"}
            if marker is not None:
                params["marker"] = marker
            data = max_api("GET", "/updates", params=params, timeout=45)
            marker = data.get("marker", marker)
            for upd in data.get("updates", []):
                try:
                    process(upd)
                except Exception:
                    log.exception("update")
        except urllib.error.HTTPError as e:
            log.error("updates HTTP %s: %s", e.code, e.read()[:300])
            if e.code == 401:
                raise SystemExit("Неверный MAX_BOT_TOKEN")
            time.sleep(5)
        except Exception as e:
            log.error("updates: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
