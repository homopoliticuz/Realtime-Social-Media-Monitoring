// Временная шкала: столбцы по дням, сегменты — уровни приоритета (порядковая шкала).

import { PRIORITY_VAR, clear, cssVar, h, hideTooltip, showTooltip, svg } from "./ui.js";

// Снизу вверх: самые важные у базовой линии
const STACK = ["very_critical", "critical", "high", "moderate", "insufficient_data", "none"];

export function renderTimeline(container, days, labels, { onDay, showTable } = {}) {
  clear(container);
  if (!days.length) {
    container.appendChild(h("div", { class: "empty", text: "Нет данных для выбранных фильтров." }));
    return;
  }
  const present = STACK.filter((k) => days.some((d) => d[k]));
  const legend = h("div", { class: "legend", "aria-label": "Легенда" },
    present.map((k) => h("span", { class: "key" },
      h("span", { class: "rect", style: { background: `var(${PRIORITY_VAR[k]})` } }), labels[k] || "Не угроза")));
  container.appendChild(legend);

  if (showTable) {
    const table = h("table", { class: "data" },
      h("thead", {}, h("tr", {}, h("th", { text: "Дата" }), present.map((k) => h("th", { class: "num", text: labels[k] || "Не угроза" })),
        h("th", { class: "num", text: "Всего" }))),
      h("tbody", {}, days.map((d) => h("tr", {}, h("td", { text: d.day }),
        present.map((k) => h("td", { class: "num", text: d[k] || 0 })), h("td", { class: "num", text: d.total })))));
    container.appendChild(table);
    return;
  }

  const wrap = h("div", { class: "chart" });
  container.appendChild(wrap);
  const width = Math.max(320, wrap.clientWidth || container.clientWidth || 800);
  const height = 260;
  const m = { top: 14, right: 12, bottom: 30, left: 36 };
  const iw = width - m.left - m.right;
  const ih = height - m.top - m.bottom;
  const max = Math.max(...days.map((d) => d.total));
  const step = niceStep(max);
  const top = Math.max(step, Math.ceil(max / step) * step);
  const y = (v) => m.top + ih - (v / top) * ih;
  const band = iw / days.length;
  const bw = Math.min(24, Math.max(4, band * 0.6));
  const gap = 2;

  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Количество материалов по дням и приоритету" });
  const grid = svg("g", { class: "grid" });
  for (let v = 0; v <= top; v += step) {
    grid.appendChild(svg("line", { x1: m.left, x2: width - m.right, y1: y(v), y2: y(v) }));
    root.appendChild(svg("text", { x: m.left - 6, y: y(v) + 4, "text-anchor": "end", text: String(v) }));
  }
  root.insertBefore(grid, root.firstChild);
  root.appendChild(svg("line", { x1: m.left, x2: width - m.right, y1: y(0), y2: y(0), stroke: cssVar("--axis") }));

  const labelEvery = Math.ceil(days.length / Math.max(1, Math.floor(iw / 56)));
  days.forEach((d, i) => {
    const cx = m.left + band * i + band / 2;
    let acc = 0;
    const segs = STACK.filter((k) => d[k]);
    const g = svg("g", { tabindex: 0, role: "button", "aria-label": `${d.day}: всего ${d.total}` });
    segs.forEach((k, j) => {
      const v = d[k];
      const y0 = y(acc);
      const y1 = y(acc + v);
      acc += v;
      const isTop = j === segs.length - 1;
      const hgt = Math.max(1, y0 - y1 - (isTop ? 0 : gap));
      const yTop = isTop ? y1 : y1 + gap;
      const x = cx - bw / 2;
      const color = cssVar(PRIORITY_VAR[k]);
      const path = isTop ? roundedTop(x, yTop, bw, hgt, 4) : `M${x},${yTop}h${bw}v${hgt}h${-bw}Z`;
      g.appendChild(svg("path", { d: path, fill: color, class: "seg" }));
    });
    // Зона попадания шире столбца
    const hit = svg("rect", { x: m.left + band * i, y: m.top, width: band, height: ih, fill: "transparent" });
    g.appendChild(hit);
    const show = (evt) => showTooltip(evt, (t) => {
      t.appendChild(h("div", { class: "t-title", text: `${d.day} · всего ${d.total}` }));
      STACK.filter((k) => d[k]).forEach((k) => t.appendChild(h("div", { class: "t-row" },
        h("span", { class: "k", style: { borderTopColor: `var(${PRIORITY_VAR[k]})` } }),
        h("span", { class: "v", text: d[k] }), h("span", { text: labels[k] || "Не угроза" }))));
      t.appendChild(h("div", { class: "small muted", text: "Нажмите, чтобы показать карточки за день" }));
    });
    g.addEventListener("pointermove", show);
    g.addEventListener("pointerleave", hideTooltip);
    g.addEventListener("focus", (e) => {
      const r = e.target.getBoundingClientRect();
      show({ clientX: r.left + r.width / 2, clientY: r.top });
    });
    g.addEventListener("blur", hideTooltip);
    g.addEventListener("click", () => onDay && onDay(d.day));
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" && onDay) onDay(d.day); });
    root.appendChild(g);
    if (i % labelEvery === 0) {
      const [yy, mm, dd] = d.day.split("-");
      root.appendChild(svg("text", { x: cx, y: height - 10, "text-anchor": "middle", text: `${dd}.${mm}` }));
    }
  });
  wrap.appendChild(root);
}

function roundedTop(x, y, w, hgt, r) {
  const rr = Math.min(r, w / 2, hgt);
  return `M${x},${y + hgt}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + hgt}Z`;
}

function niceStep(max) {
  if (max <= 5) return 1;
  const raw = max / 5;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const n = raw / pow;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * pow;
}
