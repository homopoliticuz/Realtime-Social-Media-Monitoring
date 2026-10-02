// Дозор — веб-интерфейс аналитика.

import { api, qs } from "./api.js";
import { Globe } from "./globe.js";
import { PropagationGraph, evidenceLabel, relationLegend } from "./graph.js";
import { renderTimeline } from "./timeline.js";
import {
  PRIORITY_ORDER, append, clear, fmtDate, fmtNum, h, hideTooltip, highlighted, priorityBadge, safeLink, statusIcon, toast,
} from "./ui.js";

const app = document.getElementById("app");
const add = (el, ...kids) => append(el, kids);

const PURPOSES = [
  "Мониторинг угроз насилия в публичных источниках",
  "Проверка сообщения о конкретной угрозе",
  "Модерация сообщества / платформы",
  "Исследование распространения опасного контента",
];
const STAGE_ORDER = ["validate", "expand", "plan", "fetch", "normalize", "filter", "dedup", "classify", "propagation", "store", "done"];
const KIND_LABELS = { fact: "Факт", assessment: "Оценка", rule: "Правило", limitation: "Ограничение" };

const state = {
  user: null,
  meta: null,
  connectors: [],
  tab: "incidents",
  filters: null,
  search: null,
  timelineField: "published",
  timelineTable: false,
  graphSimilarity: false,
  graphTable: false,
  lastSearch: null,
  stats: null,
};

function defaultFilters() {
  const demoMode = state.meta && state.meta.demo_enabled;
  return {
    priority: [], category: "", status: "", platform: "", lang: "", country: "", q: "", include_non_threat: false,
    requires_review: false, demo: demoMode ? "include" : "exclude", date_from: "", date_to: "", search_id: "", day: "", sort: "priority", offset: 0,
  };
}

function defaultSearch() {
  const m = state.meta;
  return {
    query: "", topics: [], languages: [...m.parallel_default, ...m.regional], date_from: "", date_to: "", countries: [],
    material_langs: [], source_url: "", connectors: state.connectors.filter((c) => usable(c) && c.name !== "demo").map((c) => c.name),
    include_demo: false, purpose: PURPOSES[0], mode: "all",
  };
}

const usable = (c) => ["connected", "watchlist_needed", "demo"].includes(c.status.state) && c.name !== "manual_import";
const can = (p) => state.user && state.user.permissions.includes(p);

// ------------------------------------------------------------------ тема
function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("dozor-theme"); } catch (_) { /* недоступно */ }
  if (saved) document.documentElement.dataset.theme = saved;
}
function toggleTheme() {
  const cur = document.documentElement.dataset.theme
    || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("dozor-theme", next); } catch (_) { /* недоступно */ }
  renderTab();
}

// ------------------------------------------------------------------ вход
async function boot() {
  initTheme();
  try {
    state.user = await api.get("/api/me");
  } catch (_) {
    renderLogin();
    return;
  }
  await loadApp();
}

function renderLogin(error) {
  clear(app);
  const err = h("div", { class: "error", role: "alert", text: error || "" });
  const user = h("input", { type: "text", name: "username", autocomplete: "username", required: true });
  const pass = h("input", { type: "password", name: "password", autocomplete: "current-password", required: true });
  const form = h("form", {},
    h("div", { class: "brand" }, brandMark(), h("div", {}, h("div", { class: "brand-name", text: "ДОЗОР" }),
      h("div", { class: "brand-sub", text: "мониторинг угроз в общедоступном контенте" }))),
    h("label", { class: "field" }, "Имя пользователя", user),
    h("label", { class: "field" }, "Пароль", pass),
    err,
    h("button", { class: "btn primary", type: "submit", text: "Войти" }),
    h("p", { class: "small muted", text: "Доступ разграничен по ролям. Все действия фиксируются в журнале." }));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const res = await api.post("/api/auth/login", { username: user.value, password: pass.value });
      state.user = res.user;
      await loadApp();
    } catch (ex) {
      err.textContent = ex.message;
    }
  });
  app.appendChild(h("div", { class: "login" }, form));
  user.focus();
}

function brandMark() {
  const ns = "http://www.w3.org/2000/svg";
  const s = document.createElementNS(ns, "svg");
  s.setAttribute("viewBox", "0 0 32 32");
  s.setAttribute("class", "brand-mark");
  s.setAttribute("aria-hidden", "true");
  const c1 = document.createElementNS(ns, "circle");
  Object.entries({ cx: 16, cy: 16, r: 13, fill: "none", stroke: "var(--p3)", "stroke-width": 3 }).forEach(([k, v]) => c1.setAttribute(k, v));
  const c2 = document.createElementNS(ns, "circle");
  Object.entries({ cx: 16, cy: 16, r: 4, fill: "var(--p3)" }).forEach(([k, v]) => c2.setAttribute(k, v));
  add(s, c1, c2);
  return s;
}

async function loadApp() {
  const [meta, connectors, stats] = await Promise.all([api.get("/api/meta"), api.get("/api/connectors"), api.get("/api/stats")]);
  state.meta = meta;
  state.connectors = connectors;
  state.stats = stats;
  state.filters = defaultFilters();
  state.search = defaultSearch();
  renderShell();
}

// ------------------------------------------------------------------ каркас
const refs = {};

function renderShell() {
  clear(app);
  const u = state.user;
  refs.demoBadge = h("span", { class: "pill demo-badge hidden", title: state.meta.demo_notice, text: "ДЕМО-ДАННЫЕ В БАЗЕ" });
  const top = h("header", { class: "topbar" },
    h("div", { class: "brand" }, brandMark(), h("div", {}, h("div", { class: "brand-name", text: "ДОЗОР" }),
      h("div", { class: "brand-sub", text: "мониторинг угроз в общедоступном контенте" }))),
    refs.demoBadge,
    h("span", { class: "spacer" }),
    h("span", { class: "pill llm", title: "Второй классификатор (LLM)", text: state.meta.llm_enabled ? `LLM: ${state.meta.llm_model}` : "LLM: выключен" }),
    h("button", { class: "btn sm ghost", onclick: toggleTheme, "aria-label": "Переключить тему", text: "◐ Тема" }),
    h("span", { class: "pill user", text: `${u.username} · ${state.meta.roles[u.role].split(" — ")[0]}` }),
    h("button", { class: "btn sm", onclick: logout, text: "Выйти" }));
  refs.sidebar = h("aside", { class: "sidebar", id: "search-panel", "aria-label": "Поиск" });
  refs.filterbar = h("div", { class: "filterbar", role: "group", "aria-label": "Фильтры" });
  refs.kpis = h("div", { class: "kpis" });
  refs.tabs = h("div", { class: "tabs", role: "tablist" });
  refs.content = h("div", { id: "content" });
  const jump = can("search") ? h("a", { class: "btn sm mobile-only", href: "#search-panel", text: "Поиск ↓" }) : null;
  const main = h("main", { class: "main" }, jump, refs.filterbar, refs.kpis, refs.tabs, refs.content);
  add(app, top, h("div", { class: "layout" }, refs.sidebar, main));
  renderSidebar();
  renderFilters();
  renderTabs();
  refresh();
}

async function logout() {
  try { await api.post("/api/auth/logout"); } catch (_) { /* сессия могла истечь */ }
  state.user = null;
  renderLogin();
}

// ------------------------------------------------------------------ поиск (боковая панель)
function chip(label, pressed, onToggle, attrs = {}) {
  const b = h("button", { type: "button", class: "chip", "aria-pressed": pressed ? "true" : "false", ...attrs }, label);
  b.addEventListener("click", () => {
    const now = b.getAttribute("aria-pressed") !== "true";
    b.setAttribute("aria-pressed", now ? "true" : "false");
    onToggle(now);
  });
  return b;
}

function toggleIn(list, value, on) {
  const i = list.indexOf(value);
  if (on && i < 0) list.push(value);
  if (!on && i >= 0) list.splice(i, 1);
}

function details(title, open, ...content) {
  return h("details", { class: "section", open: open || null }, h("summary", { text: title }), h("div", { class: "content" }, ...content));
}

