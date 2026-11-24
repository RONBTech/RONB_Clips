/* Entry point: routing, polling, global event wiring and startup. */
import { $, $$, esc, api, send, taskResult, toast, ask, askTyped, closePops, fromPath, remember, zipDownload, openFeedback } from './ui.js';
import { ic } from './icons.js';
import { S, D, creator, creatorId, jobOf, sel, H } from './state.js';
import { drawShell, drawStream, drawOverview, loadOverview, loadUsage, updateHow, openBell, markRead, checkNew, PLAT } from './shell.js';
import { drawAdd, makeClips, attach, onLink } from './add.js';
import { drawJobs, drawSelBar, drawCreatorForm, saveCreator, drawLog, openClip, drawClipDlg, selParts, selClips, dirty, costHint, saveChannels } from './clips.js';
import { setTabs, drawSettings, saveSettings, aiHints } from './settings.js';
import { startTour } from './tour.js';

const globalMessages = new Map();
function renderGlobalState() { const box = $("#global-state"); if (!box) return; box.innerHTML = [...globalMessages].map(([key, value]) => `<div class="load-state${value.failed ? " failed" : ""}" role="${value.failed ? "alert" : "status"}">${esc(value.message)}${value.action ? ` <button class="btn sm" data-act="${value.action}">Try again</button>` : ""}</div>`).join(""); }
function globalState(key, message, action="") { globalMessages.set(key, {message, failed: !!action, action}); renderGlobalState(); }
function clearGlobalState(key) { globalMessages.delete(key); renderGlobalState(); }
H.go = go;
H.tick = tick;
H.dirty = dirty;

