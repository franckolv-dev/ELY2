// Réglages : profil, mémoire, connexions, identifiants, tâches, fichiers, administration.
import { html, useEffect, useState } from "/static/vendor/preact-htm.js";
import { del, get, patch, post, put, upload } from "/static/js/api.js";
import { LANGS, t, tn } from "/static/js/i18n.js";
import { FileTag, Icon, bytes, dateTime, md, timeAgo, toast } from "/static/js/util.js";
import { ExtensionSteps } from "/static/js/install.js";
import { RECORDED, loadRecordedVoices, speak, voicesForLang } from "/static/js/voice.js";

function useLoad(fn, deps = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const reload = () => fn().then(setData).catch((e) => setError(e.message));
  useEffect(() => { reload(); }, deps);
  return [data, reload, error];
}

const copy = (text) => navigator.clipboard?.writeText(text).then(() => toast(t("common.copied")));
const Loading = () => html`<div class="spinner big"></div>`;

// Section de réglages : libellé et aide à gauche, contrôles à droite
const Row = ({ title, hint, badge, children }) => html`<section class="srow">
  <div class="srow-label"><b>${title}${badge || null}</b>${hint ? html`<span>${hint}</span>` : null}</div>
  <div class="srow-body">${children}</div>
</section>`;

const Seg = ({ options, value, onChange }) => html`<div class="seg">${options.map(([v, label]) => html`
  <button class=${value === v ? "on" : ""} onClick=${() => onChange(v)}>${label}</button>`)}</div>`;

const Switch = ({ checked, onChange, title }) => html`<span class="switch" title=${title}>
  <input type="checkbox" checked=${checked} onChange=${(e) => onChange(e.target.checked)} /><span></span></span>`;

const Empty = ({ children }) => html`<div class="empty">${children}</div>`;
const TrashBtn = ({ title, onClick }) => html`<button class="icon-btn" title=${title || t("common.delete")} onClick=${onClick}><${Icon} name="trash" size=${14} /></button>`;

// ---------------------------------------------------------------- profil
function Profile({ me, onMe, pwa, prefs }) {
  const [name, setName] = useState(me.name);
  const [tz, setTz] = useState(me.settings.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone);
  const [pw, setPw] = useState({ current: "", next: "" });
  const [readAloud, setReadAloud] = useState(localStorage.getItem("ely-read") === "1");
  const [voice, setVoice] = useState(localStorage.getItem("ely-voice") || "");
  const [sample, setSample] = useState("");
  const [, setVoicesLoaded] = useState(0);
  const [recorded, setRecorded] = useState([]);
  const voices = voicesForLang();
  useEffect(() => { // les voix du système arrivent parfois après le premier affichage
    loadRecordedVoices().then(setRecorded);
    if (!("speechSynthesis" in window)) return;
    const onVoices = () => setVoicesLoaded((n) => n + 1);
    speechSynthesis.addEventListener("voiceschanged", onVoices);
    return () => speechSynthesis.removeEventListener("voiceschanged", onVoices);
  }, []);
  async function save() {
    const u = await patch("/api/me", { name, settings: { timezone: tz } });
    onMe(u); toast(t("common.saved"));
  }
  async function changePw() {
    try { await patch("/api/me", { password: pw.next, current_password: pw.current }); setPw({ current: "", next: "" }); toast(t("profil.pwChanged")); }
    catch (e) { toast(e.message); }
  }
  return html`
    <${Row} title=${t("profil.account")} hint=${t("profil.accountHint")}>
      <label class="field">${t("profil.firstName")}<input class="input" value=${name} onInput=${(e) => setName(e.target.value)} /></label>
      <label class="field">${t("profil.tz")}<input class="input" value=${tz} onInput=${(e) => setTz(e.target.value)} /></label>
      <div class="row"><button class="btn primary" onClick=${save}>${t("common.save")}</button>
        <span class="meta-text">${me.email} · ${me.role === "admin" ? t("profil.admin") : t("profil.user")}</span></div>
    <//>
    <${Row} title=${t("profil.look")} hint=${t("profil.lookHint")}>
      <${Seg} value=${prefs.theme} onChange=${prefs.setTheme} options=${[["auto", t("profil.auto")], ["light", t("profil.light")], ["dark", t("profil.dark")]]} />
      <${Seg} value=${prefs.lang} onChange=${prefs.setLang} options=${LANGS} />
      ${pwa ? html`<div><button class="btn" onClick=${pwa}><${Icon} name="phone" /> ${t("profil.install")}</button></div>` : null}
    <//>
    <${Row} title=${t("profil.address")} hint=${t("profil.addressHint")}>
      <${Seg} value=${me.settings.address === "tu" ? "tu" : "vous"} options=${[["vous", t("profil.vous")], ["tu", t("profil.tu")]]}
        onChange=${async (v) => { onMe(await patch("/api/me", { settings: { address: v } })); toast(t("common.saved")); }} />
    <//>
    <${Row} title=${t("profil.voice")} hint=${t("profil.voiceHint")}>
      <label class="toggle"><${Switch} checked=${readAloud} onChange=${(v) => { setReadAloud(v); localStorage.setItem("ely-read", v ? "1" : "0"); }} />
        ${t("profil.readAloud")}</label>
      <select class="input" aria-label=${t("profil.voiceLabel")} value=${voice} onChange=${(e) => { setVoice(e.target.value); localStorage.setItem("ely-voice", e.target.value); }}>
        <option value="">${t("common.automatic")}</option>
        ${recorded.length ? html`
          <optgroup label=${t("profil.voiceRecorded")}>${recorded.map((n) => html`<option value=${RECORDED + n}>${n[0].toUpperCase() + n.slice(1)}</option>`)}</optgroup>
          <optgroup label=${t("profil.voiceBrowser")}>${voices.map((v) => html`<option value=${v.name}>${v.name}</option>`)}</optgroup>`
        : voices.map((v) => html`<option value=${v.name}>${v.name}</option>`)}</select>
      <div class="row voice-test">
        <input class="input" aria-label=${t("profil.voiceSampleLabel")} placeholder=${t("voice.sample")} value=${sample}
          onInput=${(e) => setSample(e.target.value)} onKeyDown=${(e) => { if (e.key === "Enter") speak(sample || t("voice.sample")); }} />
        <button class="btn" onClick=${() => speak(sample || t("voice.sample"))}><${Icon} name="speaker" size=${18} /> ${t("profil.voiceTest")}</button>
      </div>
    <//>
    <${Row} title=${t("profil.notif")} hint=${t("profil.notifHint")}>
      <div class="row">
        <button class="btn primary" onClick=${() => window.elyEnablePush?.()}>${t("profil.enableDevice")}</button>
        <button class="btn" onClick=${async () => { const r = await post("/api/push/test"); toast(r.sent ? t("profil.testSent") : t("profil.testNone")); }}>${t("profil.test")}</button>
      </div>
    <//>
    <${Row} title=${t("profil.password")} hint=${t("profil.passwordHint")}>
      <input class="input" type="password" placeholder=${t("profil.currentPw")} autocomplete="current-password" value=${pw.current} onInput=${(e) => setPw({ ...pw, current: e.target.value })} />
      <input class="input" type="password" placeholder=${t("profil.newPw")} autocomplete="new-password" value=${pw.next} onInput=${(e) => setPw({ ...pw, next: e.target.value })} />
      <div><button class="btn" onClick=${changePw} disabled=${!pw.next}>${t("profil.change")}</button></div>
    <//>
    <${Row} title=${t("profil.session")}>
      <div><button class="btn danger" onClick=${async () => { await post("/api/auth/logout"); location.href = "/"; }}><${Icon} name="logout" size=${16} /> ${t("side.logout")}</button></div>
    <//>`;
}