function renderSidebar() {
  const s = state.search;
  const m = state.meta;
  const sb = clear(refs.sidebar);
  if (!can("search")) {
    sb.appendChild(h("h2", { text: "Поиск" }));
    sb.appendChild(h("p", { class: "muted", text: "Ваша роль позволяет просматривать карточки. Поиск доступен аналитикам." }));
    return;
  }
  const query = h("input", { type: "search", value: s.query, placeholder: "Например: убить, взорвать, «присоединяйтесь»", "aria-label": "Поисковый запрос" });
  query.addEventListener("input", () => { s.query = query.value; });
  query.addEventListener("keydown", (e) => { if (e.key === "Enter") startSearch(); });
  const purposeSel = h("select", { "aria-label": "Цель поиска" }, PURPOSES.map((p) => h("option", { value: p, text: p, selected: p === s.purpose || null })),
    h("option", { value: "__other", text: "Другое…" }));
  const purposeOther = h("input", { type: "text", placeholder: "Опишите цель поиска", class: PURPOSES.includes(s.purpose) ? "hidden" : "" });
  if (!PURPOSES.includes(s.purpose)) { purposeSel.value = "__other"; purposeOther.value = s.purpose; }
  purposeSel.addEventListener("change", () => {
    if (purposeSel.value === "__other") { purposeOther.classList.remove("hidden"); s.purpose = purposeOther.value; purposeOther.focus(); }
    else { purposeOther.classList.add("hidden"); s.purpose = purposeSel.value; }
  });
  purposeOther.addEventListener("input", () => { s.purpose = purposeOther.value; });

  const modeSel = h("select", { "aria-label": "Сочетание слов" },
    h("option", { value: "all", text: "все слова запроса", selected: s.mode === "all" || null }),
    h("option", { value: "any", text: "любое слово запроса", selected: s.mode === "any" || null }));
  modeSel.addEventListener("change", () => { s.mode = modeSel.value; });

  const topics = h("div", { class: "chips" }, m.topics.map((t) => chip(t.label, s.topics.includes(t.id), (on) => toggleIn(s.topics, t.id, on))));

  const langGroup = (codes) => h("div", { class: "chips" }, codes.map((c) => {
    const l = m.languages.find((x) => x.code === c);
    return chip(l ? l.label : c, s.languages.includes(c), (on) => toggleIn(s.languages, c, on),
      { title: l ? `Лексикон: ${l.lexicon_concepts} понятий, ${l.lexicon_terms} терминов` : "" });
  }));
  const others = m.languages.map((l) => l.code).filter((c) => !m.parallel_default.includes(c) && !m.regional.includes(c));
  const langBox = h("div", { class: "stack" },
    h("div", { class: "small muted", text: "Параллельный поиск (по умолчанию)" }), langGroup(m.parallel_default),
    h("div", { class: "small muted", text: "Языки региона (с транслитерацией и двумя графиками)" }), langGroup(m.regional),
    others.length ? h("div", { class: "small muted", text: "Дополнительно" }) : null, others.length ? langGroup(others) : null,
    h("div", { class: "row" },
      h("button", { class: "btn sm", type: "button", text: "Все", onclick: () => { s.languages = m.languages.map((l) => l.code); renderSidebar(); } }),
      h("button", { class: "btn sm", type: "button", text: "По умолчанию", onclick: () => { s.languages = [...m.parallel_default, ...m.regional]; renderSidebar(); } })));

  const df = h("input", { type: "date", value: s.date_from, "aria-label": "С даты" });
  const dt = h("input", { type: "date", value: s.date_to, "aria-label": "По дату" });
  df.addEventListener("change", () => { s.date_from = df.value; });
  dt.addEventListener("change", () => { s.date_to = dt.value; });
  const preset = (label, days) => h("button", { type: "button", class: "chip", text: label, onclick: () => {
    if (days === null) { s.date_from = ""; s.date_to = ""; }
    else {
      const to = new Date();
      const from = new Date(Date.now() - days * 86400000);
      s.date_from = from.toISOString().slice(0, 10);
      s.date_to = to.toISOString().slice(0, 10);
    }
    df.value = s.date_from; dt.value = s.date_to;
  } });
  const period = h("div", { class: "stack" },
    h("div", { class: "chips" }, preset("Сутки", 1), preset("7 дней", 7), preset("30 дней", 30), preset("90 дней", 90), preset("Весь период", null)),
    h("div", { class: "row" }, h("label", { class: "field grow" }, "С", df), h("label", { class: "field grow" }, "По", dt)));

  const countries = h("div", { class: "stack" },
    h("div", { class: "chips" }, m.countries.map((c) => chip(c.name, s.countries.includes(c.code), (on) => toggleIn(s.countries, c.code, on)))),
    h("div", { class: "small muted", text: "Только страна, явно упомянутая в тексте материала. Местонахождение авторов не определяется." }));

  const matLangs = h("div", { class: "chips" }, m.languages.map((l) => chip(l.label, s.material_langs.includes(l.code), (on) => toggleIn(s.material_langs, l.code, on))));

  const link = h("input", { type: "text", value: s.source_url, placeholder: "https://t.me/канал или ссылка на пост" });
  link.addEventListener("input", () => { s.source_url = link.value; });

  const conns = h("div", {}, state.connectors.filter((c) => c.name !== "manual_import").map((c) => {
    const ok = usable(c);
    const cb = h("input", { type: "checkbox", checked: (c.name === "demo" ? s.include_demo : s.connectors.includes(c.name)) || null, disabled: !ok || null,
      "aria-label": c.title });
    cb.addEventListener("change", () => {
      if (c.name === "demo") s.include_demo = cb.checked;
      else toggleIn(s.connectors, c.name, cb.checked);
    });
    return h("label", { class: "conn", title: `${c.access}\n${c.status.message}` },
      cb, h("div", {}, h("div", { class: "name", text: c.platform + (c.name === "telegram_mtproto" ? " (API)" : "") }),
        h("div", { class: "state", text: c.status.label })), statusIcon(c.status.state, c.status.label));
  }));

  refs.expansion = h("div", { class: "expansion" });
  add(sb, 
    h("h2", { text: "Поиск" }),
    h("div", { class: "stack", style: { marginTop: "10px" } },
      h("label", { class: "field" }, "Ключевые слова (на любом языке, транслитом)", query),
      h("label", { class: "field" }, "Сочетание", modeSel),
      h("label", { class: "field" }, "Цель поиска (фиксируется в журнале)", purposeSel, purposeOther)),
    details("Тематика", false, topics),
    details(`Языки параллельного поиска (${s.languages.length})`, false, langBox),
    details("Период публикации", false, period),
    details("Страна, упомянутая в публикации", false, countries),
    details("Язык материала", false, matLangs),
    details("Ссылка на материал или публичный канал", false, link,
      h("div", { class: "small muted", text: "Telegram, VK, YouTube, X, Bluesky или любая публичная страница (с соблюдением robots.txt)." })),
    details("Источники", true, conns,
      h("div", { class: "small muted", text: "Опрашиваются только подключённые источники. Остальные показаны, чтобы ограничения охвата были видны." })),
    h("div", { class: "row", style: { marginTop: "14px" } },
      h("button", { class: "btn", type: "button", text: "Языковые варианты", onclick: previewExpansion }),
      h("button", { class: "btn primary grow", type: "button", text: "Найти", onclick: startSearch })),
    refs.expansion,
    h("p", { class: "small muted", style: { marginTop: "12px" },
      text: "Поиск по телефонам, e-mail и номерам документов запрещён. Упоминания имён анализируются только в контексте конкретных сообщений." }));
}

async function previewExpansion() {
  const s = state.search;
  clear(refs.expansion);
  try {
    const exp = await api.post("/api/expand", { query: s.query, topics: s.topics, languages: s.languages, mode: s.mode });
    renderExpansion(refs.expansion, exp);
  } catch (ex) {
    refs.expansion.appendChild(h("div", { class: "notice warn", text: ex.message }));
  }
}

function renderExpansion(target, exp) {
  clear(target);
  target.appendChild(h("h4", { style: { margin: "12px 0 6px" }, text: `Варианты запроса: ${exp.variant_count}` }));
  for (const w of exp.warnings || []) target.appendChild(h("div", { class: "notice warn", text: w }));
  for (const t of exp.terms) {
    target.appendChild(h("div", { class: "small", style: { margin: "8px 0 4px" } },
      h("b", { text: `«${t.query_term}»` }), " → ", t.concept_labels.length ? t.concept_labels.join(", ") : (t.method === "llm" ? "машинный перевод" : "буквальный поиск")));
    if (t.note) target.appendChild(h("div", { class: "small muted", text: t.note }));
    const rows = Object.entries(t.by_lang).map(([lang, words]) => h("tr", {}, h("td", { text: exp.language_labels[lang] || lang }), h("td", { text: words.join(", ") })));
    if (t.literal_variants && t.literal_variants.length) rows.push(h("tr", {}, h("td", { text: "написания" }), h("td", { text: t.literal_variants.join(", ") })));
    target.appendChild(h("table", { class: "data" }, h("tbody", {}, rows)));
  }
}

// ------------------------------------------------------------------ фильтры, показатели, вкладки
function renderFilters() {
  const f = state.filters;
  const m = state.meta;
  const bar = clear(refs.filterbar);
  const prio = h("div", { class: "chips", role: "group", "aria-label": "Приоритет" }, PRIORITY_ORDER.map((p) =>
    chip(h("span", { class: "row", style: { gap: "6px" } }, h("span", { class: `sw p-${p}`, style: { width: "10px", height: "10px", borderRadius: "2px", display: "inline-block" } }), m.priorities[p]),
      f.priority.includes(p), (on) => { toggleIn(f.priority, p, on); f.offset = 0; refresh(); })));
  const sel = (key, label, options) => {
    const s = h("select", { "aria-label": label }, h("option", { value: "", text: label }),
      options.map(([v, t]) => h("option", { value: v, text: t, selected: f[key] === v || null })));
    s.addEventListener("change", () => { f[key] = s.value; f.offset = 0; refresh(); });
    return s;
  };
  const platforms = [...new Set(state.connectors.map((c) => c.platform).filter((p) => !["Демо", "Импорт"].includes(p)))];
  const q = h("input", { type: "search", placeholder: "Текст во фрагменте", value: f.q, "aria-label": "Фильтр по тексту" });
  let qTimer;
  q.addEventListener("input", () => { clearTimeout(qTimer); qTimer = setTimeout(() => { f.q = q.value; f.offset = 0; refresh(); }, 350); });
  const chk = (key, label) => {
    const c = h("input", { type: "checkbox", checked: f[key] || null });
    c.addEventListener("change", () => { f[key] = c.checked; f.offset = 0; refresh(); });
    return h("label", { class: "check small" }, c, label);
  };
  let demo = null;
  if (m.demo_enabled) {
    demo = sel("demo", "Демо-данные", [["include", "Демо: включать"], ["exclude", "Демо: скрыть"], ["only", "Только демо"]]);
    demo.value = f.demo;
  }
  add(bar, prio,
    sel("category", "Категория", Object.entries(m.categories)),
    sel("status", "Статус проверки", Object.entries(m.statuses)),
    sel("platform", "Платформа", platforms.map((p) => [p, p])),
    sel("lang", "Язык", m.languages.map((l) => [l.code, l.label])),
    sel("country", "Страна в тексте", m.countries.map((c) => [c.code, c.name])),
    q, chk("requires_review", "Требуют обязательной проверки"), chk("include_non_threat", "Показать материалы без угроз"), demo,
    f.search_id ? h("span", { class: "pill" }, `Результаты поиска ${f.search_id}`, h("button", { class: "btn sm ghost", text: "×", "aria-label": "Сбросить",
      onclick: () => { f.search_id = ""; renderFilters(); refresh(); } })) : null,
    f.day ? h("span", { class: "pill" }, `Дата ${f.day}`, h("button", { class: "btn sm ghost", text: "×", "aria-label": "Сбросить",
      onclick: () => { f.day = ""; renderFilters(); refresh(); } })) : null,
    h("button", { class: "btn sm", text: "Сбросить", onclick: () => { state.filters = defaultFilters(); renderFilters(); refresh(); } }));
}

