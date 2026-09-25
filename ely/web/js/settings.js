// Réglages : profil, mémoire, connexions, identifiants, tâches, fichiers, administration.
import { html, useEffect, useState } from "/static/vendor/preact-htm.js";
import { del, get, patch, post, put, upload } from "/static/js/api.js";
import { ACCENTS, FileTag, Icon, bytes, dateTime, md, timeAgo, toast } from "/static/js/util.js";
import { frenchVoices, speak } from "/static/js/voice.js";

function useLoad(fn, deps = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const reload = () => fn().then(setData).catch((e) => setError(e.message));
  useEffect(() => { reload(); }, deps);
  return [data, reload, error];
}

const copy = (text) => navigator.clipboard?.writeText(text).then(() => toast("Copié"));
const Loading = () => html`<div class="spinner big"></div>`;

// Section de réglages : libellé et aide à gauche, contrôles à droite
const Row = ({ title, hint, badge, children }) => html`<section class="srow">
  <div class="srow-label"><b>${title}${badge || null}</b>${hint ? html`<span>${hint}</span>` : null}</div>
  <div class="srow-body">${children}</div>
</section>`;

const Seg = ({ options, value, onChange }) => html`<div class="seg">${options.map(([v, label, swatch]) => html`
  <button class=${value === v ? "on" : ""} onClick=${() => onChange(v)}>${swatch ? html`<span class="swatch" style=${"background:" + swatch}></span>` : null}${label}</button>`)}</div>`;

const Switch = ({ checked, onChange, title }) => html`<span class="switch" title=${title}>
  <input type="checkbox" checked=${checked} onChange=${(e) => onChange(e.target.checked)} /><span></span></span>`;

const Empty = ({ children }) => html`<div class="empty">${children}</div>`;