// ---------------------------------------------------------------- mémoire
function Memory() {
  const [data, reload] = useLoad(() => get("/api/memory"));
  const [profile, setProfile] = useState(null);
  const [fact, setFact] = useState("");
  const [open, setOpen] = useState({});
  if (!data) return html`<${Loading} />`;
  const prof = profile ?? data.profile;
  async function add() {
    if (!fact.trim()) return;
    await post("/api/memory", { content: fact }); setFact(""); reload();
  }
  return html`
    <div class="block">
      <div class="block-head"><b>${t("mem.knows")}</b><span>${t("mem.knowsHint")}</span></div>
      <textarea class="memo" value=${prof} placeholder=${t("mem.empty")} onInput=${(e) => setProfile(e.target.value)}></textarea>
      <div class="row"><button class="btn primary" disabled=${profile === null} onClick=${async () => { await put("/api/memory/profile", { content: prof }); setProfile(null); reload(); toast(t("mem.profileSaved")); }}>${t("common.save")}</button>
        <span class="meta-text">${t("mem.autoUpdated")}</span></div>
    </div>
    <div class="block">
      <div class="block-head"><b>${t("mem.memories")}</b><span>${data.memories.length}</span></div>
      <div class="addrow"><input placeholder=${t("mem.addPh")} value=${fact}
          onInput=${(e) => setFact(e.target.value)} onKeyDown=${(e) => { if (e.key === "Enter") add(); }} />
        <button disabled=${!fact.trim()} onClick=${add}>${t("mem.add")}</button></div>
      <div class="list">${data.memories.map((m) => html`<div class="list-item" key=${m.id}>
        <div class="grow"><span>${m.content}</span>
          <div class="tags"><span class="tag">${m.category}</span><span class="tag">${m.source}</span><span>${timeAgo(m.updated_at)}</span>${m.uses ? html`<span>${t("mem.used", { n: m.uses })}</span>` : null}</div></div>
        <${TrashBtn} title=${t("mem.forget")} onClick=${async () => { await del(`/api/memory/${m.id}`); reload(); }} /></div>`)}
        ${!data.memories.length ? html`<${Empty}>${t("mem.none")}<//>` : null}</div>
    </div>
    <div class="block">
      <div class="block-head"><b>${t("mem.skills")}</b><span>${data.skills.length}</span><span>${t("mem.skillsHint")}</span></div>
      <div class="list">${data.skills.map((s) => html`<div class="list-item" key=${s.id}><div class="grow">
        <b style="cursor:pointer" onClick=${() => setOpen({ ...open, [s.id]: !open[s.id] })}>${s.name}</b>
        <div class="tags">${s.user_id === null ? html`<span class="tag">${t("mem.shared")}</span>` : null}<span>${t("mem.skillUsed", { d: s.description, n: s.uses })}</span></div>
        ${open[s.id] ? html`<div class="md skill-body" dangerouslySetInnerHTML=${{ __html: md(s.content) }}></div>` : null}</div>
        <${TrashBtn} onClick=${async () => { if (confirm(t("mem.deleteSkill"))) { await del(`/api/skills/${s.id}`); reload(); } }} /></div>`)}</div>
    </div>`;
}

// ---------------------------------------------------------------- connexions
const Status = ({ on, label }) => (on ? html` <span class="pill ok">${label}</span>` : null);

function ChromeRow({ onMe }) {
  const [st, reload] = useLoad(() => get("/api/chrome"));
  useEffect(() => { const timer = setInterval(reload, 4000); return () => clearInterval(timer); }, []);
  if (!st) return null;
  return html`<${Row} title="Chrome" hint=${t("conn.chromeHint")} badge=${html`<${Status} on=${st.connected} label=${t("common.connected")} />`}>
    ${st.connected ? html`
      <label class="toggle"><${Switch} checked=${st.use !== "interne"} onChange=${async (v) => {
        const u = await patch("/api/me", { settings: { browser: v ? "chrome" : "interne" } }); onMe(u); reload(); }} />
        ${t("conn.useChrome")}</label>
      <p class="desc">${t("conn.chromeOn")}</p>`
    : html`
      <${ExtensionSteps} folder=${st.folder} />
      <p class="desc">${t("conn.chromeMeanwhile")}</p>`}
  <//>`;
}

