import { $, $$, esc, fmt, clock, when, opts, api, send, toast, put, fmtK, avatarUrl, closePops, openPop, barChart, tokenChart, hbars, fromPath, navIc } from './ui.js';
import { ic } from './icons.js';
import { S, creator, creatorId, H } from './state.js';

/* ============================================================ shell */
function aiCard() {
  const k = S.status.ai; if (!k) return "";
  return `<div class="card ai-home"><h2>AI</h2>
    <div class="ai-big">${k.ready ? esc(k.provider || "Ready") : "Not set up"}</div>
    <p class="ai-sub">${k.ready ? esc(k.model || "") : "Clips are picked by loudness until an AI key is added."}</p>
    <div class="ai-stats"><div><b>${(k.today_calls || 0).toLocaleString()}</b><span>AI calls today</span></div>
      <div><b>${fmtK(k.today_tokens)}</b><span>Tokens today</span></div></div>
    <div class="row">${!k.ready ? `<button class="btn primary" data-act="go" data-v="settings" data-tab="ai">${ic("plus")}Add AI key</button>` : ""}<button class="btn" data-act="go" data-v="usage">View usage ${ic("arrowr")}</button></div></div>`;
}
export const PLAT = {
  youtube: {name: "YouTube", bg: "#FF0000", fg: "#fff", hint: "https://www.youtube.com/@name",
    svg: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M23.5 6.19a3.02 3.02 0 0 0-2.12-2.14C19.5 3.55 12 3.55 12 3.55s-7.5 0-9.38.5A3.02 3.02 0 0 0 .5 6.19C0 8.07 0 12 0 12s0 3.93.5 5.81a3.02 3.02 0 0 0 2.12 2.14c1.88.5 9.38.5 9.38.5s7.5 0 9.38-.5a3.02 3.02 0 0 0 2.12-2.14C24 15.93 24 12 24 12s0-3.93-.5-5.81zM9.55 15.57V8.43L15.82 12l-6.27 3.57z"/></svg>'},
  twitch: {name: "Twitch", bg: "#9146FF", fg: "#fff", hint: "https://www.twitch.tv/name",
    svg: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M11.57 4.71h1.72v5.15h-1.72zm4.72 0H18v5.15h-1.71zM6 0 1.71 4.29v15.42h5.15V24l4.28-4.29h3.43L22.29 12V0zm14.57 11.14-3.43 3.43h-3.43l-3 3v-3H6.86V1.71h13.71z"/></svg>'},
  kick: {name: "Kick", bg: "#0B0E0F", fg: "#53FC18", hint: "https://kick.com/name",
    svg: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M1.5 1.5h7v6h2.5V5h2.5V2.5H16v-1h6.5v7H20V11h-2.5v2H20v2.5h2.5v7H16v-1h-2.5V19H11v-2.5H8.5v6h-7z"/></svg>'},
};
export const platLogo = k => `<span class="logo" style="background:${PLAT[k].bg};color:${PLAT[k].fg}">${PLAT[k].svg}</span>`;
function playerSrc(k, e) {
  if (!e) return "";
  if (k === "twitch") return `https://player.twitch.tv/?channel=${encodeURIComponent(e)}&parent=${location.hostname}&muted=true&autoplay=true`;
  if (k === "youtube") return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(e)}?autoplay=1&mute=1&playsinline=1`;
  if (k === "kick") return `https://player.kick.com/${encodeURIComponent(e)}?muted=true&autoplay=true`;
  return "";
}
const since = ts => { const m = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60)); return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`; };
export function drawStream() {
  const box = $("#stream"); if (!box) return;
  const c = creator(); if (!c) return;
  const on = liveOn(c);
  if (!on.length) { if (box.innerHTML) box.innerHTML = ""; return; }
  S.spick = S.spick || {};
  const [k, p] = on.find(([x]) => x === S.spick[c.id]) || on[0];
  if (!box.querySelector(".stream")) box.innerHTML = `<div class="stream"><div class="player"></div><div class="sinfo"></div></div>`;
  const pl = box.querySelector(".player"), src = playerSrc(k, p.embed);
  if (pl.dataset.src !== src) {           // only swap the player when the stream changes, never on a refresh
    pl.dataset.src = src;
    const ipHost = /^[\d.]+$|^\[/.test(location.hostname);
    pl.innerHTML = k === "twitch" && ipHost
      ? `<div class="noembed">${platLogo(k)}<b>Twitch only plays inside sites with a web address</b><span>It will play here once RONB Clips is on its domain, or open RONB Clips at localhost instead of ${esc(location.hostname)}.</span><a class="btn sm" href="${esc(p.channel)}" target="_blank" rel="noopener">Watch on Twitch ${ic("upright")}</a></div>`
      : src ? `<iframe src="${src}" allow="autoplay; fullscreen; picture-in-picture" allowfullscreen title="${PLAT[k].name} live stream"></iframe>` : "";
  }
  const stats = [p.viewers != null ? [fmtK(p.viewers), "watching"] : null, p.started ? [since(p.started), "live for"] : null,
                 p.followers ? [p.followers.split(" ")[0], p.followers.split(" ").slice(1).join(" ")] : null].filter(Boolean);
  const rec = c.live_job;
  put(box.querySelector(".sinfo"), `<div class="top">${platLogo(k)}<span class="lv"><i></i>Live on ${PLAT[k].name}</span>
      ${on.length > 1 ? `<div class="splat">${on.map(([x]) => `<button class="${x === k ? "on" : ""}" data-act="spick" data-p="${x}" title="Watch on ${PLAT[x].name}">${PLAT[x].svg}</button>`).join("")}</div>` : ""}</div>
    <h3>${esc(p.title || "Live now")}</h3><p class="by">${esc(p.name || c.name)}</p>
    ${stats.length ? `<div class="sstats">${stats.map(([v, l]) => `<div><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join("")}</div>` : ""}
    <div class="cta">${rec
      ? `<button class="btn danger" data-act="stoplive" data-j="${esc(rec)}">${ic("stop")}Stop recording</button><small>Recording now. Clips appear below as they're made.</small>`
      : `<button class="btn primary" data-act="golive" data-url="${esc(p.channel)}"><span class="rdot"></span>Record this stream</button><small>RONB Clips watches it and clips the best moments as they happen.</small>`}</div>`);
}
export function liveOn(c) { return Object.entries(c.profiles || {}).filter(([k, p]) => p.live && c.channels?.[k] === p.channel).sort(([, p]) => p.channel === c.channel ? -1 : 1); }
export function statusBadge(c) {
  if (c.live_job) return `<span class="sbadge rec"><i></i>Recording</span>`;
  const on = liveOn(c);
  if (on.length) { const [k, p] = on[0]; return `<span class="sbadge live"><i></i>Live on ${PLAT[k].name}${p.viewers ? " Â· " + fmtK(p.viewers) + " watching" : ""}</span>`; }
  if (!Object.keys(c.channels || {}).length) return `<span class="sbadge">No channel yet</span>`;
  return `<span class="sbadge"><i></i>Offline${c.auto_watch ? " Â· records when live" : ""}</span>`;
}
const cav = c => { const r = c.live_job || liveOn(c).length ? " ring" : "";
  return c.profile?.pic && c.profile.channel === c.channel ? `<img class="avatar${r}" src="${avatarUrl(c)}" alt="">` : `<span class="avatar${r}">${esc((c.name || "?")[0].toUpperCase())}</span>`; };
const NT_ICON = {live: "radio", clips: "scissors", post: "warn", fail: "warn", account: "user", feedback: "chat"};
const ago = t => { const s = Date.now() / 1000 - t; return s < 60 ? "just now" : s < 3600 ? Math.floor(s / 60) + " min ago" : s < 86400 ? Math.floor(s / 3600) + " h ago" : new Date(t * 1000).toLocaleDateString([], {day: "numeric", month: "short"}); };
export async function openBell() {
  const p = $("#bellpop");
  if (!p.hidden) return closePops();
  p.innerHTML = `<div class="pop-head"><h3>Notifications</h3></div><div class="pop-empty">Loadingâ€¦</div>`;
  openPop(p, $("[data-act=bell]"));
  try {
    const d = await api("/api/notifications");
    p.innerHTML = `<div class="pop-head"><h3>Notifications</h3>${d.unread ? `<button data-act="bellread">Mark all as read</button>` : ""}</div>
      ${d.items.length ? `<div class="pop-list">${d.items.map(n => `<button class="nt k-${esc(n.kind)} ${n.read ? "" : "new"}" data-act="ntgo" data-link="${esc(n.link)}">
        <i>${ic(NT_ICON[n.kind] || "bell")}</i><span class="tx"><b>${esc(n.title)}</b>${n.body ? `<span>${esc(n.body)}</span>` : ""}<small>${ago(n.t)}</small></span></button>`).join("")}</div>`
        : `<div class="pop-empty">${ic("bell")}Nothing yet. Recordings, finished clips and posts that need a look show up here.</div>`}`;
    if (d.unread) setTimeout(markRead, 1500);
  } catch (e) { p.innerHTML = `<div class="pop-head"><h3>Notifications</h3></div><div class="pop-empty">${esc(e.message)}</div>`; }
}
export async function markRead() { try { await send("/api/notifications/read"); S.status.unread = 0; drawShell(); } catch {} }
document.addEventListener("click", e => { if (!e.target.closest(".pop, .social-wrap, [data-act=bell]")) { closePops(); $$(".social-menu").forEach(m => { m.hidden = true; m.previousElementSibling?.setAttribute("aria-expanded", "false"); }); } });
document.addEventListener("keydown", e => { if (e.key === "Escape") { closePops(); $$(".social-menu").forEach(m => { m.hidden = true; m.previousElementSibling?.setAttribute("aria-expanded", "false"); }); } });
/* new notifications pop up as a small card with a soft chime */
let lastNotice = Number(localStorage.getItem("lastNotice") || 0), chimeCtx = null;
function chime() {
  try {
    chimeCtx = chimeCtx || new (window.AudioContext || window.webkitAudioContext)();
    const c = chimeCtx, now = c.currentTime;
    [[880, 0], [1318.5, .11]].forEach(([f, d]) => {         // two soft notes, A5 then E6
      const o = c.createOscillator(), g = c.createGain();
      o.type = "sine"; o.frequency.value = f;
      g.gain.setValueAtTime(0, now + d); g.gain.linearRampToValueAtTime(.08, now + d + .015);
      g.gain.exponentialRampToValueAtTime(.0001, now + d + .5);
      o.connect(g).connect(c.destination); o.start(now + d); o.stop(now + d + .55);
    });
  } catch {}
}
function noticeToast(n) {
  const el = document.createElement("div");
  el.className = `ntoast k-${n.kind}`; el.setAttribute("role", "status");
  el.innerHTML = `<i>${ic(NT_ICON[n.kind] || "bell")}</i><span class="tx"><b>${esc(n.title)}</b>${n.body ? `<span>${esc(n.body)}</span>` : ""}</span><button class="x" aria-label="Dismiss">${ic("x")}</button>`;
  const close = () => { el.classList.add("out"); setTimeout(() => el.remove(), 250); };
  el.addEventListener("click", e => {
    close();
    if (e.target.closest(".x")) return;
    if (n.link && (n.link.startsWith("/creators") || n.link.startsWith("/videos") || n.link.startsWith("/settings") || n.link.startsWith("/usage"))) { const [v, tab] = fromPath(n.link); H.go(v, tab); } else if ((n.link || "").startsWith("/")) location.href = n.link;
  });
  $("#toasts").append(el);
  setTimeout(close, 7000);
}
export async function checkNew(first) {
  if (!first && !S.status.unread) return;
  try {
    const d = await api("/api/notifications");
    const top = d.items.reduce((m, n) => Math.max(m, n.id), 0);
    if (first && !lastNotice) { lastNotice = top; localStorage.setItem("lastNotice", top); return; }   // don't replay old ones
    const fresh = d.items.filter(n => n.id > lastNotice && !n.read).reverse().slice(-3);
    if (fresh.length) { fresh.forEach(noticeToast); chime(); }
    lastNotice = Math.max(lastNotice, top); localStorage.setItem("lastNotice", lastNotice);
  } catch {}
}
export function updateHow() {
  const up = S.status?.update, d = $("#formdlg"); if (!up || d.open) return;
  const ai = "Update this RONB Clips folder to the latest version from GitHub (git pull), keep my .env and data folder as they are, install any new requirements, and restart RONB Clips. Read AGENTS.md first: if this folder's path is long or inside a temporary or assistant workspace, ask me before moving it to a short folder like C:\\ronbclips, and never delete anything without asking.";
  const cmd = (label, text) => `<h3>${label}</h3><div class="cmd"><code>${esc(text)}</code><button class="btn sm" type="button" data-act="copytext" data-t="${esc(text)}">${ic("copy")}Copy</button></div>`;
  d.innerHTML = `<form method="dialog" class="dlg upd"><h2>${up.restart ? "The new version is ready" : "A new version of RONB Clips is out"}</h2>
    <p class="help">${up.restart ? "The new files are already in the RONB Clips folder. <b>Close RONB Clips and start it again</b> to use them." : `You're on <b>${esc(up.current)}</b>. ${up.behind ? `${up.behind} update${up.behind === 1 ? " has" : "s have"} been added since.` : "Newer changes have been added since."}`} Your settings, keys and clips stay as they are.</p>
    ${up.notes.length ? `<ul>${up.notes.map(n => `<li>${esc(n.title)} <small>${esc(n.date)}</small></li>`).join("")}</ul>` : ""}
    ${up.restart ? "" : cmd("Ask your AI coding assistant (Claude, Cursor, Copilot...)", ai) + cmd("Or run it yourself, in the RONB Clips folder", "git pull && pip install -r requirements.txt")}
    <p class="sub">${up.restart ? "" : "Then stop RONB Clips and start it again. With Docker, run <code>git pull && docker compose up -d --build</code> instead. "}<a class="lnk" href="${esc(up.url)}" target="_blank" rel="noopener">See every change on GitHub</a></p>
    <div class="dlg-foot"><button class="btn" value="later">Remind me next version</button><button class="btn primary" value="ok" autofocus>Got it</button></div></form>`;
  d.onclose = () => { if (d.returnValue === "later") { try { localStorage.setItem("ronbclips_update_seen", up.latest); } catch {} drawShell(); } };
  d.returnValue = "ok"; d.showModal();
}
function greeting() { const h = new Date().getHours(); return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"; }
let lastInd = null;
addEventListener("resize", () => { lastInd = null; const i = $("#utabs .ind"); if (i) i.remove(); moveInd(); });   // where the orange pill was, so it can slide from there to the new tab
function moveInd() {
  const nav = $("#utabs"), on = nav?.querySelector(".tabbtn.on");
  if (!on) return;
  let ind = nav.querySelector(".ind");
  if (!ind) {
    ind = document.createElement("span"); ind.className = "ind"; nav.prepend(ind);
    if (lastInd) { ind.style.transition = "none"; ind.style.width = lastInd.w + "px"; ind.style.transform = `translateX(${lastInd.x}px)`; ind.offsetWidth; ind.style.transition = ""; }
  }
  const x = on.offsetLeft, w = on.offsetWidth;
  requestAnimationFrame(() => { ind.style.width = w + "px"; ind.style.transform = `translateX(${x}px)`; });
  lastInd = {x, w};
}
export function drawShell() {  const nav = (v, icon, label, extra = "") => `<button class="nav-item ${S.view === v ? "on" : ""}" data-act="go" data-v="${esc(v)}" title="${esc(label)}">${icon}<span class="label">${esc(label)}</span>${extra}</button>`;
  put($("#nav"), nav("overview", navIc("home", "overview"), "Home") +
    nav("library", navIc("film", "library"), "All videos", (() => { const w = S.jobs.filter(j => ["queued", "running"].includes(j.status)).length;
      return w ? `<span class="working" title="${w} working now"><i></i>${w} working</span>` : S.jobs.length ? `<span class="count">${S.jobs.length}</span>` : ""; })()) +
    `<h4><span>Creators</span></h4>` +
    S.creators.map(c => nav("c:" + c.id, cav(c), c.name,
      c.live_job ? `<span class="sb-tag rec" title="Recording now"><i></i>Rec</span>`
        : liveOn(c).length ? `<span class="sb-tag" title="Live on ${esc(liveOn(c).map(([k]) => PLAT[k].name).join(" and "))}"><i></i>Live</span>` : "")).join("") +
    `<button class="nav-item" data-act="newcreator" title="New creator">${ic("plusc")}<span class="label">New creator</span></button>`);
  const st = S.status;
  const k = S.status.ai;
  put($("#foot"), (k ? `<button class="ai-card ${S.view === "usage" ? "on" : ""}" data-act="go" data-v="usage" title="${k.ready ? `AI: ${esc(k.provider || "ready")}, ${k.today_calls || 0} calls today` : "The AI is not set up"}">
      <span class="ai-ic">${ic("bolt")}</span><span class="ai-tx"><small>AI</small><b>${k.ready ? esc(k.provider || "Ready") : "Not set up"}</b></span>${k.ready ? `<span class="ai-n">${(k.today_calls || 0).toLocaleString()} today</span>` : ""}</button>` : "") +
    `<div class="me"><button class="me-btn" data-act="go" data-v="settings" aria-label="Open settings">
      <span class="who"><b>Settings</b><span>AI key, connected apps</span></span></button>
      <button class="icon-btn bell" data-act="bell" title="Notifications" aria-label="Notifications${S.status.unread ? `, ${S.status.unread} new` : ""}" aria-haspopup="dialog">${ic("bell")}${S.status.unread ? `<span class="dotn">${S.status.unread > 9 ? "9+" : S.status.unread}</span>` : ""}</button></div>`);
  const c = creator();
  const recording = S.creators.filter(x => x.live_job).length;
  let title = {overview:"Home", library:"All videos", settings:"Settings", usage:"AI usage"}[S.view] || "", sub = "", act = "";
  if (S.view === "usage") sub = "What the AI has been used for.";
  if (S.view === "overview") sub = `${greeting()}. ${S.creators.length} creator${S.creators.length === 1 ? "" : "s"}${recording ? `, ${recording} recording now` : ""}.`;
  if (S.view === "library") sub = "Every video and recording, across all creators.";
  if (S.view === "settings") sub = "Your AI key and connected apps.";
  let pic = "", subHtml = null, tabs = "";
  if (c) {
    title = c.name;
    pic = cav(c);
    subHtml = statusBadge(c);
    const nJobs = S.jobs.filter(j => j.creator === c.id).length, nLog = (c.log || []).length;
    tabs = [["clips", "Clips", "scissors", nJobs], ["setup", "Setup", "cog", 0], ["activity", "Activity", "clock", nLog]].map(([k, l, i, n]) =>
      `<button class="tabbtn ${S.ctab === k ? "on" : ""}" data-act="tab" data-t="${k}" role="tab" aria-selected="${S.ctab === k}">${ic(i)}${l}${n ? `<span class="n">${n > 99 ? "99+" : n}</span>` : ""}</button>`).join("");
    act = c.live_job ? `<button class="btn danger" data-act="stoplive" data-j="${esc(c.live_job)}">${ic("stop")}Stop recording</button>`
                     : `<button class="btn primary" data-act="golive"><span class="rdot"></span>${liveOn(c).length ? "Record live stream" : "Start recording"}</button>`;
  }
  if (S.view === "overview" || S.view === "library") {
    const L = S.status.links || {};
    act = (L.repo ? `<a class="btn" href="${esc(L.repo)}" target="_blank" rel="noopener" title="Star RONB Clips on GitHub">${ic("github")}<span>Star<span class="long"> on GitHub</span></span></a>` : "")
        + (L.socials?.instagram || L.socials?.facebook || L.follow ? `<div class="social-wrap"><button class="btn primary" type="button" data-act="socials" aria-haspopup="menu" aria-expanded="false">${ic("instagram")}Follow us${ic("caret").replace("<svg", '<svg class="social-caret"')}</button><div class="social-menu" role="menu" hidden>${L.socials?.instagram || L.follow ? `<a href="${esc(L.socials?.instagram || L.follow)}" target="_blank" rel="noopener" role="menuitem">${ic("instagram")}Instagram</a>` : ""}${L.socials?.facebook ? `<a href="${esc(L.socials.facebook)}" target="_blank" rel="noopener" role="menuitem">${ic("chat")}Facebook</a>` : ""}</div></div>` : "");
  }
  const kk = S.status.ai;
  const up = S.status.update;
  let upSeen = "";
  try { upSeen = localStorage.getItem("ronbclips_update_seen") || ""; } catch {}
  const upBar = up && up.latest !== upSeen ? `<div class="vb" role="status">${ic("up")}<span>${up.restart ? "<b>The new version is downloaded.</b> Close RONB Clips and start it again to use it." : `A new version of RONB Clips is out${up.behind ? `: <b>${up.behind} update${up.behind === 1 ? "" : "s"}</b> since yours` : ""}.`}</span>
    <button class="btn sm primary" data-act="updhow">${up.restart ? "What's new" : "How to update"}</button><button class="btn sm ghost" data-act="updlater" aria-label="Hide until the next version">Later</button></div>` : "";
  const toolBar = S.status.no_ffmpeg ? `<div class="vb" role="alert">${ic("warn")}<span><b>RONB Clips can't make clips yet: ffmpeg isn't installed on this computer.</b> Open a terminal, run <code>winget install Gyan.FFmpeg</code> (Mac: <code>brew install ffmpeg</code>, Linux: <code>sudo apt install ffmpeg</code>), then close RONB Clips and start it again.</span></div>` : "";
  const aiBar = !toolBar && S.status.ai?.problem && S.view !== "settings" ? `<div class="vb" role="status">${ic("warn")}<span><b>The AI isn't answering.</b> ${esc(S.status.ai.problem)} Until then, clips are picked by loudness.</span><button class="btn sm primary" data-act="go" data-v="settings" data-tab="ai">Open AI settings</button></div>` : "";
  const diskBar = !toolBar && S.status.disk_full && S.view !== "settings" ? `<div class="vb" role="alert">${ic("warn")}<span><b>The drive RONB Clips saves to is almost full, so it can't take new videos.</b> Free up space on it, or move RONB Clips's folder to a bigger drive.</span><button class="btn sm primary" data-act="go" data-v="settings" data-tab="storage">Open Storage</button></div>` : "";
  put($("#vbanner"), toolBar ? toolBar : diskBar ? diskBar : aiBar ? aiBar : upBar ? upBar : kk && kk.ready === false && S.view !== "usage" ? `<div class="vb" role="status">${ic("bolt")}<span>Add your AI key to let the AI pick clips and write titles. Until then, clips are picked by loudness.</span>${S.view !== "settings" ? `<button class="btn sm primary" data-act="go" data-v="settings" data-tab="ai">${ic("plus")}Add AI key</button>` : ""}</div>` : "");
  put($("#tpic"), pic); $("#tpic").classList.toggle("live", !!(c && (c.live_job || liveOn(c).length)));
  put($("#utabs"), tabs); moveInd(); $(".topbar").classList.toggle("tabbed", !!tabs);
  put($("#title"), `<span>${esc(title)}</span>`); put($("#subtitle"), subHtml ?? esc(sub)); put($("#actions"), act);
}