function filterParams(extra = {}) {
  const f = state.filters;
  return qs({ ...f, priority: f.priority, ...extra });
}

async function renderKpis() {
  const f = state.filters;
  const stats = await api.get(`/api/stats${qs({ demo: f.demo })}`);
  state.stats = stats;
  refs.demoBadge.classList.toggle("hidden", !(state.meta.demo_enabled && stats.demo_incidents > 0));
  const box = clear(refs.kpis);
  const hero = h("button", { class: "tile hero", type: "button", onclick: () => { f.requires_review = true; renderFilters(); refresh(); } },
    h("span", { class: "label" }, "⚑ Требуют обязательной проверки человеком"), h("span", { class: "value", text: fmtNum(stats.pending_mandatory_review) }));
  box.appendChild(hero);
  [...PRIORITY_ORDER].reverse().forEach((p) => {
    box.appendChild(h("button", { class: "tile", type: "button", onclick: () => { f.priority = [p]; f.offset = 0; renderFilters(); refresh(); } },
      h("span", { class: "label" }, h("span", { class: `swatch sw p-${p}` }), state.meta.priorities[p]),
      h("span", { class: "value", text: fmtNum(stats.by_priority[p] || 0) })));
  });
}

const TABS = [
  ["incidents", "Карточки инцидентов", null],
  ["timeline", "Временная шкала", null],
  ["graph", "Карта распространения", null],
  ["sources", "Источники и охват", null],
  ["tools", "Анализ и импорт", "analyze_text"],
  ["audit", "Журнал действий", "audit"],
  ["admin", "Администрирование", "manage_settings"],
];

function renderTabs() {
  const box = clear(refs.tabs);
  for (const [id, label, perm] of TABS) {
    if (perm && !can(perm)) continue;
    box.appendChild(h("button", { class: "tab", role: "tab", "aria-selected": state.tab === id ? "true" : "false", text: label,
      onclick: () => { state.tab = id; renderTabs(); renderTab(); } }));
  }
}

async function refresh() {
  try {
    await Promise.all([renderKpis(), renderTab()]);
  } catch (ex) {
    if (ex.status === 401) { renderLogin("Сессия истекла. Войдите снова."); return; }
    toast(ex.message, true);
  }
}

async function renderTab() {
  hideTooltip();
  const c = refs.content;
  c.style.opacity = "0.6";
  try {
    if (state.tab === "incidents") await renderIncidents(c);
    else if (state.tab === "timeline") await renderTimelineTab(c);
    else if (state.tab === "graph") await renderGraphTab(c);
    else if (state.tab === "sources") renderSources(c);
    else if (state.tab === "tools") renderTools(c);
    else if (state.tab === "audit") await renderAudit(c);
    else if (state.tab === "admin") await renderAdmin(c);
  } finally {
    c.style.opacity = "";
  }
}

// ------------------------------------------------------------------ карточки
async function renderIncidents(c) {
  const f = state.filters;
  const data = await api.get(`/api/incidents${filterParams({ limit: 50 })}`);
  clear(c);
  c.appendChild(h("div", { class: "chart-head" },
    h("div", { class: "muted", text: `Найдено карточек: ${fmtNum(data.total)}` + (f.include_non_threat ? " (включая материалы без угроз)" : "") }),
    sortSelect()));
  if (!data.items.length) {
    if (state.stats && state.stats.total_materials === 0) {
      c.appendChild(renderOnboarding());
      return;
    }
    c.appendChild(h("div", { class: "empty" }, h("p", { text: "Карточек по выбранным фильтрам нет." })));
    return;
  }
  const list = h("div", { class: "cards" }, data.items.map(cardEl));
  c.appendChild(list);
  if (data.total > 50) {
    c.appendChild(h("div", { class: "pager" },
      h("button", { class: "btn sm", disabled: f.offset === 0 || null, text: "← Назад", onclick: () => { f.offset = Math.max(0, f.offset - 50); renderTab(); } }),
      h("span", { class: "muted small", text: `${f.offset + 1}–${Math.min(f.offset + 50, data.total)} из ${data.total}` }),
      h("button", { class: "btn sm", disabled: f.offset + 50 >= data.total || null, text: "Далее →", onclick: () => { f.offset += 50; renderTab(); } })));
  }
}

function sortSelect() {
  const s = h("select", { "aria-label": "Сортировка", style: { width: "auto" } },
    [["priority", "По приоритету"], ["published", "По дате публикации"], ["detected", "По дате обнаружения"], ["confidence", "По уверенности"]]
      .map(([v, t]) => h("option", { value: v, text: t, selected: state.filters.sort === v || null })));
  s.addEventListener("change", () => { state.filters.sort = s.value; renderTab(); });
  return s;
}

function cardEl(it) {
  const reviewPending = it.requires_review && ["new", "needs_review", "in_review"].includes(it.status);
  const card = h("article", { class: `card pr-${it.priority || "none"}`, tabindex: 0, "aria-label": `${it.id}: ${it.category_label}` },
    h("div", { class: "head" },
      priorityBadge(it.priority, it.priority_label),
      h("span", { class: "tag", text: it.category_label }),
      it.is_demo ? h("span", { class: "tag demo-badge", text: "ДЕМО" }) : null,
      reviewPending ? h("span", { class: "tag review", text: "обязательная проверка" }) : null,
      ["new", "needs_review"].includes(it.status) ? null : h("span", { class: "tag", text: it.status_label }),
      it.corrected ? h("span", { class: "tag", text: "исправлено аналитиком" }) : null,
      h("span", { class: "grow" }),
      h("span", { class: "mono muted", text: it.id })),
    h("div", { class: "excerpt" }, highlighted(it.excerpt, it.highlights)),
    it.obfuscation && it.obfuscation.length ? h("div", { class: "small muted", text: `Снята обфускация: ${it.obfuscation.join(", ")}` }) : null,
    h("div", { class: "meta" },
      h("span", { text: it.platform }),
      h("span", { text: it.source_name }),
      h("span", { text: `опубликовано: ${fmtDate(it.published_at)}` }),
      h("span", { text: `обнаружено: ${fmtDate(it.detected_at)}` }),
      h("span", { text: `язык: ${it.lang_label || "—"}` }),
      it.countries.length ? h("span", { text: `страны в тексте: ${it.countries.join(", ")}` }) : null,
      it.copies ? h("span", { text: `копий: ${it.copies}` }) : null,
      it.is_threat ? h("span", { text: `тяжесть: ${it.severity_label}` }) : null,
      h("span", { text: `уверенность: ${(it.confidence || 0).toFixed(2)} (${it.confidence_label})` })));
  card.addEventListener("click", () => openIncident(it.id));
  card.addEventListener("keydown", (e) => { if (e.key === "Enter") openIncident(it.id); });
  return card;
}

// ------------------------------------------------------------------ карточка (ящик)
async function openIncident(id) {
  let card;
  try {
    card = await api.get(`/api/incidents/${encodeURIComponent(id)}`);
  } catch (ex) {
    toast(ex.message, true);
    return;
  }
  closeDrawer();
  const backdrop = h("div", { class: "drawer-backdrop", onclick: closeDrawer });
  const drawer = h("aside", { class: "drawer", role: "dialog", "aria-modal": "true", "aria-label": `Карточка ${card.id}` });
  refs.drawer = { backdrop, drawer };
  add(document.body, backdrop, drawer);
  document.addEventListener("keydown", escClose);
  fillDrawer(drawer, card);
  drawer.focus();
}

function escClose(e) { if (e.key === "Escape") closeDrawer(); }
function closeDrawer() {
  if (refs.drawer) {
    refs.drawer.backdrop.remove();
    refs.drawer.drawer.remove();
    refs.drawer = null;
    document.removeEventListener("keydown", escClose);
  }
}