function Connections({ onMe }) {
  const [data, reload] = useLoad(() => get("/api/integrations"));
  const [mail, setMail] = useState({ address: "", password: "", imap_host: "", smtp_host: "", smtp_port: "" });
  const [adv, setAdv] = useState(false);
  const [fb, setFb] = useState({ page_id: "", page_token: "" });
  const [tg, setTg] = useState(null);
  const [saving, setSaving] = useState(false);
  if (!data) return html`<${Loading} />`;
  const disconnect = async (p) => { if (confirm(t("common.confirmDisconnect"))) { await del(`/api/integrations/${p}`); reload(); } };
  async function saveMail() {
    setSaving(true);
    try {
      const body = { ...mail, smtp_port: parseInt(mail.smtp_port) || 0 };
      await post("/api/integrations/email", body); toast(t("conn.mailOk")); reload();
    } catch (e) { toast(e.message, 6000); }
    setSaving(false);
  }
  const Linked = ({ who, extra, onOff, off = t("common.disconnect") }) => html`<div class="row"><span>${who}</span>${extra ? html`<span class="meta-text">${extra}</span>` : null}
    <button class="btn small danger" onClick=${onOff}>${off}</button></div>`;
  return html`
    <${ChromeRow} onMe=${onMe} />
    <${Row} title="Google" hint=${t("conn.googleHint")} badge=${html`<${Status} on=${data.google.connected} label=${t("common.connected")} />`}>
      ${data.google.connected ? html`<${Linked} who=${data.google.email} onOff=${() => disconnect("google")} />`
        : data.google.available ? html`<div><a class="btn primary" href="/api/integrations/google/start">${t("conn.googleConnect")}</a></div>`
        : html`<p class="desc">${t("conn.googleAdmin", { uri: data.google.redirect_uri })}</p>`}
    <//>
    <${Row} title=${t("conn.mail")} hint=${t("conn.mailHint")} badge=${html`<${Status} on=${data.email.connected} label=${t("common.connectedF")} />`}>
      ${data.email.connected ? html`<${Linked} who=${data.email.address} extra=${data.email.imap_host} onOff=${() => disconnect("email")} />`
        : html`<p class="desc">${t("conn.mailHelp")}</p>
          <input class="input" placeholder=${t("conn.mailPh")} value=${mail.address} onInput=${(e) => setMail({ ...mail, address: e.target.value })} />
          <input class="input" type="password" placeholder=${t("conn.appPw")} value=${mail.password} onInput=${(e) => setMail({ ...mail, password: e.target.value })} />
          ${adv ? html`<div class="row nowrap">
              <input class="input" placeholder=${t("conn.imap")} value=${mail.imap_host} onInput=${(e) => setMail({ ...mail, imap_host: e.target.value })} />
              <input class="input" placeholder=${t("conn.smtp")} value=${mail.smtp_host} onInput=${(e) => setMail({ ...mail, smtp_host: e.target.value })} />
              <input class="input" placeholder=${t("conn.port")} style="width:90px" value=${mail.smtp_port} onInput=${(e) => setMail({ ...mail, smtp_port: e.target.value })} /></div>` : null}
          <div class="row"><button class="btn primary" disabled=${!mail.address || !mail.password || saving} onClick=${saveMail}>${saving ? t("conn.checking") : t("conn.connect")}</button>
            <button class="btn ghost small" onClick=${() => setAdv(!adv)}>${t("conn.advanced")}</button></div>`}
    <//>
    <${Row} title="LinkedIn" hint=${t("conn.linkedinHint")} badge=${html`<${Status} on=${data.linkedin.connected} label=${t("common.connected")} />`}>
      ${data.linkedin.connected ? html`<${Linked} who=${data.linkedin.name} onOff=${() => disconnect("linkedin")} />`
        : data.linkedin.available ? html`<div><a class="btn primary" href="/api/integrations/linkedin/start">${t("conn.linkedinConnect")}</a></div>`
        : html`<p class="desc">${t("conn.linkedinNoApi")}</p>`}
    <//>
    <${Row} title=${t("conn.facebook")} hint=${t("conn.facebookHint")} badge=${html`<${Status} on=${data.facebook.connected} label=${t("common.connectedF")} />`}>
      ${data.facebook.connected ? html`<${Linked} who=${t("conn.page", { id: data.facebook.page_id })} onOff=${() => disconnect("facebook")} />`
        : html`<p class="desc">${t("conn.facebookNoApi")}</p>
          <div class="row nowrap"><input class="input" placeholder=${t("conn.pageId")} value=${fb.page_id} onInput=${(e) => setFb({ ...fb, page_id: e.target.value })} />
          <input class="input" placeholder=${t("conn.pageToken")} value=${fb.page_token} onInput=${(e) => setFb({ ...fb, page_token: e.target.value })} /></div>
          <div><button class="btn" disabled=${!fb.page_id || !fb.page_token} onClick=${async () => { await post("/api/integrations/facebook", fb); reload(); }}>${t("common.save")}</button></div>`}
    <//>
    <${Row} title="Telegram" hint=${t("conn.telegramHint")} badge=${html`<${Status} on=${data.telegram.linked} label=${t("conn.linked")} />`}>
      ${!data.telegram.available ? html`<p class="desc">${t("conn.telegramAdmin")}</p>`
        : data.telegram.linked ? html`<${Linked} who=${"@" + (data.telegram.username || t("conn.linked"))} onOff=${() => disconnect("telegram")} off=${t("conn.unlink")} />`
        : tg ? html`<p class="desc">${t("conn.telegramOpen", { command: tg.command })}</p>${tg.url ? html`<div><a class="btn primary" href=${tg.url} target="_blank">${t("conn.openTelegram")}</a></div>` : null}`
        : html`<div><button class="btn" onClick=${async () => setTg(await post("/api/integrations/telegram/link"))}>${t("conn.linkTelegram")}</button></div>`}
    <//>
    <${Row} title=${t("conn.ics")} hint=${t("conn.icsHint")}>
      <p class="desc">${t("conn.icsHelp")}</p>
      <div class="copy-field"><input class="input" readonly value=${data.ics_url} /><button class="btn" onClick=${() => copy(data.ics_url)}>${t("common.copy")}</button></div>
    <//>`;
}

