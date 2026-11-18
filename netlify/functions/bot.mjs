// Бот MAX «наличие Systeme Electric» — webhook-функция Netlify.
// MAX присылает сюда события (POST /bot), бот ищет остатки на api.systeme.ru/stock
// и отвечает пользователю через MAX Bot API.
import https from "node:https";
import tls from "node:tls";
import { MINCIFRY_CA } from "./ca.mjs";

const MAX_API = "https://platform-api2.max.ru";
const agent = new https.Agent({ ca: [...tls.rootCertificates, MINCIFRY_CA], keepAlive: true });

const env = (k) => (globalThis.Netlify ? Netlify.env.get(k) : process.env[k]) || "";

function request(url, { method = "GET", headers = {}, body, timeout = 20000 } = {}) {
  return new Promise((resolve, reject) => {
    const req = https.request(url, { method, headers, agent, timeout }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => resolve({ status: res.statusCode, text: Buffer.concat(chunks).toString("utf8") }));
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

function maxApi(method, path, params, body) {
  const qs = params ? "?" + new URLSearchParams(params) : "";
  const data = body ? JSON.stringify(body) : undefined;
  return request(MAX_API + path + qs, {
    method,
    body: data,
    headers: {
      Authorization: env("MAX_BOT_TOKEN"),
      ...(data ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) } : {}),
    },
  });
}

// ---------- Systeme Electric: открытая страница остатков ----------

const decode = (s) =>
  s.replace(/<[^>]+>/g, "")
    .replace(/&quot;/g, '"').replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&#039;/g, "'").replace(/&nbsp;/g, " ").replace(/\s+/g, " ").trim();

async function fetchStock(refs) {
  const p = new URLSearchParams();
  p.set("StockDataSearch[query]", refs.join(";"));
  p.set("StockDataSearch[stocks][55994]", "1");
  p.set("StockDataSearch[stocks][55995]", "1");
  p.set("per-page", "100");
  const res = await request("https://api.systeme.ru/stock?" + p, {
    headers: { "User-Agent": "Mozilla/5.0 (MAX stock bot)", "Accept-Language": "ru" },
  });
  if (res.status !== 200) throw new Error("systeme HTTP " + res.status);
  const rows = [];
  for (const tr of res.text.match(/<tr class="item_table" data-key[\s\S]*?<\/tr>/g) || []) {
    const td = (tr.match(/<td[\s\S]*?<\/td>/g) || []).map(decode);
    if (td.length >= 5) rows.push({ ref: td[0], desc: td[1], unit: td[2], count: td[3], wh: td[4].replace(/\s*\(stockID\d+\)/, "") });
  }
  return rows;
}

const UNITS = { PCE: "шт.", MTR: "м", M: "м", KGM: "кг", SET: "компл." };

function formatRef(ref, rows) {
  if (!rows.length) return `❓ **${ref}** — не найден на складах. Проверьте артикул.`;
  const lines = [`📦 **${rows[0].ref}**`, rows[0].desc, ""];
  let total = 0;
  for (const r of rows) {
    const n = parseInt(String(r.count).replace(/\s/g, ""), 10) || 0;
    total += n;
    lines.push(`🏬 ${r.wh}: **${r.count}** ${UNITS[r.unit] || r.unit} ${n > 0 ? "✅" : "❌"}`);
  }
  lines.push("", total > 0 ? `✅ В наличии: **${total}** шт.` : "❌ Нет в наличии (под заказ — уточните у менеджера)");
  return lines.join("\n");
}

const HELP =
  "👋 Я проверяю наличие оборудования **Systeme Electric** на складах Лобня и Екатеринбург.\n\n" +
  "Пришлите артикул, например: `SB5AD35`\n" +
  "Можно несколько через пробел или запятую: `GSL000545, 11001DEK`\n\n" +
  "_Остатки обновляются 4 раза в день (8, 12, 16, 22 ч МСК), данные ориентировочные._";

async function answer(text) {
  const t = text.trim();
  if (/^\/?(start|help|старт|помощь|начать)$/i.test(t)) return HELP;
  const refs = [...new Set((t.match(/[A-Za-z0-9][A-Za-z0-9._\/-]{2,39}/g) || []).filter((r) => /\d/.test(r)).map((r) => r.toUpperCase()))].slice(0, 20);
  if (!refs.length) return "Не вижу артикула 🤔\n\n" + HELP;
  let rows;
  try {
    rows = await fetchStock(refs);
  } catch (e) {
    console.error("stock error", e);
    return "⚠️ Сайт Systeme Electric сейчас не отвечает. Попробуйте через пару минут.";
  }
  return refs.map((ref) => formatRef(ref, rows.filter((r) => r.ref.toUpperCase() === ref))).join("\n\n———\n\n");
}

async function send(target, text) {
  for (let i = 0; i < text.length; i += 3900) {
    const r = await maxApi("POST", "/messages", target, { text: text.slice(i, i + 3900), format: "markdown" });
    if (r.status !== 200) console.error("send", r.status, r.text.slice(0, 300));
  }
}

async function processUpdate(u) {
  if (u.update_type === "bot_started") {
    const uid = u.user?.user_id;
    return send(uid ? { user_id: uid } : { chat_id: u.chat_id }, HELP);
  }
  if (u.update_type === "message_created") {
    const m = u.message || {};
    const text = m.body?.text;
    if (!text || m.sender?.is_bot) return;
    console.log("msg", m.sender?.user_id, text.slice(0, 100));
    const reply = await answer(text);
    const target = m.recipient?.chat_type === "dialog" && m.sender?.user_id
      ? { user_id: m.sender.user_id }
      : { chat_id: m.recipient?.chat_id };
    return send(target, reply);
  }
}

export default async (req) => {
  const url = new URL(req.url);

  // Проверка работы: /bot?check=АРТИКУЛ  (нужен ?key=WEBHOOK_SECRET)
  if (req.method === "GET") {
    if (url.searchParams.get("key") !== env("WEBHOOK_SECRET")) return new Response("ok");
    const me = await maxApi("GET", "/me").catch((e) => ({ status: 0, text: String(e) }));
    const ref = url.searchParams.get("check");
    const stock = ref ? await answer(ref).catch((e) => String(e)) : "";
    return Response.json({ max: me.status, me: me.text.slice(0, 200), stock });
  }

  if (req.headers.get("x-max-bot-api-secret") !== env("WEBHOOK_SECRET")) {
    return new Response("forbidden", { status: 403 });
  }
  let update;
  try {
    update = await req.json();
  } catch {
    return new Response("bad json", { status: 400 });
  }
  try {
    await processUpdate(update);
  } catch (e) {
    console.error("update error", e);
  }
  return new Response("ok");
};

export const config = { path: "/bot" };
