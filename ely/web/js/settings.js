// Réglages : profil, mémoire, connexions, identifiants, tâches, fichiers, administration.
import { html, useEffect, useState } from "/static/vendor/preact-htm.js";
import { api, del, get, patch, post, put, upload } from "/static/js/api.js";
import { Icon, bytes, dateTime, fileIcon, md, timeAgo, toast } from "/static/js/util.js";
import { frenchVoices, speak } from "/static/js/voice.js";

function useLoad(fn, deps = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const reload = () => fn().then(setData).catch((e) => setError(e.message));
  useEffect(() => { reload(); }, deps);
  return [data, reload, error];
}

const copy = (text) => navigator.clipboard?.writeText(text).then(() => toast("Copié"));

// ---------------------------------------------------------------- profil
function Profile({ me, onMe, pwa }) {
  const [name, setName] = useState(me.name);
  const [tz, setTz] = useState(me.settings.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone);
  const [pw, setPw] = useState({ current: "", next: "" });
  const [readAloud, setReadAloud] = useState(localStorage.getItem("ely-read") === "1");
  const [voice, setVoice] = useState(localStorage.getItem("ely-voice") || "");
  const [theme, setTheme] = useState(localStorage.getItem("ely-theme") || "auto");
  const voices = frenchVoices();
  async function save() {
    const u = await patch("/api/me", { name, settings: { timezone: tz } });
    onMe(u); toast("Enregistré");
  }
  async function changePw() {
    try { await patch("/api/me", { password: pw.next, current_password: pw.current }); setPw({ current: "", next: "" }); toast("Mot de passe modifié"); }
    catch (e) { toast(e.message); }
  }
  function applyTheme(v) {
    setTheme(v); localStorage.setItem("ely-theme", v);
    if (v === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", v);
  }
  return html`
    <h2>Profil</h2><p class="muted">Ton compte et tes préférences d'utilisation.</p>
    <div class="card">
      <label class="field">Prénom (utilisé par Ely pour signer tes messages)<input class="input" value=${name} onInput=${(e) => setName(e.target.value)} /></label>
      <label class="field">Fuseau horaire<input class="input" value=${tz} onInput=${(e) => setTz(e.target.value)} /></label>
      <div class="row"><button class="btn primary" onClick=${save}>Enregistrer</button><span class="faint small">${me.email} · ${me.role === "admin" ? "administrateur" : "utilisateur"}</span></div>
    </div>
    <div class="card">
      <h3>🔔 Notifications</h3>
      <p class="desc">Sois prévenu sur ton téléphone quand Ely a terminé une tâche ou a besoin de toi.</p>
      <div class="row">
        <button class="btn" onClick=${() => window.elyEnablePush?.()}>Activer sur cet appareil</button>
        <button class="btn ghost" onClick=${async () => { const r = await post("/api/push/test"); toast(r.sent ? "Notification envoyée" : "Aucun appareil abonné"); }}>Tester</button>
      </div>
    </div>
    <div class="card">
      <h3>🔊 Voix</h3>
      <label class="row"><span class="switch"><input type="checkbox" checked=${readAloud} onChange=${(e) => { setReadAloud(e.target.checked); localStorage.setItem("ely-read", e.target.checked ? "1" : "0"); }} /><span></span></span>
        Lire les réponses à voix haute</label>
      ${voices.length ? html`<label class="field">Voix<select class="input" value=${voice} onChange=${(e) => { setVoice(e.target.value); localStorage.setItem("ely-voice", e.target.value); speak("Bonjour, je suis Ely."); }}>
        <option value="">Automatique</option>${voices.map((v) => html`<option value=${v.name}>${v.name}</option>`)}</select></label>` : null}
    </div>
    <div class="card">
      <h3>🎨 Apparence</h3>
      <div class="row">${[["auto", "Automatique"], ["light", "Clair"], ["dark", "Sombre"]].map(([v, l]) => html`<button class=${"btn small" + (theme === v ? " primary" : "")} onClick=${() => applyTheme(v)}>${l}</button>`)}</div>
      ${pwa ? html`<button class="btn" onClick=${pwa}>📲 Installer l'application sur cet appareil</button>` : null}
    </div>
    <div class="card">
      <h3>🔒 Mot de passe</h3>
      <input class="input" type="password" placeholder="Mot de passe actuel" value=${pw.current} onInput=${(e) => setPw({ ...pw, current: e.target.value })} />
      <input class="input" type="password" placeholder="Nouveau mot de passe" value=${pw.next} onInput=${(e) => setPw({ ...pw, next: e.target.value })} />
      <div><button class="btn" onClick=${changePw} disabled=${!pw.next}>Changer</button></div>
    </div>
    <button class="btn danger" onClick=${async () => { await post("/api/auth/logout"); location.href = "/"; }}><${Icon} name="logout" size=${16} /> Se déconnecter</button>`;
}

// ---------------------------------------------------------------- mémoire
function Memory() {
  const [data, reload] = useLoad(() => get("/api/memory"));
  const [profile, setProfile] = useState(null);
  const [fact, setFact] = useState("");
  const [open, setOpen] = useState({});
  if (!data) return html`<div class="spinner"></div>`;
  const prof = profile ?? data.profile;
  return html`
    <h2>Mémoire</h2><p class="muted">Ce qu'Ely a appris sur toi au fil des échanges. Tout est modifiable.</p>
    <div class="card">
      <h3>🧠 Ce qu'Ely sait de toi</h3>
      <p class="desc">Toujours présent dans son esprit. Ely le met à jour elle-même après chaque échange.</p>
      <textarea class="input" rows="10" value=${prof} placeholder="Rien encore : parle-lui de toi !" onInput=${(e) => setProfile(e.target.value)}></textarea>
      <div><button class="btn primary" disabled=${profile === null} onClick=${async () => { await put("/api/memory/profile", { content: prof }); setProfile(null); reload(); toast("Profil enregistré"); }}>Enregistrer</button></div>
    </div>
    <div class="card">
      <h3>💭 Souvenirs (${data.memories.length})</h3>
      <div class="row" style="flex-wrap:nowrap"><input class="input" placeholder="Ajouter un souvenir (ex. Mon dentiste est le Dr Leroy)" value=${fact} onInput=${(e) => setFact(e.target.value)} />
        <button class="btn" disabled=${!fact} onClick=${async () => { await post("/api/memory", { content: fact }); setFact(""); reload(); }}>Ajouter</button></div>
      <div class="list">${data.memories.map((m) => html`<div class="list-item">
        <div class="grow">${m.content}<div class="sub">${m.category} · ${m.source} · ${timeAgo(m.updated_at)}${m.uses ? ` · utilisé ${m.uses}×` : ""}</div></div>
        <button class="icon-btn" title="Oublier" onClick=${async () => { await del(`/api/memory/${m.id}`); reload(); }}><${Icon} name="trash" size=${16} /></button></div>`)}</div>
    </div>
    <div class="card">
      <h3>🎓 Compétences apprises (${data.skills.length})</h3>
      <p class="desc">Procédures qu'Ely a mises au point en réussissant des tâches. Elle les réutilise automatiquement.</p>
      <div class="list">${data.skills.map((s) => html`<div class="list-item"><div class="grow">
        <b style="cursor:pointer" onClick=${() => setOpen({ ...open, [s.id]: !open[s.id] })}>${s.name}</b> ${s.user_id === null ? html`<span class="pill accent">partagée</span>` : null}
        <div class="sub">${s.description} · utilisée ${s.uses}×</div>
        ${open[s.id] ? html`<div class="md small" style="margin-top:8px" dangerouslySetInnerHTML=${{ __html: md(s.content) }}></div>` : null}</div>
        <button class="icon-btn" title="Supprimer" onClick=${async () => { if (confirm("Supprimer cette compétence ?")) { await del(`/api/skills/${s.id}`); reload(); } }}><${Icon} name="trash" size=${16} /></button></div>`)}</div>
    </div>`;
}

// ---------------------------------------------------------------- connexions
function Connections() {
  const [data, reload] = useLoad(() => get("/api/integrations"));
  const [mail, setMail] = useState({ address: "", password: "", imap_host: "", smtp_host: "", smtp_port: "" });
  const [adv, setAdv] = useState(false);
  const [fb, setFb] = useState({ page_id: "", page_token: "" });
  const [tg, setTg] = useState(null);
  const [saving, setSaving] = useState(false);
  if (!data) return html`<div class="spinner"></div>`;
  const disconnect = async (p) => { if (confirm("Déconnecter ?")) { await del(`/api/integrations/${p}`); reload(); } };
  async function saveMail() {
    setSaving(true);
    try {
      const body = { ...mail, smtp_port: parseInt(mail.smtp_port) || 0 };
      await post("/api/integrations/email", body); toast("Boîte mail connectée"); reload();
    } catch (e) { toast(e.message, 6000); }
    setSaving(false);
  }
  return html`
    <h2>Connexions</h2><p class="muted">Donne à Ely l'accès à tes services pour qu'elle agisse à ta place. Sans connexion, elle utilise son navigateur.</p>
    <div class="card">
      <h3>🟢 Google — Gmail, Agenda, Contacts ${data.google.connected ? html`<span class="pill ok">connecté</span>` : null}</h3>
      ${data.google.connected ? html`<div class="row"><span>${data.google.email}</span><button class="btn small danger" onClick=${() => disconnect("google")}>Déconnecter</button></div>`
        : data.google.available ? html`<div><a class="btn primary" href="/api/integrations/google/start">Connecter mon compte Google</a></div>`
        : html`<p class="desc">À activer par l'administrateur : GOOGLE_CLIENT_ID et GOOGLE_CLIENT_SECRET dans le fichier .env
            (URI de redirection : <code>${data.google.redirect_uri}</code>). En attendant, utilise la boîte mail ci-dessous.</p>`}
    </div>
    <div class="card">
      <h3>✉️ Boîte mail (IMAP/SMTP) ${data.email.connected ? html`<span class="pill ok">connectée</span>` : null}</h3>
      ${data.email.connected ? html`<div class="row"><span>${data.email.address}</span><span class="faint small">${data.email.imap_host}</span>
          <button class="btn small danger" onClick=${() => disconnect("email")}>Déconnecter</button></div>`
        : html`<p class="desc">Fonctionne avec Gmail, Outlook, iCloud, Free, Orange, SFR, OVH… Utilise un <b>mot de passe d'application</b>
            (Gmail : myaccount.google.com/apppasswords · iCloud : appleid.apple.com).</p>
          <input class="input" placeholder="adresse@exemple.fr" value=${mail.address} onInput=${(e) => setMail({ ...mail, address: e.target.value })} />
          <input class="input" type="password" placeholder="Mot de passe d'application" value=${mail.password} onInput=${(e) => setMail({ ...mail, password: e.target.value })} />
          ${adv ? html`<div class="row" style="flex-wrap:nowrap">
              <input class="input" placeholder="Serveur IMAP (auto)" value=${mail.imap_host} onInput=${(e) => setMail({ ...mail, imap_host: e.target.value })} />
              <input class="input" placeholder="Serveur SMTP (auto)" value=${mail.smtp_host} onInput=${(e) => setMail({ ...mail, smtp_host: e.target.value })} />
              <input class="input" placeholder="Port" style="width:90px" value=${mail.smtp_port} onInput=${(e) => setMail({ ...mail, smtp_port: e.target.value })} /></div>` : null}
          <div class="row"><button class="btn primary" disabled=${!mail.address || !mail.password || saving} onClick=${saveMail}>${saving ? "Vérification…" : "Connecter"}</button>
            <button class="btn ghost small" onClick=${() => setAdv(!adv)}>Réglages avancés</button></div>`}
    </div>
    <div class="card">
      <h3>💼 LinkedIn ${data.linkedin.connected ? html`<span class="pill ok">connecté</span>` : null}</h3>
      ${data.linkedin.connected ? html`<div class="row"><span>${data.linkedin.name}</span><button class="btn small danger" onClick=${() => disconnect("linkedin")}>Déconnecter</button></div>`
        : data.linkedin.available ? html`<div><a class="btn primary" href="/api/integrations/linkedin/start">Connecter LinkedIn</a></div>`
        : html`<p class="desc">Sans API, Ely publie avec son navigateur : connecte-toi une fois à LinkedIn dans le navigateur d'Ely (bouton 🌐 en haut).</p>`}
    </div>
    <div class="card">
      <h3>📘 Page Facebook ${data.facebook.connected ? html`<span class="pill ok">connectée</span>` : null}</h3>
      ${data.facebook.connected ? html`<div class="row"><span>Page ${data.facebook.page_id}</span><button class="btn small danger" onClick=${() => disconnect("facebook")}>Déconnecter</button></div>`
        : html`<p class="desc">Pour publier sur une page par API (sinon Ely utilise son navigateur, y compris pour ton profil personnel).</p>
          <div class="row" style="flex-wrap:nowrap"><input class="input" placeholder="ID de la page" value=${fb.page_id} onInput=${(e) => setFb({ ...fb, page_id: e.target.value })} />
          <input class="input" placeholder="Jeton d'accès de la page" value=${fb.page_token} onInput=${(e) => setFb({ ...fb, page_token: e.target.value })} />
          <button class="btn" disabled=${!fb.page_id || !fb.page_token} onClick=${async () => { await post("/api/integrations/facebook", fb); reload(); }}>Enregistrer</button></div>`}
    </div>
    <div class="card">
      <h3>✈️ Telegram ${data.telegram.linked ? html`<span class="pill ok">relié</span>` : null}</h3>
      ${!data.telegram.available ? html`<p class="desc">À activer par l'administrateur (TELEGRAM_BOT_TOKEN dans .env). Permet de parler à Ely depuis Telegram, y compris en vocal.</p>`
        : data.telegram.linked ? html`<div class="row"><span>@${data.telegram.username || "relié"}</span><button class="btn small danger" onClick=${() => disconnect("telegram")}>Délier</button></div>`
        : tg ? html`<p class="desc">Ouvre ce lien sur ton téléphone, ou envoie <code>${tg.command}</code> au bot :</p>${tg.url ? html`<a class="btn primary" href=${tg.url} target="_blank">Ouvrir Telegram</a>` : null}`
        : html`<div><button class="btn" onClick=${async () => setTg(await post("/api/integrations/telegram/link"))}>Relier mon Telegram</button></div>`}
    </div>
    <div class="card">
      <h3>📆 Agenda d'Ely sur ton téléphone</h3>
      <p class="desc">Sans compte Google connecté, Ely tient ton agenda. Abonne-toi à ce lien depuis Google Agenda (« À partir de l'URL »), Apple Calendrier ou Outlook.</p>
      <div class="copy-field"><input class="input" readonly value=${data.ics_url} /><button class="btn" onClick=${() => copy(data.ics_url)}>Copier</button></div>
    </div>`;
}

// ---------------------------------------------------------------- identifiants
function Vault() {
  const [items, reload] = useLoad(() => get("/api/credentials"));
  const [f, setF] = useState({ service: "", url: "", username: "", password: "", notes: "" });
  return html`
    <h2>Identifiants</h2><p class="muted">Les accès qu'Ely utilise pour se connecter à tes sites (Doctolib, Ameli, impots.gouv…). Elle y enregistre aussi ceux que tu lui donnes.</p>
    <div class="card">
      <div class="row" style="flex-wrap:nowrap"><input class="input" placeholder="Service (ex. Doctolib)" value=${f.service} onInput=${(e) => setF({ ...f, service: e.target.value })} />
        <input class="input" placeholder="Adresse du site" value=${f.url} onInput=${(e) => setF({ ...f, url: e.target.value })} /></div>
      <div class="row" style="flex-wrap:nowrap"><input class="input" placeholder="Identifiant" value=${f.username} onInput=${(e) => setF({ ...f, username: e.target.value })} />
        <input class="input" type="password" placeholder="Mot de passe" value=${f.password} onInput=${(e) => setF({ ...f, password: e.target.value })} /></div>
      <div><button class="btn primary" disabled=${!f.service} onClick=${async () => { await post("/api/credentials", f); setF({ service: "", url: "", username: "", password: "", notes: "" }); reload(); }}>Enregistrer</button></div>
    </div>
    <div class="card"><div class="list">${(items || []).map((c) => html`<div class="list-item">
      <div class="grow"><b>${c.service}</b><div class="sub">${c.username || "—"} · ${c.url || "—"} · ${timeAgo(c.updated_at)}</div></div>
      <button class="icon-btn" onClick=${async () => { if (confirm("Supprimer ?")) { await del(`/api/credentials/${c.id}`); reload(); } }}><${Icon} name="trash" size=${16} /></button></div>`)}
      ${items && !items.length ? html`<div class="faint">Aucun identifiant pour l'instant.</div>` : null}</div></div>`;
}

// ---------------------------------------------------------------- tâches planifiées
function Schedules() {
  const [items, reload] = useLoad(() => get("/api/schedules"));
  return html`
    <h2>Tâches planifiées</h2><p class="muted">Rappels, veilles et routines qu'Ely exécute seule. Pour en créer : « tous les lundis à 8h, fais-moi un point sur… ».</p>
    <div class="card"><div class="list">${(items || []).map((s) => html`<div class="list-item">
      <label class="switch" title="Activer"><input type="checkbox" checked=${!!s.enabled} onChange=${async (e) => { await patch(`/api/schedules/${s.id}`, { enabled: e.target.checked }); reload(); }} /><span></span></label>
      <div class="grow">${s.instruction}<div class="sub">${s.cron ? `Récurrente (${s.cron})` : "Une fois"} · ${s.next_run && s.enabled ? "prochaine : " + dateTime(s.next_run) : "terminée"}${s.last_run ? " · dernière : " + timeAgo(s.last_run) : ""}</div></div>
      <button class="icon-btn" onClick=${async () => { await del(`/api/schedules/${s.id}`); reload(); }}><${Icon} name="trash" size=${16} /></button></div>`)}
      ${items && !items.length ? html`<div class="faint">Aucune tâche planifiée.</div>` : null}</div></div>`;
}

// ---------------------------------------------------------------- fichiers
function Files() {
  const [items, reload] = useLoad(() => get("/api/files"));
  return html`
    <h2>Fichiers</h2><p class="muted">Documents reçus, créés ou téléchargés par Ely.</p>
    <div class="card">
      <label class="btn" style="justify-self:start">Ajouter des fichiers<input type="file" multiple style="display:none" onChange=${async (e) => { for (const f of e.target.files) await upload(f); reload(); }} /></label>
      <div class="list">${(items || []).map((f) => html`<div class="list-item">
        <span style="font-size:20px">${fileIcon(f.path)}</span>
        <div class="grow"><a href=${"/files/" + encodeURI(f.path)} target="_blank">${f.path}</a><div class="sub">${bytes(f.size)} · ${timeAgo(f.mtime)}</div></div>
        <a class="icon-btn" href=${"/files/" + encodeURI(f.path) + "?download=1"} title="Télécharger"><${Icon} name="download" size=${16} /></a>
        <button class="icon-btn" onClick=${async () => { if (confirm("Supprimer ce fichier ?")) { await del(`/api/files?path=${encodeURIComponent(f.path)}`); reload(); } }}><${Icon} name="trash" size=${16} /></button></div>`)}
        ${items && !items.length ? html`<div class="faint">Aucun fichier.</div>` : null}</div>
    </div>`;
}

// ---------------------------------------------------------------- administration : modèles
const ROLE_INFO = {
  main: ["Agent principal", "Réfléchit et agit. Le plus capable possible."],
  strong: ["Escalade", "Pris quand l'agent piétine (facultatif, ex. anthropic:claude-fable-5-1)."],
  fast: ["Contrôle rapide", "Vérifie que l'objectif est atteint, résume les longues tâches."],
  local: ["Tâches de fond", "Mémoire et titres. Idéalement LM Studio (gratuit)."],
  embed: ["Vecteurs mémoire", "Recherche sémantique des souvenirs (LM Studio nomic-embed…)."],
};

function ChatGPTCard({ onChange }) {
  const [st, reload, error] = useLoad(() => get("/api/admin/chatgpt"));
  const [paste, setPaste] = useState("");
  const [busy, setBusy] = useState(false);
  if (error) return html`<div class="card"><h3>💬 Abonnement ChatGPT</h3>
    <p class="desc">Indisponible : ${/404|Not Found/i.test(error) ? "redémarre Ely (Ctrl+C puis ./ely.sh) pour activer cette fonction." : error}</p></div>`;
  if (!st) return null;
  async function doImport(text) {
    setBusy(true);
    try { await post("/api/admin/chatgpt", { auth_json: text || "" }); setPaste(""); toast("Abonnement ChatGPT connecté ✓"); await reload(); onChange(); }
    catch (e) { toast(e.message, 7000); }
    setBusy(false);
  }
  return html`<div class="card">
    <h3>💬 Abonnement ChatGPT ${st.connected ? html`<span class="pill ok">connecté</span>` : st.reconnect_required ? html`<span class="pill err">à reconnecter</span>` : null}</h3>
    <p class="desc">Utilise GPT avec ton forfait ChatGPT, sans payer au token (même mécanisme que l'ancienne version : jetons du CLI Codex).
      Les modèles apparaissent ensuite sous le fournisseur <code>chatgpt</code>. Mécanisme non officiel, soumis aux limites de ton forfait.</p>
    ${st.codex_model ? html`<p class="desc">Modèle configuré dans Codex : <code>${st.codex_model}</code> (proposé comme <code>chatgpt:${st.codex_model}</code>).</p>` : null}
    ${st.connected ? html`<div class="row"><span class="faint small">Compte ${st.account_id || "ChatGPT"}</span>
        <button class="btn small danger" onClick=${async () => { await del("/api/admin/chatgpt"); reload(); onChange(); }}>Déconnecter</button></div>`
      : html`
        <ol class="small muted" style="margin:0;padding-left:18px">
          <li>Sur ce Mac, dans un terminal : <code>brew install codex</code> (ou <code>npm i -g @openai/codex</code>)</li>
          <li><code>codex login</code> puis choisis « Sign in with ChatGPT »</li>
          <li>Reviens ici et clique « Importer ».</li>
        </ol>
        <div class="row"><button class="btn primary" disabled=${busy} onClick=${() => doImport("")}>${busy ? "Vérification…" : st.codex_file ? "Importer depuis ce Mac (~/.codex/auth.json)" : "Importer depuis ~/.codex/auth.json"}</button></div>
        <details><summary class="small muted" style="cursor:pointer">Ely tourne sur une autre machine ? Colle le contenu de auth.json</summary>
          <textarea class="input" rows="4" style="margin-top:8px;font-family:var(--mono);font-size:12px" value=${paste} onInput=${(e) => setPaste(e.target.value)}></textarea>
          <button class="btn small" style="margin-top:8px" disabled=${!paste || busy} onClick=${() => doImport(paste)}>Importer ce contenu</button>
        </details>
        <p class="desc">Après l'import, Ely renouvelle elle-même les jetons ; le CLI Codex pourra te redemander « codex login ».</p>`}
  </div>`;
}

function Models() {
  const [data, reload] = useLoad(() => Promise.all([get("/api/models"), get("/api/admin/models/all")]).then(([a, b]) => ({ ...a, all: b })));
  const [busy, setBusy] = useState(false);
  if (!data) return html`<div class="spinner"></div>`;
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
    <h2>Modèles</h2><p class="muted">Ely choisit automatiquement les meilleurs modèles disponibles ; tu peux imposer les tiens. En cas de panne, elle bascule sur le suivant.</p>
    <div class="card">
      ${Object.entries(ROLE_INFO).map(([role, [label, desc]]) => html`<div class="list-item" style="align-items:center">
        <div class="grow"><b>${label}</b><div class="sub">${desc}<br/>Actuel : <code>${data.roles[role]?.effective || "—"}</code></div></div>
        <select class="input" style="width:min(320px, 50vw)" value=${data.roles[role]?.configured} onChange=${(e) => setRole(role, e.target.value)}>
          <option value="auto">Automatique</option>
          ${(role === "embed" ? embeds : llms).map((m) => html`<option value=${m.ref}>${m.ref}${m.reachable ? "" : " (injoignable)"}</option>`)}
          ${data.roles[role]?.configured !== "auto" && !data.all.some((m) => m.ref === data.roles[role]?.configured) ? html`<option value=${data.roles[role]?.configured}>${data.roles[role]?.configured}</option>` : null}
          <option value="__autre__">Autre modèle…</option>
        </select></div>`)}
    </div>
    <${ChatGPTCard} onChange=${reload} />
    <div class="card">
      <h3>Fournisseurs</h3>
      <div class="list">${Object.entries(data.providers).map(([p, s]) => html`<div class="list-item"><div class="grow"><b>${p}</b><div class="sub">${s}</div></div>
        <span class=${"pill " + (s.startsWith("ok") ? "ok" : "err")}>${s.startsWith("ok") ? "OK" : "hors ligne"}</span></div>`)}</div>
      <p class="desc">Ajoute des clés dans le fichier .env (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, MISTRAL_API_KEY, DEEPSEEK_API_KEY, OPENROUTER_API_KEY…)
        puis clique « Actualiser » : pas besoin de redémarrer Ely.</p>
      <div><button class="btn" disabled=${busy} onClick=${async () => { setBusy(true); await post("/api/admin/models/refresh"); await reload(); setBusy(false); }}>${busy ? "Interrogation…" : "Actualiser les modèles"}</button></div>
    </div>`;
}

function Users() {
  const [users, reload] = useLoad(() => get("/api/admin/users"));
  const [f, setF] = useState({ email: "", name: "", password: "" });
  const [invite, setInvite] = useState("");
  return html`
    <h2>Utilisateurs</h2><p class="muted">Chaque utilisateur a son propre Ely : mémoire, connexions, fichiers et navigateur séparés.</p>
    <div class="card">
      <h3>Inviter</h3>
      <div class="row"><button class="btn" onClick=${async () => { const r = await post("/api/admin/invites"); setInvite(`${location.origin}/?invite=${r.code}`); }}>Créer un lien d'invitation</button></div>
      ${invite ? html`<div class="copy-field"><input class="input" readonly value=${invite} /><button class="btn" onClick=${() => copy(invite)}>Copier</button></div>` : null}
      <h3>Ou créer directement un compte</h3>
      <div class="row" style="flex-wrap:nowrap"><input class="input" placeholder="E-mail" value=${f.email} onInput=${(e) => setF({ ...f, email: e.target.value })} />
        <input class="input" placeholder="Prénom" value=${f.name} onInput=${(e) => setF({ ...f, name: e.target.value })} />
        <input class="input" placeholder="Mot de passe" value=${f.password} onInput=${(e) => setF({ ...f, password: e.target.value })} />
        <button class="btn primary" onClick=${async () => { try { await post("/api/admin/users", f); setF({ email: "", name: "", password: "" }); reload(); } catch (e) { toast(e.message); } }}>Créer</button></div>
    </div>
    <div class="card"><table class="table"><thead><tr><th>Nom</th><th>E-mail</th><th>Rôle</th><th>Tâches</th><th>Vu</th><th></th></tr></thead><tbody>
      ${(users || []).map((u) => html`<tr><td>${u.name}</td><td>${u.email}</td>
        <td><select class="input" style="padding:4px 8px" value=${u.role} onChange=${async (e) => { try { await patch(`/api/admin/users/${u.id}`, { role: e.target.value }); } catch (err) { toast(err.message); } reload(); }}>
          <option value="user">utilisateur</option><option value="admin">admin</option></select></td>
        <td>${u.runs}</td><td>${u.last_seen ? timeAgo(u.last_seen) : "—"}</td>
        <td><button class="icon-btn" onClick=${async () => { if (confirm(`Supprimer ${u.email} et toutes ses données ?`)) { try { await del(`/api/admin/users/${u.id}`); reload(); } catch (e) { toast(e.message); } } }}><${Icon} name="trash" size=${16} /></button></td></tr>`)}
    </tbody></table></div>`;
}

function Usage() {
  const [days, setDays] = useState(30);
  const [data] = useLoad(() => get(`/api/admin/usage?days=${days}`), [days]);
  return html`
    <h2>Consommation</h2><p class="muted">Tokens et coût estimé par utilisateur, modèle et usage (les modèles locaux sont gratuits).</p>
    <div class="row" style="margin-bottom:12px">${[1, 7, 30, 365].map((d) => html`<button class=${"btn small" + (d === days ? " primary" : "")} onClick=${() => setDays(d)}>${d === 1 ? "24 h" : d === 365 ? "1 an" : d + " jours"}</button>`)}</div>
    ${data ? html`<div class="kpis"><div class="kpi"><b>${data.total_cost.toFixed(2)} $</b><span>coût estimé</span></div>
      <div class="kpi"><b>${data.rows.reduce((a, r) => a + r.calls, 0)}</b><span>appels de modèles</span></div>
      <div class="kpi"><b>${Math.round(data.rows.reduce((a, r) => a + (r.input || 0) + (r.output || 0), 0) / 1000)} k</b><span>tokens</span></div></div>
      <div class="card" style="overflow-x:auto"><table class="table"><thead><tr><th>Utilisateur</th><th>Modèle</th><th>Usage</th><th>Appels</th><th>Entrée</th><th>Cache</th><th>Sortie</th><th>Coût</th></tr></thead><tbody>
        ${data.rows.map((r) => html`<tr><td>${r.user}</td><td><code>${r.model}</code></td><td>${r.purpose}</td><td>${r.calls}</td><td>${r.input}</td><td>${r.cached}</td><td>${r.output}</td><td>${r.cost ? r.cost.toFixed(3) + " $" : "—"}</td></tr>`)}
      </tbody></table></div>` : html`<div class="spinner"></div>`}`;
}

const KIND = { code: "🧬", plugin: "🔌", guidelines: "📝", skill: "🎓" };

function SelfDev({ openConversation }) {
  const [data, reload] = useLoad(() => get("/api/admin/selfdev"));
  const [goal, setGoal] = useState("");
  const [guide, setGuide] = useState(null);
  const [detail, setDetail] = useState(null);
  if (!data) return html`<div class="spinner"></div>`;
  const m = data.metrics;
  const ok = (m.by_status.done || 0);
  return html`
    <h2>Auto-amélioration</h2><p class="muted">Ely mesure ses performances et s'améliore : leçons, compétences, nouveaux outils, et corrections de son propre code (testées, avec retour arrière automatique).</p>
    <div class="kpis">
      <div class="kpi"><b>${m.runs}</b><span>tâches (7 j)</span></div>
      <div class="kpi"><b>${m.runs ? Math.round(100 * ok / m.runs) : 0} %</b><span>terminées</span></div>
      <div class="kpi"><b>${m.avg_duration_s} s</b><span>durée moyenne</span></div>
      <div class="kpi"><b>${m.verify_rejections}</b><span>relances du contrôleur</span></div>
      <div class="kpi"><b>${m.estimated_cost_usd} $</b><span>coût estimé</span></div>
    </div>
    <div class="card">
      <h3>🛠️ Lancer une session</h3>
      <div class="row" style="flex-wrap:nowrap"><input class="input" placeholder="Objectif (facultatif) : ex. « sois plus rapide sur Doctolib »" value=${goal} onInput=${(e) => setGoal(e.target.value)} />
        <button class="btn primary" onClick=${async () => { const r = await post("/api/admin/selfdev/run", { goal }); toast("Session lancée"); openConversation(r.conversation_id); }}>Lancer</button></div>
      <label class="row"><span class="switch"><input type="checkbox" checked=${data.auto} onChange=${async (e) => { await put("/api/admin/selfdev", { auto: e.target.checked }); reload(); }} /><span></span></span>
        Session automatique chaque nuit à <input class="input" type="number" min="0" max="23" style="width:70px" value=${data.hour} onChange=${async (e) => { await put("/api/admin/selfdev", { hour: parseInt(e.target.value) }); reload(); }} /> h</label>
    </div>
    <div class="card">
      <h3>📝 Leçons tirées de l'expérience</h3>
      <p class="desc">Consignes injectées dans chaque tâche. Ely les écrit elle-même ; tu peux les corriger.</p>
      <textarea class="input" rows="6" value=${guide ?? data.guidelines} placeholder="Aucune leçon pour l'instant." onInput=${(e) => setGuide(e.target.value)}></textarea>
      <div><button class="btn" disabled=${guide === null} onClick=${async () => { await put("/api/admin/selfdev", { guidelines: guide }); setGuide(null); reload(); toast("Enregistré"); }}>Enregistrer</button></div>
    </div>
    ${data.plugins.length ? html`<div class="card"><h3>🔌 Outils créés par Ely</h3><div class="list">${data.plugins.map((p) => html`<div class="list-item">
      <label class="switch"><input type="checkbox" checked=${p.enabled} onChange=${async (e) => { await post(`/api/admin/plugins/${p.name}`, { enabled: e.target.checked }); reload(); }} /><span></span></label>
      <div class="grow"><b>${p.name}</b><div class="sub">${p.tools.join(", ") || "—"}</div></div></div>`)}</div></div>` : null}
    ${m.tools.length ? html`<div class="card"><h3>📊 Outils les plus en échec (7 j)</h3><table class="table"><thead><tr><th>Outil</th><th>Appels</th><th>Erreurs</th><th>Durée moy.</th></tr></thead><tbody>
      ${m.tools.slice(0, 8).map((t) => html`<tr><td>${t.name}</td><td>${t.calls}</td><td>${t.errors || 0}</td><td>${t.avg_ms} ms</td></tr>`)}</tbody></table></div>` : null}
    <div class="card">
      <h3>📜 Journal des améliorations</h3>
      <div class="list">${data.journal.map((j) => html`<div class="list-item">
        <span style="font-size:18px">${KIND[j.kind] || "•"}</span>
        <div class="grow"><b style="cursor:pointer" onClick=${async () => setDetail(detail?.id === j.id ? null : await get(`/api/admin/improvements/${j.id}`))}>${j.title}</b>
          <div class="sub">${j.status} · ${dateTime(j.created_at)}${j.commit_sha ? " · " + j.commit_sha.slice(0, 10) : ""}</div>
          ${detail?.id === j.id ? html`${detail.detail ? html`<div class="small muted">${detail.detail}</div>` : null}${detail.diff ? html`<pre class="diff">${detail.diff}</pre>` : null}` : null}</div>
        ${j.kind === "code" && j.status === "deployed" ? html`<button class="btn small" onClick=${async () => { if (confirm("Annuler cette modification du code ?")) { try { const r = await post(`/api/admin/improvements/${j.id}/revert`); toast(r.message); reload(); } catch (e) { toast(e.message); } } }}>Annuler</button>` : null}
      </div>`)}${!data.journal.length ? html`<div class="faint">Rien pour l'instant.</div>` : null}</div>
    </div>`;
}

function Extensions() {
  const [data, reload] = useLoad(() => get("/api/admin/mcp"));
  const [text, setText] = useState(null);
  const [busy, setBusy] = useState(false);
  if (!data) return html`<div class="spinner"></div>`;
  const value = text ?? JSON.stringify(data.servers, null, 2);
  async function save() {
    let servers;
    try { servers = JSON.parse(value || "{}"); } catch (e) { toast("JSON invalide : " + e.message); return; }
    setBusy(true);
    try { await put("/api/admin/mcp", { servers }); setText(null); await reload(); toast("Serveurs MCP rechargés"); } catch (e) { toast(e.message); }
    setBusy(false);
  }
  return html`
    <h2>Extensions MCP</h2><p class="muted">Branche des serveurs MCP : leurs outils deviennent ceux d'Ely (Home Assistant, Notion, GitHub, fichiers du Mac…).
      Tu peux aussi simplement demander à Ely : « branche-toi sur tel serveur MCP ».</p>
    <div class="card">
      ${Object.keys(data.status).length ? html`<div class="list">${Object.entries(data.status).map(([n, s]) => html`<div class="list-item">
        <div class="grow"><b>${n}</b><div class="sub">${s.status}${s.tools.length ? " · " + s.tools.join(", ") : ""}</div></div>
        <span class=${"pill " + (s.status.startsWith("ok") ? "ok" : "err")}>${s.status.startsWith("ok") ? "OK" : "erreur"}</span></div>`)}</div>`
        : html`<div class="faint">Aucun serveur branché.</div>`}
    </div>
    <div class="card">
      <h3>Configuration</h3>
      <p class="desc">Format : <code>{"nom": {"command": "npx", "args": ["-y", "paquet-mcp"], "env": {}}}</code> ou <code>{"nom": {"url": "https://…/mcp"}}</code></p>
      <textarea class="input" rows="12" style="font-family:var(--mono);font-size:13px" value=${value} onInput=${(e) => setText(e.target.value)}></textarea>
      <div><button class="btn primary" disabled=${busy} onClick=${save}>${busy ? "Connexion…" : "Enregistrer et recharger"}</button></div>
    </div>`;
}

// ---------------------------------------------------------------- conteneur
export function Settings({ me, onMe, tab, onTab, onClose, pwa, openConversation }) {
  const tabs = [["profil", "👤", "Profil"], ["memoire", "🧠", "Mémoire"], ["connexions", "🔗", "Connexions"], ["identifiants", "🔑", "Identifiants"],
    ["taches", "⏰", "Tâches planifiées"], ["fichiers", "🗂️", "Fichiers"]];
  const admin = [["modeles", "🤖", "Modèles"], ["utilisateurs", "👥", "Utilisateurs"], ["conso", "📈", "Consommation"], ["mcp", "🧩", "Extensions MCP"], ["auto", "🛠️", "Auto-amélioration"]];
  const pane = {
    profil: html`<${Profile} me=${me} onMe=${onMe} pwa=${pwa} />`, memoire: html`<${Memory} />`, connexions: html`<${Connections} />`,
    identifiants: html`<${Vault} />`, taches: html`<${Schedules} />`, fichiers: html`<${Files} />`, modeles: html`<${Models} />`,
    utilisateurs: html`<${Users} />`, conso: html`<${Usage} />`, mcp: html`<${Extensions} />`, auto: html`<${SelfDev} openConversation=${(id) => { onClose(); openConversation(id); }} />`,
  }[tab] || html`<${Profile} me=${me} onMe=${onMe} pwa=${pwa} />`;
  useEffect(() => { const k = (e) => e.key === "Escape" && onClose(); addEventListener("keydown", k); return () => removeEventListener("keydown", k); }, []);
  return html`<div class="sheet-scrim" onClick=${onClose}></div>
    <div class="sheet" role="dialog" aria-label="Réglages">
      <button class="icon-btn sheet-close" onClick=${onClose} title="Fermer"><${Icon} name="close" /></button>
      <nav>
        <div class="nav-title">Réglages</div>
        ${tabs.map(([k, i, l]) => html`<button class=${tab === k ? "active" : ""} onClick=${() => onTab(k)}><span>${i}</span>${l}</button>`)}
        ${me.role === "admin" ? html`<div class="nav-title">Administration</div>
          ${admin.map(([k, i, l]) => html`<button class=${tab === k ? "active" : ""} onClick=${() => onTab(k)}><span>${i}</span>${l}</button>`)}` : null}
      </nav>
      <div class="pane">${pane}</div>
    </div>`;
}