// ---------------------------------------------------------------- identifiants
function Vault() {
  const [items, reload] = useLoad(() => get("/api/credentials"));
  const [f, setF] = useState({ service: "", url: "", username: "", password: "", notes: "" });
  return html`
    <${Row} title=${t("vault.add")} hint=${t("vault.addHint")}>
      <div class="row nowrap"><input class="input" placeholder=${t("vault.service")} value=${f.service} onInput=${(e) => setF({ ...f, service: e.target.value })} />
        <input class="input" placeholder=${t("vault.url")} value=${f.url} onInput=${(e) => setF({ ...f, url: e.target.value })} /></div>
      <div class="row nowrap"><input class="input" placeholder=${t("vault.username")} autocomplete="off" value=${f.username} onInput=${(e) => setF({ ...f, username: e.target.value })} />
        <input class="input" type="password" placeholder=${t("vault.password")} autocomplete="new-password" value=${f.password} onInput=${(e) => setF({ ...f, password: e.target.value })} /></div>
      <div><button class="btn primary" disabled=${!f.service} onClick=${async () => { await post("/api/credentials", f); setF({ service: "", url: "", username: "", password: "", notes: "" }); reload(); }}>${t("common.save")}</button></div>
      <p class="desc">${t("vault.chromeNote")}</p>
    <//>
    <${Row} title=${t("vault.saved")} hint=${items ? tn("vault.count", items.length) : ""}>
      <div class="list">${(items || []).map((c) => html`<div class="list-item center" key=${c.id}>
        <div class="grow"><b>${c.service}</b><div class="tags"><span>${c.username || "—"}</span><span>·</span><span>${c.url || "—"}</span><span>·</span><span>${timeAgo(c.updated_at)}</span></div></div>
        <${TrashBtn} onClick=${async () => { if (confirm(t("common.confirmDelete"))) { await del(`/api/credentials/${c.id}`); reload(); } }} /></div>`)}
        ${items && !items.length ? html`<${Empty}>${t("vault.none")}<//>` : null}</div>
    <//>`;
}

// ---------------------------------------------------------------- tâches planifiées
function Schedules() {
  const [items, reload] = useLoad(() => get("/api/schedules"));
  if (!items) return html`<${Loading} />`;
  return html`<div class="list">${items.map((s) => html`<div class="list-item" key=${s.id}>
      <${Switch} title=${t("sched.enable")} checked=${!!s.enabled} onChange=${async (v) => { await patch(`/api/schedules/${s.id}`, { enabled: v }); reload(); }} />
      <div class="grow"><span>${s.instruction}</span>
        <div class="tags"><span class="tag">${s.cron ? s.cron : t("sched.once")}</span>
          <span>${s.next_run && s.enabled ? t("sched.next", { d: dateTime(s.next_run) }) : t("sched.done")}${s.last_run ? t("sched.last", { d: timeAgo(s.last_run) }) : ""}</span></div></div>
      <${TrashBtn} onClick=${async () => { await del(`/api/schedules/${s.id}`); reload(); }} /></div>`)}
    ${!items.length ? html`<${Empty}>${t("sched.none")}<//>` : null}</div>`;
}

// ---------------------------------------------------------------- fichiers
function Files() {
  const [items, reload] = useLoad(() => get("/api/files"));
  return html`
    <div class="row" style="margin-bottom:8px"><label class="btn primary">${t("files.add")}<input type="file" multiple style="display:none"
      onChange=${async (e) => { for (const f of e.target.files) await upload(f); reload(); }} /></label></div>
    <div class="list">${(items || []).map((f) => html`<div class="list-item center" key=${f.path}>
      <${FileTag} path=${f.path} />
      <div class="grow"><a href=${"/files/" + encodeURI(f.path)} target="_blank">${f.path}</a><div class="tags"><span>${bytes(f.size)}</span><span>·</span><span>${timeAgo(f.mtime)}</span></div></div>
      <a class="icon-btn" href=${"/files/" + encodeURI(f.path) + "?download=1"} title=${t("files.download")}><${Icon} name="download" size=${14} /></a>
      <${TrashBtn} onClick=${async () => { if (confirm(t("files.deleteConfirm"))) { await del(`/api/files?path=${encodeURIComponent(f.path)}`); reload(); } }} /></div>`)}
      ${items && !items.length ? html`<${Empty}>${t("files.none")}<//>` : null}</div>`;
}

