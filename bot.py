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
        try:
            info, stock = web_stock(ref)
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            log.warning("web_stock %s: %s — повтор", ref, e)
            time.sleep(1.5)
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
        return (f"❓ Артикул **{ref}** не найден. Проверьте написание.\n"
                f"Если это оборудование другого бренда, подберу замену: `аналог {ref}`")
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


# ---------- Подбор аналогов (analogs.tools.systeme.ru) ----------

ANALOGS_API = "https://analogs.tools.systeme.ru/api"
_analog_cache = {}


def analogs_api(method, body):
    """POST JSON в API подбора аналогов. Один повтор при обрыве соединения."""
    url = f"{ANALOGS_API}/{method}"
    for attempt in (1, 2):
        try:
            raw = http("POST", url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                       body=body, timeout=30)
            return json.loads(raw or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"status": "error", "http_code": 404}
            if attempt == 2:
                raise
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(1.5)


def stock_short(ref):
    """Короткая строка об остатках аналога: «✅ 120 шт. на складах» / «нет на складах»."""
    stock = None
    for attempt in (1, 2):
        try:
            stock = get_stock(ref) if SYSTEME_TOKEN else web_stock(ref)[1]
            break
        except Exception as e:
            log.warning("stock_short %s (попытка %s): %s", ref, attempt, e)
            time.sleep(1.5)
    if stock is None:
        return ""
    total = 0
    for s in stock:
        try:
            total += int(float(s.get("count", "0") or 0))
        except ValueError:
            pass
    return f"✅ на складах: **{total}** шт." if total > 0 else "🕓 на складах нет"


def find_analog(ref):
    now = time.time()
    key = ref.upper()
    hit = _analog_cache.get(key)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]

    res = analogs_api("search_analog", {"reference": ref})
    options = ((res.get("data") or {}).get("analogs")) or []

    if not options:
        # Возможно, артикул неполный — предложим варианты из поиска
        sr = analogs_api("search", {"query": ref})
        found = ((sr.get("data") or {}).get("results")) or []
        refs = [x["name"] for x in found if x.get("category") == "ref"]
        refs += [x["ref"] for x in found if x.get("category") == "desc" and x.get("ref")]
        refs = [r for r in dict.fromkeys(refs) if r.upper() != key][:10]
        if refs:
            answer = (f"🔎 Точного артикула **{ref}** нет в базе аналогов. Возможно, вы имели в виду:\n"
                      + "\n".join(f"• `{r}`" for r in refs)
                      + "\n\nПришлите: `аналог ПОЛНЫЙ_АРТИКУЛ`")
        else:
            answer = (f"❓ Для **{ref}** аналог не найден.\n"
                      "Проверьте артикул или подберите вручную: https://analogs.tools.systeme.ru")
        _analog_cache[key] = (now, answer)
        return answer

    lines = [f"🔁 Аналог для **{ref.upper()}**"]
    try:
        info = analogs_api("reference_info", {"reference": ref})
        d = info.get("data") or {}
        orig = " · ".join(x for x in (d.get("vendor"), d.get("description"), d.get("product_line")) if x)
        if orig:
            lines.append(f"_Исходный: {orig}_")
    except Exception as e:
        log.warning("reference_info %s: %s", ref, e)

    for n, option in enumerate(options[:3], 1):
        lines.append("")
        if len(options) > 1:
            lines.append(f"**Вариант {n}:**")
        if len(option) > 1:
            lines.append("_Комплект из нескольких позиций:_")
        for a in option:
            r = a.get("reference", "")
            qty = a.get("quantity") or 1
            head = f"➡️ **{r}**" + (f" × {qty}" if qty and qty != 1 else "")
            lines.append(head)
            if a.get("description"):
                lines.append(a["description"])
            if a.get("product_line"):
                lines.append(f"Серия: {a['product_line']}")
            if a.get("comment"):
                lines.append(f"💬 {a['comment']}")
            st = stock_short(r)
            if st:
                lines.append(st)
    lines.append("\n_Подбор ориентировочный, перед заказом сверьте характеристики._")
    answer = "\n".join(lines)
    _analog_cache[key] = (now, answer)
    return answer


# ---------- Статистика пользователей ----------

DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
USERS_FILE = os.path.join(DATA_DIR, "users.json")
VISITS_FILE = os.path.join(DATA_DIR, "visits.csv")
ADMIN_IDS = {x.strip() for x in os.environ.get("ADMIN_IDS", "").replace(";", ",").split(",") if x.strip()}
MSK = 3 * 3600


def now_msk():
    return time.strftime("%d.%m.%Y %H:%M", time.gmtime(time.time() + MSK))