// ---------------------------------------------------------------- profil
function Profile({ me, onMe, pwa, prefs }) {
  const [name, setName] = useState(me.name);
  const [tz, setTz] = useState(me.settings.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone);
  const [pw, setPw] = useState({ current: "", next: "" });
  const [readAloud, setReadAloud] = useState(localStorage.getItem("ely-read") === "1");
  const [voice, setVoice] = useState(localStorage.getItem("ely-voice") || "");
  const voices = frenchVoices();
  async function save() {
    const u = await patch("/api/me", { name, settings: { timezone: tz } });
    onMe(u); toast("Enregistré");
  }
  async function changePw() {
    try { await patch("/api/me", { password: pw.next, current_password: pw.current }); setPw({ current: "", next: "" }); toast("Mot de passe modifié"); }
    catch (e) { toast(e.message); }
  }
  return html`
    <${Row} title="Compte" hint="Ely utilise ton prénom pour signer tes messages.">
      <label class="field">Prénom<input class="input" value=${name} onInput=${(e) => setName(e.target.value)} /></label>
      <label class="field">Fuseau horaire<input class="input mono" value=${tz} onInput=${(e) => setTz(e.target.value)} /></label>
      <div class="row"><button class="btn primary" onClick=${save}>Enregistrer</button>
        <span class="meta-text">${me.email} · ${me.role === "admin" ? "administrateur" : "utilisateur"}</span></div>
    <//>
    <${Row} title="Apparence" hint="Thème et couleur d'accent sur cet appareil.">
      <${Seg} value=${prefs.theme} onChange=${prefs.setTheme} options=${[["auto", "Automatique"], ["light", "Clair"], ["dark", "Sombre"]]} />
      <${Seg} value=${prefs.accent} onChange=${prefs.setAccent} options=${ACCENTS} />
      ${pwa ? html`<div><button class="btn" onClick=${pwa}><${Icon} name="phone" /> Installer l'application sur cet appareil</button></div>` : null}
    <//>
    <${Row} title="Voix" hint="Lecture des réponses.">
      <label class="toggle"><${Switch} checked=${readAloud} onChange=${(v) => { setReadAloud(v); localStorage.setItem("ely-read", v ? "1" : "0"); }} />
        Lire les réponses à voix haute</label>
      <select class="input" aria-label="Voix" value=${voice} onChange=${(e) => { setVoice(e.target.value); localStorage.setItem("ely-voice", e.target.value); speak("Bonjour, je suis Ely."); }}>
        <option value="">Automatique</option>${voices.map((v) => html`<option value=${v.name}>${v.name}</option>`)}</select>
    <//>
    <${Row} title="Notifications" hint="Sois prévenu sur ton téléphone quand Ely a terminé une tâche ou a besoin de toi.">
      <div class="row">
        <button class="btn accent" onClick=${() => window.elyEnablePush?.()}>Activer sur cet appareil</button>
        <button class="btn" onClick=${async () => { const r = await post("/api/push/test"); toast(r.sent ? "Notification envoyée" : "Aucun appareil abonné"); }}>Tester</button>
      </div>
    <//>
    <${Row} title="Mot de passe" hint="Au moins 6 caractères.">
      <input class="input" type="password" placeholder="Mot de passe actuel" autocomplete="current-password" value=${pw.current} onInput=${(e) => setPw({ ...pw, current: e.target.value })} />
      <input class="input" type="password" placeholder="Nouveau mot de passe" autocomplete="new-password" value=${pw.next} onInput=${(e) => setPw({ ...pw, next: e.target.value })} />
      <div><button class="btn" onClick=${changePw} disabled=${!pw.next}>Changer</button></div>
    <//>
    <${Row} title="Session">
      <div><button class="btn danger" onClick=${async () => { await post("/api/auth/logout"); location.href = "/"; }}><${Icon} name="logout" /> Se déconnecter</button></div>
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
      <div class="block-head"><b>Ce qu'Ely sait de toi</b><span>Toujours présent dans son esprit.</span></div>
      <textarea class="memo" value=${prof} placeholder="Rien encore : parle-lui de toi !" onInput=${(e) => setProfile(e.target.value)}></textarea>
      <div class="row"><button class="btn primary" disabled=${profile === null} onClick=${async () => { await put("/api/memory/profile", { content: prof }); setProfile(null); reload(); toast("Profil enregistré"); }}>Enregistrer</button>
        <span class="meta-text">mis à jour après chaque échange</span></div>
    </div>
    <div class="block">
      <div class="block-head"><b>Souvenirs</b><span class="count">${data.memories.length}</span></div>
      <div class="addrow"><input placeholder="Ajouter un souvenir (ex. Mon dentiste est le Dr Leroy)" value=${fact}
          onInput=${(e) => setFact(e.target.value)} onKeyDown=${(e) => { if (e.key === "Enter") add(); }} />
        <button disabled=${!fact.trim()} onClick=${add}>Ajouter</button></div>
      <div class="list">${data.memories.map((m) => html`<div class="list-item" key=${m.id}>
        <div class="grow"><span>${m.content}</span>
          <div class="tags"><span class="tag">${m.category}</span><span class="tag">${m.source}</span><span>${timeAgo(m.updated_at)}</span>${m.uses ? html`<span>· utilisé ${m.uses}×</span>` : null}</div></div>
        <button class="icon-btn" title="Oublier" onClick=${async () => { await del(`/api/memory/${m.id}`); reload(); }}><${Icon} name="trash" size=${14} /></button></div>`)}
        ${!data.memories.length ? html`<${Empty}>Aucun souvenir pour l'instant.<//>` : null}</div>
    </div>
    <div class="block">
      <div class="block-head"><b>Compétences apprises</b><span class="count">${data.skills.length}</span>
        <span>Procédures mises au point en réussissant des tâches, réutilisées automatiquement.</span></div>
      <div class="list">${data.skills.map((s) => html`<div class="list-item" key=${s.id}><div class="grow">
        <b style="cursor:pointer" onClick=${() => setOpen({ ...open, [s.id]: !open[s.id] })}>${s.name}</b>
        <div class="tags">${s.user_id === null ? html`<span class="tag">partagée</span>` : null}<span>${s.description} · utilisée ${s.uses}×</span></div>
        ${open[s.id] ? html`<div class="md skill-body" dangerouslySetInnerHTML=${{ __html: md(s.content) }}></div>` : null}</div>
        <button class="icon-btn" title="Supprimer" onClick=${async () => { if (confirm("Supprimer cette compétence ?")) { await del(`/api/skills/${s.id}`); reload(); } }}><${Icon} name="trash" size=${14} /></button></div>`)}</div>
    </div>`;
}

// ---------------------------------------------------------------- connexions
const Status = ({ on, label }) => (on ? html`<span class="pill ok">${label}</span>` : null);

function Connections() {
  const [data, reload] = useLoad(() => get("/api/integrations"));
  const [mail, setMail] = useState({ address: "", password: "", imap_host: "", smtp_host: "", smtp_port: "" });
  const [adv, setAdv] = useState(false);
  const [fb, setFb] = useState({ page_id: "", page_token: "" });
  const [tg, setTg] = useState(null);
  const [saving, setSaving] = useState(false);
  if (!data) return html`<${Loading} />`;
  const disconnect = async (p) => { if (confirm("Déconnecter ?")) { await del(`/api/integrations/${p}`); reload(); } };
  async function saveMail() {
    setSaving(true);
    try {
      const body = { ...mail, smtp_port: parseInt(mail.smtp_port) || 0 };
      await post("/api/integrations/email", body); toast("Boîte mail connectée"); reload();
    } catch (e) { toast(e.message, 6000); }
    setSaving(false);
  }
  const Linked = ({ who, extra, onOff, off = "Déconnecter" }) => html`<div class="row"><span>${who}</span>${extra ? html`<span class="meta-text">${extra}</span>` : null}
    <button class="btn small danger" onClick=${onOff}>${off}</button></div>`;
  return html`
    <${Row} title="Google" hint="Gmail, Agenda, Contacts." badge=${html` <${Status} on=${data.google.connected} label="connecté" />`}>
      ${data.google.connected ? html`<${Linked} who=${data.google.email} onOff=${() => disconnect("google")} />`
        : data.google.available ? html`<div><a class="btn primary" href="/api/integrations/google/start">Connecter mon compte Google</a></div>`
        : html`<p class="desc">À activer par l'administrateur : GOOGLE_CLIENT_ID et GOOGLE_CLIENT_SECRET dans le fichier .env
            (URI de redirection : <code>${data.google.redirect_uri}</code>). En attendant, utilise la boîte mail ci-dessous.</p>`}
    <//>
    <${Row} title="Boîte mail" hint="IMAP / SMTP : Gmail, Outlook, iCloud, Free, Orange, OVH…" badge=${html` <${Status} on=${data.email.connected} label="connectée" />`}>
      ${data.email.connected ? html`<${Linked} who=${data.email.address} extra=${data.email.imap_host} onOff=${() => disconnect("email")} />`
        : html`<p class="desc">Utilise un <b>mot de passe d'application</b> (Gmail : myaccount.google.com/apppasswords · iCloud : appleid.apple.com).</p>
          <input class="input" placeholder="adresse@exemple.fr" value=${mail.address} onInput=${(e) => setMail({ ...mail, address: e.target.value })} />
          <input class="input" type="password" placeholder="Mot de passe d'application" value=${mail.password} onInput=${(e) => setMail({ ...mail, password: e.target.value })} />
          ${adv ? html`<div class="row nowrap">
              <input class="input" placeholder="Serveur IMAP (auto)" value=${mail.imap_host} onInput=${(e) => setMail({ ...mail, imap_host: e.target.value })} />
              <input class="input" placeholder="Serveur SMTP (auto)" value=${mail.smtp_host} onInput=${(e) => setMail({ ...mail, smtp_host: e.target.value })} />
              <input class="input" placeholder="Port" style="width:90px" value=${mail.smtp_port} onInput=${(e) => setMail({ ...mail, smtp_port: e.target.value })} /></div>` : null}
          <div class="row"><button class="btn primary" disabled=${!mail.address || !mail.password || saving} onClick=${saveMail}>${saving ? "Vérification…" : "Connecter"}</button>
            <button class="btn ghost small" onClick=${() => setAdv(!adv)}>Réglages avancés</button></div>`}
    <//>
    <${Row} title="LinkedIn" hint="Publication de posts." badge=${html` <${Status} on=${data.linkedin.connected} label="connecté" />`}>
      ${data.linkedin.connected ? html`<${Linked} who=${data.linkedin.name} onOff=${() => disconnect("linkedin")} />`
        : data.linkedin.available ? html`<div><a class="btn primary" href="/api/integrations/linkedin/start">Connecter LinkedIn</a></div>`
        : html`<p class="desc">Sans API, Ely publie avec son navigateur : connecte-toi une fois à LinkedIn dans le navigateur d'Ely (bouton globe en haut).</p>`}
    <//>
    <${Row} title="Page Facebook" hint="Publication sur une page par API." badge=${html` <${Status} on=${data.facebook.connected} label="connectée" />`}>
      ${data.facebook.connected ? html`<${Linked} who=${"Page " + data.facebook.page_id} onOff=${() => disconnect("facebook")} />`
        : html`<p class="desc">Sinon Ely utilise son navigateur, y compris pour ton profil personnel.</p>
          <div class="row nowrap"><input class="input" placeholder="ID de la page" value=${fb.page_id} onInput=${(e) => setFb({ ...fb, page_id: e.target.value })} />
          <input class="input" placeholder="Jeton d'accès de la page" value=${fb.page_token} onInput=${(e) => setFb({ ...fb, page_token: e.target.value })} /></div>
          <div><button class="btn" disabled=${!fb.page_id || !fb.page_token} onClick=${async () => { await post("/api/integrations/facebook", fb); reload(); }}>Enregistrer</button></div>`}
    <//>
    <${Row} title="Telegram" hint="Parler à Ely depuis Telegram, y compris en vocal." badge=${html` <${Status} on=${data.telegram.linked} label="relié" />`}>
      ${!data.telegram.available ? html`<p class="desc">À activer par l'administrateur (TELEGRAM_BOT_TOKEN dans .env).</p>`
        : data.telegram.linked ? html`<${Linked} who=${"@" + (data.telegram.username || "relié")} onOff=${() => disconnect("telegram")} off="Délier" />`
        : tg ? html`<p class="desc">Ouvre ce lien sur ton téléphone, ou envoie <code>${tg.command}</code> au bot :</p>${tg.url ? html`<div><a class="btn primary" href=${tg.url} target="_blank">Ouvrir Telegram</a></div>` : null}`
        : html`<div><button class="btn" onClick=${async () => setTg(await post("/api/integrations/telegram/link"))}>Relier mon Telegram</button></div>`}
    <//>
    <${Row} title="Agenda sur ton téléphone" hint="Abonnement iCal à l'agenda tenu par Ely.">
      <p class="desc">Sans compte Google connecté, Ely tient ton agenda. Abonne-toi à ce lien depuis Google Agenda (« À partir de l'URL »), Apple Calendrier ou Outlook.</p>
      <div class="copy-field"><input class="input" readonly value=${data.ics_url} /><button class="btn" onClick=${() => copy(data.ics_url)}>Copier</button></div>
    <//>`;
}

// ---------------------------------------------------------------- identifiants
function Vault() {
  const [items, reload] = useLoad(() => get("/api/credentials"));
  const [f, setF] = useState({ service: "", url: "", username: "", password: "", notes: "" });
  return html`
    <${Row} title="Ajouter" hint="Ely s'en sert pour se connecter à ta place.">
      <div class="row nowrap"><input class="input" placeholder="Service (ex. Doctolib)" value=${f.service} onInput=${(e) => setF({ ...f, service: e.target.value })} />
        <input class="input" placeholder="Adresse du site" value=${f.url} onInput=${(e) => setF({ ...f, url: e.target.value })} /></div>
      <div class="row nowrap"><input class="input" placeholder="Identifiant" autocomplete="off" value=${f.username} onInput=${(e) => setF({ ...f, username: e.target.value })} />
        <input class="input" type="password" placeholder="Mot de passe" autocomplete="new-password" value=${f.password} onInput=${(e) => setF({ ...f, password: e.target.value })} /></div>
      <div><button class="btn primary" disabled=${!f.service} onClick=${async () => { await post("/api/credentials", f); setF({ service: "", url: "", username: "", password: "", notes: "" }); reload(); }}>Enregistrer</button></div>
      <p class="desc">Site protégé par une clé d'accès (passkey) ? Connecte-toi une fois dans le navigateur d'Ely : la session reste enregistrée.</p>
    <//>
    <${Row} title="Enregistrés" hint=${items ? `${items.length} identifiant${items.length > 1 ? "s" : ""}` : ""}>
      <div class="list">${(items || []).map((c) => html`<div class="list-item center" key=${c.id}>
        <div class="grow"><b>${c.service}</b><div class="tags"><span>${c.username || "—"}</span><span>·</span><span>${c.url || "—"}</span><span>·</span><span>${timeAgo(c.updated_at)}</span></div></div>
        <button class="icon-btn" title="Supprimer" onClick=${async () => { if (confirm("Supprimer ?")) { await del(`/api/credentials/${c.id}`); reload(); } }}><${Icon} name="trash" size=${14} /></button></div>`)}
        ${items && !items.length ? html`<${Empty}>Aucun identifiant pour l'instant.<//>` : null}</div>
    <//>`;
}

// ---------------------------------------------------------------- tâches planifiées
function Schedules() {
  const [items, reload] = useLoad(() => get("/api/schedules"));
  if (!items) return html`<${Loading} />`;
  return html`<div class="list">${items.map((s) => html`<div class="list-item" key=${s.id}>
      <${Switch} title="Activer" checked=${!!s.enabled} onChange=${async (v) => { await patch(`/api/schedules/${s.id}`, { enabled: v }); reload(); }} />
      <div class="grow"><span>${s.instruction}</span>
        <div class="tags"><span class="tag">${s.cron ? s.cron : "une fois"}</span>
          <span>${s.next_run && s.enabled ? "prochaine : " + dateTime(s.next_run) : "terminée"}${s.last_run ? " · dernière : " + timeAgo(s.last_run) : ""}</span></div></div>
      <button class="icon-btn" title="Supprimer" onClick=${async () => { await del(`/api/schedules/${s.id}`); reload(); }}><${Icon} name="trash" size=${14} /></button></div>`)}
    ${!items.length ? html`<${Empty}>Aucune tâche planifiée.<//>` : null}</div>`;
}

// ---------------------------------------------------------------- fichiers
function Files() {
  const [items, reload] = useLoad(() => get("/api/files"));
  return html`
    <div class="row" style="margin-bottom:8px"><label class="btn primary">Ajouter des fichiers<input type="file" multiple style="display:none"
      onChange=${async (e) => { for (const f of e.target.files) await upload(f); reload(); }} /></label></div>
    <div class="list">${(items || []).map((f) => html`<div class="list-item center" key=${f.path}>
      <${FileTag} path=${f.path} />
      <div class="grow"><a href=${"/files/" + encodeURI(f.path)} target="_blank">${f.path}</a><div class="tags"><span>${bytes(f.size)}</span><span>·</span><span>${timeAgo(f.mtime)}</span></div></div>
      <a class="icon-btn" href=${"/files/" + encodeURI(f.path) + "?download=1"} title="Télécharger"><${Icon} name="download" size=${14} /></a>
      <button class="icon-btn" title="Supprimer" onClick=${async () => { if (confirm("Supprimer ce fichier ?")) { await del(`/api/files?path=${encodeURIComponent(f.path)}`); reload(); } }}><${Icon} name="trash" size=${14} /></button></div>`)}
      ${items && !items.length ? html`<${Empty}>Aucun fichier.<//>` : null}</div>`;
}

// ---------------------------------------------------------------- administration : modèles
const ROLE_INFO = {
  main: ["Agent principal", "Réfléchit et agit. Le plus capable possible."],
  strong: ["Escalade", "Pris quand l'agent piétine (facultatif, ex. anthropic:claude-fable-5-1)."],
  fast: ["Contrôle rapide", "Vérifie que l'objectif est atteint, résume les longues tâches."],
  local: ["Tâches de fond", "Mémoire et titres. Idéalement LM Studio (gratuit)."],
  embed: ["Vecteurs mémoire", "Recherche sémantique des souvenirs (LM Studio nomic-embed…)."],
};

function ChatGPTRow({ onChange }) {
  const [st, reload, error] = useLoad(() => get("/api/admin/chatgpt"));
  const [paste, setPaste] = useState("");
  const [busy, setBusy] = useState(false);
  const hint = "GPT avec ton forfait ChatGPT, sans payer au token.";
  if (error) return html`<${Row} title="Abonnement ChatGPT" hint=${hint}>
    <p class="desc">Indisponible : ${/404|Not Found/i.test(error) ? "redémarre Ely (Ctrl+C puis ./ely.sh) pour activer cette fonction." : error}</p><//>`;
  if (!st) return null;
  async function doImport(text) {
    setBusy(true);
    try { await post("/api/admin/chatgpt", { auth_json: text || "" }); setPaste(""); toast("Abonnement ChatGPT connecté ✓"); await reload(); onChange(); }
    catch (e) { toast(e.message, 7000); }
    setBusy(false);
  }
  const badge = st.connected ? html` <span class="pill ok">connecté</span>` : st.reconnect_required ? html` <span class="pill err">à reconnecter</span>` : null;
  return html`<${Row} title="Abonnement ChatGPT" hint=${hint} badge=${badge}>
    <p class="desc">Même mécanisme que l'ancienne version : jetons du CLI Codex. Les modèles apparaissent sous le fournisseur <code>chatgpt</code>.
      Mécanisme non officiel, soumis aux limites de ton forfait.</p>
    ${st.codex_model ? html`<p class="desc">Modèle configuré dans Codex : <code>${st.codex_model}</code> (proposé comme <code>chatgpt:${st.codex_model}</code>).</p>` : null}
    ${st.connected ? html`<div class="row"><span class="meta-text">compte ${st.account_id || "ChatGPT"}</span>
        <button class="btn small danger" onClick=${async () => { await del("/api/admin/chatgpt"); reload(); onChange(); }}>Déconnecter</button></div>`
      : html`
        <ol>
          <li>Sur ce Mac, dans un terminal : <code>brew install codex</code> (ou <code>npm i -g @openai/codex</code>)</li>
          <li><code>codex login</code> puis choisis « Sign in with ChatGPT »</li>
          <li>Reviens ici et clique « Importer ».</li>
        </ol>
        <div><button class="btn primary" disabled=${busy} onClick=${() => doImport("")}>${busy ? "Vérification…" : st.codex_file ? "Importer depuis ce Mac (~/.codex/auth.json)" : "Importer depuis ~/.codex/auth.json"}</button></div>
        <details><summary>Ely tourne sur une autre machine ? Colle le contenu de auth.json</summary>
          <textarea class="input mono" rows="4" value=${paste} onInput=${(e) => setPaste(e.target.value)}></textarea>
          <button class="btn small" style="margin-top:8px" disabled=${!paste || busy} onClick=${() => doImport(paste)}>Importer ce contenu</button>
        </details>
        <p class="desc">Après l'import, Ely renouvelle elle-même les jetons ; le CLI Codex pourra te redemander « codex login ».</p>`}
  <//>`;
}

function Models() {
  const [data, reload] = useLoad(() => Promise.all([get("/api/models"), get("/api/admin/models/all")]).then(([a, b]) => ({ ...a, all: b })));
  const [busy, setBusy] = useState(false);
  if (!data) return html`<${Loading} />`;
  const llms = data.all.filter((m) => m.kind === "llm");
  const embeds = data.all.filter((m) => m.kind === "embeddings" || /embed/i.test(m.id));
  async function setRole(role, value) {
    if (value === "__autre__") {
      value = (prompt("Modèle (fournisseur:nom), ex. chatgpt:gpt-6 ou openai:gpt-5.5", "") || "").trim();
      if (!value) { reload(); return; }
    }
    await put("/api/admin/models", { [role]: value }); reload(); toast("Modèle enregistré — actif immédiatement");
  }
  return html`
    ${Object.entries(ROLE_INFO).map(([role, [label, desc]]) => html`<${Row} title=${label} hint=${desc}>
      <select class="input" value=${data.roles[role]?.configured} onChange=${(e) => setRole(role, e.target.value)}>
        <option value="auto">Automatique</option>
        ${(role === "embed" ? embeds : llms).map((m) => html`<option value=${m.ref}>${m.ref}${m.reachable ? "" : " (injoignable)"}</option>`)}
        ${data.roles[role]?.configured !== "auto" && !data.all.some((m) => m.ref === data.roles[role]?.configured) ? html`<option value=${data.roles[role]?.configured}>${data.roles[role]?.configured}</option>` : null}
        <option value="__autre__">Autre modèle…</option>
      </select>
      <span class="meta-text">actuel : ${data.roles[role]?.effective || "—"}</span>
    <//>`)}
    <${ChatGPTRow} onChange=${reload} />
    <${Row} title="Fournisseurs" hint="Clés d'API lues dans le fichier .env, sans redémarrage.">
      <div class="list">${Object.entries(data.providers).map(([p, s]) => html`<div class="list-item center" key=${p}><div class="grow"><b>${p}</b><div class="sub">${s}</div></div>
        <span class=${"pill " + (s.startsWith("ok") ? "ok" : "err")}>${s.startsWith("ok") ? "ok" : "hors ligne"}</span></div>`)}</div>
      <p class="desc">Ajoute des clés dans .env (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, MISTRAL_API_KEY, DEEPSEEK_API_KEY, OPENROUTER_API_KEY…) puis clique « Actualiser ».</p>
      <div><button class="btn" disabled=${busy} onClick=${async () => { setBusy(true); await post("/api/admin/models/refresh"); await reload(); setBusy(false); }}>${busy ? "Interrogation…" : "Actualiser les modèles"}</button></div>
    <//>`;
}

function Users() {
  const [users, reload] = useLoad(() => get("/api/admin/users"));
  const [f, setF] = useState({ email: "", name: "", password: "" });
  const [invite, setInvite] = useState("");
  return html`
    <${Row} title="Inviter" hint="Un lien à ouvrir pour créer son compte.">
      <div><button class="btn" onClick=${async () => { const r = await post("/api/admin/invites"); setInvite(`${location.origin}/?invite=${r.code}`); }}>Créer un lien d'invitation</button></div>
      ${invite ? html`<div class="copy-field"><input class="input" readonly value=${invite} /><button class="btn" onClick=${() => copy(invite)}>Copier</button></div>` : null}
    <//>
    <${Row} title="Créer un compte" hint="Directement, avec un mot de passe provisoire.">
      <div class="row nowrap"><input class="input" placeholder="E-mail" value=${f.email} onInput=${(e) => setF({ ...f, email: e.target.value })} />
        <input class="input" placeholder="Prénom" value=${f.name} onInput=${(e) => setF({ ...f, name: e.target.value })} /></div>
      <input class="input" placeholder="Mot de passe" value=${f.password} onInput=${(e) => setF({ ...f, password: e.target.value })} />
      <div><button class="btn primary" onClick=${async () => { try { await post("/api/admin/users", f); setF({ email: "", name: "", password: "" }); reload(); } catch (e) { toast(e.message); } }}>Créer</button></div>
    <//>
    <div class="table-wrap"><table class="table"><thead><tr><th>Nom</th><th>E-mail</th><th>Rôle</th><th>Tâches</th><th>Vu</th><th></th></tr></thead><tbody>
      ${(users || []).map((u) => html`<tr key=${u.id}><td>${u.name}</td><td>${u.email}</td>
        <td><select class="input" value=${u.role} onChange=${async (e) => { try { await patch(`/api/admin/users/${u.id}`, { role: e.target.value }); } catch (err) { toast(err.message); } reload(); }}>
          <option value="user">utilisateur</option><option value="admin">admin</option></select></td>
        <td class="num">${u.runs}</td><td class="num">${u.last_seen ? timeAgo(u.last_seen) : "—"}</td>
        <td><button class="icon-btn soft" title="Supprimer" onClick=${async () => { if (confirm(`Supprimer ${u.email} et toutes ses données ?`)) { try { await del(`/api/admin/users/${u.id}`); reload(); } catch (e) { toast(e.message); } } }}><${Icon} name="trash" size=${14} /></button></td></tr>`)}
    </tbody></table></div>`;
}

function Usage() {
  const [days, setDays] = useState(30);
  const [data] = useLoad(() => get(`/api/admin/usage?days=${days}`), [days]);
  return html`
    <div style="margin-bottom:24px"><${Seg} value=${days} onChange=${setDays} options=${[[1, "24 h"], [7, "7 jours"], [30, "30 jours"], [365, "1 an"]]} /></div>
    ${data ? html`<div class="kpis"><div class="kpi"><b>${data.total_cost.toFixed(2)} $</b><span>coût estimé</span></div>
      <div class="kpi"><b>${data.rows.reduce((a, r) => a + r.calls, 0)}</b><span>appels de modèles</span></div>
      <div class="kpi"><b>${Math.round(data.rows.reduce((a, r) => a + (r.input || 0) + (r.output || 0), 0) / 1000)} k</b><span>tokens</span></div></div>
      <div class="table-wrap"><table class="table"><thead><tr><th>Utilisateur</th><th>Modèle</th><th>Usage</th><th>Appels</th><th>Entrée</th><th>Cache</th><th>Sortie</th><th>Coût</th></tr></thead><tbody>
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
      <div class="kpi"><b>${m.runs}</b><span>tâches (7 j)</span></div>
      <div class="kpi"><b>${m.runs ? Math.round(100 * ok / m.runs) : 0} %</b><span>terminées</span></div>
      <div class="kpi"><b>${m.avg_duration_s} s</b><span>durée moyenne</span></div>
      <div class="kpi"><b>${m.verify_rejections}</b><span>relances du contrôleur</span></div>
      <div class="kpi"><b>${m.estimated_cost_usd} $</b><span>coût estimé</span></div>
    </div>
    <${Row} title="Lancer une session" hint="Ely analyse ses mesures et s'améliore.">
      <div class="row nowrap"><input class="input" placeholder="Objectif (facultatif) : « sois plus rapide sur Doctolib »" value=${goal} onInput=${(e) => setGoal(e.target.value)} />
        <button class="btn primary" onClick=${async () => { const r = await post("/api/admin/selfdev/run", { goal }); toast("Session lancée"); openConversation(r.conversation_id); }}>Lancer</button></div>
      <label class="toggle small"><${Switch} checked=${data.auto} onChange=${async (v) => { await put("/api/admin/selfdev", { auto: v }); reload(); }} />
        Session automatique chaque nuit à <input class="input mono" type="number" min="0" max="23" style="width:72px;min-height:34px" value=${data.hour}
          onChange=${async (e) => { await put("/api/admin/selfdev", { hour: parseInt(e.target.value) }); reload(); }} /> h</label>
    <//>
    <${Row} title="Leçons tirées de l'expérience" hint="Consignes injectées dans chaque tâche. Ely les écrit ; tu peux les corriger.">
      <textarea class="input" rows="6" value=${guide ?? data.guidelines} placeholder="Aucune leçon pour l'instant." onInput=${(e) => setGuide(e.target.value)}></textarea>
      <div><button class="btn" disabled=${guide === null} onClick=${async () => { await put("/api/admin/selfdev", { guidelines: guide }); setGuide(null); reload(); toast("Enregistré"); }}>Enregistrer</button></div>
    <//>
    ${data.plugins.length ? html`<${Row} title="Outils créés par Ely" hint="Extensions chargées à chaud."><div class="list">${data.plugins.map((p) => html`<div class="list-item center" key=${p.name}>
      <${Switch} checked=${p.enabled} onChange=${async (v) => { await post(`/api/admin/plugins/${p.name}`, { enabled: v }); reload(); }} />
      <div class="grow"><b>${p.name}</b><div class="sub">${p.tools.join(", ") || "—"}</div></div></div>`)}</div><//>` : null}
    ${m.tools.length ? html`<${Row} title="Outils les plus en échec" hint="Sur 7 jours."><div class="table-wrap"><table class="table"><thead><tr><th>Outil</th><th>Appels</th><th>Erreurs</th><th>Durée</th></tr></thead><tbody>
      ${m.tools.slice(0, 8).map((t) => html`<tr><td><code>${t.name}</code></td><td class="num">${t.calls}</td><td class="num">${t.errors || 0}</td><td class="num">${t.avg_ms} ms</td></tr>`)}</tbody></table></div><//>` : null}
    <${Row} title="Journal des améliorations" hint="Chaque modification, avec retour arrière possible.">
      <div class="list">${data.journal.map((j) => html`<div class="list-item" key=${j.id}>
        <div class="grow"><b style="cursor:pointer" onClick=${async () => setDetail(detail?.id === j.id ? null : await get(`/api/admin/improvements/${j.id}`))}>${j.title}</b>
          <div class="tags"><span class="tag">${j.kind}</span><span>${j.status} · ${dateTime(j.created_at)}${j.commit_sha ? " · " + j.commit_sha.slice(0, 10) : ""}</span></div>
          ${detail?.id === j.id ? html`${detail.detail ? html`<div class="small muted">${detail.detail}</div>` : null}${detail.diff ? html`<pre class="diff">${detail.diff}</pre>` : null}` : null}</div>
        ${j.kind === "code" && j.status === "deployed" ? html`<button class="btn small" onClick=${async () => { if (confirm("Annuler cette modification du code ?")) { try { const r = await post(`/api/admin/improvements/${j.id}/revert`); toast(r.message); reload(); } catch (e) { toast(e.message); } } }}>Annuler</button>` : null}
      </div>`)}${!data.journal.length ? html`<${Empty}>Rien pour l'instant.<//>` : null}</div>
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
    try { servers = JSON.parse(value || "{}"); } catch (e) { toast("JSON invalide : " + e.message); return; }
    setBusy(true);
    try { await put("/api/admin/mcp", { servers }); setText(null); await reload(); toast("Serveurs MCP rechargés"); } catch (e) { toast(e.message); }
    setBusy(false);
  }
  return html`
    <${Row} title="Serveurs" hint="État et outils fournis.">
      ${Object.keys(data.status).length ? html`<div class="list">${Object.entries(data.status).map(([n, s]) => html`<div class="list-item center" key=${n}>
        <div class="grow"><b>${n}</b><div class="sub">${s.status}${s.tools.length ? " · " + s.tools.join(", ") : ""}</div></div>
        <span class=${"pill " + (s.status.startsWith("ok") ? "ok" : "err")}>${s.status.startsWith("ok") ? "ok" : "erreur"}</span></div>`)}</div>`
        : html`<${Empty}>Aucun serveur branché.<//>`}
    <//>
    <${Row} title="Configuration" hint="JSON des serveurs MCP.">
      <p class="desc">Format : <code>{"nom": {"command": "npx", "args": ["-y", "paquet-mcp"], "env": {}}}</code> ou <code>{"nom": {"url": "https://…/mcp"}}</code></p>
      <textarea class="input mono" rows="12" value=${value} onInput=${(e) => setText(e.target.value)}></textarea>
      <div><button class="btn primary" disabled=${busy} onClick=${save}>${busy ? "Connexion…" : "Enregistrer et recharger"}</button></div>
    <//>`;
}

