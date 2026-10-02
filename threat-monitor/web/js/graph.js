// Карта распространения: узлы — материалы, рёбра — подтверждённые связи
// (репост, цитата, ссылка, ответ) с источником подтверждения.
// Текстовое сходство показывается только по запросу и пунктиром — это не связь.
// Сеть личных контактов и рейтинги людей не строятся.

import { PRIORITY_VAR, clear, cssVar, fmtDate, h, hideTooltip, showTooltip, svg } from "./ui.js";

const REL_VAR = { repost: "--rel-repost", quote: "--rel-quote", link: "--rel-link", reply: "--rel-reply", text_similarity: "--muted" };
const EVIDENCE_LABELS = {
  telegram_forward_header: "Telegram: поле «Переслано из» в публичном предпросмотре/API",
  telegram_reply_header: "Telegram: ссылка «ответ на сообщение»",
  vk_copy_history: "VK API: поле copy_history (репост)",
  bluesky_embed_record: "Bluesky: встроенная запись (embed.record)",
  bluesky_reply_ref: "Bluesky: ссылка reply.parent",
  mastodon_reblog: "Mastodon: поле reblog",
  x_referenced_tweets: "X API: referenced_tweets",
  discord_message_reference: "Discord: message_reference (ответ)",
  discord_forward_reference: "Discord: message_reference (пересылка)",
  youtube_comment_thread: "YouTube: комментарий к видео",
  url_in_text: "Ссылка на материал в тексте публикации",
  demo_forward_header: "ДЕМО: поле «Переслано из» (вымышленные данные)",
  demo_copy_history: "ДЕМО: поле репоста (вымышленные данные)",
  simhash_jaccard: "Совпадение текста (SimHash + Жаккар) — НЕ доказательство связи",
};

export function evidenceLabel(type) {
  return EVIDENCE_LABELS[type] || type;
}

export class PropagationGraph {
  constructor(container, { onOpen } = {}) {
    this.container = container;
    this.onOpen = onOpen;
    this.view = { x: 0, y: 0, k: 1 };
  }