function fillDrawer(d, card) {
  clear(d);
  const a = card.assessment || {};
  const m = card.material;
  const reviewPending = card.requires_review && ["new", "needs_review", "in_review"].includes(card.status);
  add(d, 
    h("button", { class: "btn sm close", text: "✕ Закрыть", onclick: closeDrawer }),
    h("div", { class: "row" }, h("h2", { text: `Карточка опасного материала ${card.id}` })),
    h("div", { class: "row", style: { margin: "8px 0" } },
      priorityBadge(card.priority, card.priority_label), h("span", { class: "tag", text: card.category_label }),
      h("span", { class: "tag", text: card.status_label }), card.is_demo ? h("span", { class: "tag demo-badge", text: "ДЕМО" }) : null,
      card.corrected ? h("span", { class: "tag", text: "исправлено аналитиком" }) : null,
      card.legal_hold ? h("span", { class: "tag", text: "legal hold" }) : null));
  if (card.is_demo) d.appendChild(h("div", { class: "banner demo", style: { marginBottom: "10px" } }, h("span", { text: state.meta.demo_notice })));
  if (reviewPending) {
    d.appendChild(h("div", { class: "banner", style: { marginBottom: "10px" } }, h("span", { class: "ico", text: "⚑" }),
      h("div", {}, h("b", { text: "Требуется обязательная проверка человеком. " }), card.review_reason || "")));
  }

  // Факты
  const facts = h("section", { class: "box fact" },
    h("h3", { text: "Факты (из материала и метаданных источника)" }),
    h("dl", { class: "kv" },
      h("dt", { text: "Платформа" }), h("dd", { text: m.platform }),
      h("dt", { text: "Ссылка на источник" }), h("dd", {}, safeLink(m.source_url)),
      h("dt", { text: "Источник" }), h("dd", { text: m.source_name || "—" }),
      h("dt", { text: "Автор" }), h("dd", { text: m.author_kind_label || "—" }),
      h("dt", { text: "Тип материала" }), h("dd", { text: m.source_kind || "—" }),
      h("dt", { text: "Дата публикации" }), h("dd", { text: fmtDate(m.published_at) }),
      h("dt", { text: "Дата обнаружения" }), h("dd", { text: fmtDate(card.detected_at) }),
      h("dt", { text: "Язык" }), h("dd", { text: `${m.lang_label || "—"} (уверенность ${m.lang_confidence ?? "—"})` }),
      h("dt", { text: "Происхождение" }), h("dd", { text: m.provenance === "user_provided" ? `предоставлено пользователем; основание: ${m.legal_basis}` : m.provenance === "demo" ? "демонстрационный набор" : `коннектор ${m.connector}` }),
      h("dt", { text: "SHA-256 исходного текста" }), h("dd", { class: "mono", text: m.evidence_hash || "—" }),
      h("dt", { text: "Хранится до" }), h("dd", { text: card.legal_hold ? "без срока (legal hold)" : fmtDate(card.retention_until, false) })),
    h("h4", { style: { margin: "10px 0 4px" }, text: "Необходимый фрагмент текста" }),
    h("div", { class: "excerpt", style: { whiteSpace: "pre-wrap", overflowWrap: "anywhere" } },
      highlighted(m.excerpt, (a.evidence || []).filter((e) => e.group !== "movement").map((e) => e.surface))),
    m.pii_masked ? h("div", { class: "small muted", text: "Телефоны и e-mail в тексте скрыты." }) : null);
  if (m.has_context && can("view_context")) {
    const ctxBox = h("div");
    facts.appendChild(h("button", { class: "btn sm", style: { marginTop: "8px" }, text: "Показать контекст", onclick: async () => {
      const ctx = await api.get(`/api/incidents/${encodeURIComponent(card.id)}/context`);
      clear(ctxBox).appendChild(h("div", { class: "notice", style: { marginTop: "8px" } }, h("div", { class: "small muted", text: "Контекст (родительский пост / обсуждение):" }), h("div", { text: ctx.context || "—" })));
    } }), ctxBox);
  }
  const observed = (a.explanation || []).filter((e) => e.kind === "fact");
  if (observed.length) {
    facts.appendChild(h("h4", { style: { margin: "10px 0 4px" }, text: "Наблюдаемые признаки в тексте" }));
    facts.appendChild(h("ul", { class: "small", style: { margin: 0, paddingLeft: "18px" } }, observed.map((e) => h("li", { text: e.text }))));
  }
  if (card.copies.length) {
    facts.appendChild(h("h4", { style: { margin: "10px 0 4px" }, text: `Точные копии (${card.copies.length})` }));
    facts.appendChild(h("ul", { class: "small", style: { margin: 0, paddingLeft: "18px" } }, card.copies.map((cp) =>
      h("li", {}, `${cp.platform} · ${cp.source_name} · ${fmtDate(cp.published_at)} · `, safeLink(cp.source_url, "ссылка")))));
  }

  const assessBox = renderAssessment(a, card);
  d.appendChild(h("div", { class: "split" }, facts, assessBox));

  // Распространение
  const graphBox = h("section", { class: "box", style: { marginTop: "12px" } }, h("h3", { text: "Распространение материала" }));
  d.appendChild(graphBox);
  loadMiniGraph(graphBox, card);

  // Действия аналитика
  if (can("review") || can("correct")) d.appendChild(renderActions(card));

  // История
  const hist = h("section", { class: "box", style: { marginTop: "12px" } }, h("h3", { text: "История оценок и исправлений" }));
  hist.appendChild(h("table", { class: "data" }, h("thead", {}, h("tr", {}, ["Дата", "Движок", "Категория", "Приоритет", "Уверенность"].map((x) => h("th", { text: x })))),
    h("tbody", {}, card.history.map((r) => h("tr", {}, h("td", { text: fmtDate(r.created_at) }), h("td", { class: "mono", text: r.engine }),
      h("td", { text: r.category_label }), h("td", { text: state.meta.priorities[r.priority] || "—" }), h("td", { class: "num", text: (r.confidence || 0).toFixed(2) }))))));
  if (card.corrections.length) {
    hist.appendChild(h("h4", { style: { margin: "10px 0 4px" }, text: "Исправления аналитиков" }));
    hist.appendChild(h("ul", { class: "small", style: { margin: 0, paddingLeft: "18px" } }, card.corrections.map((x) =>
      h("li", { text: `${fmtDate(x.created_at)} · ${x.user}: ${x.field} «${x.old_value ?? "—"}» → «${x.new_value ?? "—"}». Причина: ${x.reason}` }))));
  }
  if (card.audit && card.audit.length) {
    hist.appendChild(h("h4", { style: { margin: "10px 0 4px" }, text: "Журнал действий по карточке" }));
    hist.appendChild(h("ul", { class: "small", style: { margin: 0, paddingLeft: "18px" } }, card.audit.map((x) =>
      h("li", { text: `${fmtDate(x.ts)} · ${x.user} (${x.role || "—"}) · ${x.action_label}${x.details && x.details.status ? ": " + x.details.status : ""}` }))));
  }
  d.appendChild(hist);
}

function renderAssessment(a, card) {
  const m = state.meta;
  const box = h("section", { class: "box assess" }, h("h3", { text: "Аналитическая оценка (автоматическая)" }));
  const conf = a.confidence ?? (card ? card.confidence : 0);
  box.appendChild(h("dl", { class: "kv" },
    h("dt", { text: "Категория" }), h("dd", { text: card ? card.category_label : a.category_label }),
    card && card.corrected ? h("dt", { text: "Автоматически" }) : null,
    card && card.corrected ? h("dd", { text: `${card.auto.category_label}, ${card.auto.priority_label}` }) : null,
    h("dt", { text: "Рамка высказывания" }), h("dd", { text: a.framing_label || "—" }),
    h("dt", { text: "Приоритет проверки" }), h("dd", {}, priorityBadge(card ? card.priority : a.priority, card ? card.priority_label : a.priority_label)),
    h("dt", { text: "Тяжесть вреда" }), h("dd", { text: card ? card.severity_label : a.severity_label }),
    h("dt", { text: "Уверенность" }), h("dd", {},
      h("div", { class: "row" }, h("div", { class: "meter grow", role: "meter", "aria-valuemin": 0, "aria-valuemax": 1, "aria-valuenow": conf },
        h("span", { style: { width: `${Math.round(conf * 100)}%` } })), h("span", { text: `${conf.toFixed(2)} (${a.confidence_label || ""})` }))),
    h("dt", { text: "Движок" }), h("dd", { class: "mono", text: a.engine || "—" })));
  const sp = a.specificity || {};
  const specItem = (label, yes, detail) => h("div", { class: `item ${yes ? "yes" : "no"}` }, h("b", { text: label }), h("span", { text: detail || (yes ? "указано" : "не указано") }));
  box.appendChild(h("h4", { style: { margin: "10px 0 4px" }, text: "Конкретность (определяет приоритет)" }));
  box.appendChild(h("div", { class: "spec" },
    specItem("Цель", sp.target && sp.target.present, sp.target && sp.target.details && sp.target.details.join("; ")),
    specItem("Время", (sp.time || []).length, (sp.time || []).join(", ")),
    specItem("Место", (sp.place || []).length, (sp.place || []).join(", ")),
    specItem("Подготовка", (sp.preparation || []).length, (sp.preparation || []).join(", ")),
    specItem("Тревожные признаки", (sp.warning_behaviors || []).length, (sp.warning_behaviors || []).join(", ")),
    specItem("Намерение / призыв", sp.intent || sp.call, [sp.intent ? "намерение 1-го лица" : "", sp.call ? "призыв" : ""].filter(Boolean).join(", "))));
  const groups = ["assessment", "rule", "limitation"];
  const list = h("ul", { class: "explain" });
  for (const e of (a.explanation || []).filter((x) => groups.includes(x.kind))) {
    list.appendChild(h("li", {}, h("span", { class: "kind", text: KIND_LABELS[e.kind] }), h("span", { text: e.text })));
  }
  box.appendChild(h("h4", { style: { margin: "10px 0 4px" }, text: "Обоснование" }));
  box.appendChild(list);
  if (a.rabat) {
    const labels = { context: "Контекст", speaker: "Говорящий", intent: "Намерение", content_form: "Содержание и форма", extent: "Масштаб распространения", likelihood_imminence: "Вероятность и неотвратимость" };
    box.appendChild(h("h4", { style: { margin: "10px 0 4px" }, text: "Рабатский тест (6 факторов порога подстрекательства)" }));
    box.appendChild(h("dl", { class: "kv small" }, Object.entries(labels).map(([k, l]) => [h("dt", { text: l }), h("dd", { text: a.rabat[k] || "—" })])));
  }
  box.appendChild(h("h4", { style: { margin: "10px 0 4px" }, text: "География, упомянутая в материале" }));
  if ((a.geo || []).length) {
    box.appendChild(h("ul", { class: "small", style: { margin: 0, paddingLeft: "18px" } }, a.geo.map((g) =>
      h("li", {}, h("b", { text: g.kind === "city" ? `${g.name} (${g.country_name})` : g.name }), ` — ${g.relation_label}. `,
        h("span", { class: "muted", text: `Фрагмент: «${g.evidence}»` })))));
  } else {
    box.appendChild(h("div", { class: "small muted", text: "Явных упоминаний стран и городов нет." }));
  }
  box.appendChild(h("div", { class: "small muted", style: { marginTop: "4px" }, text: m.geo_disclaimer }));
  const engines = Object.entries(a.engines || {});
  if (engines.length > 1) {
    box.appendChild(h("h4", { style: { margin: "10px 0 4px" }, text: "Сравнение классификаторов" }));
    box.appendChild(h("table", { class: "data" }, h("tbody", {}, engines.map(([name, e]) => h("tr", {},
      h("td", { class: "mono", text: name }), h("td", { text: m.categories[e.category] || e.category }),
      h("td", { text: m.priorities[e.priority] || "—" }), h("td", { class: "num", text: (e.confidence ?? 0).toFixed(2) }))))));
  }
  if ((a.evidence || []).length) {
    box.appendChild(h("details", {}, h("summary", { class: "small", text: `Совпадения лексикона (${a.evidence.length})` }),
      h("table", { class: "data" }, h("tbody", {}, a.evidence.map((e) => h("tr", {},
        h("td", { text: e.surface }), h("td", { text: e.label }), h("td", { text: e.form_label }),
        h("td", { class: "small muted", text: [e.lang, e.quoted ? "в кавычках" : "", e.idiom ? "идиома" : "", e.negated ? "отрицание" : "", e.obfuscated ? "обфускация" : ""].filter(Boolean).join(", ") })))))));
  }
  return box;
}

