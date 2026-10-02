// Анимированный глобус — индикатор процесса поиска.
// Точки суши — статичная карта мира (Natural Earth 110m). Спутники на орбите —
// подключённые источники; их цвет отражает фактическое состояние опроса.
// Глобус НЕ показывает местоположение пользователей или публикаций.

const STATUS_COLORS = {
  pending: "#6b6a65",
  running: "#3987e5",
  ok: "#0ca30c",
  partial: "#fab219",
  error: "#d03b3b",
  skip: "#6b6a65",
};

let landDots = null;
async function loadDots() {
  if (landDots) return landDots;
  try {
    const resp = await fetch("/static/data/land-dots.json");
    landDots = await resp.json();
  } catch (_) {
    landDots = [];
  }
  return landDots;
}

const RAD = Math.PI / 180;

export class Globe {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.lon0 = 66; // начальный вид — Центральная Азия
    this.lat0 = 28;
    this.satellites = [];
    this.running = false;
    this.done = false;
    this.reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.t0 = performance.now();
    this.resize();
  }

  resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const size = this.canvas.clientWidth || 360;
    this.canvas.width = size * dpr;
    this.canvas.height = size * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.size = size;
  }

  setSatellites(list) {
    // list: [{id, label}] — источники, которые будут опрошены
    this.satellites = list.map((s, i) => ({ ...s, status: "pending", phase: (i / Math.max(1, list.length)) * Math.PI * 2 }));
  }

  setStatus(id, status) {
    const s = this.satellites.find((x) => x.id === id);
    if (s) s.status = status;
  }

  async start() {
    await loadDots();
    this.running = true;
    const loop = (now) => {
      if (!this.running) return;
      this.draw(now);
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }

  stop() {
    this.running = false;
  }

  finish() {
    this.done = true;
  }

  project(lon, lat, R, cx, cy) {
    const l = (lon - this.lon0) * RAD;
    const p = lat * RAD;
    const p0 = this.lat0 * RAD;
    const cosc = Math.sin(p0) * Math.sin(p) + Math.cos(p0) * Math.cos(p) * Math.cos(l);
    const x = cx + R * Math.cos(p) * Math.sin(l);
    const y = cy - R * (Math.cos(p0) * Math.sin(p) - Math.sin(p0) * Math.cos(p) * Math.cos(l));
    return [x, y, cosc];
  }

  draw(now) {
    const ctx = this.ctx;
    const S = this.size;
    const cx = S / 2;
    const cy = S / 2;
    const R = S * 0.31;
    const t = (now - this.t0) / 1000;
    const speed = this.reduced ? 0 : this.done ? 4 : 14; // градусов в секунду
    this.lon0 = 66 + t * speed;
    ctx.clearRect(0, 0, S, S);

    // Орбита (задняя половина)
    this.drawOrbit(ctx, cx, cy, R, t, "back");

    // Атмосфера
    const glow = ctx.createRadialGradient(cx, cy, R * 0.9, cx, cy, R * 1.25);
    glow.addColorStop(0, "rgba(57,135,229,0.22)");
    glow.addColorStop(1, "rgba(57,135,229,0)");
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(cx, cy, R * 1.25, 0, Math.PI * 2);
    ctx.fill();

    // Сфера
    const sphere = ctx.createRadialGradient(cx - R * 0.35, cy - R * 0.35, R * 0.1, cx, cy, R);
    sphere.addColorStop(0, "#14263f");
    sphere.addColorStop(1, "#0a1220");
    ctx.fillStyle = sphere;
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "rgba(134,182,239,0.35)";
    ctx.lineWidth = 1;
    ctx.stroke();

    // Сетка координат
    ctx.fillStyle = "rgba(134,182,239,0.18)";
    for (let lon = -180; lon < 180; lon += 30) {
      for (let lat = -80; lat <= 80; lat += 4) {
        const [x, y, c] = this.project(lon, lat, R, cx, cy);
        if (c > 0) ctx.fillRect(x, y, 0.8, 0.8);
      }
    }
    for (let lat = -60; lat <= 60; lat += 30) {
      for (let lon = -180; lon < 180; lon += 3) {
        const [x, y, c] = this.project(lon, lat, R, cx, cy);
        if (c > 0) ctx.fillRect(x, y, 0.8, 0.8);
      }
    }

    // Суша
    const scan = ((t * 40) % 360) - 180; // полоса «сканирования» (декоративная)
    for (const [lon, lat] of landDots || []) {
      const [x, y, c] = this.project(lon, lat, R, cx, cy);
      if (c <= 0) continue;
      let alpha = 0.25 + 0.65 * c;
      let color = "134,182,239";
      if (!this.done && !this.reduced) {
        let d = Math.abs(((lon - this.lon0 - scan + 540) % 360) - 180);
        if (d < 6) {
          color = "190,220,255";
          alpha = Math.min(1, alpha + 0.3);
        }
      }
      ctx.fillStyle = `rgba(${color},${alpha.toFixed(3)})`;
      ctx.beginPath();
      ctx.arc(x, y, 1.15, 0, Math.PI * 2);
      ctx.fill();
    }

    // Орбита (передняя половина) и спутники
    this.drawOrbit(ctx, cx, cy, R, t, "front");
  }

  drawOrbit(ctx, cx, cy, R, t, half) {
    const rx = R * 1.42;
    const ry = R * 0.42;
    const tilt = -0.32;
    const rot = this.reduced ? 0 : t * 0.18;
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(tilt);
    ctx.strokeStyle = "rgba(195,194,183,0.22)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    if (half === "back") ctx.ellipse(0, 0, rx, ry, 0, Math.PI, Math.PI * 2);
    else ctx.ellipse(0, 0, rx, ry, 0, 0, Math.PI);
    ctx.stroke();
    ctx.restore();

    const n = this.satellites.length;
    const fontSize = n > 10 ? 9 : 10.5;
    for (const s of this.satellites) {
      const a = s.phase + rot;
      const depth = Math.sin(a); // > 0 — перед глобусом
      if ((half === "front") !== depth > 0) continue;
      const ex = Math.cos(a) * rx;
      const ey = Math.sin(a) * ry;
      const x = cx + ex * Math.cos(tilt) - ey * Math.sin(tilt);
      const y = cy + ex * Math.sin(tilt) + ey * Math.cos(tilt);
      const color = STATUS_COLORS[s.status] || STATUS_COLORS.pending;
      const alpha = half === "front" ? 1 : 0.55;
      ctx.globalAlpha = alpha;
      if (s.status === "running" && !this.reduced) {
        const pr = 6 + 4 * (0.5 + 0.5 * Math.sin(t * 6));
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(x, y, pr, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.fillStyle = color;
      ctx.strokeStyle = "#070b12";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(x, y, 4.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "#e8e6df";
      ctx.font = `${fontSize}px system-ui, sans-serif`;
      ctx.textAlign = "center";
      const halfW = ctx.measureText(s.label).width / 2 + 2;
      const lx = Math.min(this.size - halfW, Math.max(halfW, x));
      ctx.fillText(s.label, lx, y < cy ? y - 9 : y + 16);
      ctx.globalAlpha = 1;
    }
  }
}