  render(data) {
    clear(this.container);
    this.data = data;
    const nodes = data.nodes.map((n) => ({ ...n }));
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const edges = data.edges.filter((e) => byId.has(e.src) && byId.has(e.dst)).map((e) => ({ ...e, s: byId.get(e.src), t: byId.get(e.dst) }));
    if (!nodes.length) {
      this.container.appendChild(h("div", { class: "empty", text: "Подтверждённых связей между материалами пока нет." }));
      return;
    }
    const W = this.container.clientWidth || 800;
    const H = this.container.clientHeight || 560;
    nodes.forEach((n, i) => {
      const a = (i / nodes.length) * Math.PI * 2;
      n.x = W / 2 + Math.cos(a) * Math.min(W, H) * 0.3 + (Math.random() - 0.5) * 20;
      n.y = H / 2 + Math.sin(a) * Math.min(W, H) * 0.3 + (Math.random() - 0.5) * 20;
      n.vx = 0;
      n.vy = 0;
    });
    this.nodes = nodes;
    this.edges = edges;

    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Граф распространения материалов" });
    const defs = svg("defs");
    for (const [rel, v] of Object.entries(REL_VAR)) {
      defs.appendChild(svg("marker", { id: `arrow-${rel}`, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" },
        svg("path", { d: "M0,0 L10,5 L0,10 z", fill: cssVar(v) })));
    }
    root.appendChild(defs);
    const scene = svg("g");
    root.appendChild(scene);
    const edgeLayer = svg("g");
    const nodeLayer = svg("g");
    scene.appendChild(edgeLayer);
    scene.appendChild(nodeLayer);
    this.container.appendChild(root);
    this.info = h("div", { class: "graph-info hidden" });
    this.container.appendChild(this.info);

    for (const e of edges) {
      const color = cssVar(REL_VAR[e.relation] || "--muted");
      e.el = svg("path", {
        class: `edge${e.relation === "text_similarity" ? " sim" : ""}`,
        stroke: color,
        "marker-end": e.relation === "text_similarity" ? null : `url(#arrow-${e.relation})`,
      });
      e.hit = svg("path", { class: "edge-hit", tabindex: 0 });
      const show = (evt) => this.edgeTooltip(evt, e);
      e.hit.addEventListener("pointermove", show);
      e.hit.addEventListener("pointerleave", hideTooltip);
      e.hit.addEventListener("click", () => this.showEdge(e));
      e.hit.addEventListener("keydown", (k) => { if (k.key === "Enter") this.showEdge(e); });
      edgeLayer.appendChild(e.el);
      edgeLayer.appendChild(e.hit);
    }
    const showLabels = nodes.length <= 40;
    for (const n of nodes) {
      const fill = n.external ? cssVar("--surface") : cssVar(PRIORITY_VAR[n.is_threat ? n.priority || "none" : "none"]);
      n.el = svg("g", { class: `node${n.external ? " external" : ""}`, tabindex: 0, role: "button",
        "aria-label": n.external ? `Внешний материал ${n.label}` : `${n.platform}: ${n.source_name}` });
      n.el.appendChild(svg("circle", { r: 14, fill: "transparent", stroke: "none" }));
      n.el.appendChild(svg("circle", { r: n.is_copy ? 6 : 8, fill }));
      if (showLabels) {
        n.el.appendChild(svg("text", { x: 12, y: 4, text: n.external ? "внешняя ссылка" : `${n.platform} · ${(n.source_name || "").slice(0, 28)}` }));
      }
      n.el.addEventListener("pointermove", (evt) => this.nodeTooltip(evt, n));
      n.el.addEventListener("pointerleave", hideTooltip);
      n.el.addEventListener("click", () => { if (!this.dragMoved && n.incident_id && this.onOpen) this.onOpen(n.incident_id); });
      n.el.addEventListener("keydown", (k) => { if (k.key === "Enter" && n.incident_id && this.onOpen) this.onOpen(n.incident_id); });
      this.enableDrag(n, root);
      nodeLayer.appendChild(n.el);
    }
    this.root = root;
    this.scene = scene;
    this.W = W;
    this.H = H;
    this.enablePanZoom(root);
    this.simulate();
  }

  simulate() {
    const nodes = this.nodes;
    const edges = this.edges;
    let alpha = 1;
    const tick = () => {
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i];
          const b = nodes[j];
          let dx = b.x - a.x;
          let dy = b.y - a.y;
          let d2 = dx * dx + dy * dy || 0.01;
          const f = (1600 / d2) * alpha;
          const d = Math.sqrt(d2);
          dx /= d;
          dy /= d;
          a.vx -= dx * f; a.vy -= dy * f;
          b.vx += dx * f; b.vy += dy * f;
        }
      }
      for (const e of edges) {
        const dx = e.t.x - e.s.x;
        const dy = e.t.y - e.s.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const rest = e.relation === "text_similarity" ? 140 : 110;
        const f = (d - rest) * 0.04 * alpha;
        e.s.vx += (dx / d) * f; e.s.vy += (dy / d) * f;
        e.t.vx -= (dx / d) * f; e.t.vy -= (dy / d) * f;
      }
      for (const n of nodes) {
        n.vx += (this.W / 2 - n.x) * 0.006 * alpha;
        n.vy += (this.H / 2 - n.y) * 0.006 * alpha;
        if (n.fixed) { n.vx = 0; n.vy = 0; continue; }
        n.vx *= 0.82; n.vy *= 0.82;
        n.x += Math.max(-20, Math.min(20, n.vx));
        n.y += Math.max(-20, Math.min(20, n.vy));
      }
      this.paint();
      alpha *= 0.985;
      if (alpha > 0.02) this.raf = requestAnimationFrame(tick);
    };
    cancelAnimationFrame(this.raf);
    this.raf = requestAnimationFrame(tick);
  }

  paint() {
    for (const e of this.edges) {
      const dx = e.t.x - e.s.x;
      const dy = e.t.y - e.s.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const r = (e.t.is_copy ? 6 : 8) + 4;
      const ex = e.t.x - (dx / d) * r;
      const ey = e.t.y - (dy / d) * r;
      const dpath = `M${e.s.x},${e.s.y} L${ex},${ey}`;
      e.el.setAttribute("d", dpath);
      e.hit.setAttribute("d", dpath);
    }
    for (const n of this.nodes) n.el.setAttribute("transform", `translate(${n.x},${n.y})`);
  }

  enableDrag(n, root) {
    n.el.addEventListener("pointerdown", (evt) => {
      evt.stopPropagation();
      this.dragMoved = false;
      n.fixed = true;
      const pt = this.toScene(root, evt);
      const off = { x: n.x - pt.x, y: n.y - pt.y };
      const move = (e) => {
        const p = this.toScene(root, e);
        n.x = p.x + off.x;
        n.y = p.y + off.y;
        this.dragMoved = true;
        this.paint();
      };
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        setTimeout(() => { this.dragMoved = false; }, 0);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
  }

  toScene(root, evt) {
    const r = root.getBoundingClientRect();
    const sx = ((evt.clientX - r.left) / r.width) * this.W;
    const sy = ((evt.clientY - r.top) / r.height) * this.H;
    return { x: (sx - this.view.x) / this.view.k, y: (sy - this.view.y) / this.view.k };
  }

  enablePanZoom(root) {
    const apply = () => this.scene.setAttribute("transform", `translate(${this.view.x},${this.view.y}) scale(${this.view.k})`);
    root.addEventListener("wheel", (evt) => {
      evt.preventDefault();
      const r = root.getBoundingClientRect();
      const sx = ((evt.clientX - r.left) / r.width) * this.W;
      const sy = ((evt.clientY - r.top) / r.height) * this.H;
      const k = Math.max(0.3, Math.min(4, this.view.k * (evt.deltaY < 0 ? 1.12 : 1 / 1.12)));
      this.view.x = sx - ((sx - this.view.x) * k) / this.view.k;
      this.view.y = sy - ((sy - this.view.y) * k) / this.view.k;
      this.view.k = k;
      apply();
    }, { passive: false });
    root.addEventListener("pointerdown", (evt) => {
      const start = { x: evt.clientX, y: evt.clientY, vx: this.view.x, vy: this.view.y };
      const r = root.getBoundingClientRect();
      const move = (e) => {
        this.view.x = start.vx + ((e.clientX - start.x) / r.width) * this.W;
        this.view.y = start.vy + ((e.clientY - start.y) / r.height) * this.H;
        apply();
      };
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
  }

  nodeTooltip(evt, n) {
    showTooltip(evt, (t) => {
      if (n.external) {
        t.appendChild(h("div", { class: "t-title", text: "Материал вне базы (только ссылка)" }));
        t.appendChild(h("div", { class: "mono", text: n.source_url }));
        return;
      }
      t.appendChild(h("div", { class: "t-title", text: `${n.platform} · ${fmtDate(n.published_at)}${n.is_demo ? " · ДЕМО" : ""}` }));
      t.appendChild(h("div", { text: n.source_name || "" }));
      if (n.category_label) t.appendChild(h("div", { class: "small", text: `Оценка: ${n.category_label}` }));
      t.appendChild(h("div", { class: "small muted", text: n.excerpt || "" }));
      if (n.is_copy) t.appendChild(h("div", { class: "small muted", text: "Копия материала (точный дубль)" }));
    });
  }

  edgeTooltip(evt, e) {
    showTooltip(evt, (t) => {
      t.appendChild(h("div", { class: "t-row" },
        h("span", { class: "k", style: { borderTopColor: `var(${REL_VAR[e.relation] || "--muted"})` } }),
        h("span", { class: "v", text: e.relation_label }), h("span")));
      t.appendChild(h("div", { class: "small", text: `Подтверждение: ${evidenceLabel(e.evidence_type)}` }));
      t.appendChild(h("div", { class: "small muted", text: "Нажмите, чтобы закрепить сведения" }));
    });
  }

  showEdge(e) {
    clear(this.info);
    this.info.classList.remove("hidden");
    this.info.appendChild(h("div", { class: "row" },
      h("b", { text: e.relation_label }), h("span", { class: "grow" }),
      h("button", { class: "btn sm ghost", text: "×", "aria-label": "Закрыть", onclick: () => this.info.classList.add("hidden") })));
    this.info.appendChild(h("dl", { class: "kv" },
      h("dt", { text: "Источник подтверждения" }), h("dd", { text: evidenceLabel(e.evidence_type) }),
      h("dt", { text: "Сведения" }), h("dd", { text: e.evidence_detail || "—" }),
      h("dt", { text: "Где зафиксировано" }), h("dd", {}, e.evidence_url && /^https?:/.test(e.evidence_url)
        ? h("a", { href: e.evidence_url, target: "_blank", rel: "noopener noreferrer nofollow", text: e.evidence_url }) : (e.evidence_url || "—")),
      h("dt", { text: "Зафиксировано" }), h("dd", { text: fmtDate(e.observed_at) }),
      h("dt", { text: "Доказательная сила" }), h("dd", {
        text: e.is_evidence ? "Связь между материалами подтверждена полем платформы или ссылкой. Это не доказательство сотрудничества, общей идеологии или принадлежности к организации."
          : "Не является доказательством связи: совпадение текста может быть случайным или результатом независимого копирования.",
      })));
  }
}

export function relationLegend(includeSimilarity) {
  const items = [
    ["repost", "Репост / пересылка"],
    ["quote", "Цитирование"],
    ["link", "Ссылка на материал"],
    ["reply", "Ответ"],
  ];
  const legend = h("div", { class: "legend" },
    items.map(([rel, label]) => h("span", { class: "key" }, h("span", { class: "line", style: { borderTopColor: `var(${REL_VAR[rel]})` } }), label)));
  if (includeSimilarity) {
    legend.appendChild(h("span", { class: "key" }, h("span", { class: "line dashed", style: { borderTopColor: "var(--muted)" } }), "Текстовое сходство (не связь)"));
  }
  legend.appendChild(h("span", { class: "key" }, h("span", { class: "rect", style: { background: "var(--surface)", border: "1px dashed var(--muted)", borderRadius: "50%" } }), "Материал вне базы"));
  return legend;
}
