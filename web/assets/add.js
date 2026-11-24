import { $, $$, esc, api, send, taskResult, toast, avatarUrl } from './ui.js';
import { ic } from './icons.js';
import { S, DRAFTS, creator, creatorId, H } from './state.js';

/* ============================================================ add bar */
export function drawAdd(variant) {
  const c = creator();
  $("#add").innerHTML = `<form class="composer ${variant === "hero" ? "big" : "compact"}" id="addform">
    <div class="composer-input">${ic("link")}<input id="url" placeholder="Paste a YouTube, Twitch or Kick video link" autocomplete="off" aria-label="Video link"></div>
    <div class="att" id="att"><div><div class="att-in" id="attIn"></div></div></div>
    <div class="composer-bar">
      <button type="button" class="chip" data-act="pickfile">${ic("up")}Upload video</button>
      ${c ? "" : picker("o_creator", "File under creator", [{v:"", label:"No creator", icon:"user"}, ...S.creators.map(x => ({v:x.id, label:x.name, img:x.profile?.pic && x.profile.channel === x.channel ? avatarUrl(x) : ""}))], "")}
      <button type="button" class="chip" data-act="toggleopts" aria-expanded="false">${ic("sliders")}Options</button>
      <span class="grow"></span><span class="hint">or drop a video file here</span>
      <button class="btn primary" id="goBtn">${ic("scissors")}Make clips</button>
    </div>
    <div class="composer-opts" inert><div class="og">
      <div class="ob wide"><div class="ot">Clips</div><div class="nsteps">
        ${stepper("o_max", "How many", c?.max_clips ?? 6, 1, 20, 1, "")}
        ${stepper("o_min", "Shortest", c?.min_len ?? 15, 5, 120, 5, "s")}
        ${stepper("o_maxlen", "Longest", c?.max_len ?? 60, 10, 180, 5, "s")}
      </div></div>
      <div class="ob"><div class="ot cap-head"><span id="o_style_label">Caption style</span>
        <label><input type="checkbox" id="o_captions" data-caption-toggle ${c?.captions === false ? "" : "checked"}>Generate captions</label></div>
        ${choice("o_style", Object.entries(S.status.styles).map(([k, v]) => ({v:k, label:v, prev:capPrev(k)})), c?.style ?? "bounce", c?.captions === false)}</div>
      <div class="ob"><div class="ot">Layout</div>${choice("o_layout", Object.entries(S.status.layouts).map(([k, v]) => ({v:k, label:LAYOUT_SHORT[k] || v, title:v, prev:`<span class="lay-prev ${esc(k)}"><span class="ph"><i></i></span></span>`})), c?.layout ?? "blur")}</div>
      <div class="ob"><div class="ot">Video quality</div><input type="hidden" id="o_quality" value="720"><div class="seg" role="group" aria-label="Video quality">
        <button type="button" data-seg="o_quality" data-v="720" aria-pressed="true">720p<small>Fast</small></button>
        <button type="button" data-seg="o_quality" data-v="1080" aria-pressed="false">1080p<small>Sharper, about 2x slower</small></button></div></div>
      <div class="ob"><div class="ot">Part of the video</div><div class="range">
        <label>From<input id="o_from" type="number" min="0" placeholder="start" aria-label="From minute">min</label>${ic("arrowr")}
        <label>To<input id="o_to" type="number" min="0" placeholder="end" aria-label="Up to minute">min</label></div></div>
    </div></div></form>`;
  drawAtt();
}
const LAYOUT_SHORT = {blur:"Blurred fill", zoom:"Zoomed in", crop:"Fill screen", gameplay:"Gameplay + facecam"};
const CAP_LOOK = {bounce:{font:"'Archivo Black'", hl:"#FFFF00", up:true}, hype:{font:"Anton", hl:"#FF3C3C", up:true}, clean:{font:"Poppins", hl:"#6BD7FF", up:false, w:800}};
function capPrev(k) {
  const s = CAP_LOOK[k] || {font:"inherit", hl:"#2D7DFF", up:true};
  const t = s.up ? ["CLIP", "THIS"] : ["Clip", "this"];
  return `<span class="cap-prev" style="font-family:${s.font},sans-serif;font-weight:${s.w || 400}">${t[0]}&nbsp;<b style="color:${s.hl}">${t[1]}</b></span>`;
}
function picker(id, aria, items, val) {
  const cur = items.find(x => String(x.v) === String(val)) || items[0];
  const av = x => x.img ? `<img class="av" src="${x.img}" alt="">` : `<span class="av">${x.icon ? ic(x.icon) : esc((x.label || "?").trim().slice(0, 1).toUpperCase())}</span>`;
  return `<div class="pick"><input type="hidden" id="${id}" value="${esc(cur.v)}">
    <button type="button" class="chip pick-btn" aria-haspopup="listbox" aria-expanded="false" aria-label="${esc(aria)}">${ic(cur.icon || "user")}<span class="pv">${esc(cur.label)}</span>${ic("caret").replace("<svg", '<svg class="car"')}</button>
    <div class="pick-menu" role="listbox">${items.map(x => `<button type="button" class="pick-opt" role="option" data-v="${esc(x.v)}" aria-selected="${String(x.v) === String(cur.v)}">${av(x)}<span class="pl">${esc(x.label)}</span>${ic("check").replace("<svg", '<svg class="tick"')}</button>`).join("")}${id === "o_creator" ? `${items.length < 2 ? `<p class="pick-empty">No creators yet. Add one to keep each streamer's clips, settings and accounts together.</p>` : ""}<button type="button" class="pick-new" data-act="newcreator"><span class="av">${ic("plus")}</span>New creator</button>` : ""}</div></div>`;
}
function stepper(id, label, val, min, max, step, unit) {
  return `<div class="nstep"><span>${esc(label)}</span><div class="ctl">
    <button type="button" data-step="${id}" data-d="-1" aria-label="Less">${ic("minus")}</button>
    <input id="${id}" type="number" min="${min}" max="${max}" step="${step}" value="${esc(val)}" aria-label="${esc(label)}">${unit ? `<i>${unit}</i>` : ""}
    <button type="button" data-step="${id}" data-d="1" aria-label="More">${ic("plus")}</button></div></div>`;
}
function choice(id, items, val, disabled = false) {
  const captions = id === "o_style";
  return `<input type="hidden" id="${id}" value="${esc(val)}"><div class="tiles3" role="group"${captions ? ' aria-labelledby="o_style_label"' : ""}>${items.map(x =>
    `<button type="button" class="tile-o" data-seg="${id}" data-v="${esc(x.v)}" aria-pressed="${String(x.v) === String(val)}"${captions ? " data-caption-style" : ""}${disabled ? " disabled" : ""}${x.title ? ` title="${esc(x.title)}"` : ""}>${x.prev}${esc(x.label)}</button>`).join("")}</div>`;
}
const closePicks = except => $$(".pick.open").forEach(p => { if (p !== except) { p.classList.remove("open"); p.querySelector(".pick-btn").setAttribute("aria-expanded", "false"); } });
document.addEventListener("click", e => {
  const pb = e.target.closest(".pick-btn");
  if (pb) { const p = pb.parentElement, open = !p.classList.contains("open"); closePicks(p);
    p.classList.toggle("open", open); pb.setAttribute("aria-expanded", String(open));
    if (open) (p.querySelector('.pick-opt[aria-selected="true"]') || p.querySelector(".pick-opt")).focus(); return; }
  const po = e.target.closest(".pick-opt");
  if (po) { const p = po.closest(".pick"); p.querySelector("input[type=hidden]").value = po.dataset.v;
    p.querySelectorAll(".pick-opt").forEach(o => o.setAttribute("aria-selected", String(o === po)));
    p.querySelector(".pv").textContent = po.querySelector(".pl").textContent; closePicks(); p.querySelector(".pick-btn").focus();
    p.querySelector("input[type=hidden]").dispatchEvent(new Event("change", {bubbles: true})); return; }
  if (!e.target.closest(".pick") || e.target.closest(".pick-new")) closePicks();
  const sg = e.target.closest("[data-seg]");
  if (sg && !sg.disabled) { $("#" + sg.dataset.seg).value = sg.dataset.v;
    sg.parentElement.querySelectorAll("[data-seg]").forEach(b => b.setAttribute("aria-pressed", String(b === sg))); return; }
  const st = e.target.closest("[data-step]");
  if (st) { const i = $("#" + st.dataset.step), s = Number(i.step) || 1, d = Number(st.dataset.d);
    const v = Math.min(Number(i.max), Math.max(Number(i.min), (Number(i.value) || 0) + d * s)); i.value = v; }
});
document.addEventListener("keydown", e => {
  const p = e.target.closest?.(".pick"); if (!p) return;
  if (e.key === "Escape" && p.classList.contains("open")) { closePicks(); p.querySelector(".pick-btn").focus(); e.preventDefault(); return; }
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  e.preventDefault();
  if (!p.classList.contains("open")) { p.querySelector(".pick-btn").click(); return; }
  const os = [...p.querySelectorAll(".pick-opt")], i = os.indexOf(document.activeElement);
  os[(i + (e.key === "ArrowDown" ? 1 : -1) + os.length) % os.length].focus();
});
const targetCreator = () => creatorId() || $("#o_creator")?.value || "";
function jobOptions() {
  const n = id => { const v = $(id).value; return v === "" ? null : Number(v); };
  const o = {max_clips:n("#o_max"), min_len:n("#o_min"), max_len:n("#o_maxlen"), captions:$("#o_captions").checked, style:$("#o_style").value, layout:$("#o_layout").value, quality:n("#o_quality")};
  if (n("#o_from") !== null) o.range_start = n("#o_from") * 60;
  if (n("#o_to") !== null) o.range_end = n("#o_to") * 60;
  return o;
}
/* ---------- attachments: nothing starts until Make clips is pressed.
   Each page keeps its own draft (Home, All videos and every creator), so a link typed for one creator never
   shows up under another. Links move out of the box into the list as soon as they are recognised. */