// ---------------------------------------------------------------- administration : modèles
const ROLES = ["main", "strong", "selfdev", "fast", "local", "embed"];

function ChatGPTRow({ onChange }) {
  const [st, reload, error] = useLoad(() => get("/api/admin/chatgpt"));
  const [paste, setPaste] = useState("");
  const [busy, setBusy] = useState(false);
  if (error) return html`<${Row} title=${t("gpt.title")} hint=${t("gpt.hint")}>
    <p class="desc">${t("gpt.unavailable", { e: /404|Not Found/i.test(error) ? t("gpt.restart") : error })}</p><//>`;
  if (!st) return null;
  async function doImport(text) {
    setBusy(true);
    try { await post("/api/admin/chatgpt", { auth_json: text || "" }); setPaste(""); toast(t("gpt.ok")); await reload(); onChange(); }
    catch (e) { toast(e.message, 7000); }
    setBusy(false);
  }
  const badge = st.connected ? html` <span class="pill ok">${t("common.connected")}</span>` : st.reconnect_required ? html` <span class="pill err">${t("gpt.reconnect")}</span>` : null;
  return html`<${Row} title=${t("gpt.title")} hint=${t("gpt.hint")} badge=${badge}>
    <p class="desc">${t("gpt.desc")}</p>
    ${st.codex_model ? html`<p class="desc">${t("gpt.codexModel", { m: st.codex_model })}</p>` : null}
    ${st.connected ? html`<div class="row"><span class="meta-text">${t("gpt.account", { id: st.account_id || "ChatGPT" })}</span>
        <button class="btn small danger" onClick=${async () => { await del("/api/admin/chatgpt"); reload(); onChange(); }}>${t("common.disconnect")}</button></div>`
      : html`
        <ol><li>${t("gpt.step1")}</li><li>${t("gpt.step2")}</li><li>${t("gpt.step3")}</li></ol>
        <div><button class="btn primary" disabled=${busy} onClick=${() => doImport("")}>${busy ? t("gpt.checking") : st.codex_file ? t("gpt.importMac") : t("gpt.import")}</button></div>
        <details><summary>${t("gpt.paste")}</summary>
          <textarea class="input mono" rows="4" value=${paste} onInput=${(e) => setPaste(e.target.value)}></textarea>
          <button class="btn small" style="margin-top:8px" disabled=${!paste || busy} onClick=${() => doImport(paste)}>${t("gpt.importPaste")}</button>
        </details>
        <p class="desc">${t("gpt.after")}</p>`}
  <//>`;
}

// Claude par l'Agent SDK : moteur des missions d'auto-amélioration
function ClaudeRow({ st, onChange }) {
  const [busy, setBusy] = useState(false);
  const [budget, setBudget] = useState(null);
  if (!st) return null;
  async function test() {
    setBusy(true);
    try {
      const r = await post("/api/admin/claude/test", {});
      toast(r.ok ? t("claude.testOk", { cost: (r.cost || 0).toFixed(3) }) : t("claude.testFail", { e: r.error || "?" }), 7000);
    } catch (e) { toast(e.message, 7000); }
    setBusy(false);
  }
  async function saveBudget() {
    if (budget === null) return;
    const v = parseFloat(budget);
    setBudget(null);
    if (v > 0) { await put("/api/admin/claude", { budget: v }); onChange(); toast(t("common.saved")); }
  }
  const badge = st.ready ? html` <span class="pill ok">${t("claude.ready")}</span>` : null;
  return html`<${Row} title=${t("claude.title")} hint=${t("claude.hint")} badge=${badge}>
    <p class="desc">${t("claude.desc")}</p>
    ${st.ready ? html`
        <p class="desc">${t("claude.status", { v: st.version, source: t(`claude.source.${st.source}`) })}
          ${st.source === "api_key" ? " " + t("claude.apiKey") : ""}</p>
        <label class="meta-text">${t("claude.budget")} <input class="input" type="number" min="0.1" step="0.5" style="width:88px;min-height:34px"
          value=${budget ?? st.budget} onInput=${(e) => setBudget(e.target.value)} onBlur=${saveBudget}
          onKeyDown=${(e) => { if (e.key === "Enter") e.target.blur(); }} /> $</label>
        <div><button class="btn" disabled=${busy} onClick=${test}>${busy ? t("claude.testing") : t("claude.test")}</button></div>`
      : html`<ol>
          ${st.source ? null : html`<li>${t("claude.step1")}</li>`}
          ${st.installed ? null : html`<li>${t("claude.step2")}</li>`}
          <li>${t("claude.step3")}</li></ol>`}
  <//>`;
}

// Modèles essayés dans l'ordre quand le modèle choisi ne répond pas
function FallbackRow({ fb, onSave }) {
  const [text, setText] = useState(null);
  if (!fb) return null;
  const value = text ?? (fb.configured === "auto" ? "" : fb.configured);
  const save = () => { if (text !== null) { onSave(text.trim()); setText(null); } };
  return html`<${Row} title=${t("models.fallback")} hint=${t("models.fallbackHint")}>
    <input class="input mono" placeholder="auto" value=${value} onInput=${(e) => setText(e.target.value)}
      onBlur=${save} onKeyDown=${(e) => { if (e.key === "Enter") e.target.blur(); }} />
    <span class="meta-text">${t("common.current", { v: fb.effective ? fb.effective.split(", ").join(" → ") : t("common.none") })}</span>
    <p class="desc">${t("models.fallbackHelp")}</p>
  <//>`;
}

