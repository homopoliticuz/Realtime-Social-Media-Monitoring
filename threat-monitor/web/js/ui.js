// Вспомогательные функции интерфейса.
// Все данные материалов — недоверенные: вставляются только через textContent.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null) continue;
    if (k === "text") el.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, String(v));
  }
  for (const c of children.flat()) if (c) el.appendChild(c);
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function fmtDate(iso, withTime = true) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const opts = withTime
    ? { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { year: "numeric", month: "2-digit", day: "2-digit" };
  return d.toLocaleString("ru-RU", opts);
}

export function fmtNum(n) {
  return new Intl.NumberFormat("ru-RU").format(n || 0);
}

export const PRIORITY_ORDER = ["insufficient_data", "moderate", "high", "critical", "very_critical"];
export const PRIORITY_VAR = {
  insufficient_data: "--p1", moderate: "--p2", high: "--p3", critical: "--p4", very_critical: "--p5", none: "--p-none",
};

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function priorityBadge(priority, label) {
  const key = priority || "none";
  return h("span", { class: "prio" }, h("span", { class: `sw p-${key}`, "aria-hidden": "true" }), label || "— (не угроза)");
}

export function statusIcon(state, title) {
  const glyph = {
    connected: "✓", ok: "✓", done: "✓", watchlist_needed: "!", partial: "!", warn: "!", requires_approval: "⧗",
    not_configured: "–", unavailable: "✕", error: "✕", demo: "Д", running: "◌", pending: "·", start: "◌", skip: "–",
  }[state] || "·";
  return h("span", { class: `status-icon st-${state}`, title: title || state, "aria-label": title || state }, glyph);
}

// Подсветка найденных фрагментов без innerHTML
export function highlighted(text, terms) {
  const frag = document.createDocumentFragment();
  const clean = (terms || []).filter((t) => t && t.length > 1).sort((a, b) => b.length - a.length);
  if (!clean.length) {
    frag.appendChild(document.createTextNode(text || ""));
    return frag;
  }
  const esc = clean.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const re = new RegExp(`(${esc.join("|")})`, "giu");
  let last = 0;
  const src = text || "";
  for (const m of src.matchAll(re)) {
    if (m.index > last) frag.appendChild(document.createTextNode(src.slice(last, m.index)));
    frag.appendChild(h("mark", { text: m[0] }));
    last = m.index + m[0].length;
  }
  if (last < src.length) frag.appendChild(document.createTextNode(src.slice(last)));
  return frag;
}

export function safeLink(url, label) {
  if (!url || !/^https?:\/\//i.test(url)) return h("span", { class: "mono", text: url || "—" });
  return h("a", { href: url, target: "_blank", rel: "noopener noreferrer nofollow", text: label || url });
}

let toastTimer = null;
export function toast(message, isError = false) {
  document.querySelectorAll(".toast").forEach((t) => t.remove());
  const t = h("div", { class: `toast${isError ? " err" : ""}`, role: isError ? "alert" : "status", text: message });
  document.body.appendChild(t);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.remove(), isError ? 7000 : 3500);
}

const tip = () => document.getElementById("tooltip");

export function showTooltip(evt, build) {
  const t = tip();
  clear(t);
  build(t);
  t.hidden = false;
  const pad = 14;
  const rect = t.getBoundingClientRect();
  let x = evt.clientX + pad;
  let y = evt.clientY + pad;
  if (x + rect.width > window.innerWidth - 8) x = evt.clientX - rect.width - pad;
  if (y + rect.height > window.innerHeight - 8) y = evt.clientY - rect.height - pad;
  t.style.left = `${Math.max(8, x)}px`;
  t.style.top = `${Math.max(8, y)}px`;
}

export function hideTooltip() {
  const t = tip();
  if (t) t.hidden = true;
}