const draftKey = () => creatorId() ? "c:" + creatorId() : S.view;
const att = () => DRAFTS[draftKey()] ||= {files: [], links: [], note: null, busy: false, queued: false};
const VIDEO_EXT = /\.(mp4|mov|mkv|webm|m4v|ts|flv)$/i;
const fmtLen = s => { s = Math.round(s || 0); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = String(s % 60).padStart(2, "0"); return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`; };
const fmtSize = b => b >= 1e9 ? (b / 1e9).toFixed(1) + " GB" : Math.max(1, Math.round(b / 1e6)) + " MB";
const isChannel = u => /^(https?:\/\/)?(www\.)?(twitch\.tv|kick\.com)\/[A-Za-z0-9_]+\/?$/.test(u) || /youtube\.com\/(@[^/]+|channel\/[^/]+)\/?(live|videos|streams)?\/?$/.test(u);
const looksLink = u => /^https?:\/\/([\w-]+\.)*(youtube\.com|youtu\.be|twitch\.tv|kick\.com)\//i.test(u);
const fullLink = u => /[?&]v=[\w-]{11}\b|youtu\.be\/[\w-]{11}\b|\/(shorts|live|embed)\/[\w-]{11}\b|twitch\.tv\/videos\/\d{6,}|clips\.twitch\.tv\/\w|twitch\.tv\/\w+\/clip\/\w|kick\.com\/[\w-]+\/(videos?|clips?)\/[\w-]{6,}/i.test(u);
const batchMax = () => S.status.max_batch || 5;
const batchUsed = A => A.files.length + A.links.filter(l => l.state !== "bad").length;
const newId = () => Math.random().toString(36).slice(2);

export function attach(list) {
  const A = att(), max = (S.status.max_upload_mb || 2048) * 1024 * 1024;
  let full = false;
  for (const f of list || []) {
    if (!(f.type.startsWith("video/") || VIDEO_EXT.test(f.name))) { toast(`${f.name} is not a video file.`, "bad"); continue; }
    if (f.size > max) { toast(`${f.name} is over the ${S.status.max_upload_mb} MB limit.`, "bad"); continue; }
    if (A.files.some(x => x.file.name === f.name && x.file.size === f.size)) continue;
    if (batchUsed(A) >= batchMax()) { full = true; continue; }
    A.files.push({id: newId(), file: f, src: URL.createObjectURL(f), dur: 0, state: "ready", pct: 0, err: ""});
  }
  if (full) toast(`You can send up to ${batchMax()} videos at a time. Start these, then add more.`, "bad");
  drawAtt();
}
function canGo(A = att()) { return !A.busy && (A.files.length > 0 || A.links.some(l => l.state === "ok")); }

/* one row style for everything: picture on the left, name and details, state on the right */
function rowHtml(A, {id, kind, thumb, len, title, sub, side, bar}) {
  return `<div class="lp" data-row="${id}"><div class="th">${thumb}${len ? `<span class="du">${len}</span>` : ""}</div>
    <div class="tx"><b title="${esc(title)}">${esc(title)}</b><span>${sub}</span></div>${side || ""}
    ${A.busy ? "" : `<button type="button" class="rm" data-rm="${kind}" data-id="${id}" aria-label="Remove ${esc(title)}">${ic("x")}</button>`}
    ${bar != null ? `<i class="pbar" style="width:${bar}%"></i>` : ""}</div>`;
}
function drawAtt() {
  const box = $("#attIn"); if (!box) return;
  const A = att();
  let h = A.note ? `<div class="lp bad">${ic("warn")}<span>${esc(A.note)}</span></div>` : "";
  h += A.links.map(l => {
    if (l.state === "wait") return `<div class="lp wait" data-row="${l.id}"><div class="th"></div><div class="tx"><span class="sk" style="width:62%"></span><span class="sk" style="width:34%"></span></div>
      ${A.busy ? "" : `<button type="button" class="rm" data-rm="link" data-id="${l.id}" aria-label="Remove link">${ic("x")}</button>`}</div>`;
    if (l.state === "bad") return `<div class="lp bad" data-row="${l.id}">${ic("warn")}<span><b>${esc(l.url)}</b>${esc(l.msg)}</span>
      ${A.busy ? "" : `<button type="button" class="rm" data-rm="link" data-id="${l.id}" aria-label="Remove link">${ic("x")}</button>`}</div>`;
    const i = l.info;
    return rowHtml(A, {id: l.id, kind: "link", thumb: i.thumb ? `<img src="${i.thumb}" alt="">` : "", len: i.duration && !i.is_live ? fmtLen(i.duration) : "",
      title: i.title, sub: esc(i.uploader || "Video link"),
      side: l.sending ? `<span class="ok up">Sending</span>` : i.is_live ? `<span class="ok live"><i></i>Live now</span>` : `<span class="ok">${ic("check")}Ready</span>`});
  }).join("");
  h += A.files.map(f => rowHtml(A, {id: f.id, kind: "file", thumb: `<video src="${f.src}#t=1" muted preload="metadata" playsinline></video>`,
    len: f.dur ? fmtLen(f.dur) : "", title: f.file.name,
    sub: f.state === "error" ? `<em>${esc(f.err)}</em>` : fmtSize(f.file.size) + " · uploaded file",
    side: f.state === "up" ? `<span class="ok up">${f.pct < 100 ? f.pct + "%" : "Starting"}</span>` : f.state === "error" ? "" : `<span class="ok">${ic("check")}Ready</span>`,
    bar: f.state === "up" ? f.pct : null})).join("");
  const n = batchUsed(A);
  if (n && !A.busy && n < batchMax())
    h += `<button type="button" class="addrow" data-act="addmore">${ic("plus")}Add another video<span>${n} of ${batchMax()}</span></button>`;
  if (box.innerHTML !== h) box.innerHTML = h;
  box.querySelectorAll("video").forEach(v => v.addEventListener("loadedmetadata", () => {
    const f = att().files.find(x => x.id === v.closest(".lp").dataset.row);
    if (f && !f.dur) { f.dur = v.duration; const th = v.parentElement; if (!th.querySelector(".du")) th.insertAdjacentHTML("beforeend", `<span class="du">${fmtLen(f.dur)}</span>`); }
  }, {once: true}));
  $("#att")?.classList.toggle("has", !!h);
  const u = $("#url"); if (u) u.placeholder = A.links.length || A.files.length ? "Paste another link" : "Paste a YouTube, Twitch or Kick video link";
  const b = $("#goBtn");
  if (b) { b.disabled = A.busy;
    const k = A.files.length + A.links.filter(l => l.state === "ok").length;
    b.innerHTML = A.busy ? `${ic("up")}Sending…` : A.queued ? `${ic("clock")}Finding video…` : `${ic("scissors")}Make clips${k > 1 ? ` (${k})` : ""}`; }
}

async function lookUp(u) {
  const start = () => api("/api/preview?url=" + encodeURIComponent(u)).then(taskResult);
  try { return await start(); }
  catch (e) {
    if (e instanceof TypeError) {           // the request itself dropped (network blip): try once more quietly
      await new Promise(r => setTimeout(r, 800));
      try { return await start(); }
      catch (e2) { throw new Error(e2 instanceof TypeError ? "Couldn't reach RONB Clips to check that link. Check your connection and paste it again." : e2.message); }
    }
    throw e;
  }
}
function takeLink(A, u, input) {
  if (A.links.some(l => l.url === u && l.state !== "bad")) { A.note = null; if (input) input.value = ""; return drawAtt(); }
  if (batchUsed(A) >= batchMax()) { A.note = `You can send up to ${batchMax()} videos at a time. Remove one first.`; return drawAtt(); }
  const l = {id: newId(), url: u, state: "wait"};
  A.links.push(l); A.note = null;
  if (input) input.value = "";            // the box is free for the next link straight away
  drawAtt();
  lookUp(u).then(info => { l.state = "ok"; l.info = info; }, e => { l.state = "bad"; l.msg = e.message; }).then(() => {
    drawAtt();
    if (A.queued && !A.links.some(x => x.state === "wait")) {
      A.queued = false;
      if (canGo(A) && att() === A) makeClips(); else { drawAtt(); if (!canGo(A)) toast("That link couldn't be used. The reason is shown on it.", "bad"); }
    }
  });
}
let linkTimer = 0;
export function onLink(input) {
  clearTimeout(linkTimer);
  const A = att(), u = input.value.trim();
  if (!u) { A.note = null; return drawAtt(); }
  if (isChannel(u)) { A.note = "That's a channel, not a video. To clip their live streams, add them as a creator and press Start recording."; return drawAtt(); }
  if (!looksLink(u)) { A.note = u.length > 10 ? "Paste a full YouTube, Twitch or Kick video link." : null; return drawAtt(); }
  A.note = null; drawAtt();
  if (fullLink(u)) return takeLink(A, u, input);
  linkTimer = setTimeout(() => { if (input.value.trim() === u) takeLink(A, u, input); }, 900);   // other link shapes: take it once typing stops
}
document.addEventListener("input", e => { if (e.target.id === "url") onLink(e.target);
  if (e.target.closest?.("#cform") && e.target.dataset.k && !/checkbox|radio/.test(e.target.type)) H.dirty(true); });
document.addEventListener("keydown", e => { if (e.target.id === "url" && e.key === "Enter" && e.target.value.trim() && looksLink(e.target.value.trim())) { e.preventDefault(); clearTimeout(linkTimer); takeLink(att(), e.target.value.trim(), e.target); } });
document.addEventListener("click", e => {
  const r = e.target.closest("[data-rm]"), A = att(); if (!r || A.busy) return;
  if (r.dataset.rm === "link") A.links = A.links.filter(l => l.id !== r.dataset.id);
  else { const i = A.files.findIndex(f => f.id === r.dataset.id); if (i >= 0) { URL.revokeObjectURL(A.files[i].src); A.files.splice(i, 1); } }
  if (!A.links.some(l => l.state === "wait")) A.queued = false;
  drawAtt();
});

// A big file goes in pieces (anything standing in front of the app refuses one huge request), two at a time,
// and a piece that fails is simply sent again. The bar shows the whole file.
async function sendBig(f, who, options) {
  const file = f.file;
  const s = await send("/api/upload/start", {name: file.name, size: file.size, options, creator: who});
  const sent = new Array(s.parts).fill(0);
  const show = () => { f.pct = Math.min(99, Math.round(sent.reduce((a, b) => a + b, 0) / file.size * 100)); drawAtt(); };
  const put = i => new Promise((ok, fail) => {
    const x = new XMLHttpRequest();
    x.open("PUT", `/api/upload/${s.id}/${i}`);
    x.setRequestHeader("Content-Type", "application/octet-stream");
    x.upload.onprogress = e => { sent[i] = e.loaded; show(); };
    x.onload = () => {
      if (x.status === 401) { location.href = "/"; return; }
      if (x.status >= 200 && x.status < 300) { sent[i] = Math.min(s.part, file.size - i * s.part); show(); return ok(); }
      let d = "Upload failed"; try { d = JSON.parse(x.responseText).detail || d; } catch {}
      const err = new Error(typeof d === "string" ? d : "Upload failed"); err.final = x.status >= 400 && x.status < 500 && x.status !== 408 && x.status !== 429; fail(err);
    };
    x.onerror = x.ontimeout = () => fail(new Error("Upload failed. Check your connection."));
    x.send(file.slice(i * s.part, Math.min(file.size, (i + 1) * s.part)));
  });
  let next = 0;
  const worker = async () => {
    while (next < s.parts) {
      const i = next++;
      for (let tries = 0; ; tries++) {
        try { await put(i); break; }
        catch (e) { sent[i] = 0; if (e.final || tries >= 3) throw e; await new Promise(r => setTimeout(r, 1500 * (tries + 1))); }
      }
    }
  };
  try {
    await Promise.all([worker(), worker()]);
    await send(`/api/upload/${s.id}/finish`, {});
    f.pct = 100; drawAtt();
  } catch (e) {
    next = s.parts;                                   // stop the other sender
    api(`/api/upload/${s.id}`, {method: "DELETE"}).catch(() => {});
    throw e;
  }
}
function sendFile(f, who, options) {
  if (S.status.upload_part_mb && f.file.size > S.status.upload_part_mb * 1048576) return sendBig(f, who, options);
  return new Promise((ok, fail) => {
    const x = new XMLHttpRequest(), fd = new FormData();
    fd.append("file", f.file); fd.append("options", JSON.stringify(options)); fd.append("creator", who);
    x.open("POST", "/api/upload");
    x.upload.onprogress = e => { if (e.lengthComputable) { f.pct = Math.round(e.loaded / e.total * 100); drawAtt(); } };
    x.onload = () => {
      if (x.status === 401) { location.href = "/"; return; }
      if (x.status >= 200 && x.status < 300) return ok();
      let d = "Upload failed"; try { d = JSON.parse(x.responseText).detail || d; } catch {}
      fail(new Error(typeof d === "string" ? d : "Upload failed"));
    };
    x.onerror = () => fail(new Error("Upload failed. Check your connection."));
    x.send(fd);
  });
}
export async function makeClips() {
  const A = att();
  if (A.busy) return;
  const typed = $("#url")?.value.trim();
  if (typed && looksLink(typed) && !isChannel(typed)) { clearTimeout(linkTimer); takeLink(A, typed, $("#url")); }
  if (A.links.some(l => l.state === "wait")) {   // still looking a link up: start as soon as it is found
    A.queued = true; drawAtt();
    toast("Finding the video first. Clipping starts as soon as it's found.");
    return;
  }
  if (!canGo(A)) {
    toast(A.note || "Paste a YouTube, Twitch or Kick video link, or add a video file first.", "bad");
    $("#url")?.focus();
    return;
  }
  A.busy = true; drawAtt();
  let started = 0, failed = 0;
  const who = targetCreator(), opts = jobOptions();
  for (const l of A.links.filter(x => x.state === "ok")) {
    l.sending = true; drawAtt();
    try { await send("/api/jobs", {url: l.url, options: opts, creator: who}); l.state = "done"; started++; }
    catch (e) { l.state = "bad"; l.msg = e.message; failed++; }
    l.sending = false; drawAtt();
  }
  for (const f of A.files) {
    f.state = "up"; f.pct = 0; drawAtt();
    try { await sendFile(f, who, opts); f.state = "done"; started++; }
    catch (e) { f.state = "error"; f.err = e.message; failed++; }
    drawAtt();
  }
  A.links = A.links.filter(l => l.state !== "done");
  A.files = A.files.filter(f => { if (f.state === "done") { URL.revokeObjectURL(f.src); return false; } return true; });
  A.busy = false; drawAtt();
  if (started) {
    toast(`${started === 1 ? "Your video is" : started + " videos are"} being clipped now.`, "ok");
    if (!failed && S.view === "overview") H.go(who ? "c:" + who : "library"); else H.tick();
  } else if (failed) toast("Nothing was started. The reason is shown on the video.", "bad");
}