function Models() {
  const [data, reload] = useLoad(() => Promise.all([get("/api/models"), get("/api/admin/models/all"), get("/api/admin/claude").catch(() => null)])
    .then(([a, b, claude]) => ({ ...a, all: b, claude })));
  const [busy, setBusy] = useState(false);
  if (!data) return html`<${Loading} />`;
  const llms = data.all.filter((m) => m.kind === "llm");
  const embeds = data.all.filter((m) => m.kind === "embeddings" || /embed/i.test(m.id));
  const claudeModels = data.claude?.models || []; // confiés à l'Agent SDK : pour l'auto-amélioration seulement
  const known = (ref) => data.all.some((m) => m.ref === ref) || claudeModels.some((m) => m.ref === ref);
  async function setRole(role, value) {
    if (value === "__autre__") {
      value = (prompt(t("models.otherPrompt"), "") || "").trim();
      if (!value) { reload(); return; }
    }
    await put("/api/admin/models", { [role]: value }); reload(); toast(t("models.saved"));
  }
  return html`
    ${ROLES.map((role) => html`<${Row} title=${t(`role.${role}`)} hint=${t(`role.${role}Desc`)}>
      <select class="input" value=${data.roles[role]?.configured} onChange=${(e) => setRole(role, e.target.value)}>
        <option value="auto">${t("common.automatic")}</option>
        ${role === "selfdev" ? claudeModels.map((m) => html`<option value=${m.ref}>${m.ref} · ${m.name}</option>`) : null}
        ${(role === "embed" ? embeds : llms).map((m) => html`<option value=${m.ref}>${m.ref}${m.reachable ? "" : t("models.unreachable")}</option>`)}
        ${data.roles[role]?.configured !== "auto" && !known(data.roles[role]?.configured) ? html`<option value=${data.roles[role]?.configured}>${data.roles[role]?.configured}</option>` : null}
        <option value="__autre__">${t("models.other")}</option>
      </select>
      <span class="meta-text">${t("common.current", { v: data.roles[role]?.effective || "—" })}</span>
    <//>`)}
    <${FallbackRow} fb=${data.roles.fallbacks} onSave=${async (v) => { await put("/api/admin/models", { fallbacks: v || "auto" }); reload(); toast(t("models.fallbackSaved")); }} />
    <${ChatGPTRow} onChange=${reload} />
    <${ClaudeRow} st=${data.claude} onChange=${reload} />
    <${Row} title=${t("models.providers")} hint=${t("models.providersHint")}>
      <div class="list">${Object.entries(data.providers).map(([p, s]) => html`<div class="list-item center" key=${p}><div class="grow"><b>${p}</b><div class="sub">${s}</div></div>
        <span class=${"pill " + (s.startsWith("ok") ? "ok" : "err")}>${s.startsWith("ok") ? t("common.ok") : t("common.offline")}</span></div>`)}</div>
      <p class="desc">${t("models.providersHelp")}</p>
      <div><button class="btn" disabled=${busy} onClick=${async () => { setBusy(true); await post("/api/admin/models/refresh"); await reload(); setBusy(false); }}>${busy ? t("models.refreshing") : t("models.refresh")}</button></div>
    <//>`;
}

function Users() {
  const [users, reload] = useLoad(() => get("/api/admin/users"));
  const [f, setF] = useState({ email: "", name: "", password: "" });
  const [invite, setInvite] = useState("");
  return html`
    <${Row} title=${t("users.invite")} hint=${t("users.inviteHint")}>
      <div><button class="btn" onClick=${async () => { const r = await post("/api/admin/invites"); setInvite(`${location.origin}/?invite=${r.code}`); }}>${t("users.createLink")}</button></div>
      ${invite ? html`<div class="copy-field"><input class="input" readonly value=${invite} /><button class="btn" onClick=${() => copy(invite)}>${t("common.copy")}</button></div>` : null}
    <//>
    <${Row} title=${t("users.create")} hint=${t("users.createHint")}>
      <div class="row nowrap"><input class="input" placeholder=${t("users.email")} value=${f.email} onInput=${(e) => setF({ ...f, email: e.target.value })} />
        <input class="input" placeholder=${t("users.firstName")} value=${f.name} onInput=${(e) => setF({ ...f, name: e.target.value })} /></div>
      <input class="input" placeholder=${t("users.password")} value=${f.password} onInput=${(e) => setF({ ...f, password: e.target.value })} />
      <div><button class="btn primary" onClick=${async () => { try { await post("/api/admin/users", f); setF({ email: "", name: "", password: "" }); reload(); } catch (e) { toast(e.message); } }}>${t("users.createBtn")}</button></div>
    <//>
    <div class="table-wrap"><table class="table"><thead><tr><th>${t("users.colName")}</th><th>${t("users.colEmail")}</th><th>${t("users.colRole")}</th><th>${t("users.colRuns")}</th><th>${t("users.colSeen")}</th><th></th></tr></thead><tbody>
      ${(users || []).map((u) => html`<tr key=${u.id}><td>${u.name}</td><td>${u.email}</td>
        <td><select class="input" value=${u.role} onChange=${async (e) => { try { await patch(`/api/admin/users/${u.id}`, { role: e.target.value }); } catch (err) { toast(err.message); } reload(); }}>
          <option value="user">${t("users.roleUser")}</option><option value="admin">${t("users.roleAdmin")}</option></select></td>
        <td class="num">${u.runs}</td><td class="num">${u.last_seen ? timeAgo(u.last_seen) : "—"}</td>
        <td><${TrashBtn} onClick=${async () => { if (confirm(t("users.deleteConfirm", { email: u.email }))) { try { await del(`/api/admin/users/${u.id}`); reload(); } catch (e) { toast(e.message); } } }} /></td></tr>`)}
    </tbody></table></div>`;
}

