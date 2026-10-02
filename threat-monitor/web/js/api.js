// Обёртка над HTTP API.

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, body, { raw = false } = {}) {
  const opts = { method, credentials: "same-origin", headers: { "X-Requested-With": "dozor" } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(path, opts);
  if (raw) {
    if (!resp.ok) throw new ApiError(resp.status, `HTTP ${resp.status}`);
    return resp;
  }
  let data = null;
  try {
    data = await resp.json();
  } catch (_) {
    data = null;
  }
  if (!resp.ok) {
    let msg = data && data.detail ? data.detail : `HTTP ${resp.status}`;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg || JSON.stringify(d)).join("; ");
    throw new ApiError(resp.status, msg);
  }
  return data;
}

export const api = {
  get: (p) => request("GET", p),
  post: (p, b = {}) => request("POST", p, b),
  put: (p, b = {}) => request("PUT", p, b),
  patch: (p, b = {}) => request("PATCH", p, b),
  del: (p) => request("DELETE", p),
  raw: (p) => request("GET", p, undefined, { raw: true }),
};

export function qs(params) {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    if (Array.isArray(v)) {
      if (v.length) u.set(k, v.join(","));
    } else u.set(k, v === true ? "1" : String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}