async function loadMiniGraph(box, card) {
  try {
    const data = await api.get(`/api/graph${qs({ incident: card.id, similarity: true })}`);
    const evidenceEdges = data.edges.filter((e) => e.is_evidence);
    const simEdges = data.edges.filter((e) => !e.is_evidence);
    box.appendChild(h("div", { class: "small muted", text: `Подтверждённых связей: ${evidenceEdges.length}; похожих текстов (не связь): ${simEdges.length}.` }));
    if (!data.edges.length) return;
    box.appendChild(relationLegend(simEdges.length > 0));
    const wrap = h("div", { class: "graph-wrap small", style: { marginTop: "8px" } });
    box.appendChild(wrap);
    new PropagationGraph(wrap, { onOpen: (id) => { if (id !== card.id) openIncident(id); } }).render(data);
    box.appendChild(h("table", { class: "data", style: { marginTop: "8px" } },
      h("thead", {}, h("tr", {}, ["Связь", "Подтверждение", "Сведения"].map((x) => h("th", { text: x })))),
      h("tbody", {}, data.edges.map((e) => h("tr", {}, h("td", { text: e.relation_label }), h("td", { text: evidenceLabel(e.evidence_type) }), h("td", { class: "small", text: e.evidence_detail || "" }))))));
  } catch (ex) {
    box.appendChild(h("div", { class: "small error", text: ex.message }));
  }
}

function renderActions(card) {
  const m = state.meta;
  const box = h("section", { class: "box", style: { marginTop: "12px" } }, h("h3", { text: "Проверка аналитиком" }));
  if (can("review")) {
    const status = h("select", {}, Object.entries(m.statuses).map(([k, v]) => h("option", { value: k, text: v, selected: k === card.status || null })));
    const note = h("textarea", { placeholder: "Обоснование решения (обязательно для подтверждения, передачи и снятия оценки)" });
    note.value = card.analyst_note || "";
    const critical = ["critical", "very_critical"].includes(card.priority);
    add(box, 
      critical ? h("div", { class: "notice warn", text: "Критическая оценка: подтверждение или передачу утверждает руководитель; снять оценку может руководитель или второй аналитик после первичной проверки." }) : null,
      h("div", { class: "row", style: { marginTop: "8px" } }, h("label", { class: "field grow" }, "Статус проверки", status)),
      h("label", { class: "field", style: { marginTop: "6px" } }, "Комментарий", note),
      h("div", { class: "row", style: { marginTop: "6px" } },
        h("button", { class: "btn primary", text: "Сохранить статус", onclick: async () => {
          try {
            const updated = await api.post(`/api/incidents/${encodeURIComponent(card.id)}/review`, { status: status.value, note: note.value });
            toast("Статус сохранён");
            fillDrawer(refs.drawer.drawer, { ...updated, audit: card.audit, propagation_count: card.propagation_count });
            refresh();
          } catch (ex) { toast(ex.message, true); }
        } }),
        card.reviewed_by ? h("span", { class: "small muted", text: `Проверил: ${card.reviewed_by}, ${fmtDate(card.reviewed_at)}${card.second_reviewed_by ? `; второй проверяющий: ${card.second_reviewed_by}` : ""}` }) : null));
  }
  if (can("correct")) {
    const sel = (opts, cur, label) => h("label", { class: "field grow" }, label,
      h("select", {}, h("option", { value: "", text: "без изменений" }), Object.entries(opts).map(([k, v]) => h("option", { value: k, text: v + (k === cur ? " (текущее)" : "") }))));
    const cat = sel(m.categories, card.category, "Категория");
    const pr = sel(m.priorities, card.priority, "Приоритет");
    const sev = sel(m.severities, card.severity, "Тяжесть");
    const reason = h("input", { type: "text", placeholder: "Причина исправления (обязательно)" });
    add(box, h("h4", { style: { margin: "14px 0 6px" }, text: "Исправить ошибочную оценку" }),
      h("div", { class: "row" }, cat, pr, sev),
      h("div", { class: "row", style: { marginTop: "6px" } }, reason,
        h("button", { class: "btn", text: "Исправить", onclick: async () => {
          try {
            const updated = await api.post(`/api/incidents/${encodeURIComponent(card.id)}/correct`, {
              category: cat.querySelector("select").value || null, priority: pr.querySelector("select").value || null,
              severity: sev.querySelector("select").value || null, reason: reason.value,
            });
            toast("Оценка исправлена; исправление записано в журнал");
            fillDrawer(refs.drawer.drawer, { ...updated, audit: card.audit });
            refresh();
          } catch (ex) { toast(ex.message, true); }
        } })),
      h("div", { class: "small muted", text: "Исправления сохраняются отдельно от автоматической оценки и могут использоваться для улучшения лексикона и моделей." }));
  }
  if (can("legal_hold") || can("delete")) {
    const reason = h("input", { type: "text", placeholder: "Основание (обязательно)" });
    add(box, h("h4", { style: { margin: "14px 0 6px" }, text: "Хранение" }), h("div", { class: "row" }, reason,
      can("legal_hold") ? h("button", { class: "btn", text: card.legal_hold ? "Снять legal hold" : "Legal hold (не удалять)", onclick: async () => {
        try {
          const updated = await api.post(`/api/incidents/${encodeURIComponent(card.id)}/legal-hold`, { hold: !card.legal_hold, reason: reason.value });
          fillDrawer(refs.drawer.drawer, { ...updated, audit: card.audit });
          toast("Режим хранения изменён");
        } catch (ex) { toast(ex.message, true); }
      } }) : null,
      can("delete") ? h("button", { class: "btn danger", text: "Удалить материал", onclick: async () => {
        if (!confirm(`Удалить карточку ${card.id} и связанные материалы без возможности восстановления?`)) return;
        try {
          await api.del(`/api/incidents/${encodeURIComponent(card.id)}`);
          toast("Удалено");
          closeDrawer();
          refresh();
        } catch (ex) { toast(ex.message, true); }
      } }) : null));
  }
  return box;
}

// ------------------------------------------------------------------ шкала и граф
async function renderTimelineTab(c) {
  const days = await api.get(`/api/timeline${filterParams({ field: state.timelineField })}`);
  clear(c);
  const panel = h("div", { class: "panel" });
  const fieldSel = h("select", { style: { width: "auto" }, "aria-label": "Ось времени" },
    h("option", { value: "published", text: "По дате публикации", selected: state.timelineField === "published" || null }),
    h("option", { value: "detected", text: "По дате обнаружения", selected: state.timelineField === "detected" || null }));
  fieldSel.addEventListener("change", () => { state.timelineField = fieldSel.value; renderTab(); });
  const tableBtn = h("button", { class: "btn sm", text: state.timelineTable ? "Показать график" : "Показать таблицей", onclick: () => { state.timelineTable = !state.timelineTable; renderTab(); } });
  panel.appendChild(h("div", { class: "chart-head" }, h("h3", { text: "Материалы по дням и приоритету проверки" }), h("div", { class: "row" }, fieldSel, tableBtn)));
  const body = h("div");
  panel.appendChild(body);
  c.appendChild(panel);
  const labels = { ...state.meta.priorities, none: "Не угроза" };
  renderTimeline(body, days, labels, {
    showTable: state.timelineTable,
    onDay: (day) => { state.filters.day = day; state.tab = "incidents"; renderFilters(); renderTabs(); refresh(); },
  });
  panel.appendChild(h("p", { class: "small muted", text: "Сегменты окрашены по порядковой шкале приоритета (светлее — ниже приоритет). Нажмите на столбец, чтобы открыть карточки за день." }));
}