addEventListener("popstate", () => { const [v, tab] = fromPath(location.pathname); go(v, tab, true); });
function go(v, tab, fromHistory) {
  if (v.startsWith("c:") && !S.creators.some(c => "c:" + c.id === v)) v = "overview";
  S.view = v; if (tab) S.ctab = tab; else if (!v.startsWith("c:")) S.ctab = "clips";
  if (v === "settings") S.stab = setTabs().some(x => x[0] === tab) ? tab : setTabs()[0][0];
  if (!fromHistory) remember(v, v.startsWith("c:") ? S.ctab : v === "settings" ? S.stab : null);
  localStorage.setItem("view", v);
  $("#app").classList.remove("open");
  const view = $("#view");
  if (window._gaStop) { window._gaStop(); window._gaStop = null; }
  $("#heroSlot").innerHTML = v === "overview" ? `<section class="hero"><div class="ga-bg" id="appAscii" aria-hidden="true"></div><h2>What do you want to clip today?</h2>
      <p>Paste a link or drop in videos. You get captioned shorts back.</p><div id="add"></div></section>` : "";
  if (v === "overview") view.innerHTML = `<div id="ov"></div>`;
  else if (v === "library") view.innerHTML = `<div id="add"></div><div id="jobs"></div>`;
  else if (v === "settings") view.innerHTML = `<div id="sform"></div>`;
  else if (v === "usage") { view.innerHTML = `<div id="usage"></div>`; loadUsage(); }
  else view.innerHTML = `<div id="stream"></div><div id="cbody"></div>`;   // the live player stays put across tabs: no restart, no rebuffer
  if ($("#add")) drawAdd(v === "overview" ? "hero" : "compact");
  if (v === "overview" && window.GradientASCII) { try { window._gaStop = GradientASCII($("#appAscii"), {colors: ["#5A9BFF", "#ffffff", "#A9C7FF", "#C9DCFF", "#EAF1FF"], shape: "horizon", lightMode: true, cellSize: 16, density: 0.8, glyphOpacity: 0.26, gradientOpacity: 0.32, glow: 0.06, grain: 0.06, mouseInteraction: false, clickEffect: "none"}); } catch {} }
  if (v === "settings") drawSettings();
  if (v.startsWith("c:")) setTab(S.ctab);
  $("#main").scrollTo(0, 0); render();
  if (v === "overview") loadOverview();
}
function setTab(t) {
  S.ctab = t;
  if (S.view.startsWith("c:")) remember(S.view, t, "replace");
  drawShell();
  $("#cbody").innerHTML = t === "clips" ? `<div id="live"></div><div id="add"></div><div id="jobs"></div>` : t === "setup" ? `<div id="cform"></div>` : `<div id="clog"></div>`;
  if ($("#stream")) $("#stream").style.display = t === "clips" ? "" : "none";
  if (t === "clips") drawAdd("compact");
  if (t === "setup") drawCreatorForm();
  render();
}
/* ============================================================ render + polling */
function render() {
  drawShell(); drawJobs(); drawStream(); drawSelBar();
  if (S.view === "overview") drawOverview();
  if ($("#clog")) drawLog();
  if (D.jid) drawClipDlg(false);
}
function mergeJobs(have, changed) {
  // The server only sends the videos that changed. Put those in place of the old copies and keep the rest, so the
  // list stays whole while a video is being made and its progress comes in every couple of seconds.
  if (!changed.length) return have;
  const by = new Map(have.map(j => [j.id, j]));
  changed.forEach(j => by.set(j.id, j));
  return [...by.values()].sort((a, b) => b.created - a.created);
}
async function tick() {
  try {
    const got = await api("/api/sync?v=" + encodeURIComponent(S.syncV || ""));
    const fresh = !!got.jobs, stale = Date.now() - (S.ovAt || 0) > 20000;
    S.syncV = got.v;
    if (!fresh) {                                   // nothing of ours changed on the server
      if (S.view === "overview" && stale) { S.overview = await api("/api/overview"); S.ovAt = Date.now(); render(); }
      clearGlobalState("sync");
      return true;
    }
    const {jobs, creators, accounts} = got;
    // a server from before multi-channel support sends only `channel`: show it as that platform's channel
    creators.forEach(c => { if (!c.channels) { const u = c.channel || "", k = /youtu/i.test(u) ? "youtube" : /twitch/i.test(u) ? "twitch" : /kick/i.test(u) ? "kick" : ""; c.channels = k ? {[k]: u} : {}; } });
    S.jobs = got.jobs_full ? jobs : mergeJobs(S.jobs, jobs); S.creators = creators; S.accounts = accounts;
    if (got.ai) S.status.ai = got.ai;
    if (got.unread != null) S.status.unread = got.unread;
    if (S.view === "overview") { S.overview = await api("/api/overview"); S.ovAt = Date.now(); }
    if (creatorId() && !creator()) return go("overview");
    render();
    clearGlobalState("sync");
    return true;
  } catch (e) { globalState("sync", `Could not refresh the app: ${e.message}`, "retrysync"); return false; }
}
// Back on the tab (or window): ask straight away instead of waiting out the slow background timer, and put a
// live player that fell behind while hidden back at the live edge.
let hiddenAt = 0;
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { hiddenAt = Date.now(); return; }
  tick();
  if (hiddenAt && Date.now() - hiddenAt > 60000) {   // away a while: jump back to the live moment
    const pl = $("#stream .player"), f = pl?.querySelector("iframe");
    if (f) { const src = f.src; f.src = "about:blank"; requestAnimationFrame(() => { f.src = src; }); }
  }
  hiddenAt = 0;
});
window.addEventListener("focus", () => { if (!document.hidden) tick(); });
function loop() {
  const active = S.jobs.some(j => ["queued", "running", "live"].includes(j.status) || j.clips.some(c => c.status === "queued" || c.status === "rendering"));
  // quick while something is being made, relaxed otherwise, and slow when the tab is in the background
  const waiting = S.creators.some(c => (c.channels && Object.keys(c.channels).length && !c.profile?.pic) || c.live_job && !jobOf(c.live_job));
  setTimeout(async () => { await tick(); loop(); }, document.hidden ? 20000 : waiting ? 1000 : active ? 2000 : 4000);
}
/* ============================================================ actions */
document.addEventListener("submit", e => { if (e.target.id === "addform") { e.preventDefault(); makeClips(); } });
$("#file").addEventListener("change", e => { attach(e.target.files); e.target.value = ""; });
document.addEventListener("dragover", e => { const a = $(".composer"); if (a) { e.preventDefault(); a.classList.add("over"); } });
document.addEventListener("dragleave", () => $(".composer")?.classList.remove("over"));
document.addEventListener("drop", e => { const a = $(".composer"); if (a) { e.preventDefault(); a.classList.remove("over"); attach(e.dataTransfer.files); } });
function syncCaptionStyle(toggle) {
  toggle.closest("#addform, #cform, #clipdlg").querySelectorAll("[data-caption-style]").forEach(i => i.disabled = !toggle.checked);
}
document.addEventListener("change", async e => {
  const t = e.target;
  if (t.id === "o_creator") {
    const toggle = $("#o_captions"), c = S.creators.find(c => c.id === t.value);
    if (!toggle.dataset.overridden) { toggle.checked = c?.captions !== false; syncCaptionStyle(toggle); }
    return;
  }
  if (t.hasAttribute("data-caption-toggle")) {
    if (t.id === "o_captions") t.dataset.overridden = "true";
    syncCaptionStyle(t);
  }
  if (t.id === "ai_provider") { $("#ai_models").innerHTML = ""; if (S.ai && t.value !== S.ai.provider) { $("#ai_model").value = ""; $("#ai_base").value = ""; } return aiHints(); }
  if (t.dataset.sel) {
    const k = t.dataset.sel;
    if (t.checked) sel().add(k); else sel().delete(k);
    if (k.startsWith("j:")) (jobOf(k.slice(2))?.clips || []).forEach(c => sel().delete(`c:${k.slice(2)}:${c.id}`));   // the video covers its clips
    return render();
  }
  if (t.dataset.every !== undefined) {
    const cid = t.dataset.every; if (!cid) return toast("This recording isn't filed under a creator.", "bad");
    try { await send(`/api/creators/${cid}`, {ai_every_min: Number(t.value)}, "PUT"); toast(`AI checks every ${t.value} min`, "ok"); tick(); }
    catch (err) { toast(err.message, "bad"); }
    return;
  }
  if (t.dataset.k === "ai_every_min" && $("#costhint")) $("#costhint").textContent = costHint(Number(t.value));
  if (t.name === "mainch") return;  // saved by its own click handler
  if (t.closest("#cform") && t.type === "radio") { $$("#cform .plan").forEach(l => l.classList.toggle("on", l.contains(t))); return saveCreator(true); }
  if (!t.closest("#cform") || t.type !== "checkbox") return;
  if (t.dataset.k === "autopost" && t.checked && !await ask("Turn on auto-posting?", "Clips that score high enough will be posted to the ticked accounts without you seeing them first.", "Turn on")) { t.checked = false; return; }
  saveCreator(true);
});