// ---------------------------------------------------------------- conteneur
const PAGES = {
  profil: ["Profil", "Ton compte et tes préférences d'utilisation."],
  memoire: ["Mémoire", "Ce qu'Ely a appris sur toi au fil des échanges. Tout est modifiable."],
  connexions: ["Connexions", "Donne à Ely l'accès à tes services pour qu'elle agisse à ta place. Sans connexion, elle utilise son navigateur."],
  identifiants: ["Identifiants", "Les accès qu'Ely utilise pour se connecter à tes sites (Doctolib, Ameli, impots.gouv…). Elle y enregistre aussi ceux que tu lui donnes."],
  taches: ["Tâches planifiées", "Rappels, veilles et routines qu'Ely exécute seule. Pour en créer : « tous les lundis à 8h, fais-moi un point sur… »."],
  fichiers: ["Fichiers", "Documents reçus, créés ou téléchargés par Ely."],
  modeles: ["Modèles", "Ely choisit automatiquement les meilleurs modèles disponibles ; tu peux imposer les tiens. En cas de panne, elle bascule sur le suivant."],
  utilisateurs: ["Utilisateurs", "Chaque utilisateur a son propre Ely : mémoire, connexions, fichiers et navigateur séparés."],
  conso: ["Consommation", "Tokens et coût estimé par utilisateur, modèle et usage (les modèles locaux sont gratuits)."],
  mcp: ["Extensions MCP", "Branche des serveurs MCP : leurs outils deviennent ceux d'Ely (Home Assistant, Notion, GitHub, fichiers du Mac…). Tu peux aussi demander à Ely : « branche-toi sur tel serveur MCP »."],
  auto: ["Auto-amélioration", "Ely mesure ses performances et s'améliore : leçons, compétences, nouveaux outils, et corrections de son propre code (testées, avec retour arrière automatique)."],
};
const USER_TABS = ["profil", "memoire", "connexions", "identifiants", "taches", "fichiers"];
const ADMIN_TABS = ["modeles", "utilisateurs", "conso", "mcp", "auto"];