function Usage() {
  const [days, setDays] = useState(30);
  const [data] = useLoad(() => get(`/api/admin/usage?days=${days}`), [days]);
  return html`
    <div style="margin-bottom:24px"><${Seg} value=${days} onChange=${setDays} options=${[[1, t("usage.24h")], [7, t("usage.7d")], [30, t("usage.30d")], [365, t("usage.1y")]]} /></div>
    ${data ? html`<div class="kpis"><div class="kpi"><b>${data.total_cost.toFixed(2)} $</b><span>${t("usage.cost")}</span></div>
      <div class="kpi"><b>${data.rows.reduce((a, r) => a + r.calls, 0)}</b><span>${t("usage.calls")}</span></div>
      <div class="kpi"><b>${Math.round(data.rows.reduce((a, r) => a + (r.input || 0) + (r.output || 0), 0) / 1000)} k</b><span>${t("usage.tokens")}</span></div></div>
      <div class="table-wrap"><table class="table"><thead><tr><th>${t("usage.colUser")}</th><th>${t("usage.colModel")}</th><th>${t("usage.colPurpose")}</th><th>${t("usage.colCalls")}</th>
        <th>${t("usage.colIn")}</th><th>${t("usage.colCache")}</th><th>${t("usage.colOut")}</th><th>${t("usage.colCost")}</th></tr></thead><tbody>
        ${data.rows.map((r) => html`<tr><td>${r.user}</td><td><code>${r.model}</code></td><td>${r.purpose}</td><td class="num">${r.calls}</td><td class="num">${r.input}</td>
          <td class="num">${r.cached}</td><td class="num">${r.output}</td><td class="num">${r.cost ? r.cost.toFixed(3) + " $" : "—"}</td></tr>`)}
      </tbody></table></div>` : html`<${Loading} />`}`;
}

function SelfDev({ openConversation }) {
  const [data, reload] = useLoad(() => get("/api/admin/selfdev"));
  const [goal, setGoal] = useState("");
  const [guide, setGuide] = useState(null);
  const [detail, setDetail] = useState(null);
  if (!data) return html`<${Loading} />`;
  const m = data.metrics;
  const ok = (m.by_status.done || 0);
  return html`
    <div class="kpis">
      <div class="kpi"><b>${m.runs}</b><span>${t("self.runs")}</span></div>
      <div class="kpi"><b>${m.runs ? Math.round(100 * ok / m.runs) : 0} %</b><span>${t("self.done")}</span></div>
      <div class="kpi"><b>${m.avg_duration_s} s</b><span>${t("self.avg")}</span></div>
      <div class="kpi"><b>${m.verify_rejections}</b><span>${t("self.rejections")}</span></div>
      <div class="kpi"><b>${m.estimated_cost_usd} $</b><span>${t("self.cost")}</span></div>
    </div>
    <${Row} title=${t("self.session")} hint=${t("self.sessionHint")}>
      <div class="row nowrap"><input class="input" placeholder=${t("self.goalPh")} value=${goal} onInput=${(e) => setGoal(e.target.value)} />
        <button class="btn primary" onClick=${async () => { const r = await post("/api/admin/selfdev/run", { goal }); toast(t("self.launched")); openConversation(r.conversation_id); }}>${t("self.launch")}</button></div>
      <label class="toggle small"><${Switch} checked=${data.auto} onChange=${async (v) => { await put("/api/admin/selfdev", { auto: v }); reload(); }} />
        ${t("self.nightly")} <input class="input" type="number" min="0" max="23" style="width:72px;min-height:34px" value=${data.hour}
          onChange=${async (e) => { await put("/api/admin/selfdev", { hour: parseInt(e.target.value) }); reload(); }} /> ${t("self.hour")}</label>
    <//>
    <${Row} title=${t("self.lessons")} hint=${t("self.lessonsHint")}>
      <textarea class="input" rows="6" value=${guide ?? data.guidelines} placeholder=${t("self.noLessons")} onInput=${(e) => setGuide(e.target.value)}></textarea>
      <div><button class="btn" disabled=${guide === null} onClick=${async () => { await put("/api/admin/selfdev", { guidelines: guide }); setGuide(null); reload(); toast(t("common.saved")); }}>${t("common.save")}</button></div>
    <//>
    ${data.plugins.length ? html`<${Row} title=${t("self.plugins")} hint=${t("self.pluginsHint")}><div class="list">${data.plugins.map((p) => html`<div class="list-item center" key=${p.name}>
      <${Switch} checked=${p.enabled} onChange=${async (v) => { await post(`/api/admin/plugins/${p.name}`, { enabled: v }); reload(); }} />
      <div class="grow"><b>${p.name}</b><div class="sub">${p.tools.join(", ") || "—"}</div></div></div>`)}</div><//>` : null}
    ${m.tools.length ? html`<${Row} title=${t("self.failing")} hint=${t("self.failingHint")}><div class="table-wrap"><table class="table"><thead><tr><th>${t("self.colTool")}</th><th>${t("self.colCalls")}</th><th>${t("self.colErrors")}</th><th>${t("self.colDuration")}</th></tr></thead><tbody>
      ${m.tools.slice(0, 8).map((x) => html`<tr><td><code>${x.name}</code></td><td class="num">${x.calls}</td><td class="num">${x.errors || 0}</td><td class="num">${x.avg_ms} ms</td></tr>`)}</tbody></table></div><//>` : null}
    <${Row} title=${t("self.journal")} hint=${t("self.journalHint")}>
      <div class="list">${data.journal.map((j) => html`<div class="list-item" key=${j.id}>
        <div class="grow"><b style="cursor:pointer" onClick=${async () => setDetail(detail?.id === j.id ? null : await get(`/api/admin/improvements/${j.id}`))}>${j.title}</b>
          <div class="tags"><span class="tag">${j.kind}</span><span>${j.status} · ${dateTime(j.created_at)}${j.commit_sha ? " · " + j.commit_sha.slice(0, 10) : ""}</span></div>
          ${detail?.id === j.id ? html`${detail.detail ? html`<div class="small muted">${detail.detail}</div>` : null}${detail.diff ? html`<pre class="diff">${detail.diff}</pre>` : null}` : null}</div>
        ${j.kind === "code" && j.status === "deployed" ? html`<button class="btn small" onClick=${async () => { if (confirm(t("self.revertConfirm"))) { try { const r = await post(`/api/admin/improvements/${j.id}/revert`); toast(r.message); reload(); } catch (e) { toast(e.message); } } }}>${t("self.revert")}</button>` : null}
      </div>`)}${!data.journal.length ? html`<${Empty}>${t("self.nothing")}<//>` : null}</div>
    <//>`;
}