async function renderGraphTab(c) {
  const data = await api.get(`/api/graph${filterParams({ similarity: state.graphSimilarity })}`);
  clear(c);
  const sim = h("input", { type: "checkbox", checked: state.graphSimilarity || null });
  sim.addEventListener("change", () => { state.graphSimilarity = sim.checked; renderTab(); });
  const panel = h("div", { class: "panel" },
    h("div", { class: "chart-head" }, h("h3", { text: "Карта распространения публикаций" }),
      h("div", { class: "row" }, h("label", { class: "check small" }, sim, "Показать текстовое сходство (не является связью)"),
        h("button", { class: "btn sm", text: state.graphTable ? "Показать граф" : "Показать таблицей", onclick: () => { state.graphTable = !state.graphTable; renderTab(); } }))),
    h("div", { class: "notice", style: { marginBottom: "8px" },
      text: "Узлы — материалы, а не люди. Связи — только подтверждённые публичные репосты, цитаты, ссылки и ответы; у каждой указан источник подтверждения. Подписка, реакция, комментарий или совпадение текста не считаются доказательством сотрудничества, общей идеологии или принадлежности к организации." }),
    relationLegend(state.graphSimilarity),
    h("div", { class: "legend", style: { marginTop: "4px" } }, ["insufficient_data", "moderate", "high", "critical", "very_critical", "none"].map((p) =>
      h("span", { class: "key" }, h("span", { class: `rect sw p-${p}` }), p === "none" ? "Не угроза" : state.meta.priorities[p]))));
  c.appendChild(panel);
  if (state.graphTable) {
    const nodes = new Map(data.nodes.map((n) => [n.id, n]));
    const name = (id) => { const n = nodes.get(id); return n ? (n.external ? n.source_url : `${n.platform}: ${n.source_name}`) : id; };
    panel.appendChild(h("table", { class: "data", style: { marginTop: "8px" } },
      h("thead", {}, h("tr", {}, ["Исходный материал", "Связь", "Производный материал", "Подтверждение"].map((x) => h("th", { text: x })))),
      h("tbody", {}, data.edges.map((e) => h("tr", {}, h("td", { text: name(e.src) }), h("td", { text: e.relation_label }), h("td", { text: name(e.dst) }), h("td", { text: evidenceLabel(e.evidence_type) }))))));
    return;
  }
  const wrap = h("div", { class: "graph-wrap", style: { marginTop: "8px" } });
  panel.appendChild(wrap);
  new PropagationGraph(wrap, { onOpen: openIncident }).render(data);
  panel.appendChild(h("p", { class: "small muted", text: "Колесо мыши — масштаб, перетаскивание фона — сдвиг, перетаскивание узла — закрепление. Нажмите на связь, чтобы увидеть источник подтверждения." }));
}

// ------------------------------------------------------------------ источники
function renderSources(c) {
  clear(c);
  const last = state.lastSearch;
  if (last && last.stats && last.stats.coverage) {
    const cov = last.stats.coverage;
    c.appendChild(h("div", { class: "panel", style: { marginBottom: "12px" } },
      h("h3", { text: `Охват последнего поиска ${last.id}` }),
      h("dl", { class: "kv", style: { marginTop: "8px" } },
        h("dt", { text: "Опрошены" }), h("dd", { text: cov.polled.join(", ") || "—" }),
        h("dt", { text: "Ошибки подключения" }), h("dd", { text: cov.failed.join(", ") || "нет" }),
        h("dt", { text: "Нужен список источников" }), h("dd", { text: cov.watchlist_needed.join(", ") || "—" }),
        h("dt", { text: "Не настроены" }), h("dd", { text: cov.not_configured.join(", ") || "—" }),
        h("dt", { text: "Требуют одобрения платформы" }), h("dd", { text: cov.requires_approval.join(", ") || "—" }),
        h("dt", { text: "Подключение невозможно" }), h("dd", { text: cov.unavailable.join(", ") || "—" })),
      h("p", { class: "notice", text: cov.statement }),
      h("table", { class: "data" }, h("thead", {}, h("tr", {}, ["Источник", "Статус", "Запросов", "Материалов", "Ошибки и примечания"].map((x) => h("th", { text: x })))),
        h("tbody", {}, Object.entries(last.stats.connectors || {}).map(([name, s]) => h("tr", {},
          h("td", { text: s.platform }), h("td", {}, h("span", { class: "row" }, statusIcon(s.status === "ok" ? "ok" : s.status, s.status), s.status)),
          h("td", { class: "num", text: s.requests }), h("td", { class: "num", text: s.items }),
          h("td", { class: "small", text: [...(s.errors || []), ...(s.notes || [])].join("; ") || "—" })))))));
  }
  const table = h("table", { class: "data" },
    h("thead", {}, h("tr", {}, ["Платформа", "Состояние", "Способ доступа", "Возможности", "Ограничения"].map((x) => h("th", { text: x })))),
    h("tbody", {}, state.connectors.map((cn) => h("tr", {},
      h("td", {}, h("b", { text: cn.platform }), h("div", { class: "small muted", text: cn.title }), cn.docs_url ? safeLink(cn.docs_url, "документация") : null),
      h("td", {}, h("div", { class: "row" }, statusIcon(cn.status.state, cn.status.label), h("span", { text: cn.status.label })), h("div", { class: "small muted", text: cn.status.message })),
      h("td", { class: "small", text: cn.access }),
      h("td", { class: "small", text: (cn.capabilities || []).join(", ") }),
      h("td", { class: "small" }, h("ul", { style: { margin: 0, paddingLeft: "16px" } }, (cn.limitations || []).map((l) => h("li", { text: l }))))))));
  c.appendChild(h("div", { class: "panel" }, h("h3", { text: "Подключение источников" }),
    h("p", { class: "small muted", text: "Состояние отражает фактическую конфигурацию. Неподключённые платформы не опрашиваются и не изображаются как охваченные." }),
    can("manage_settings") ? h("p", {}, h("button", { class: "btn primary", text: "Подключить источники и ключи", onclick: openSourceSettings })) : null,
    table));
}

// ------------------------------------------------------------------ анализ и импорт
function renderTools(c) {
  clear(c);
  const text = h("textarea", { placeholder: "Вставьте текст публикации или комментария для разовой оценки (не сохраняется)" });
  const out = h("div", { style: { marginTop: "10px" } });
  const analyzeBtn = h("button", { class: "btn primary", text: "Оценить текст", onclick: async () => {
    if (!text.value.trim()) return;
    try {
      const a = await api.post("/api/analyze", { text: text.value });
      clear(out).appendChild(renderAssessment(a, null));
      const observed = (a.explanation || []).filter((e) => e.kind === "fact");
      if (observed.length) out.appendChild(h("div", { class: "box fact", style: { marginTop: "10px" } }, h("h3", { text: "Наблюдаемые признаки" }),
        h("ul", { class: "small", style: { margin: 0, paddingLeft: "18px" } }, observed.map((e) => h("li", { text: e.text })))));
    } catch (ex) { toast(ex.message, true); }
  } });
  c.appendChild(h("div", { class: "panel" }, h("h3", { text: "Разовый анализ текста" }), h("div", { class: "stack", style: { marginTop: "8px" } }, text, h("div", {}, analyzeBtn)), out));

  if (!can("import")) return;
  const fmt = h("select", { style: { width: "auto" } }, h("option", { value: "json", text: "JSON" }), h("option", { value: "csv", text: "CSV" }), h("option", { value: "text", text: "Текст (материалы через пустую строку)" }));
  const basis = h("input", { type: "text", placeholder: "Основание получения: жалоба №…, запрос …, материалы предоставлены заявителем" });
  const content = h("textarea", { placeholder: '[{"text": "…", "url": "https://…", "platform": "Signal", "published_at": "2026-09-30T10:00:00Z", "source_name": "…"}]', style: { minHeight: "140px" } });
  const fileInput = h("input", { type: "file", accept: ".json,.csv,.txt" });
  fileInput.addEventListener("change", async () => {
    const f = fileInput.files[0];
    if (!f) return;
    content.value = await f.text();
    if (f.name.endsWith(".csv")) fmt.value = "csv";
    else if (f.name.endsWith(".txt")) fmt.value = "text";
    else fmt.value = "json";
  });
  const res = h("div");
  c.appendChild(h("div", { class: "panel", style: { marginTop: "12px" } },
    h("h3", { text: "Импорт материалов, правомерно предоставленных пользователем" }),
    h("p", { class: "small muted", text: "Для платформ без разрешённого интерфейса (Signal, WhatsApp, MAX, Facebook, Одноклассники) материалы добавляются только так — с указанием основания. Импорт фиксируется в журнале." }),
    h("div", { class: "stack" }, h("div", { class: "row" }, h("label", { class: "field" }, "Формат", fmt), h("label", { class: "field grow" }, "Основание (обязательно)", basis)),
      fileInput, content,
      h("div", {}, h("button", { class: "btn primary", text: "Импортировать и оценить", onclick: async () => {
        try {
          const r = await api.post("/api/import", { content: content.value, format: fmt.value, legal_basis: basis.value });
          clear(res).appendChild(h("div", { class: "notice", text: `Импортировано: ${r.counts.received}; сохранено карточек: ${r.counts.stored}; потенциальных угроз: ${r.counts.threats}.` }));
          refresh();
        } catch (ex) { toast(ex.message, true); }
      } })), res)));
}

