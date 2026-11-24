import { $, $$, esc, fmt, clock, when, opts, api, send, toast, put, ask, askTyped, spark, avatarUrl, fmtK } from './ui.js';
import { ic } from './icons.js';
import { S, D, creator, creatorId, jobOf, sel, H } from './state.js';
import { platLogo, PLAT } from './shell.js';

/* ============================================================ clips + jobs */
function scoreCls(s) { return s >= 80 ? "s-hi" : s >= 60 ? "s-mid" : ""; }
function postPills(c) {
  return Object.values(c.posts || {}).filter(p => p.status !== "cancelled").map(p => {
    const cls = p.status === "posted" ? "ok" : p.status === "failed" ? "bad" : "warn";
    const txt = `${esc(p.account_name)} · ${p.status === "scheduled" ? clock(p.due_at) : p.status}`;
    return p.url ? `<a class="pill ${cls}" href="${esc(p.url)}" target="_blank" rel="noopener">${txt}</a>` : `<span class="pill ${cls}" title="${esc(p.error || "")}">${txt}</span>`;
  }).join("");
}
function clipHtml(j, c) {
  const m = `/media/${j.id}/`, a = `data-j="${j.id}" data-c="${esc(c.id)}"`;
  const body = c.status === "done" ? `<img src="${m}${esc(c.cover)}?v=${c.version}" alt="" loading="lazy"><span class="veil"><span>${ic("play")}</span></span>`
    : c.status === "error" ? `<span class="making" style="animation:none;color:var(--bad);background:var(--bad-soft)">Could not make this clip. Open it to try again.</span>`
    : `<span class="making">${c.status === "rendering" ? "Making this clip…" : "Waiting its turn…"}</span>`;
  const key = `c:${j.id}:${c.id}`, busyClip = c.status === "queued" || c.status === "rendering";
  return `<article class="clip ${sel().has(key) ? "picked" : ""}">
    <button class="shot" data-act="openclip" ${a} aria-label="Open clip: ${esc(c.title || c.hook)}">${body}
      <span class="badge l ${scoreCls(c.score)}" title="AI score">${c.score || "Hand"}</span><span class="badge r">${fmt(c.end - c.start)}</span></button>
    <div class="clip-body">
      <div class="hook">${esc(c.hook || "Untitled clip")}</div>
      <div class="ttl">${esc(c.title)}</div>
      ${postPills(c) ? `<div class="row" style="gap:4px">${postPills(c)}</div>` : ""}
      <div class="clip-foot">
        ${busyClip ? "" : `<label class="selck" title="Select this clip"><input type="checkbox" data-sel="${key}" ${sel().has(key) ? "checked" : ""} aria-label="Select this clip"></label>`}
        ${c.status === "done" ? `<a class="btn sm icon ghost" href="${m}${esc(c.file)}?download=1" title="Download" aria-label="Download">${ic("down")}</a>` : ""}
        <button class="btn sm icon ghost" data-act="copy" ${a} title="Copy post text" aria-label="Copy post text">${ic("copy")}</button>
        ${busyClip ? "" : `<button class="btn sm icon ghost" data-act="delclip" ${a} title="Delete this clip" aria-label="Delete this clip">${ic("trash")}</button>`}
        <span class="grow"></span>
        ${c.status === "done" ? `<button class="btn sm" data-act="openclip" ${a}>${ic("send")}Post</button>` : ""}
      </div>
    </div></article>`;
}
const EVERY = [[1, "1 min", ""], [2, "2 min", ""], [3, "3 min", ""], [5, "5 min", ""], [10, "10 min", ""]];
function everyPick(cid, v) {
  const cur = EVERY.find(x => x[0] === Number(v)) || EVERY[2];
  return `<div class="pick evpick"><input type="hidden" data-every="${esc(cid || "")}" value="${cur[0]}">
    <button type="button" class="evbtn pick-btn" aria-haspopup="listbox" aria-expanded="false" aria-label="How often the AI checks"><span class="pv">${cur[1]}</span>${ic("caret").replace("<svg", '<svg class="car"')}</button>
    <div class="pick-menu" role="listbox">${EVERY.map(([k, l, h]) => `<button type="button" class="pick-opt" role="option" data-v="${k}" aria-selected="${k === cur[0]}"><span class="pl">${l}</span>${h ? `<small class="ph">${h}</small>` : ""}${ic("check").replace("<svg", '<svg class="tick"')}</button>`).join("")}</div></div>`;
}
function liveHtml(j) {
  const L = j.live || {elapsed:0, lines:[], level:[], chat:null, checks:0, buffer_sec:0, pending_clips:0};
  return `<div class="livecard">
    <div class="lc-head"><div class="lc-title"><span class="pill live"><span class="dot"></span>Live</span>
        <h3>${esc(j.title || j.source.name)}</h3><p class="sub">${esc(j.uploader)}${j.uploader ? " · " : ""}${esc(j.stage)}</p></div>
      <div class="lc-acts"><button class="btn primary" data-act="clipnow" data-j="${j.id}" data-s="30">${ic("scissors")}Clip last 30s</button>
        <button class="btn" data-act="clipnow" data-j="${j.id}" data-s="60">Last 60s</button>
        <button class="btn danger" data-act="stoplive" data-j="${j.id}">${ic("stop")}Stop</button></div></div>
    <div class="stats"><div class="stat"><b>${fmt(L.elapsed)}</b><span>Recorded</span></div><div class="stat"><b>${Math.round(L.buffer_sec / 60)} min</b><span>Kept in buffer</span></div>
      <div class="stat">${everyPick(j.creator, L.every_min ?? 3)}<span>${L.every_min === 0 ? (L.auto?.warming ? "AI checks: learning the stream" : L.auto?.waiting ? "AI checks: jump spotted, checking soon" : `AI checks on jumps · ${L.auto?.spikes || 0} so far`) : "AI checks every"}</span></div>
      <div class="stat"><b>${L.checks}</b><span>AI calls${L.skipped ? ` · ${L.skipped} skipped` : ""}${L.rejected ? ` · ${L.rejected} turned down` : ""}</span></div><div class="stat"><b>${j.clips.length}</b><span>Clips so far</span></div>
      ${L.pending_clips ? `<div class="stat"><b>${L.pending_clips}</b><span>Hand clip being prepared</span></div>` : ""}</div>
    <div class="meters">
      <div class="meter"><h4>Loudness, last minute</h4>${spark(L.level, "", -55, -8)}</div>
      ${L.chat ? `<div class="meter"><h4>Chat messages per second</h4>${spark(L.chat, "chat", 0, Math.max(4, ...L.chat))}</div>` : ""}
      <div class="meter"><h4>Latest AI decisions</h4><div class="said" style="justify-content:flex-start;color:var(--text)">${(L.verdicts || []).length ? L.verdicts.slice(0, 4).map(v => `<div>${fmt(v.t)} · ${v.transcript != null ? "words " + v.transcript : ""}${v.reaction != null ? " · reaction " + v.reaction : ""} · <span class="sub">${esc(v.result)}</span></div>`).join("") : "<div class='sub'>First check soon…</div>"}</div></div>
      <div class="meter"><h4>What was just said</h4><div class="said">${L.lines.length ? L.lines.map(t => `<div>${esc(t)}</div>`).join("") : "<div>Listening…</div>"}</div></div>
    </div>${j.error ? `<div class="err">${esc(j.error)}</div>` : ""}</div>
    ${j.clips.length ? `<div class="grid" style="margin-bottom:28px">${[...j.clips].reverse().map(c => clipHtml(j, c)).join("")}</div>` : ""}`;
}
function jobHtml(j) {
  if (j.status === "live") return liveHtml(j);
  const busy = j.status === "queued" || j.status === "running";
  const who = !creatorId() && j.creator ? S.creators.find(c => c.id === j.creator)?.name : "";
  const meta = [who, j.uploader, j.duration ? fmt(j.duration) + " long" : "", j.clips.length ? j.clips.length + (j.clips.length === 1 ? " clip" : " clips") : "",
                j.took ? "made in " + fmt(j.took) : "", j.kind === "live" ? "recording, " + j.stage.toLowerCase() : ""].filter(Boolean).map(esc).join(" · ");
  const pic = j.thumb ? `<img src="/media/${esc(j.id)}/thumb.jpg" alt="" loading="lazy">` : ic(j.source?.type === "file" ? "film" : "link");
  return `<div class="job-head"><div class="jt"><span class="jthumb">${pic}${j.duration ? `<span class="du">${fmt(j.duration)}</span>` : ""}</span><div style="min-width:0"><h3>${esc(j.title || j.source.name)}</h3><p class="sub">${meta}</p></div></div>
      <div class="row" style="flex-wrap:nowrap;gap:4px">${busy ? "" : `
        ${j.clips.some(c => c.status === "done") ? `<button class="btn sm icon ghost" data-act="dlall" data-j="${j.id}" title="Download all clips of this video" aria-label="Download all clips of this video">${ic("down")}</button>` : ""}
        <button class="btn sm icon ghost" data-act="deljob" data-j="${j.id}" title="Delete" aria-label="Delete this video and its clips">${ic("trash")}</button>
        <label class="selck" title="Select this video and its clips"><input type="checkbox" data-sel="j:${j.id}" ${sel().has("j:" + j.id) ? "checked" : ""} aria-label="Select this video"></label>`}</div></div>
    ${busy ? `<div class="bar"><i style="width:${Math.round(j.progress * 100)}%"></i></div><p class="sub">${esc(j.stage)} · ${Math.round(j.progress * 100)}%</p>` : ""}
    ${j.status === "done" && !j.clips.length ? `<p class="sub">${esc(j.stage)}</p>` : ""}
    ${j.error ? `<div class="err">${esc(j.error)}<button class="rep" data-act="report" data-ctx="${esc("Video failed: " + (j.title || j.source.name || "") + " | " + j.error)}">Report this</button></div>` : ""}${j.ai?.note ? `<p class="sub">${esc(j.ai.note)}</p>` : ""}
    ${j.clips.length ? `<div class="grid">${j.clips.map(c => clipHtml(j, c)).join("")}</div>` : ""}`;
}
/* ---- selecting several videos and clips: delete or download them together */
export function selParts() {
  const jobsSel = [], clipsSel = [];
  for (const k of sel()) { const p = k.split(":"); if (p[0] === "j") jobsSel.push(p[1]); else clipsSel.push([p[1], p[2]]); }
  return {jobsSel, clipsSel};
}
export function selClips() {          // every finished clip the selection covers, for downloading
  const {jobsSel, clipsSel} = selParts(), out = new Map();
  jobsSel.forEach(id => (jobOf(id)?.clips || []).forEach(c => { if (c.status === "done") out.set(id + "." + c.id, 1); }));
  clipsSel.forEach(([j, c]) => { if (jobOf(j)?.clips.find(x => x.id === c)?.status === "done") out.set(j + "." + c, 1); });
  return [...out.keys()];
}
export function drawSelBar() {
  let bar = $("#selbar");
  if (!bar) { bar = document.createElement("div"); bar.id = "selbar"; bar.setAttribute("role", "toolbar"); bar.setAttribute("aria-label", "Selected items"); document.body.append(bar); }
  for (const k of [...sel()]) {   // forget what is no longer there
    const p = k.split(":"), j = jobOf(p[1]);
    if (!j || (p[0] === "c" && !j.clips.some(c => c.id === p[2]))) sel().delete(k);
  }
  const n = sel().size, dl = selClips().length, shown = $("#jobs");
  bar.classList.toggle("on", n > 0 && !!shown);
  if (!n) return;
  put(bar, `<b>${n} selected</b>
    <button class="btn sm" data-act="selall">Select all</button>
    <button class="btn sm primary" data-act="seldl" ${dl ? "" : "disabled"}>${ic("down")}Download${dl ? ` ${dl} clip${dl === 1 ? "" : "s"}` : ""}</button>
    <button class="btn sm danger" data-act="seldel">${ic("trash")}Delete</button>
    <button class="btn sm" data-act="selclear">Cancel</button>`);
}
export function drawJobs() {
  const root = $("#jobs"); if (!root) return;
  const cid = creatorId();
  let list = cid ? S.jobs.filter(j => j.creator === cid) : S.jobs;
  const liveBox = $("#live");
  if (liveBox) { const lj = list.find(j => j.status === "live");
    if (!liveBox.querySelector(".pick.open")) put(liveBox, lj ? liveHtml(lj) : "");  // don't redraw under an open menu
    list = list.filter(j => j.status !== "live"); }
  if (!list.length) {
    root.querySelectorAll("section").forEach(e => e.remove());
    if (!$(".empty", root)) root.innerHTML = `<div class="empty"><img src="/brand/favicon.svg" alt=""><b>${cid ? "No clips for this creator yet" : "No videos yet"}</b>${cid ? "Start recording their stream, or paste a link to one of their videos above." : "Paste a link above and the studio will find the best moments and turn them into clips."}</div>`;
    return;
  }
  $(".empty", root)?.remove();
  const ids = new Set(list.map(j => j.id));
  $$("section[data-job]", root).forEach(el => { if (!ids.has(el.dataset.job)) el.remove(); });
  list.forEach((j, i) => {
    let el = $(`section[data-job="${j.id}"]`, root);
    if (!el) { el = document.createElement("section"); el.className = "job"; el.dataset.job = j.id; }
    if (root.children[i] !== el) root.insertBefore(el, root.children[i] || null);
    put(el, jobHtml(j));
  });
}
/* ============================================================ creator pages */
export function costHint(every) {
  every = Number(every) || 3;
  return `About ${Math.round(60 / every * 2)} AI calls per live hour at most. Each check covers everything since the last one, so slower checks don't miss moments; they find them later.`;
}
function channelRows(c) {
  const chs = c.channels || {}, many = Object.keys(chs).length > 1;
  return Object.keys(PLAT).map(k => {
    const url = chs[k], pr = (c.profiles || {})[k];
    if (!url) return `<div class="chan">${platLogo(k)}<input id="chan_${k}" placeholder="${PLAT[k].hint}" aria-label="${PLAT[k].name} channel link">
      <button class="btn sm" data-act="chadd" data-p="${k}">Connect</button></div>`;
    const ok = pr && pr.channel === url;
    return `<div class="chan">${platLogo(k)}${ok && pr.pic ? `<img class="pic" src="${avatarUrl(c, k)}" alt="">` : ""}
      <div class="who"><b>${esc(ok && pr.name ? pr.name : url)}</b><span>${esc([PLAT[k].name, ok ? pr.followers : "Looking it up…"].filter(Boolean).join(" · "))}</span></div>
      ${ok && pr.live_at ? (pr.live ? `<span class="sbadge live"><i></i>Live${pr.viewers ? " · " + fmtK(pr.viewers) : ""}</span>` : `<span class="sbadge">Offline</span>`) : ""}
      ${many ? `<label class="main" title="Start recording uses this channel unless another one is live"><input type="radio" name="mainch" data-act="chmain" data-p="${k}" ${c.channel === url ? "checked" : ""}>Record from</label>` : ""}
      <button class="btn sm icon ghost" data-act="chdel" data-p="${k}" title="Remove" aria-label="Remove ${PLAT[k].name} channel">${ic("x")}</button></div>`;
  }).join("");
}
export async function saveChannels(chs, main) {
  const body = {channels: chs}; if (main) body.channel = main;
  try { await send(`/api/creators/${creatorId()}`, body, "PUT"); await H.tick(); drawCreatorForm(); }
  catch (e) { toast(e.message, "bad"); }
}
export function drawCreatorForm() {
  const c = creator(), box = $("#cform"); if (!c || !box) return;
  const A = S.accounts;
  box.innerHTML = `<div class="card"><h2>Their stream</h2><p class="help">Where to record from, and whether to start by itself.</p>
      <div class="chans">${channelRows(c)}</div>
      <div class="fields" style="margin-top:14px"><label class="sw span"><input type="checkbox" data-k="auto_watch" ${c.auto_watch ? "checked" : ""}><span>Record automatically when they go live<small>Checks straight away, then every two minutes.</small></span></label></div></div>
    <div class="card"><h2>How the AI decides</h2><p class="help">Two separate AI checks. The words judge reads only what was said. The reaction judge sees only how loud the streamer got and how busy chat was, never the words.</p>
      <div class="fields">
        <label class="f">Check the live stream every<select data-k="ai_every_min">${opts({1:"1 minute", 2:"2 minutes", 3:"3 minutes", 5:"5 minutes", 10:"10 minutes"}, c.ai_every_min || 3)}</select><small class="sub" id="costhint" style="font-weight:400">${costHint(c.ai_every_min)}</small></label>
        <p class="help span" style="margin:0">On top of that, the AI always looks straight away when the stream suddenly gets louder or chat gets busier, and when someone says "clip" or "clip it".</p>
        <label class="f">Most live clips per hour<input data-k="live_per_hour" type="number" min="1" max="60" value="${c.live_per_hour}"></label>
        <label class="f">Make a clip when<select data-k="decide_mode">${opts({both:"Both judges say yes", either:"Both say yes, or the words judge is very sure (90+)"}, c.decide_mode)}</select></label>
        <label class="f">Words judge must score at least<input data-k="min_score" type="number" min="0" max="100" value="${c.min_score}"></label>
        <label class="f">Make sure there's a live clip at least every<select data-k="must_clip_min">${opts({0:"Off", 15:"15 minutes", 30:"30 minutes", 60:"1 hour", 120:"2 hours"}, c.must_clip_min)}</select></label>
        <label class="f">Minimum gap between live clips (minutes)<input data-k="min_gap_min" type="number" min="0" max="60" value="${c.min_gap_min}"></label>
        <label class="f span">Always clip the last 30 seconds when someone says<input data-k="trigger_words" value="${esc(c.trigger_words)}" placeholder="clip that, clip it"><small class="sub" style="font-weight:400">Separate phrases with commas. Matched on the transcript, so it needs no AI call.</small></label>
      </div></div>
    <div class="card"><h2>Clips</h2><p class="help">How this creator's clips look.</p>
      <div class="fields"><label class="sw span"><input type="checkbox" data-k="captions" data-caption-toggle ${c.captions === false ? "" : "checked"}>Generate captions</label>
        <label class="f">Caption style<select data-k="style" data-caption-style ${c.captions === false ? "disabled" : ""}>${opts(S.status.styles, c.style)}</select></label>
        <label class="f">Layout<select data-k="layout">${opts(S.status.layouts, c.layout)}</select></label>
        <label class="f">Game<input data-k="game" value="${esc(c.game || "")}" placeholder="Read from the title"><small class="sub" style="font-weight:400">Left empty, the game is read from the stream's title. Set it for a streamer whose title says nothing: GTA 6, Fortnite, Elden Ring.</small></label>
        <label class="f">Shortest clip (seconds)<input data-k="min_len" type="number" value="${c.min_len}"></label>
        <label class="f">Longest clip (seconds)<input data-k="max_len" type="number" value="${c.max_len}"></label>
        <label class="f">Clips per video<input data-k="max_clips" type="number" value="${c.max_clips}"></label>
        <label class="f">Minutes of live stream to keep<input data-k="buffer_min" type="number" value="${c.buffer_min}"></label></div></div>
    <div class="card"><h2>Posting</h2><p class="help">${A.length ? "Choose the accounts this creator's clips go to. One account can be shared by several creators." : "No accounts are connected yet. Connect one in Settings, then come back."}</p>
      ${A.length ? `<div class="row" style="margin-bottom:16px">${A.map(a => `<label class="ck"><input type="checkbox" data-acc value="${esc(a.id)}" ${c.accounts.includes(a.id) ? "checked" : ""}>${esc(a.label)} · ${esc(a.name)}</label>`).join("")}</div>` : `<button class="btn" data-act="go" data-v="settings" style="margin-bottom:16px">${ic("cog")}Open Settings</button>`}
      <div class="fields"><label class="sw span"><input type="checkbox" data-k="autopost" ${c.autopost ? "checked" : ""}><span>Post new clips automatically<small>Clips that score high enough go out without you approving them.</small></span></label>
        <label class="f">Only auto-post clips scoring at least<input data-k="post_min_score" type="number" min="0" max="100" value="${c.post_min_score}"></label>
        <label class="f">Minutes between posts on one account<input data-k="post_gap_min" type="number" value="${c.post_gap_min}"></label>
        <label class="f">Most posts per account per day<input data-k="daily_cap" type="number" value="${c.daily_cap}"></label>
        <label class="f">Who can see posts (YouTube, TikTok)<select data-k="privacy">${opts({public:"Public", unlisted:"Unlisted", private:"Private"}, c.privacy)}</select></label>
        <label class="f">Campaign pay per 1,000 views ($)<input data-k="rate_per_1k" type="number" step="0.1" min="0" value="${c.rate_per_1k}"></label>
        <label class="f span">Line added to every post (credit, campaign tag)<input data-k="post_footer" value="${esc(c.post_footer)}" placeholder="Clipped from @streamer"></label></div></div>
    <div class="card danger-card"><h2>Delete this creator</h2><p class="help">Removes ${esc(c.name)}, their settings and every video and clip filed under them. This cannot be undone.</p>
      <button class="btn danger" data-act="delcreator">${ic("trash")}Delete creator</button></div>
    <div class="savebar" id="savebar"><span>${ic("warn")}You have unsaved changes</span><button class="btn ghost" data-act="cdiscard">Discard</button><button class="btn primary" data-act="csave">${ic("check")}Save changes</button></div>`;
}
const creatorForm = () => { const d = {}; $$("#cform [data-k]").forEach(i => { if (i.type === "radio") { if (i.checked) d[i.dataset.k] = i.value; } else d[i.dataset.k] = i.type === "checkbox" ? i.checked : i.value; }); d.accounts = $$("#cform [data-acc]:checked").map(i => i.value); return d; };
export const dirty = on => $("#savebar")?.classList.toggle("on", on);
export async function saveCreator(quiet) {
  try { await send(`/api/creators/${creatorId()}`, creatorForm(), "PUT"); dirty(false); if (!quiet) toast("Saved", "ok"); await H.tick(); return true; }
  catch (e) { toast(e.message, "bad"); return false; }
}
export function drawLog() {
  const c = creator();
  put($("#clog"), c && c.log.length ? `<div class="card"><div class="log">${c.log.map(l => `<div><time>${when(l.t)}</time><span>${esc(l.msg)}</span></div>`).join("")}</div></div>`
    : `<div class="empty"><img src="/brand/favicon.svg" alt=""><b>Nothing has happened yet</b>Recordings, clips and posts for this creator will be listed here.</div>`);
}
/* ============================================================ clip editor */
export function openClip(jid, cid) { D.jid = jid; D.cid = cid; D.sig = ""; drawClipDlg(true); $("#clipdlg").showModal(); }
export function drawClipDlg(full) {
  const j = jobOf(D.jid), c = j?.clips.find(x => x.id === D.cid), dlg = $("#clipdlg");
  if (!c) { if (dlg.open) dlg.close(); return; }
  const m = `/media/${j.id}/`, sig = c.status + c.version;
  const stage = c.status === "done" ? `<video src="${m}${esc(c.file)}?v=${c.version}" poster="${m}${esc(c.cover)}?v=${c.version}" controls autoplay playsinline></video>`
    : c.status === "error" ? `<div class="err" style="padding:20px;text-align:center">${esc(c.error || "Could not make this clip.")}</div>`
    : `<span class="making">${c.status === "rendering" ? "Making this clip…" : "Waiting its turn…"}</span>`;
  if (full) {
    dlg.innerHTML = `<div class="editor"><div class="stage" id="stage">${stage}</div>
      <div class="panel">
        <div class="job-head" style="margin:0"><div><span class="badge ${scoreCls(c.score)}" style="position:static">${c.judges ? "Words " + c.judges.words + (c.judges.reaction ? " · Reaction " + c.judges.reaction : "") : c.score ? "Score " + c.score : "Clipped by hand"}</span>
          <p class="sub" style="margin-top:8px">${esc(c.reason || "")}</p></div><button class="btn icon ghost" data-act="closedlg" aria-label="Close">${ic("x")}</button></div>
        <h3>On the video</h3>
        <label class="f">Big text<input data-f="hook" value="${esc(c.hook)}"></label>
        <label class="sw"><input type="checkbox" data-f="captions" data-caption-toggle ${c.captions === false ? "" : "checked"}>Generate captions</label>
        <div class="two"><label class="f">Caption style<select data-f="style" data-caption-style ${c.captions === false ? "disabled" : ""}>${opts(S.status.styles, c.style)}</select></label>
          <label class="f">Layout<select data-f="layout">${opts(S.status.layouts, c.layout)}</select></label></div>
        <div class="two"><label class="f">Start (seconds)<input data-f="start" type="number" step="0.5" value="${c.start}"></label>
          <label class="f">End (seconds)<input data-f="end" type="number" step="0.5" value="${c.end}"></label></div>
        <h3>Post text</h3>
        <label class="f">Title<input data-f="title" value="${esc(c.title)}"></label>
        <label class="f">Description<textarea data-f="description" rows="2">${esc(c.description)}</textarea></label>
        <label class="f">Hashtags<input data-f="hashtags" value="${esc((c.hashtags || []).join(" "))}" placeholder="funny gaming clutch"></label>
        <div class="row"><button class="btn" type="button" data-act="writepost">${ic("bolt")}Write again with AI</button><button class="btn" type="button" data-act="savepost">Save post text</button></div>
        <button class="btn" data-act="remake">${ic("scissors")}Save and re-make clip</button>
        <h3>Post it</h3>
        <div id="dposts" class="row" style="gap:4px"></div>
        ${S.accounts.length ? `<div class="row">${S.accounts.map(a => `<label class="ck"><input type="checkbox" data-pacc value="${esc(a.id)}">${esc(a.label)} · ${esc(a.name)}</label>`).join("")}</div>
          <div class="row"><button class="btn primary" data-act="postnow">${ic("send")}Post now</button><button class="btn" data-act="postqueue">Add to queue</button></div>`
          : `<p class="help">No accounts connected yet. Connect one in Settings.</p>`}
        <div class="row" style="margin-top:auto;padding-top:8px;border-top:1px solid var(--line)">
          ${c.status === "done" ? `<a class="btn" href="${m}${esc(c.file)}?download=1">${ic("down")}Download</a>` : ""}
          <button class="btn" data-act="copy" data-j="${j.id}" data-c="${esc(c.id)}">${ic("copy")}Copy post text</button></div>
      </div></div>`;
  } else if (D.sig !== sig) $("#stage").innerHTML = stage;
  D.sig = sig;
  put($("#dposts"), postPills(c));
}
$("#clipdlg").addEventListener("close", () => { $("#clipdlg").innerHTML = ""; D.jid = null; });
$("#clipdlg").addEventListener("click", e => { if (e.target === e.currentTarget) e.currentTarget.close(); });
