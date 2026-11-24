import { $, $$, esc, api, send, pathFor } from './ui.js';
import { ic } from './icons.js';
import { S } from './state.js';

/* ============================================================ settings */
// There are no accounts to manage. The AI section is for whoever runs the server.
export const setTabs = () => [
  ["ai", "AI", "bolt", "Your AI service, model and key"],
  ["apps", "Connected apps", "plug", "Where your clips get posted"],
  ["storage", "Storage", "folder", "Where videos and clips are kept"]];
export function aiHints() {
  const k = S.ai, sel = $("#ai_provider"); if (!k || !sel) return;
  const pv = k.providers.find(x => x.id === sel.value) || k.providers[0], same = pv.id === k.provider;
  $("#ai_keywrap").hidden = !pv.needs_key;
  $("#ai_key").disabled = !pv.needs_key;
  $("#ai_key").placeholder = same && k.has_key ? `Saved${k.key_hint ? ", ends in " + k.key_hint : ""}. Paste a new key to replace it` : pv.needs_key ? "Paste your key" : "Not needed";
  $("#ai_keyhint").innerHTML = pv.key_url ? `${pv.needs_key ? "Get a key at" : "Set it up at"} <a href="${esc(pv.key_url)}" target="_blank" rel="noopener">${esc(pv.key_url.replace("https://", ""))}</a>.${pv.needs_key ? " The key stays on this server and is never shown again." : " No API key is needed."}` : pv.needs_key ? "The key stays on this server and is never shown again." : "No API key is needed.";
  $("#ai_model").placeholder = pv.example ? "For example " + pv.example : "The model id, exactly as the service names it";
  $("#ai_base").placeholder = pv.address || "http://127.0.0.1:1234/v1";
  $("#ai_basewrap").hidden = pv.id !== "custom" && !(same && k.base_url);
}
export async function drawSettings() {
  const box = $("#sform"); if (!box) return;
  const tab = S.stab || setTabs()[0][0];
  box.innerHTML = `<div class="set-layout"><nav class="set-nav" aria-label="Settings sections">${setTabs().map(([k, l, i, h]) =>
      `<a href="${pathFor("settings", k)}" class="set-link ${k === tab ? "on" : ""}" data-act="settab" data-k="${k}">${ic(i)}<span><b>${l}</b><small>${h}</small></span></a>`).join("")}</nav>
    <div class="set-body" id="setbody"><div class="set-load">Loading…</div></div></div>`;
  const body = $("#setbody");
  try {
    if (tab === "ai") {
      const k = S.ai = await api("/api/ai");
      body.innerHTML = `<section class="set-sec"><h2>AI</h2><p class="help">RONB Clips uses an AI model to pick the moments worth clipping and to write hooks, titles and captions. Use your own key from any of these services. You pay that service directly. Without a key, clips are picked by loudness.</p>
          <div class="ai-state">${k.ready ? `<span class="pill ok">${ic("check")}Ready</span><span class="sub">${esc(k.label)} · ${esc(k.models[0] || "")}</span>` : `<span class="pill warn">Not set up</span><span class="sub">Choose a service, paste your key and pick a model.</span>`}</div>
          <div class="ai-form">
            <label class="f">AI service<select id="ai_provider">${k.providers.map(x => `<option value="${esc(x.id)}" ${x.id === k.provider ? "selected" : ""}>${esc(x.label)}</option>`).join("")}</select></label>
            <label class="f" id="ai_keywrap">Key<input id="ai_key" type="password" autocomplete="off" spellcheck="false"><span class="hint" id="ai_keyhint"></span></label>
            <label class="f" id="ai_basewrap">Address<input id="ai_base" value="${esc(k.base_url || "")}" spellcheck="false"><span class="hint">Where the service answers. Only needed for a custom service.</span></label>
            <label class="f">Model<span class="with"><input id="ai_model" list="ai_models" value="${esc(k.models.join(", "))}" spellcheck="false"><button class="btn" type="button" data-act="aimodels">Find models</button></span>
              <datalist id="ai_models"></datalist><span class="hint">A fast, inexpensive model is the right choice. Add a second one after a comma to fall back on.</span></label>
          </div>
          <div class="row" style="margin-top:20px"><button class="btn primary" data-act="aisave">Save and test</button>${k.ready ? `<button class="btn" data-act="aitest">Test again</button>` : ""}</div></section>`;
      aiHints();
    }
    if (tab === "storage") {
      const s = S.stor = await api("/api/storage");
      const usedPct = s.total_gb ? Math.min(100, Math.max(1, 100 * (s.total_gb - s.free_gb) / s.total_gb)) : 0;
      const gb = n => `${Number(n).toLocaleString(undefined, {maximumFractionDigits: 1})} GB`;
      body.innerHTML = `<section class="set-sec"><h2>Storage</h2><p class="help">Everything RONB Clips keeps is in one folder: your videos, clips, recordings, creators and settings. You can open it, back it up, or move it to a drive with more room.</p>
          ${s.full ? `<div class="note" style="margin:0 0 14px">${ic("warn")}<span><b>This drive is almost full, so RONB Clips can't take new videos.</b> ${gb(s.free_gb)} free on ${esc(s.drive)}; it needs at least ${gb(s.min_free_gb)}. Free up space, or move the folder to another drive below.</span></div>` : ""}
          ${s.pending ? `<div class="note" style="margin:0 0 14px">${ic("clock")}<span><b>The folder changes to ${esc(s.pending)} the next time RONB Clips starts.</b> Close RONB Clips and start it again. <button class="btn sm ghost" data-act="storcancel">Undo</button></span></div>` : ""}
          <div class="stor-path"><code>${esc(s.path)}</code>${s.local ? `<button class="btn" data-act="storopen">${ic("folder")}Open folder</button>` : ""}</div>
          <div class="stor-bar ${s.full ? "low" : ""}" role="img" aria-label="${Math.round(usedPct)}% of the drive is used"><i style="width:${usedPct}%"></i></div>
          <div class="stor-nums"><span>RONB Clips uses <b>${gb(s.used_gb)}</b></span><span><b>${gb(s.free_gb)}</b> free of ${gb(s.total_gb)} on ${esc(s.drive)}</span></div>
          ${s.fixed ? `<p class="sub" style="margin-top:16px">This folder is set with RONBCLIPS_DATA in the .env file. Change it there, or remove that line to choose it here.</p>`
            : S.storOpen ? `<div class="stor-change"><label class="f">New folder<span class="with"><input id="stor_new" placeholder="For example D:\\RONB Clips" spellcheck="false" value="${esc(S.storNew || "")}">${s.local ? `<button class="btn" type="button" data-act="storpick">Browse</button>` : ""}</span></label>
                <label class="ck"><input type="checkbox" id="stor_move" checked>Move everything that is in the current folder there too</label>
                <p class="sub">The change happens the next time RONB Clips starts, so nothing is moved while a video is being made.</p>
                <div class="row"><button class="btn primary" data-act="storsave">Use this folder</button><button class="btn ghost" data-act="storchange">Cancel</button></div></div>`
            : `<div class="row" style="margin-top:16px"><button class="btn" data-act="storchange">Change folder</button></div>`}</section>`;
    }
    if (tab === "apps") {
      const conn = S.accounts || [];
      const app = (key, name, logo, bg, desc, action, list) => `<div class="appcard"><span class="applogo" style="background:${bg}">${logo}</span>
          <div class="apptx"><b>${name}</b><span>${desc}</span>${list}</div><div class="appact">${action}</div></div>`;
      const mine = plat => conn.filter(a => (a.platform || a.label || "").toLowerCase().includes(plat));
      const listOf = arr => arr.length ? `<div class="applist">${arr.map(a => `<div><span class="pill ok">Connected</span><b>${esc(a.name)}</b><span class="sub">${esc(a.detail || "")}</span>
          <button class="btn sm ghost" data-act="accremove" data-id="${esc(a.id)}">Disconnect</button></div>`).join("")}</div>` : "";
      const yt = '<svg viewBox="0 0 24 24"><path fill="#fff" d="M23.5 6.19a3.02 3.02 0 0 0-2.12-2.14C19.5 3.55 12 3.55 12 3.55s-7.5 0-9.38.5A3.02 3.02 0 0 0 .5 6.19C0 8.07 0 12 0 12s0 3.93.5 5.81a3.02 3.02 0 0 0 2.12 2.14c1.88.5 9.38.5 9.38.5s7.5 0 9.38-.5a3.02 3.02 0 0 0 2.12-2.14C24 15.93 24 12 24 12s0-3.93-.5-5.81zM9.55 15.57V8.43L15.82 12l-6.27 3.57z"/></svg>';
      const ig = '<svg viewBox="0 0 24 24"><path fill="#fff" d="M12 2.16c3.2 0 3.58.01 4.85.07 1.17.05 1.8.25 2.23.41.56.22.96.48 1.38.9.42.42.68.82.9 1.38.16.42.36 1.06.41 2.23.06 1.27.07 1.65.07 4.85s-.01 3.58-.07 4.85c-.05 1.17-.25 1.8-.41 2.23a3.7 3.7 0 0 1-.9 1.38c-.42.42-.82.68-1.38.9-.42.16-1.06.36-2.23.41-1.27.06-1.65.07-4.85.07s-3.58-.01-4.85-.07c-1.17-.05-1.8-.25-2.23-.41a3.7 3.7 0 0 1-1.38-.9 3.7 3.7 0 0 1-.9-1.38c-.16-.42-.36-1.06-.41-2.23C2.17 15.58 2.16 15.2 2.16 12s.01-3.58.07-4.85c.05-1.17.25-1.8.41-2.23.22-.56.48-.96.9-1.38.42-.42.82-.68 1.38-.9.42-.16 1.06-.36 2.23-.41C8.42 2.17 8.8 2.16 12 2.16zM12 5.84a6.16 6.16 0 1 0 0 12.32 6.16 6.16 0 0 0 0-12.32zm0 10.16a4 4 0 1 1 0-8 4 4 0 0 1 0 8zm6.4-11.85a1.44 1.44 0 1 0 0 2.88 1.44 1.44 0 0 0 0-2.88z"/></svg>';
      const tt = '<svg viewBox="0 0 24 24"><path fill="#fff" d="M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 0 1-5.2 1.74 2.89 2.89 0 0 1 2.31-4.64c.3 0 .59.04.86.13v-3.5a6.37 6.37 0 0 0-.86-.05A6.34 6.34 0 0 0 3.15 15.7a6.34 6.34 0 0 0 10.86 4.43 6.3 6.3 0 0 0 1.81-4.43V8.73a8.2 8.2 0 0 0 4.77 1.52V6.8a4.85 4.85 0 0 1-1-.11z"/></svg>';
      const ok = S.status.connect || {};
      const setup = await api("/api/apps").catch(() => null);
      const GUIDE = {
        youtube: ["https://console.cloud.google.com/apis/credentials", "Google Cloud",
          ["Create a project and turn on <b>YouTube Data API v3</b>.", "Create an <b>OAuth client ID</b> of type <b>Web application</b>.",
           "Add the address below under <b>Authorised redirect URIs</b>.", "Paste its Client ID and Client secret here."],
          "Until Google verifies your app, people see a warning when they connect, and uploads stay private."],
        instagram: ["https://developers.facebook.com/apps", "Meta for Developers",
          ["Create a <b>Business</b> app and add <b>Instagram</b> (API setup with Instagram login).",
           "Under <b>Business login settings</b>, add the address below as a redirect URL.", "Paste the <b>Instagram app ID</b> and <b>Instagram app secret</b> here."],
          "Works with Creator and Business Instagram accounts. Until Meta reviews your app, only accounts you add as testers can connect."],
        tiktok: ["https://developers.tiktok.com/apps", "TikTok for Developers",
          ["Create an app and add <b>Login Kit</b> and <b>Content Posting API</b> with Direct Post turned on.",
           "Add the address below as a redirect URI, with the scopes <b>user.info.basic</b> and <b>video.publish</b>.", "Paste the Client key and Client secret here."],
          "Until TikTok reviews your app, clips are posted as private: only the account owner sees them."]};
      const card = (pf, name, logo, bg, desc) => {
        const accs = mine(pf), st = setup?.[pf], open = S.setupOpen === pf, g = GUIDE[pf];
        const act = (ok[pf] ? `<a class="btn sm primary" href="/oauth/${pf}/start">${ic("plus")}${accs.length ? "Add another" : "Connect"}</a>`
            : `<button class="btn sm primary" data-act="appsetup" data-p="${pf}">${ic("cog")}Set up</button>`)
          + (ok[pf] ? `<button class="btn sm ghost" data-act="appsetup" data-p="${pf}" title="Change this server's ${name} app">${ic("cog")}</button>` : "");
        const panel = open && st ? `<div class="appsetup">
            <p class="sub">To let people connect ${name}, register this server once with <a href="${g[0]}" target="_blank" rel="noopener">${g[1]}</a>. It takes about five minutes.</p>
            <ol>${g[2].map(x => `<li>${x}</li>`).join("")}</ol>
            <div class="copyrow"><code>${esc(st.redirect)}</code><button class="btn sm" data-act="copytext" data-t="${esc(st.redirect)}">${ic("copy")}Copy</button></div>
            ${!st.https && pf !== "youtube" ? `<p class="warnline">${name} only accepts https addresses. Put RONB Clips on a domain with https (or a tunnel) and set RONBCLIPS_PUBLIC_URL to it first.</p>` : ""}
            <div class="fields"><label class="f">${esc(st.id_label)}<input id="app_id" placeholder="${st.ready ? "Saved: " + esc(st.id_hint) + ". Paste a new one to replace it" : ""}" autocomplete="off" spellcheck="false"></label>
              <label class="f">${esc(st.secret_label)}<input id="app_secret" type="password" placeholder="${st.ready ? "Saved. Paste a new one to replace it" : ""}" autocomplete="off"></label></div>
            <p class="sub">${g[3]}</p>
            <div class="row"><button class="btn primary" data-act="appsave" data-p="${pf}">Save</button><button class="btn ghost" data-act="appsetup" data-p="">Close</button></div></div>` : "";
        return `<div class="appcard"><span class="applogo" style="background:${bg}">${logo}</span>
          <div class="apptx"><b>${name}</b><span>${desc}</span>${listOf(accs)}</div><div class="appact">${act}</div>${panel}</div>`;
      };
      body.innerHTML = `<section class="set-sec"><h2>Connected apps</h2><p class="help">Connect the accounts RONB Clips posts clips to: press Connect, sign in on the platform's own page, and you're back here. RONB Clips never sees your passwords. Then pick which accounts each creator posts to, in that creator's Setup.</p>
        <div class="apps">
          ${card("youtube", "YouTube", yt, "#FF0000", "Post clips to your channel as Shorts.")}
          ${card("instagram", "Instagram", ig, "linear-gradient(45deg,#F58529,#DD2A7B,#8134AF)", "Post clips as Reels. Needs a Creator or Business account.")}
          ${card("tiktok", "TikTok", tt, "#000", "Post clips straight to your TikTok.")}
        </div></section>`;
    }
  } catch (e) { body.innerHTML = `<div class="set-load">${esc(e.message)}</div>`; }
}
export async function saveSettings() { const d = {}; $$("#sform [data-s]").forEach(i => d[i.dataset.s] = i.value); await send("/api/settings", d, "PUT"); }