def load_users():
    try:
        with open(USERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def user_name(sender):
    name = sender.get("name") or " ".join(
        x for x in (sender.get("first_name"), sender.get("last_name")) if x)
    return (name or "").strip() or "без имени"


def record(sender, text):
    """Запоминаем пользователя и его запрос (users.json + visits.csv для Excel)."""
    uid = str(sender.get("user_id") or "")
    if not uid:
        return
    try:
        users = load_users()
        u = users.get(uid) or {"first": now_msk(), "count": 0}
        u["name"] = user_name(sender)
        u["username"] = sender.get("username") or u.get("username") or ""
        u["count"] = u.get("count", 0) + 1
        u["last"] = now_msk()
        u["last_query"] = text[:100]
        users[uid] = u
        tmp = USERS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=1)
        os.replace(tmp, USERS_FILE)

        new_file = not os.path.exists(VISITS_FILE)
        with open(VISITS_FILE, "a", encoding="utf-8-sig" if new_file else "utf-8", newline="") as f:
            if new_file:
                f.write("Время (МСК);ID;Имя;Username;Запрос\r\n")
            q = text[:200].replace(";", ",").replace("\r", " ").replace("\n", " ")
            f.write(f"{u['last']};{uid};{u['name'].replace(';', ',')};{u['username']};{q}\r\n")
    except Exception:
        log.exception("record")


def stats_text(uid):
    uid = str(uid or "")
    if not ADMIN_IDS:
        return ("🔐 Администратор бота ещё не назначен.\n\n"
                f"Ваш ID в MAX: `{uid}`\n\n"
                "Чтобы статистика открывалась только вам, выполните на роутере (по ssh):\n"
                f"`echo ADMIN_IDS={uid} >> /root/max-bot/.env && /etc/init.d/maxbot restart`\n\n"
                "После этого снова пришлите /stats.")
    if uid not in ADMIN_IDS:
        return "Не понимаю команду 🤔\n\n" + HELP
    users = load_users()
    if not users:
        return "📊 Пока боту никто не писал."
    def key(item):
        try:
            return time.mktime(time.strptime(item[1].get("last", ""), "%d.%m.%Y %H:%M"))
        except ValueError:
            return 0
    rows = sorted(users.items(), key=key, reverse=True)
    total = sum(u.get("count", 0) for u in users.values())
    lines = [f"📊 **Пользователи бота: {len(users)}**, запросов всего: {total}\n"]
    for i, (k, u) in enumerate(rows[:40], 1):
        nick = f" @{u['username']}" if u.get("username") else ""
        lines.append(f"{i}. **{u.get('name', '')}**{nick} (id {k})\n"
                     f"   запросов: {u.get('count', 0)}, последний: {u.get('last', '')} — «{u.get('last_query', '')}»")
    if len(rows) > 40:
        lines.append(f"\n…и ещё {len(rows) - 40}")
    lines.append("\n_Полная история запросов — файл visits.csv в папке бота (открывается в Excel)._")
    return "\n".join(lines)


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
    "🔁 **Подбор аналога** Systeme Electric для оборудования других брендов "
    "(Schneider, ABB, Siemens, IEK, EKF и др.) — напишите слово «аналог» и артикул:\n"
    "`аналог LC1D09M7`\n\n"
    "Остатки обновляются 4 раза в день (8:00, 12:00, 16:00, 22:00 МСК)."
)

ANALOG_RE = re.compile(r"^\s*(?:/analog|/аналог|аналоги?|замена|подбор)\b[\s:,-]*(.*)$", re.I | re.S)
REF_RE = re.compile(r"[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9._/\-]{2,39}")


def handle_text(text):
    t = text.strip()
    if t.lower() in ("/start", "/help", "start", "помощь", "старт"):
        return HELP
    m = ANALOG_RE.match(t)
    if m:
        refs = [r for r in dict.fromkeys(REF_RE.findall(m.group(1))) if any(c.isdigit() for c in r)][:5]
        if not refs:
            return "Напишите артикул после слова «аналог», например: `аналог LC1D09M7`"
        out = []
        for ref in refs:
            try:
                out.append(find_analog(ref))
            except Exception as e:
                log.exception("analog %s", ref)
                out.append(f"⚠️ {ref}: сервис подбора аналогов не ответил ({type(e).__name__}). Попробуйте позже.")
        return "\n\n———\n\n".join(out)
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
        log.info("from %s (%s): %s", sender.get("user_id"), user_name(sender), text[:100])
        cmd = text.strip().lower()
        if cmd in ("/stats", "/статистика", "статистика"):
            reply = stats_text(sender.get("user_id"))
        elif cmd in ("/myid", "/id"):
            reply = f"Ваш ID в MAX: `{sender.get('user_id')}`"
        else:
            record(sender, text)
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
