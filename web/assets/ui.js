import { I, ic } from './icons.js';
import { S } from './state.js';

/* ============================================================ helpers */
export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
export const navIc = (n, v) => ic(S.view === v && I[n + "_f"] ? n + "_f" : n);
export const fmt = s => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60); return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s % 60).padStart(2, "0"); };
export const clock = t => new Date(t * 1000).toLocaleString([], {weekday:"short", hour:"2-digit", minute:"2-digit"});
export const when = t => new Date(t * 1000).toLocaleString([], {month:"short", day:"numeric", hour:"2-digit", minute:"2-digit"});
export const opts = (map, val) => Object.entries(map).map(([k, v]) => `<option value="${esc(k)}"${String(k) === String(val) ? " selected" : ""}>${esc(v)}</option>`).join("");

export async function api(path, opt) {
  const r = await fetch(path, opt);
  if (r.status === 401) { location.href = "/"; throw new Error("Please open RONB Clips locally."); }
  if (!r.ok) { let d = r.statusText; try { d = (await r.json()).detail || d; } catch {} throw new Error(typeof d === "string" ? d : "Something went wrong"); }
  return r.json();
}
export const send = (path, body, method = "POST") => api(path, {method, headers:{"Content-Type":"application/json"}, body:JSON.stringify(body ?? {})});
/* A long call answers with a task id first. Wait for the task and give back its result (or show its error). */
export async function taskResult(started) {
  const id = typeof started === "string" ? started : started?.id;
  if (!id) throw new Error("That job did not start. Try again.");
  const url = "/api/tasks/" + encodeURIComponent(id);
  for (let n = 0; n < 600; n++) {
    const t = await api(url);
    if (t.status === "done") return t.result;
    if (t.status === "error" || t.status === "missing") throw new Error(t.error || "That job is gone. Try again.");
    await new Promise(r => setTimeout(r, 500));
  }
  throw new Error("That job is taking a long time. Check back in a moment.");
}
export function toast(msg, kind = "") {
  if (!msg) return;
  const t = document.createElement("div"); t.className = "toast " + kind; t.textContent = msg;
  if (kind === "bad") { const r = document.createElement("button"); r.className = "rep"; r.textContent = "Report"; r.onclick = () => { t.remove(); openFeedback("bug", msg); }; t.append(r); }
  $("#toasts").append(t); setTimeout(() => t.remove(), kind === "bad" ? 7000 : 3200);
}
const cache = new WeakMap();
export function put(el, html) { if (el && cache.get(el) !== html) { cache.set(el, html); el.innerHTML = html; } }
export function ask(title, text, okLabel = "Confirm", danger = false) {
  return new Promise(res => {
    const d = $("#formdlg");
    d.innerHTML = `<form method="dialog" class="dlg"><h2>${esc(title)}</h2><p class="help">${esc(text)}</p>
      <div class="dlg-foot"><button class="btn" value="no">Cancel</button><button class="btn ${danger ? "danger" : "primary"}" value="yes" autofocus>${esc(okLabel)}</button></div></form>`;
    d.onclose = () => res(d.returnValue === "yes"); d.returnValue = "no"; d.showModal();
  });
}
export const avatarUrl = (c, k) => `/api/creators/${encodeURIComponent(c.id)}/avatar?${k ? "p=" + k + "&" : ""}v=${Math.round(((k ? c.profiles?.[k] : c.profile) || {}).at || 0)}`;
export const fmtK = n => (n = Number(n) || 0) >= 1e6 ? (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M" : n >= 1e3 ? (n / 1e3).toFixed(1).replace(/\.0$/, "") + "K" : String(n);
export function closePops(except) { ["bellpop"].forEach(id => { const p = $("#" + id); if (p !== except && !p.hidden) { p.dataset.opening = "false"; p.classList.remove("open"); $("[data-act=bell]")?.setAttribute("aria-expanded", "false"); setTimeout(() => { if (!p.classList.contains("open")) p.hidden = true; }, 180); } }); }
export function openPop(p, anchor) {
  closePops(p); p.hidden = false;
  p.dataset.opening = "true";
  anchor?.setAttribute("aria-expanded", "true");
  // beside the sidebar, bottom lined up with the button that opened it
  const side = $(".side").getBoundingClientRect(), a = anchor?.getBoundingClientRect();
  if (innerWidth > 900) { p.style.left = side.right + 8 + "px"; p.style.bottom = Math.max(12, innerHeight - (a ? a.bottom + 6 : side.bottom - 12)) + "px"; }
  else { p.style.left = ""; p.style.bottom = ""; }
  requestAnimationFrame(() => { if (p.dataset.opening === "true") { p.classList.add("open"); p.dataset.opening = "false"; } });
}
/* feedback goes to the maker's Instagram. A problem's message is copied first, so it can be pasted there. */
export async function openFeedback(kind = "bug", context = "") {
  const url = S.status?.links?.follow;
  if (!url) return toast(context ? "Copy this message and open an issue on GitHub." : "There's no feedback link on this server.");
  if (context) { try { await navigator.clipboard.writeText(context); toast("The problem is copied. Paste it in your message.", "ok"); } catch {} }
  window.open(url, "_blank", "noopener");
}
export const ROUTES = {overview: "", library: "videos", settings: "settings", usage: "usage"};
export function pathFor(v, tab) {
  if (v.startsWith("c:")) return `/creators/${encodeURIComponent(v.slice(2))}${tab && tab !== "clips" ? "/" + tab : ""}`;
  if (v === "settings") return "/settings" + (tab ? "/" + tab : "");
  return "/" + (ROUTES[v] ? ROUTES[v] : "");
}
export function fromPath(path) {
  const m = path.match(/^\/creators\/([^/]+)(?:\/(setup|activity|clips))?\/?$/);
  if (m) return ["c:" + decodeURIComponent(m[1]), m[2] || "clips"];
  const st = path.match(/^\/settings(?:\/(ai|apps|storage))?\/?$/);
  if (st) return ["settings", st[1] || null];
  const k = Object.keys(ROUTES).find(x => ROUTES[x] && path.replace(/\/$/, "") === "/" + ROUTES[x]);
  return [k || "overview", "clips"];
}
export function remember(v, tab, how = "push") {
  const url = pathFor(v, tab);
  if (location.pathname !== url) history[how === "replace" ? "replaceState" : "pushState"]({v, tab}, "", url);
}
export function spark(vals, cls, lo, hi) { return `<div class="spark ${cls}">${vals.map(v => `<i style="height:${Math.max(4, Math.min(100, (v - lo) / (hi - lo) * 100))}%"></i>`).join("")}</div>`; }
export function zipDownload(ids) {
  if (!ids.length) return toast("None of those clips are ready to download yet.");
  if (ids.length > 150) return toast("That is a lot at once. Download up to 150 clips at a time.", "bad");
  toast(ids.length === 1 ? "Preparing your clip…" : `Preparing ${ids.length} clips as one download…`, "ok");
  const a = document.createElement("a"); a.href = "/api/clips.zip?ids=" + encodeURIComponent(ids.join(",")); a.download = ""; document.body.append(a); a.click(); a.remove();
}
export function askTyped(title, text, mustType, okLabel) {
  return new Promise(res => {
    const d = $("#formdlg");
    d.innerHTML = `<form method="dialog" class="dlg"><h2>${esc(title)}</h2><p class="help">${text}</p>
      <label class="f"><span>Type <b>${esc(mustType)}</b> to confirm</span><input id="typedconfirm" autocomplete="off" spellcheck="false"></label>
      <div class="dlg-foot"><button class="btn" value="no">Cancel</button><button class="btn danger" value="yes" id="typedok" disabled>${esc(okLabel)}</button></div></form>`;
    const i = $("#typedconfirm");
    i.addEventListener("input", () => { $("#typedok").disabled = i.value.trim().toLowerCase() !== mustType.toLowerCase(); });
    d.onclose = () => res(d.returnValue === "yes" ? i.value.trim() : null); d.returnValue = "no"; d.showModal(); i.focus();
  });
}
export function barChart(daily) {
  const W = 720, H = 230, pl = 30, pb = 28, pt = 12, n = daily.length, ch = H - pt - pb;
  const peak = Math.max(1, ...daily.map(d => Math.max(d.clips, d.posted)));
  const step = Math.max(1, Math.ceil(peak / 4)), top = step * 4, cw = (W - pl) / n, bw = Math.min(14, (cw - 10) / 2);
  const y = v => pt + ch * (1 - v / top);
  let g = "";
  for (let i = 0; i <= 4; i++) g += `<line class="gl" x1="${pl}" x2="${W}" y1="${y(i * step)}" y2="${y(i * step)}"/><text x="${pl - 8}" y="${y(i * step) + 4}" text-anchor="end">${i * step}</text>`;
  daily.forEach((d, i) => {
    const cx = pl + i * cw + cw / 2, lab = new Date(d.day + "T12:00").toLocaleDateString([], {day:"numeric", month:"short"});
    g += `<g><title>${lab}: ${d.clips} clips made, ${d.posted} posted${d.views ? ", " + d.views.toLocaleString() + " views" : ""}</title>
      <rect class="b1" x="${cx - bw - 1}" y="${y(d.clips)}" width="${bw}" height="${Math.max(0, ch * d.clips / top)}" rx="3"/>
      <rect class="b2" x="${cx + 1}" y="${y(d.posted)}" width="${bw}" height="${Math.max(0, ch * d.posted / top)}" rx="3"/>
      <rect x="${pl + i * cw}" y="${pt}" width="${cw}" height="${ch}" fill="transparent"/></g>`;
    if (i % 2 === (n - 1) % 2) g += `<text x="${cx}" y="${H - 8}" text-anchor="middle">${i === n - 1 ? "Today" : lab}</text>`;
  });
  const none = daily.every(d => !d.clips && !d.posted) ? `<text class="none" x="${(W + pl) / 2}" y="${pt + ch / 2}" text-anchor="middle">No clips in the last 14 days yet</text>` : "";
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Clips made and posted per day, last 14 days">${g}${none}</svg>`;
}
export function tokenChart(daily) {
  const W = 1000, H = 220, pl = 64, pb = 26, pt = 10, n = daily.length, ch = H - pt - pb;
  const peak = Math.max(...daily.map(d => d.tokens), 1), top = peak * 1.15, cw = (W - pl) / n, bw = Math.min(14, cw - 6);
  const y = v => pt + ch * (1 - v / top);
  let g = "";
  for (let i = 0; i <= 3; i++) { const v = top * i / 3; g += `<line class="gl" x1="${pl}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text x="${pl - 8}" y="${y(v) + 4}" text-anchor="end">${fmtK(Math.round(v))}</text>`; }
  daily.forEach((d, i) => {
    const cx = pl + i * cw + cw / 2, lab = new Date(d.day + "T12:00").toLocaleDateString([], {day:"numeric", month:"short"});
    g += `<g><title>${lab}: ${d.tokens.toLocaleString()} tokens in ${d.calls} call${d.calls === 1 ? "" : "s"}</title><rect class="b1" x="${cx - bw / 2}" y="${y(d.tokens)}" width="${bw}" height="${Math.max(0, ch * d.tokens / top)}" rx="3"/></g>`;
    if ((n - 1 - i) % 5 === 0) g += `<text x="${cx}" y="${H - 6}" text-anchor="middle">${i === n - 1 ? "Today" : lab}</text>`;
  });
  const none = daily.every(d => !d.tokens) ? `<text class="none" x="${(W + pl) / 2}" y="${pt + ch / 2}" text-anchor="middle">No AI used in the last 30 days</text>` : "";
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="AI tokens used per day, last 30 days">${g}${none}</svg>`;
}
export function hbars(rows) {
  const total = rows.reduce((a, r) => a + r[1], 0) || 1;
  return rows.length ? `<div class="hbar">${rows.map(r => `<div class="hbar-row"><div class="top"><span>${esc(r[0])}</span><span>${fmtK(r[1])} tokens · ${r[2]} call${r[2] === 1 ? "" : "s"}</span></div>
    <div class="track"><i style="width:${Math.max(2, r[1] / total * 100)}%"></i></div></div>`).join("")}</div>` : `<p class="help">Nothing yet.</p>`;
}