document.addEventListener("click", async e => {
  const b = e.target.closest("[data-act]"); if (!b) return;
  const a = b.dataset.act, d = b.dataset;
  if (a === "socials") {
    const wrap = b.closest(".social-wrap"), menu = $(".social-menu", wrap), open = menu.hidden;
    $$(".social-menu").forEach(m => { m.hidden = true; m.previousElementSibling?.setAttribute("aria-expanded", "false"); });
    menu.hidden = !open; b.setAttribute("aria-expanded", String(open)); return;
  }
  if (b.tagName === "A") e.preventDefault();
  try {
    if (a === "go") { closePops(); return go(d.v, d.tab); }
    if (a === "aimodels") {
      b.disabled = true;
      try {
        const r = await taskResult(await send("/api/ai/models", {provider: $("#ai_provider").value, base_url: $("#ai_base").value, key: $("#ai_key").value}));
        $("#ai_models").innerHTML = r.models.map(m => `<option value="${esc(m)}">`).join("");
        toast(r.models.length ? `${r.models.length} models found. Click the model box to pick one.` : "The service listed no models. Type the model yourself.", r.models.length ? "ok" : "");
        if (r.models.length) $("#ai_model").focus();
      } finally { b.disabled = false; }
      return;
    }
    if (a === "aisave" || a === "aitest") {
      b.disabled = true;
      try {
        if (a === "aisave") {
          const pv = S.ai.providers.find(x => x.id === $("#ai_provider").value);
          await send("/api/ai", {provider: $("#ai_provider").value, models: $("#ai_model").value, base_url: $("#ai_base").value, key: pv?.needs_key ? $("#ai_key").value : ""}, "PUT");
        }
        let ok = null, why = "";
        try { ok = await taskResult(await send("/api/ai/test")); } catch (err) { why = err.message; }
        S.status = await api("/api/status"); drawShell(); await drawSettings();
        if (ok) toast(`Saved. ${ok.model} answered, so the AI is ready.`, "ok");
        else toast(`Saved, but the test failed. ${why}`, "bad");
      } finally { b.disabled = false; }
      return;
    }
    if (a === "retrystatus") { try { S.status = await api("/api/status"); clearGlobalState("status"); await tick(); drawShell(); } catch (err) { globalState("status", `Could not load app status: ${err.message}`, "retrystatus"); } return; }
    if (a === "retrysync") { await tick(); return; }
    if (a === "retryload") { if (d.load === "usage") return loadUsage(); return loadOverview(); }
    if (a === "menu") return $("#app").classList.toggle("open");
    if (a === "collapse") { const m = $("#app").classList.toggle("mini"); try { localStorage.setItem("mini", m ? "1" : ""); } catch {} return; }
    if (a === "toggleopts") { const p = $(".composer-opts"), open = !p.classList.contains("open"); p.classList.toggle("open", open); p.inert = !open; b.classList.toggle("on", open); b.setAttribute("aria-expanded", String(open)); return; }
    if (a === "focusurl") return $("#url")?.focus();
    if (a === "step") {
      const t = d.to;
      if (t === "newcreator") return $('[data-act="newcreator"]').click();
      if (t === "focusurl") return $("#url")?.focus();
      const [, v1, v2, tab] = t.split(":");
      return v1 === "c" ? go("c:" + v2, tab) : go(v1);
    }
    if (a === "tab") return setTab(d.t);
    if (a === "pickfile") return $("#file").click();
    if (a === "addmore") { const u = $("#url"); return u && !u.value ? (u.focus(), toast("Paste another link, or drop a file anywhere on the box.")) : $("#file").click(); }
    if (a === "closedlg") return $("#clipdlg").close();
    if (a === "openclip") return openClip(d.j, d.c);
    if (a === "newcreator") {
      const dlg = $("#formdlg");
      dlg.innerHTML = `<form method="dialog" class="dlg"><h2>New creator</h2><p class="help">Each creator gets their own workspace, folder, settings and accounts.</p>
        <label class="f">Channel link<input id="nc_chan" placeholder="https://www.youtube.com/@name, twitch.tv/name or kick.com/name" autofocus></label>
        <div id="nc_prev"></div>
        <label class="f">Name<input id="nc_name" placeholder="Filled in from the channel, or type one" required></label>
        <div class="dlg-foot"><button class="btn" value="no" formnovalidate>Cancel</button><button class="btn primary" value="yes">Create workspace</button></div></form>`;
      dlg.onclose = async () => {
        if (dlg.returnValue !== "yes") return;
        try { const c = await send("/api/creators", {name:$("#nc_name").value, channel:$("#nc_chan").value}); await tick(); go("c:" + c.id, c.channel ? "clips" : "setup"); toast("Workspace created", "ok"); }
        catch (err) { toast(err.message, "bad"); }
      };
      let seq = 0, timer = 0, auto = "";
      $("#nc_chan").addEventListener("input", e => {
        clearTimeout(timer); const u = e.target.value.trim(), me = ++seq, box = $("#nc_prev");
        if (!u) { box.innerHTML = ""; return; }
        box.innerHTML = `<div class="chprev"><span class="ph"></span><div><span class="sk"></span><span class="sk" style="width:80px"></span></div></div>`;
        timer = setTimeout(async () => {
          try {
            const p = await taskResult(await api("/api/channel-preview?url=" + encodeURIComponent(u))); if (me !== seq) return;
            box.innerHTML = `<div class="chprev">${p.pic ? `<img src="${p.pic}" alt="">` : `<span class="ph"></span>`}<div><b>${esc(p.name)}</b><span>${esc([p.platform, p.followers].filter(Boolean).join(" · "))}</span></div></div>`;
            const n = $("#nc_name"); if (!n.value.trim() || n.value === auto) { n.value = auto = p.name; }
          } catch (err) { if (me === seq) box.innerHTML = `<div class="chprev bad">${esc(err.message)}</div>`; }
        }, 400);
      });
      dlg.returnValue = "no"; return dlg.showModal();
    }
    if (a === "copy") {
      const c = jobOf(d.j).clips.find(x => x.id === d.c);
      await navigator.clipboard.writeText(`${c.title}\n\n${c.description}\n\n${(c.hashtags || []).map(h => "#" + h).join(" ")}`); return toast("Post text copied", "ok");
    }
    if (a === "delclip") {
      if (!await ask("Delete this clip?", "The clip is removed for good. The video and its other clips stay.", "Delete clip", true)) return;
      await api(`/api/jobs/${d.j}/clips/${encodeURIComponent(d.c)}`, {method:"DELETE"});
      const j = jobOf(d.j); if (j) j.clips = j.clips.filter(c => c.id !== d.c);
      render(); toast("Clip deleted", "ok"); return tick();
    }
    if (a === "dlall") return zipDownload((jobOf(d.j)?.clips || []).filter(c => c.status === "done").map(c => d.j + "." + c.id));
    if (a === "seldl") return zipDownload(selClips());
    if (a === "selclear") { sel().clear(); return render(); }
    if (a === "selall") {
      const cid = creatorId();
      (cid ? S.jobs.filter(j => j.creator === cid) : S.jobs).forEach(j => { if (!["queued", "running", "live"].includes(j.status)) { sel().add("j:" + j.id); j.clips.forEach(c => sel().delete(`c:${j.id}:${c.id}`)); } });
      return render();
    }
    if (a === "seldel") {
      const {jobsSel, clipsSel} = selParts();
      const what = [jobsSel.length ? (jobsSel.length === 1 ? "1 video with all its clips" : `${jobsSel.length} videos with all their clips`) : "", clipsSel.length ? `${clipsSel.length} clip${clipsSel.length === 1 ? "" : "s"}` : ""].filter(Boolean).join(" and ");
      if (!await ask("Delete what you selected?", `This removes ${what} for good. It cannot be undone.`, "Delete", true)) return;
      const r = await send("/api/bulk/delete", {videos: jobsSel, clips: clipsSel});
      sel().clear(); await tick(); render();
      return toast(`Deleted ${r.videos} video${r.videos === 1 ? "" : "s"} and ${r.clips} clip${r.clips === 1 ? "" : "s"}` + (r.skipped ? `. ${r.skipped} still being made were left alone.` : ""), "ok");
    }
    if (a === "delcreator") {
      const c = creator(); if (!c) return;
      const n = S.jobs.filter(j => j.creator === c.id).length;
      if (!await askTyped(`Delete ${c.name}?`, `This removes the creator, their settings${n ? `, and their ${n} video${n === 1 ? "" : "s"} with all the clips` : ""} for good. It cannot be undone.`, c.name, "Delete creator")) return;
      await api(`/api/creators/${encodeURIComponent(c.id)}`, {method:"DELETE"});
      S.creators = S.creators.filter(x => x.id !== c.id); S.jobs = S.jobs.filter(j => j.creator !== c.id);
      go("overview"); toast("Creator deleted", "ok"); return tick();
    }
    if (a === "deljob") {
      if (!await ask("Delete this video?", "The video and all of its clips are permanently deleted. This cannot be undone.", "Delete", true)) return;
      await api(`/api/jobs/${d.j}`, {method:"DELETE"}); toast("Deleted"); return tick();
    }
    if (a === "chadd") {
      const k = d.p, v = $("#chan_" + k)?.value.trim();
      if (!v) { $("#chan_" + k)?.focus(); return toast(`Paste their ${PLAT[k].name} channel link.`, "bad"); }
      const full = /^https?:\/\//.test(v) ? v : "https://" + v;
      return saveChannels({...creator().channels, [k]: full});
    }
    if (a === "chdel") { const chs = {...creator().channels}; delete chs[d.p]; return saveChannels(chs); }
    if (a === "spick") { S.spick = {...S.spick, [creatorId()]: d.p}; return drawStream(); }
    if (a === "chmain") return saveChannels(creator().channels, creator().channels[d.p]);
    if (a === "golive") {
      if (!creator().channel) { setTab("setup"); return toast("Connect one of their channels first, then press Start recording.", "bad"); }
      b.disabled = true; const was = b.innerHTML; b.innerHTML = `<span class="rdot"></span>Starting…`;
      try {
        // The creator is fixed before asking: starting can take a few seconds, and the person may open another
        // creator meanwhile. The recording must stay with the one whose button was pressed.
        const cid0 = creatorId();
        const j = await send(`/api/creators/${cid0}/live`, d.url ? {channel: d.url} : {});
        if (j?.id) { const c = S.creators.find(x => x.id === cid0); if (c) c.live_job = j.id; if (!jobOf(j.id)) S.jobs.unshift(j); render(); }
        const still = creatorId() === cid0;
        toast(still ? "Recording started. Clips appear below as they're made." : `Recording started for ${S.creators.find(x => x.id === cid0)?.name || "that creator"}.`, "ok");
        if (still) setTab("clips");
      } catch (e) { b.disabled = false; b.innerHTML = was; throw e; }
      return tick();
    }
    if (a === "stoplive") {
      if (!await ask("Stop recording?", "Clips already made are kept. The rest of the buffer is cleared.", "Stop recording", true)) return;
      const j = jobOf(d.j); if (j) { j.status = "done"; j.stage = "Stopped by you"; }
      S.creators.forEach(c => { if (c.live_job === d.j) c.live_job = null; }); render();
      toast("Recording stopped. Clips already made are kept.", "ok");
      api(`/api/live/${d.j}/stop`, {method:"POST"}).catch(e => toast(e.message, "bad")).finally(tick); return;
    }
    if (a === "clipnow") { await send(`/api/live/${d.j}/clip`, {seconds:Number(d.s)}); return toast(`Clipping the last ${d.s} seconds…`, "ok"); }
    if (a === "tour") return startTour();
    if (a === "settab") { e.preventDefault(); S.stab = d.k; remember("settings", d.k); return drawSettings(); }
    if (a === "updhow") return updateHow();
    if (a === "updlater") { try { localStorage.setItem("ronbclips_update_seen", S.status.update?.latest || ""); } catch {} return drawShell(); }
    if (a === "storopen") { await send("/api/storage/open"); return; }
    if (a === "storchange") { S.storOpen = !S.storOpen; S.storNew = ""; return drawSettings(); }
    if (a === "storpick") {
      b.disabled = true;
      try { const r = await send("/api/storage/pick"); if (r.path) { S.storNew = r.path; $("#stor_new").value = r.path; } } finally { b.disabled = false; }
      return;
    }
    if (a === "storsave") {
      const s = await send("/api/storage", {path: $("#stor_new").value, move: $("#stor_move").checked}, "PUT");
      S.storOpen = false; toast("Saved. Close RONB Clips and start it again to switch to the new folder.", "ok"); return drawSettings();
    }
    if (a === "storcancel") { await send("/api/storage", {cancel: true}, "PUT"); toast("Kept the current folder", "ok"); return drawSettings(); }
    if (a === "appsetup") { S.setupOpen = d.p && S.setupOpen !== d.p ? d.p : ""; return drawSettings(); }
    if (a === "copytext") { await navigator.clipboard.writeText(d.t); return toast("Copied", "ok"); }
    if (a === "appsave") {
      await send("/api/apps", {platform: d.p, client_id: $("#app_id").value, client_secret: $("#app_secret").value}, "PUT");
      S.setupOpen = ""; S.status = await api("/api/status"); toast("Saved. People can connect now.", "ok"); return drawSettings();
    }
    if (a === "bell") return openBell();
    if (a === "feedback") return openFeedback("bug");
    if (a === "report") return openFeedback("bug", d.ctx);
    if (a === "bellread") { await markRead(); return openBell().then(openBell); }
    if (a === "ntgo") { closePops(); const l = d.link || ""; if (l.startsWith("/creators") || l.startsWith("/videos") || l.startsWith("/settings") || l.startsWith("/usage")) { const [v, tab] = fromPath(l); return go(v, tab); } if (l.startsWith("/")) location.href = l; return; }
    if (a === "csave") return void await saveCreator();
    if (a === "cdiscard") { drawCreatorForm(); return toast("Changes discarded"); }
    if (a === "pause") { await send("/api/settings", {paused:!S.overview.paused}, "PUT"); return loadOverview(); }
    if (a === "pcancel" || a === "pretry") { await api(`/api/posts/${d.id}/${a === "pcancel" ? "cancel" : "retry"}`, {method:"POST"}); return loadOverview(); }
    if (a === "ssave") { await saveSettings(); toast("Saved", "ok"); return drawSettings(); }
    if (a === "plansave") {
      const p = {}; $$("#sform [data-plan]").forEach(i => { (p[i.dataset.plan] ??= {})[i.dataset.f] = Number(i.value); });
      await send("/api/settings", {plans:p}, "PUT"); S.status = await api("/api/status"); toast("Plans saved", "ok"); return drawSettings();
    }
    if (a === "testalert") { await saveSettings(); await taskResult(await api("/api/alerts/test", {method:"POST"})); return toast("Test message sent. Check your phone.", "ok"); }
    if (a === "accremove") {
      if (!await ask("Disconnect this account?", "Posts still waiting for it will fail.", "Disconnect", true)) return;
      await api(`/api/accounts/${d.id}`, {method:"DELETE"}); await tick(); return drawSettings();
    }
    if (a === "remake") {
      const body = {}; $$("#clipdlg [data-f]").forEach(i => {
        body[i.dataset.f] = i.type === "checkbox" ? i.checked : i.type === "number" ? Number(i.value) : i.value;
      });
      await send(`/api/jobs/${D.jid}/clips/${D.cid}/render`, body); toast("Re-making the clip…", "ok"); return tick();
    }
    if (a === "savepost") {
      const body = {};
      $$("#clipdlg [data-f]").forEach(i => {
        if (["title", "description", "hashtags"].includes(i.dataset.f)) body[i.dataset.f] = i.value;
      });
      const r = await send(`/api/jobs/${D.jid}/clips/${D.cid}/text`, body, "PUT");
      const j = r.jobs?.find(x => x.id === D.jid) || S.jobs.find(x => x.id === D.jid);
      if (j) S.jobs = S.jobs.map(x => x.id === j.id ? j : x);
      toast("Post text saved", "ok"); return tick();
    }
    if (a === "writepost") {
      b.disabled = true;
      try {
        const text = await taskResult(await send(`/api/jobs/${D.jid}/clips/${D.cid}/write`));
        const dlg = $("#clipdlg");
        if (text.title !== undefined) $('input[data-f="title"]', dlg).value = text.title;
        if (text.description !== undefined) $('textarea[data-f="description"]', dlg).value = text.description;
        if (text.hashtags !== undefined) $('input[data-f="hashtags"]', dlg).value = text.hashtags.join(" ");
        toast("Fresh post text is ready. Review it before saving.", "ok");
      } finally { b.disabled = false; }
      return;
    }
    if (a === "postnow" || a === "postqueue") {
      const accs = $$("#clipdlg [data-pacc]:checked").map(i => i.value);
      if (!accs.length) return toast("Tick at least one account first.", "bad");
      if (a === "postnow" && !await ask("Post now?", `This clip goes to ${accs.length} account${accs.length > 1 ? "s" : ""} straight away.`, "Post now")) return;
      await send("/api/posts", {job:D.jid, clip:D.cid, accounts:accs, now:a === "postnow"});
      toast(a === "postnow" ? "Posting now" : "Added to the queue", "ok"); return tick();
    }
  } catch (err) { b.disabled = false; toast(err.message, "bad"); }
});
$("#collapseBtn").innerHTML = ic("sidebar"); $("#menuBtn").innerHTML = ic("menu");
try { if (localStorage.getItem("mini")) $("#app").classList.add("mini"); } catch {}
$("#main").addEventListener("scroll", () => $(".topbar").classList.toggle("stuck", $("#main").scrollTop > 4), {passive:true});
(async () => {
  globalState("boot", "Loading RONB Clips...");
  try { S.status = await api("/api/status"); clearGlobalState("status"); } catch (e) { globalState("status", `Could not load app status: ${e.message}`, "retrystatus"); }
  const q = new URLSearchParams(location.search);
  if (q.get("task")) {
    const tid = q.get("task"), pf = q.get("platform") || "Account";
    history.replaceState(null, "", location.pathname);
    setTimeout(async () => {
      try { const r = await taskResult(tid); toast(`${r?.name || pf} is connected. Pick it in a creator's Setup to post there.`, "ok"); }
      catch (err) { toast(err.message, "bad"); }
    }, 300);
  } else if (q.get("connect_error")) {
    const err = q.get("connect_error");
    history.replaceState(null, "", location.pathname);
    setTimeout(() => toast(err, "bad"), 300);
  }
  if (location.pathname !== "/") { const [v, tab] = fromPath(location.pathname); S.view = v; S.ctab = tab; }  // the address decides, before anything loads
  try { await tick(); clearGlobalState("boot"); } catch (e) { globalState("boot", `Could not start RONB Clips: ${e.message}`, "retrystatus"); }
  { const [v, tab] = location.pathname !== "/" ? fromPath(location.pathname) : ["overview", "clips"]; go(v, tab, true); remember(S.view, S.ctab, "replace"); }
  try {  // a link pasted on the home page
    const link = sessionStorage.getItem("ronbclips_link");
    if (link) { sessionStorage.removeItem("ronbclips_link"); go("overview"); $("#url").value = link; onLink($("#url")); $("#url").focus(); toast("Your link is ready. Press Make clips.", "ok"); }
  } catch {}
  loop();
  if (S.status.tour && !$("#formdlg").open) setTimeout(startTour, 500);   // once, the first time the app opens
  checkNew(true);
  setInterval(async () => { try { S.status = await api("/api/status"); clearGlobalState("status"); drawShell(); if (S.view === "overview") drawOverview(); checkNew(); } catch (e) { globalState("status", `Could not refresh app status: ${e.message}`, "retrystatus"); } }, 15000);
})();