function Extensions() {
  const [data, reload] = useLoad(() => get("/api/admin/mcp"));
  const [text, setText] = useState(null);
  const [busy, setBusy] = useState(false);
  if (!data) return html`<${Loading} />`;
  const value = text ?? JSON.stringify(data.servers, null, 2);
  async function save() {
    let servers;
    try { servers = JSON.parse(value || "{}"); } catch (e) { toast(t("mcp.invalid", { e: e.message })); return; }
    setBusy(true);
    try { await put("/api/admin/mcp", { servers }); setText(null); await reload(); toast(t("mcp.reloaded")); } catch (e) { toast(e.message); }
    setBusy(false);
  }
  return html`
    <${Row} title=${t("mcp.servers")} hint=${t("mcp.serversHint")}>
      ${Object.keys(data.status).length ? html`<div class="list">${Object.entries(data.status).map(([n, s]) => html`<div class="list-item center" key=${n}>
        <div class="grow"><b>${n}</b><div class="sub">${s.status}${s.tools.length ? " · " + s.tools.join(", ") : ""}</div></div>
        <span class=${"pill " + (s.status.startsWith("ok") ? "ok" : "err")}>${s.status.startsWith("ok") ? t("common.ok") : t("common.error")}</span></div>`)}</div>`
        : html`<${Empty}>${t("mcp.none")}<//>`}
    <//>
    <${Row} title=${t("mcp.config")} hint=${t("mcp.configHint")}>
      <p class="desc">${t("mcp.format")}</p>
      <textarea class="input mono" rows="12" value=${value} onInput=${(e) => setText(e.target.value)}></textarea>
      <div><button class="btn primary" disabled=${busy} onClick=${save}>${busy ? t("mcp.connecting") : t("mcp.save")}</button></div>
    <//>`;
}

// ---------------------------------------------------------------- conteneur
const USER_TABS = ["profil", "memoire", "connexions", "identifiants", "taches", "fichiers"];
const ADMIN_TABS = ["modeles", "utilisateurs", "conso", "mcp", "auto"];

export function Settings({ me, onMe, tab, onTab, onClose, pwa, prefs, openConversation }) {
  const groups = [[t("set.groupUser"), USER_TABS], ...(me.role === "admin" ? [[t("set.groupAdmin"), ADMIN_TABS]] : [])];
  const all = groups.flatMap(([, keys]) => keys);
  const key = all.includes(tab) ? tab : "profil";
  const num = (k) => String(all.indexOf(k) + 1).padStart(2, "0");
  const body = {
    profil: html`<${Profile} me=${me} onMe=${onMe} pwa=${pwa} prefs=${prefs} />`, memoire: html`<${Memory} />`, connexions: html`<${Connections} onMe=${onMe} />`,
    identifiants: html`<${Vault} />`, taches: html`<${Schedules} />`, fichiers: html`<${Files} />`, modeles: html`<${Models} />`,
    utilisateurs: html`<${Users} />`, conso: html`<${Usage} />`, mcp: html`<${Extensions} />`,
    auto: html`<${SelfDev} openConversation=${(id) => { onClose(); openConversation(id); }} />`,
  }[key];
  useEffect(() => { const k = (e) => e.key === "Escape" && onClose(); addEventListener("keydown", k); return () => removeEventListener("keydown", k); }, []);
  return html`<div class="sheet-scrim" onClick=${onClose}>
    <div class="sheet" role="dialog" aria-label=${t("set.dialog")} onClick=${(e) => e.stopPropagation()}>
      <nav>${groups.map(([label, keys]) => html`<div class="nav-group">
        <div class="label">${label}</div>
        ${keys.map((k) => html`<button class=${key === k ? "active" : ""} onClick=${() => onTab(k)}>
          <span class="num">${num(k)}</span><span class="lbl">${t(`page.${k}`)}</span><span class="dot"></span></button>`)}
      </div>`)}</nav>
      <section class="pane" key=${key}>
        <button class="sheet-close" onClick=${onClose} title=${t("common.close")}><${Icon} name="close" size=${12} stroke=${1.6} /></button>
        <div class="pane-in">
          <div class="pane-count">${num(key)} / ${String(all.length).padStart(2, "0")}</div>
          <h2>${t(`page.${key}`)}</h2>
          <p class="pane-desc">${t(`page.${key}Desc`)}</p>
          ${body}
        </div>
      </section>
    </div>
  </div>`;
}
