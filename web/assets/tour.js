import { $, $$, esc, closePops, send } from './ui.js';
import { ic } from './icons.js';
import { S, H } from './state.js';

/* ============================================================ guided tour */
export function startTour() {
  if ($("#tour")) return;
  closePops();
  if (S.view !== "overview") H.go("overview");
  // sel: what the step points at.  side: it lives in the sidebar (opened for the step on phones).  dock: where the card sits on phones.
  const steps = [
    {t: "Welcome to RONB Clips", x: "Long videos and live streams go in, captioned vertical clips come out. Here is a quick look around.",
     chips: [["link", "Paste a link"], ["scissors", "Get clips", 1], ["radio", "Live streams"], ["send", "Post them"]]},
    {t: "Start with any video", x: "Paste a YouTube, Twitch or Kick link here, or upload a file. RONB Clips finds the best moments and makes each one a short with captions.",
     sel: "#addform", dock: "bottom", chips: [["link", "YouTube"], ["link", "Twitch"], ["link", "Kick"], ["up", "Upload", 1]]},
    {t: "Your day at a glance", x: "Home shows what is recording, how many clips were made and posted today, and what is waiting to go out.",
     sel: "#ov .tiles", dock: "bottom", chips: [["radio", "Recording"], ["scissors", "Clips today", 1], ["send", "Posted"], ["clock", "Waiting"]]},
    {t: "Every video in one place", x: "All videos lists everything you have processed. Open a video to see its clips, trim them, change the captions or download them.",
     sel: '#nav [data-v="library"]', side: 1, dock: "bottom", chips: [["film", "All videos", 1], ["play", "Preview"], ["sliders", "Edit"], ["down", "Download"]]},
    {t: "Add the creators you clip", x: "Give each creator a workspace with their channels. RONB Clips can record them when they go live and clip the highlights as they happen.",
     sel: '#nav [data-act="newcreator"]', side: 1, dock: "bottom", chips: [["plusc", "New creator", 1], ["radio", "Record live"], ["bolt", "Clip highlights"]]},
    {t: "The AI picks the moments", x: "This shows which AI this server uses and how much it has worked today. Open it to see what the AI was used for.",
     sel: "#foot .ai-card", side: 1, dock: "top", chips: [["bolt", "AI", 1], ["chart", "See usage"]]},
    {t: "Settings and alerts", x: "The bell tells you when clips are ready or a recording ends. Settings is where you connect the accounts you post to.",
     sel: "#foot .me", side: 1, dock: "top", chips: [["bell", "Notifications", 1], ["cog", "Settings"], ["plug", "Connect accounts"]]},
    {t: "You're ready to clip", x: "Paste your first link and press Make clips. Your clips stay in your private workspace.",
     chips: [["link", "Paste a link"], ["scissors", "Make clips", 1]]}];

  const root = document.createElement("div"); root.id = "tour";
  root.innerHTML = `<div class="tr-block"></div><div class="tr-dim"></div><div class="tr-ring"></div>
    <section class="tr-card" role="dialog" aria-modal="true" aria-labelledby="trT" aria-describedby="trP">
      <div class="tr-art"><div class="tr-chips"></div></div>
      <button class="tr-x" data-tour="close" aria-label="Close the tour">${ic("x")}</button>
      <div class="tr-body"><h2 id="trT"></h2><p id="trP"></p></div>
      <div class="tr-foot"><span class="tr-bar"><i></i></span><span class="tr-n"></span><span class="tr-grow"></span>
        <button class="tr-back" data-tour="back" aria-label="Back">${ic("arrowr")}</button><button class="btn primary" data-tour="next"></button></div></section>`;
  document.body.append(root);
  const dim = $(".tr-dim", root), ring = $(".tr-ring", root), card = $(".tr-card", root), main = $("#main"), app = $("#app");
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const cur = {x: innerWidth / 2, y: innerHeight / 2, w: 0, h: 0};
  let i = 0, raf = 0, snap = true, lastPos = "", saved = false, closed = false;
  const again = el => { el.style.animation = "none"; el.offsetWidth; el.style.animation = ""; };
  const rectOf = () => { const el = steps[i].sel && $(steps[i].sel); if (!el) return null; const r = el.getBoundingClientRect(); return r.width > 1 && r.height > 1 ? r : null; };

  function placeCard(r, W, H) {
    const cw = card.offsetWidth, ch = card.offsetHeight, m = 12, gap = 18, fit = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    let x = (W - cw) / 2, y = (H - ch) / 2;                                          // no target: the middle of the screen
    if (r && W < 700) y = steps[i].dock === "top" ? m : H - ch - m;                  // phones: docked away from the target
    else if (r) {
      const midY = fit(r.top + r.height / 2 - ch / 2, m, H - ch - m), midX = fit(r.left + r.width / 2 - cw / 2, m, W - cw - m);
      if (r.right + gap + cw <= W - m) { x = r.right + gap; y = midY; }              // beside it
      else if (r.left - gap - cw >= m) { x = r.left - gap - cw; y = midY; }
      else if (r.bottom + gap + ch <= H - m) { x = midX; y = r.bottom + gap; }       // under it
      else if (r.top - gap - ch >= m) { x = midX; y = r.top - gap - ch; }            // over it
      else { x = midX; y = r.top + r.height / 2 < H / 2 ? H - ch - m : m; }          // no room: the end that covers least of it
    }
    const pos = `translate3d(${Math.round(x)}px,${Math.round(y)}px,0)`;
    if (pos !== lastPos) { card.style.transform = lastPos = pos; }
  }

  function tick() {
    const W = innerWidth, H = innerHeight, r = rectOf(), pad = 6;
    let want = {x: W / 2, y: H / 2, w: 0, h: 0};
    if (r) { const x0 = Math.max(4, r.left - pad), y0 = Math.max(4, r.top - pad);
      want = {x: x0, y: y0, w: Math.max(0, Math.min(W - 4, r.right + pad) - x0), h: Math.max(0, Math.min(H - 4, r.bottom + pad) - y0)}; }
    const k = snap || still ? 1 : 0.16;                                              // ease toward the target a little every frame
    for (const p of "xywh") { cur[p] += (want[p] - cur[p]) * k; if (Math.abs(want[p] - cur[p]) < 0.3) cur[p] = want[p]; }
    const {x, y, w, h} = cur, rad = Math.min(14, w / 2, h / 2);
    const hole = w > 1 && h > 1 ? `M${x + rad} ${y}H${x + w - rad}A${rad} ${rad} 0 0 1 ${x + w} ${y + rad}V${y + h - rad}A${rad} ${rad} 0 0 1 ${x + w - rad} ${y + h}H${x + rad}A${rad} ${rad} 0 0 1 ${x} ${y + h - rad}V${y + rad}A${rad} ${rad} 0 0 1 ${x + rad} ${y}Z` : "";
    dim.style.clipPath = `path(evenodd, "M0 0H${W}V${H}H0Z ${hole}")`;
    ring.style.transform = `translate3d(${x - 2}px,${y - 2}px,0)`; ring.style.width = w + 4 + "px"; ring.style.height = h + 4 + "px";
    ring.classList.toggle("on", !!r && w > 8);
    placeCard(r, W, H);
    snap = false;
  }
  const loop = () => { if (closed) return; tick(); raf = requestAnimationFrame(loop); };

  function show(first) {
    const s = steps[i], last = i === steps.length - 1;
    app.classList.toggle("open", !!s.side && innerWidth <= 900);                     // phones: slide the sidebar in for its steps
    const chips = $(".tr-chips", root);
    chips.innerHTML = s.chips.map(([icon, label, on]) => `<span class="tr-chip ${on ? "on" : ""}">${ic(icon)}${esc(label)}</span>`).join("");
    $("#trT").textContent = s.t; $("#trP").textContent = s.x;
    if (!first) [chips, $("#trT"), $("#trP")].forEach(again);
    $(".tr-bar i", root).style.width = (i + 1) / steps.length * 100 + "%";
    $(".tr-n", root).textContent = `${i + 1} of ${steps.length}`;
    $(".tr-back", root).disabled = i === 0;
    const next = $('[data-tour="next"]', root); next.innerHTML = last ? "Start clipping" : `Next${ic("arrowr")}`; next.focus({preventScroll: true});
    const el = s.sel && $(s.sel);
    if (el && main.contains(el)) {                                                   // scroll only when it is cut off, and only as far as needed
      const r = el.getBoundingClientRect(), phone = innerWidth < 700, room = card.offsetHeight + 26;   // on phones the docked card takes its end of the screen
      const top = Math.max(main.getBoundingClientRect().top + $(".topbar").offsetHeight + 14, phone && s.dock === "top" ? room : 0);
      const bottom = innerHeight - (phone && s.dock !== "top" ? room : 14);
      const by = r.top < top ? r.top - top : r.bottom > bottom ? Math.min(r.bottom - bottom, r.top - top) : 0;
      if (Math.abs(by) > 1) main.scrollBy({top: by, behavior: still ? "auto" : "smooth"});
    } else if (el) el.scrollIntoView({block: "nearest"});
    tick();
  }
  function close() {
    if (closed) return; closed = true;
    cancelAnimationFrame(raf); removeEventListener("keydown", keys, true); removeEventListener("resize", tick);
    app.classList.remove("open"); root.classList.remove("on"); ring.classList.remove("on");
    setTimeout(() => root.remove(), 300);
    if (!saved) { saved = true; S.status.tour = false; send("/api/settings", {tour_done: true}, "PUT").catch(() => {}); }
    if (S.view === "overview") $("#url")?.focus({preventScroll: true});
  }
  const move = by => { const n = i + by; if (n < 0) return; if (n >= steps.length) return close(); i = n; show(); };
  function keys(e) {
    if (e.key === "Escape") close(); else if (e.key === "ArrowRight") move(1); else if (e.key === "ArrowLeft") move(-1);
    else if (e.key === "Tab") { const f = $$("button:not([disabled])", card), at = f.indexOf(document.activeElement);   // keep the keyboard inside the card
      f[(at + (e.shiftKey ? -1 : 1) + f.length) % f.length]?.focus(); }
    else if (e.key === "Enter" || e.key === " ") return;                             // the focused button handles these
    else return;
    e.preventDefault(); e.stopPropagation();
  }
  root.addEventListener("click", e => { e.stopPropagation(); const b = e.target.closest("[data-tour]"); if (!b) return;
    b.dataset.tour === "close" ? close() : move(b.dataset.tour === "back" ? -1 : 1); });
  addEventListener("keydown", keys, true); addEventListener("resize", tick);
  card.style.transition = "opacity .3s ease";                                        // the first placement appears in place, later ones glide
  show(true);
  requestAnimationFrame(() => { root.classList.add("on"); requestAnimationFrame(() => { card.style.transition = ""; }); });
  setTimeout(() => root.classList.add("on"), 60);                                    // also when the tab is in the background
  loop();
}