// ------------------------------------------------------------------ журнал
async function renderAudit(c) {
  const rows = await api.get("/api/audit?limit=300");
  clear(c);
  const verifyOut = h("span", { class: "small" });
  c.appendChild(h("div", { class: "panel" },
    h("div", { class: "chart-head" }, h("h3", { text: "Журнал действий" }),
      h("div", { class: "row" }, verifyOut, h("button", { class: "btn sm", text: "Проверить целостность", onclick: async () => {
        const r = await api.get("/api/audit/verify");
        verifyOut.textContent = r.message;
        verifyOut.className = r.ok ? "small" : "small error";
      } }))),
    h("p", { class: "small muted", text: "Каждая запись связана хэш-цепочкой с предыдущей; изменение записи обнаруживается проверкой. Текст материалов в журнал не пишется." }),
    h("table", { class: "data" },
      h("thead", {}, h("tr", {}, ["Время", "Пользователь", "Роль", "Действие", "Объект", "Подробности"].map((x) => h("th", { text: x })))),
      h("tbody", {}, rows.map((r) => h("tr", {},
        h("td", { class: "small", text: fmtDate(r.ts) }), h("td", { text: r.user }), h("td", { class: "small", text: r.role || "—" }),
        h("td", { text: r.action_label }), h("td", { class: "mono", text: r.object_id || "—" }),
        h("td", { class: "small mono", text: JSON.stringify(r.details).slice(0, 220) })))))));
}

// ------------------------------------------------------------------ администрирование
function openSourceSettings() {
  state.tab = "admin";
  renderTabs();
  renderTab().then(() => {
    const el = document.getElementById("source-settings");
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

async function saveSources(values, clearList = []) {
  const r = await api.put("/api/settings/sources", { values, clear: clearList });
  state.connectors = r.connectors;
  renderSidebar();
  return r;
}

function renderSourceSettings(data) {
  const box = h("div", { class: "panel", id: "source-settings" },
    h("h3", { text: "Источники и ключи доступа" }),
    h("p", { class: "small muted", text: "Изменения применяются сразу, без перезапуска. Значения хранятся в файле .env рядом с программой. Сохранённые ключи не показываются — чтобы заменить ключ, введите новый; пустое поле оставляет прежний." }));
  const inputs = {};
  const clearSet = new Set();
  for (const g of data.groups) {
    const fieldsBox = h("div", { class: "stack" });
    for (const f of g.fields) {
      let input;
      if (f.kind === "list") {
        input = h("textarea", { rows: 3, placeholder: "по одному на строку", style: { minHeight: "70px" } });
        input.value = f.value || "";
      } else if (f.kind === "bool") {
        input = h("input", { type: "checkbox", checked: f.value === "1" || null });
      } else if (f.kind === "int") {
        input = h("input", { type: "number", min: 0, value: f.value || "", style: { width: "140px" } });
      } else {
        input = h("input", { type: f.kind === "secret" ? "password" : "text", value: f.kind === "secret" ? "" : (f.value || ""),
          autocomplete: "off", placeholder: f.kind === "secret" ? (f.is_set ? `задан (${f.hint}) — введите новый, чтобы заменить` : "не задан") : "" });
      }
      inputs[f.name] = { input, field: f };
      const clearBtn = f.kind === "secret" && f.is_set
        ? h("button", { class: "btn sm ghost", type: "button", text: "удалить ключ", onclick: (e) => {
          clearSet.add(f.name); e.target.textContent = "будет удалён при сохранении"; e.target.disabled = true;
        } }) : null;
      fieldsBox.appendChild(f.kind === "bool"
        ? h("label", { class: "check" }, input, h("span", {}, h("b", { text: f.label }), f.help ? h("div", { class: "small muted", text: f.help }) : null))
        : h("label", { class: "field" }, h("span", { class: "row" }, h("b", { text: f.label }), clearBtn), input,
          f.help ? h("span", { class: "small muted", text: f.help }) : null));
    }
    box.appendChild(details(g.label, g.id === "watch", fieldsBox));
  }
  const status = h("span", { class: "small muted" });
  box.appendChild(h("div", { class: "row", style: { marginTop: "12px" } },
    h("button", { class: "btn primary", text: "Сохранить и применить", onclick: async () => {
      const values = {};
      for (const [name, { input, field }] of Object.entries(inputs)) {
        values[name] = field.kind === "bool" ? (input.checked ? "1" : "0") : input.value;
      }
      try {
        const r = await saveSources(values, [...clearSet]);
        status.textContent = r.changed.length ? `Сохранено: ${r.changed.length} настроек. Источники обновлены.` : "Изменений нет.";
        toast("Настройки источников применены");
        renderTab();
      } catch (ex) { toast(ex.message, true); }
    } }), status));
  return box;
}

function renderOnboarding() {
  const ready = state.connectors.filter((c) => c.status.state === "connected" && !["manual_import", "web_url"].includes(c.name));
  const box = h("div", { class: "panel" },
    h("h2", { text: "Начало работы" }),
    h("p", { class: "muted", text: "База пока пуста. Чтобы найти материалы, подключите источники и выполните поиск." }));
  box.appendChild(h("h4", { style: { margin: "12px 0 6px" }, text: "1. Уже работают без настройки" }));
  box.appendChild(h("div", { class: "chips" }, ready.length
    ? ready.map((c) => h("span", { class: "pill" }, statusIcon("connected", "подключён"), c.platform))
    : h("span", { class: "muted small", text: "нет — добавьте источники ниже" })));
  box.appendChild(h("h4", { style: { margin: "14px 0 6px" }, text: "2. Добавьте публичные Telegram-каналы для наблюдения" }));
  if (can("manage_settings")) {
    const ta = h("textarea", { rows: 4, placeholder: "@channel или https://t.me/channel — по одному на строку" });
    const msg = h("span", { class: "small muted" });
    box.append(ta, h("div", { class: "row", style: { marginTop: "6px" } },
      h("button", { class: "btn primary", text: "Сохранить каналы", onclick: async () => {
        if (!ta.value.trim()) return;
        try {
          const current = (await api.get("/api/settings/sources")).groups.flatMap((g) => g.fields).find((f) => f.name === "DOZOR_TELEGRAM_CHANNELS");
          const merged = [current && current.value, ta.value].filter(Boolean).join("\n");
          await saveSources({ DOZOR_TELEGRAM_CHANNELS: merged });
          msg.textContent = "Каналы сохранены — Telegram подключён.";
          ta.value = "";
          toast("Telegram-каналы добавлены");
        } catch (ex) { toast(ex.message, true); }
      } }), msg),
      h("p", { class: "small muted", text: "Можно без списка: укажите ссылку на канал в поле «Ссылка на материал или публичный канал» в панели поиска." }));
    box.appendChild(h("h4", { style: { margin: "14px 0 6px" }, text: "3. При необходимости — ключи официальных API" }));
    box.appendChild(h("p", { class: "small muted", text: "VK, YouTube, Instagram, Threads, TikTok, Twitch, Discord, X, Bluesky, второй классификатор (LLM)." }));
    box.appendChild(h("button", { class: "btn", text: "Открыть настройки источников", onclick: openSourceSettings }));
  } else {
    box.appendChild(h("p", { class: "small muted", text: "Источники подключает администратор (Администрирование → Источники и ключи доступа)." }));
  }
  box.appendChild(h("h4", { style: { margin: "14px 0 6px" }, text: "4. Выполните поиск" }));
  box.appendChild(h("p", { class: "small", text: "Введите слово в панели «Поиск» — например, «убить» — и нажмите «Найти». Слово автоматически ищется на 16 языках с транслитерацией. Можно выбрать тему вместо слов." }));
  return box;
}

async function renderAdmin(c) {
  const [ret, users, src] = await Promise.all([
    api.get("/api/settings/retention"), can("manage_users") ? api.get("/api/users") : [], api.get("/api/settings/sources"),
  ]);
  clear(c);
  c.appendChild(renderSourceSettings(src));
  const inputs = {};
  const retTable = h("div", { class: "stack" }, Object.entries(ret.labels).map(([k, label]) => {
    inputs[k] = h("input", { type: "number", min: 1, max: 3650, value: ret.values[k], style: { width: "110px" } });
    return h("label", { class: "row" }, h("span", { class: "grow", text: label }), inputs[k], h("span", { class: "small muted", text: "дней" }));
  }));
  c.appendChild(h("div", { class: "panel", style: { marginTop: "12px" } }, h("h3", { text: "Сроки хранения данных" }),
    h("p", { class: "small muted", text: "Данные с истёкшим сроком удаляются автоматически каждый час (кроме карточек с legal hold). Сокращайте сроки до минимально необходимых." }),
    retTable,
    h("div", { class: "row", style: { marginTop: "10px" } },
      h("button", { class: "btn primary", text: "Сохранить сроки", onclick: async () => {
        const values = Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, Number(el.value)]));
        try { await api.put("/api/settings/retention", { values }); toast("Сроки сохранены"); } catch (ex) { toast(ex.message, true); }
      } }),
      can("purge") ? h("button", { class: "btn", text: "Удалить просроченные данные сейчас", onclick: async () => {
        const r = await api.post("/api/retention/purge");
        toast(`Удалено карточек: ${r.incidents}, поисков: ${r.searches}, записей журнала: ${r.audit_entries}`);
        refresh();
      } }) : null,
      !state.meta.demo_enabled ? null : h("button", { class: "btn", text: "Загрузить демо-данные", onclick: async () => {
        const r = await api.post("/api/demo/load");
        toast(`Демо: сохранено карточек ${r.counts.stored}`);
        refresh();
      } }))));
  if (!can("manage_users")) return;
  const uname = h("input", { type: "text", placeholder: "Имя" });
  const upass = h("input", { type: "password", placeholder: "Пароль (≥ 10 символов)", autocomplete: "new-password" });
  const urole = h("select", {}, Object.entries(state.meta.roles).map(([k, v]) => h("option", { value: k, text: v })));
  c.appendChild(h("div", { class: "panel", style: { marginTop: "12px" } }, h("h3", { text: "Пользователи и роли" }),
    h("table", { class: "data" }, h("thead", {}, h("tr", {}, ["Имя", "Роль", "Активен", "Создан", ""].map((x) => h("th", { text: x })))),
      h("tbody", {}, users.map((u) => h("tr", {}, h("td", { text: u.username }), h("td", { text: state.meta.roles[u.role] }),
        h("td", { text: u.active ? "да" : "нет" }), h("td", { class: "small", text: fmtDate(u.created_at) }),
        h("td", {}, u.id === state.user.id ? null : h("button", { class: "btn sm", text: u.active ? "Отключить" : "Включить", onclick: async () => {
          try { await api.patch(`/api/users/${u.id}`, { active: !u.active }); renderTab(); } catch (ex) { toast(ex.message, true); }
        } })))))),
    h("div", { class: "row", style: { marginTop: "10px" } }, uname, upass, urole,
      h("button", { class: "btn primary", text: "Создать", onclick: async () => {
        try { await api.post("/api/users", { username: uname.value, password: upass.value, role: urole.value }); toast("Пользователь создан"); renderTab(); }
        catch (ex) { toast(ex.message, true); }
      } }))));
}