/* ============================================================ overview */
function setLoad(key, msg, failed=false) { const box = document.querySelector(`#${key === "usage" ? "usage" : "ov"}`); if (box) put(box, `<div class="load-state${failed ? " failed" : ""}" role="${failed ? "alert" : "status"}">${esc(msg)}${failed ? ` <button class="btn sm" data-act="retryload" data-load="${key}">Try again</button>` : ""}</div>`); }
function clearLoad(key) { const box = document.querySelector(`#${key === "usage" ? "usage" : "ov"}`); if (box) put(box, ""); }
export async function loadOverview() { setLoad("overview", "Loading overview..."); try { S.overview = await api("/api/overview"); clearLoad("overview"); drawOverview(); } catch (e) { setLoad("overview", `Could not load overview: ${e.message}`, true); } }
function nextSteps(o) {
  const st = [];
  if (!S.creators.length) st.push(["Add your first creator", "Give each streamer their own workspace, settings and folder.", "newcreator"]);
  if (!S.jobs.length) st.push(["Make your first clips", "Paste a video link in the box above.", "focusurl"]);
  if (!S.accounts.length) st.push(["Connect a posting account", "YouTube, Instagram or TikTok, in one click.", "go:settings"]);
  if (S.creators.length && !S.creators.some(c => c.auto_watch)) st.push(["Turn on auto-record", "Record by itself whenever a creator goes live.", "go:c:" + S.creators[0].id + ":setup"]);
  return st;
}
export function drawOverview() {
  const o = S.overview, box = $("#ov"); if (!o || !box) return;
  const sum = k => o.creators.reduce((a, r) => a + r[k], 0);
  const d14 = k => (o.daily || []).reduce((a, d) => a + d[k], 0);
  const state = r => r.state === "recording" ? `<span class="pill live"><span class="dot"></span>Recording</span>` : r.state === "waiting" ? `<span class="pill warn">Waiting for live</span>` : `<span class="pill">Idle</span>`;
  const pst = p => p.status === "posted" ? `<span class="pill ok">Posted</span>` : p.status === "failed" ? `<span class="pill bad">Failed</span>` : p.status === "posting" ? `<span class="pill warn">Posting now</span>` : `<span class="pill warn">${clock(p.due_at)}</span>`;
  const steps = nextSteps(o);
  put(box, `<div class="tiles">
      <div class="tile"><i>${ic("radio")}</i><b>${o.recording}</b><span>Recording now</span></div>
      <div class="tile"><i>${ic("scissors")}</i><b>${sum("clips_today")}</b><span>Clips today</span></div>
      <div class="tile"><i>${ic("send")}</i><b>${sum("posted_today")}</b><span>Posted today</span></div>
      <div class="tile"><i>${ic(sum("failed") ? "warn" : "clock")}</i><b>${sum("queued")}</b><span>Waiting to post${sum("failed") ? ` Â· <span style="color:var(--bad)">${sum("failed")} failed</span>` : ""}</span></div></div>
    <div class="home-grid">
      <div class="card"><div class="card-head"><div><h2>Activity</h2><p class="sub">Last 14 days</p></div>
          <div class="kpis"><div class="kpi"><b>${d14("clips")}</b><span>Clips made</span></div><div class="kpi"><b>${d14("posted")}</b><span>Posted</span></div>
            <div class="kpi"><b>${sum("views").toLocaleString()}</b><span>Views</span></div><div class="kpi"><b>$${sum("earnings").toFixed(2)}</b><span>Est. earnings</span></div></div></div>
        <div class="legend"><span><i style="background:var(--brand)"></i>Clips made</span><span><i style="background:var(--brand-ink)"></i>Posted</span></div>
        ${barChart(o.daily || [])}</div>
      ${aiCard()}
    </div>
    ${steps.length ? `<div class="card"><div class="card-head"><div><h2>Next steps</h2><p class="sub">Finish setting up RONB Clips</p></div></div>
      <div class="steps across">${steps.map((x, i) => `<button class="step" data-act="step" data-to="${esc(x[2])}"><span class="n">${i + 1}</span><span><b>${esc(x[0])}</b><span class="d">${esc(x[1])}</span></span>${ic("arrowr")}</button>`).join("")}</div></div>` : ""}
    <div class="card"><div class="card-head"><div><h2>Creators</h2><p class="sub">Each creator's status and results</p></div></div>
      ${o.creators.length ? `<div class="scroll"><table><tr><th>Creator</th><th>Status</th><th>Auto-post</th><th class="num">Clips today</th><th class="num">Posted today</th><th class="num">Waiting</th><th class="num">Failed</th><th class="num">Views</th><th class="num">Est. $</th></tr>
      ${o.creators.map(r => `<tr><td><a class="lnk" href="#" data-act="go" data-v="c:${esc(r.id)}">${esc(r.name)}</a></td><td>${state(r)}</td><td>${r.autopost ? `<span class="pill ok">On</span>` : `<span class="pill">Off</span>`}</td><td class="num">${r.clips_today} <span class="sub">/ ${r.clips_total}</span></td><td class="num">${r.posted_today} <span class="sub">/ ${r.posted_total}</span></td><td class="num">${r.queued}</td><td class="num">${r.failed}</td><td class="num">${r.views.toLocaleString()}</td><td class="num">${r.earnings.toFixed(2)}</td></tr>`).join("")}</table></div>`
      : `<div class="empty"><img src="/brand/favicon.svg" alt=""><b>No creators yet</b>Add one to give each streamer their own workspace.</div>`}</div>
    <div class="card"><div class="card-head"><div><h2>Post queue</h2><p class="sub">${o.paused ? "Paused. Recording and clipping carry on; nothing is posted." : "Queued clips go out at their scheduled time."}</p></div>
        <button class="btn ${o.paused ? "primary" : ""}" data-act="pause">${ic(o.paused ? "play" : "pause")}${o.paused ? "Resume posting" : "Pause all posting"}</button></div>
      ${o.posts.length ? `<div class="scroll"><table class="stack"><tr><th>When</th><th>Clip</th><th>Creator</th><th>Account</th><th class="num">Views</th><th></th></tr>
      ${o.posts.map(p => `<tr><td data-l="Status">${pst(p)}</td><td data-l="Clip" class="c-main">${p.url ? `<a class="lnk" href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a>` : esc(p.title)}${p.error ? `<div class="err" style="margin:2px 0 0">${esc(p.error)}</div>` : ""}</td><td data-l="Creator">${esc(p.creator_name || "None")}</td><td data-l="Account">${esc(p.account_name)}</td><td data-l="Views" class="num">${p.views ?? "â€“"}</td>
        <td class="c-act" style="text-align:right">${p.status === "scheduled" ? `<button class="btn sm" data-act="pcancel" data-id="${p.id}">Cancel</button>` : p.status === "failed" ? `<button class="btn sm" data-act="pretry" data-id="${p.id}">Retry</button>` : ""}</td></tr>`).join("")}</table></div>`
      : `<p class="help">Nothing queued or posted yet.</p>`}</div>`);
}
export async function loadUsage() {
  setLoad("usage", "Loading AI usage...");
  try { S.usage = await api("/api/usage"); clearLoad("usage"); } catch (e) { setLoad("usage", `Could not load AI usage: ${e.message}`, true); return; }
  S.status.ai = {...S.status.ai, ready: S.usage.ready, provider: S.usage.provider, model: S.usage.model, today_calls: S.usage.today_calls, today_tokens: S.usage.today_tokens};
  drawShell(); drawUsage();
}
export function drawUsage() {
  const k = S.usage, box = $("#usage"); if (!k || !box) return;
  box.innerHTML = `${k.ready ? "" : `<div class="note">${ic("warn")}<span>No AI key is set, so clips are picked by loudness only. Add your key under Settings, AI.</span></div>`}
    <div class="tiles five" style="margin-top:${k.ready ? 0 : 16}px">
      <div class="tile"><i>${ic("bolt")}</i><b>${k.ready ? esc(k.provider || "Ready") : "Not set up"}</b><span>${k.ready && k.model ? esc(k.model) : "AI service"}</span></div>
      <div class="tile"><i>${ic("clock")}</i><b>${(k.today_calls || 0).toLocaleString()}</b><span>AI calls today</span></div>
      <div class="tile"><i>${ic("chart")}</i><b>${fmtK(k.today_tokens)}</b><span>Tokens today</span></div>
      <div class="tile"><i>${ic("scissors")}</i><b>${(k.calls_total || 0).toLocaleString()}</b><span>AI calls in total</span></div>
      <div class="tile"><i>${ic("receipt")}</i><b>${fmtK(k.tokens_total)}</b><span>Tokens in total</span></div></div>
    <div class="card"><div class="card-head"><div><h2>Tokens per day</h2><p class="sub">Last 30 days. Your AI provider bills you for these directly.</p></div></div>${tokenChart(k.daily || [])}</div>
    <div class="split">
      <div class="card"><div class="card-head"><div><h2>What it was used for</h2><p class="sub">By kind of AI work, all time</p></div></div>${hbars(k.by_purpose || [])}</div>
      <div class="card"><div class="card-head"><div><h2>By creator</h2><p class="sub">All time</p></div></div>${hbars(k.by_creator || [])}</div>
    </div>
    <div class="card"><div class="card-head"><div><h2>Recent AI activity</h2><p class="sub">The latest ${(k.recent || []).length} AI calls</p></div></div>
      ${(k.recent || []).length ? `<div class="scroll"><table><tr><th>When</th><th>What</th><th>Creator</th><th>Video</th><th class="num">Tokens</th></tr>
      ${k.recent.map(u => `<tr><td class="sub">${when(u.t)}</td><td>${esc(u.purpose)}</td><td>${esc(u.creator_name || "None")}</td><td class="sub">${esc(u.where || "")}</td><td class="num">${(u.tokens || 0).toLocaleString()}</td></tr>`).join("")}</table></div>`
      : `<p class="help">No AI calls yet.</p>`}</div>`;
}