export function Settings({ me, onMe, tab, onTab, onClose, pwa, prefs, openConversation }) {
  const groups = [["Réglages", USER_TABS], ...(me.role === "admin" ? [["Administration", ADMIN_TABS]] : [])];
  const all = groups.flatMap(([, keys]) => keys);
  const key = all.includes(tab) ? tab : "profil";
  const num = (k) => String(all.indexOf(k) + 1).padStart(2, "0");
  const body = {
    profil: html`<${Profile} me=${me} onMe=${onMe} pwa=${pwa} prefs=${prefs} />`, memoire: html`<${Memory} />`, connexions: html`<${Connections} />`,
    identifiants: html`<${Vault} />`, taches: html`<${Schedules} />`, fichiers: html`<${Files} />`, modeles: html`<${Models} />`,
    utilisateurs: html`<${Users} />`, conso: html`<${Usage} />`, mcp: html`<${Extensions} />`,
    auto: html`<${SelfDev} openConversation=${(id) => { onClose(); openConversation(id); }} />`,
  }[key];
  useEffect(() => { const k = (e) => e.key === "Escape" && onClose(); addEventListener("keydown", k); return () => removeEventListener("keydown", k); }, []);
  const [title, desc] = PAGES[key];
  return html`<div class="sheet-scrim" onClick=${onClose}>
    <div class="sheet" role="dialog" aria-label="Réglages" onClick=${(e) => e.stopPropagation()}>
      <nav>${groups.map(([label, keys]) => html`<div class="nav-group">
        <div class="label">${label}</div>
        ${keys.map((k) => html`<button class=${key === k ? "active" : ""} onClick=${() => onTab(k)}>
          <span class="num">${num(k)}</span><span class="lbl">${PAGES[k][0]}</span><span class="dot"></span></button>`)}
      </div>`)}</nav>
      <section class="pane" key=${key}>
        <button class="sheet-close" onClick=${onClose} title="Fermer"><${Icon} name="close" size=${12} stroke=${1.6} /></button>
        <div class="pane-in">
          <div class="pane-count">${num(key)} / ${String(all.length).padStart(2, "0")}</div>
          <h2>${title}</h2>
          <p class="pane-desc">${desc}</p>
          ${body}
        </div>
      </section>
    </div>
  </div>`;
}