// ------------------------------------------------------------------ запуск поиска и оверлей
async function startSearch() {
  const s = state.search;
  let res;
  try {
    res = await api.post("/api/search", {
      query: s.query, topics: s.topics, languages: s.languages, mode: s.mode, date_from: s.date_from || null, date_to: s.date_to || null,
      countries: s.countries, material_langs: s.material_langs, source_url: s.source_url, connectors: s.connectors,
      include_demo: s.include_demo, purpose: s.purpose,
    });
  } catch (ex) {
    toast(ex.message, true);
    return;
  }
  openSearchOverlay(res.search_id, res.warnings || []);
}

function openSearchOverlay(searchId, warnings) {
  const canvas = h("canvas", { width: 360, height: 360, "aria-hidden": "true" });
  const stageRows = {};
  const stagesList = h("div");
  for (const st of STAGE_ORDER) {
    const icon = statusIcon("pending", "ожидание");
    const msg = h("div", { class: "msg" });
    const time = h("span", { class: "time" });
    const sub = h("ul", { class: "substages" });
    const row = h("div", { class: "stage" }, icon, h("div", {}, h("div", { text: stageLabel(st) }), msg, sub), time);
    stageRows[st] = { row, icon, msg, time, sub, status: "pending" };
    stagesList.appendChild(row);
  }
  const log = h("div", { class: "log", role: "log", "aria-label": "Журнал этапов" });
  const summary = h("div");
  const closeBtn = h("button", { class: "btn", text: "Скрыть (поиск продолжится)", onclick: () => close() });
  const pane = h("div", { class: "stages-pane" },
    h("div", { class: "row" }, h("h2", { text: "Поиск и анализ" }), h("span", { class: "grow" }), h("span", { class: "mono muted", text: searchId })),
    warnings.map((w) => h("div", { class: "notice warn", text: w })),
    stagesList, h("h4", { text: "Ход обработки" }), log, summary, h("div", { class: "row" }, closeBtn));
  const overlay = h("div", { class: "overlay", role: "dialog", "aria-modal": "true", "aria-label": "Ход поиска" },
    h("div", { class: "search-modal" },
      h("div", { class: "globe-pane" }, canvas,
        h("div", { class: "globe-caption", text: "Глобус — индикатор процесса. Спутники — подключённые источники, цвет — фактическое состояние опроса. Точки не означают местоположение пользователей или публикаций." })),
      pane));
  document.body.appendChild(overlay);
  const globe = new Globe(canvas);
  globe.start();
  const subRows = {};

  function setStage(st, status, message, elapsed) {
    const r = stageRows[st];
    if (!r) return;
    const map = { start: "running", progress: "running", done: "done", warn: r.status === "done" ? "done" : "running", error: "error" };
    let next = map[status] || status;
    if (st === "fetch" && status === "error") next = r.status; // ошибка одного источника не прерывает этап
    if (r.status === "error" && st !== "fetch") next = "error";
    r.status = next;
    const ic = statusIcon(next === "done" && r.warned ? "warn" : next, next);
    r.row.replaceChild(ic, r.icon);
    r.icon = ic;
    r.row.classList.toggle("running", next === "running");
    if (message && status !== "warn") r.msg.textContent = message;
    if (status === "warn" || (status === "error" && st === "fetch")) r.warned = true;
    if (elapsed !== undefined) r.time.textContent = `${elapsed.toFixed(1)} с`;
  }

  function onEvent(ev) {
    const line = h("div", {}, `${ev.elapsed.toFixed(1)} с · ${stageLabel(ev.stage)}: `, ev.message);
    if (ev.status === "error") line.className = "error";
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
    if (ev.stage === "plan" && ev.status === "done") {
      const active = ev.active || [];
      globe.setSatellites(active.map((name) => {
        const cn = state.connectors.find((x) => x.name === name);
        return { id: name, label: cn ? cn.platform : name };
      }));
      clear(stageRows.fetch.sub);
      for (const name of active) {
        const cn = state.connectors.find((x) => x.name === name);
        const ic = statusIcon("pending", "ожидание");
        const text = h("span", { text: `${cn ? cn.platform : name}: ожидание` });
        const li = h("li", {}, ic, text);
        subRows[name] = { li, ic, text, label: cn ? cn.platform : name };
        stageRows.fetch.sub.appendChild(li);
      }
      const skipped = (ev.plan || []).filter((p) => p.action !== "будет опрошен" && p.action !== "не выбран" && p.connector !== "manual_import");
      if (skipped.length) {
        stageRows.plan.sub.appendChild(h("li", {}, h("span", { text: "–" }),
          h("span", { text: `Не опрашиваются: ${skipped.map((p) => `${p.platform} — ${p.action.replace(/^пропущен: /, "")}`).join("; ")}` })));
      }
    }
    if (ev.stage === "fetch" && ev.connector && subRows[ev.connector]) {
      const sr = subRows[ev.connector];
      let st = { start: "running", progress: "running", done: ev.connector_status || "ok", error: "error", warn: "warn" }[ev.status] || "running";
      if (ev.status === "warn") st = "running";
      const glob = { running: "running", ok: "ok", partial: "partial", error: "error" }[st];
      if (glob) globe.setStatus(ev.connector, glob);
      const ic = statusIcon(st === "running" ? "running" : st, st);
      sr.li.replaceChild(ic, sr.ic);
      sr.ic = ic;
      sr.text.textContent = `${sr.label}: ${ev.message.replace(`${sr.label}: `, "")}`;
    }
    setStage(ev.stage, ev.status, ev.message, ev.elapsed);
    if (ev.stage === "done") {
      for (const st of STAGE_ORDER) if (stageRows[st].status === "pending") setStage(st, "done", "—");
      globe.finish();
      finishSummary(ev);
    }
  }

  function finishSummary(ev) {
    clear(summary);
    const counts = ev.counts || {};
    const cov = ev.coverage || {};
    add(summary, 
      h("div", { class: ev.status === "error" ? "notice warn" : "notice", text: ev.message }),
      cov.statement ? h("div", { class: "small muted", style: { marginTop: "6px" }, text: cov.statement }) : null,
      (cov.not_configured || []).length || (cov.requires_approval || []).length || (cov.unavailable || []).length
        ? h("div", { class: "small muted", style: { marginTop: "4px" },
          text: `Не охвачены: ${[...(cov.watchlist_needed || []), ...(cov.not_configured || []), ...(cov.requires_approval || []), ...(cov.unavailable || [])].join(", ")}.` }) : null,
      h("div", { class: "row", style: { marginTop: "8px" } },
        counts.stored !== undefined ? h("button", { class: "btn primary", text: `Показать найденные карточки (${counts.stored})`, onclick: () => {
          state.filters = { ...defaultFilters(), search_id: searchId, include_non_threat: false };
          state.tab = "incidents";
          close();
          renderFilters();
          renderTabs();
          refresh();
        } }) : null));
    closeBtn.textContent = "Закрыть";
    api.get(`/api/search/${searchId}`).then((s) => { state.lastSearch = s; }).catch(() => {});
  }

  let es = null;
  let pollTimer = null;
  let seen = 0;
  function close() {
    globe.stop();
    if (es) es.close();
    clearInterval(pollTimer);
    overlay.remove();
    refresh();
  }
  if (window.EventSource) {
    es = new EventSource(`/api/search/${encodeURIComponent(searchId)}/events`);
    es.onmessage = (m) => { const ev = JSON.parse(m.data); seen = Math.max(seen, ev.seq); onEvent(ev); };
    es.addEventListener("end", () => es.close());
    es.onerror = () => { es.close(); startPolling(); };
  } else startPolling();

  function startPolling() {
    clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      try {
        const s = await api.get(`/api/search/${encodeURIComponent(searchId)}`);
        for (const ev of s.events.filter((e) => e.seq > seen)) { seen = ev.seq; onEvent(ev); }
        if (s.status !== "running") clearInterval(pollTimer);
      } catch (_) { clearInterval(pollTimer); }
    }, 800);
  }
}

function stageLabel(st) {
  return {
    validate: "Проверка запроса", expand: "Многоязычное расширение", plan: "План опроса источников", fetch: "Опрос источников",
    normalize: "Нормализация и язык", filter: "Совпадения и фильтры", dedup: "Устранение дублей", classify: "Оценка содержания",
    propagation: "Карта распространения", store: "Сохранение карточек", done: "Итог",
  }[st] || st;
}

boot();
