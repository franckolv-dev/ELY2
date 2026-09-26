// Installation : l'application (PWA) et l'extension « Ely pour Chrome ».
import { html, useEffect, useState } from "/static/vendor/preact-htm.js";
import { get } from "/static/js/api.js";
import { t } from "/static/js/i18n.js";
import { Icon, toast } from "/static/js/util.js";

export const isStandalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

// navigateur courant : on n'affiche que la marche à suivre qui le concerne
export function browserKind() {
  const ua = navigator.userAgent;
  if (/iPhone|iPad|iPod/.test(ua)) return "ios";
  if (/Android/.test(ua)) return "android";
  if (/Firefox\//.test(ua)) return "firefox";
  if (/Chrome\//.test(ua)) return "chrome"; // Chrome, Edge, Brave, Opera…
  if (/Safari\//.test(ua)) return "safari";
  return "other";
}

export const onPhone = () => ["ios", "android"].includes(browserKind());

export function Dialog({ title, sub, onClose, children }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, []);
  return html`<div class="sheet-scrim" onClick=${onClose}>
    <div class="dialog" role="dialog" aria-modal="true" aria-label=${title} onClick=${(e) => e.stopPropagation()}>
      <button class="sheet-close" title=${t("common.close")} onClick=${onClose}><${Icon} name="close" size=${14} /></button>
      <h2>${title}</h2>
      ${sub ? html`<p class="sub">${sub}</p>` : null}
      ${children}
    </div>
  </div>`;
}

// quand le navigateur ne propose pas lui-même l'installation (Safari, iPhone, adresse non sécurisée…)
export function InstallDialog({ onClose }) {
  const kind = isSecureContext ? browserKind() : "insecure";
  return html`<${Dialog} title=${t("install.title")} sub=${t("install.sub")} onClose=${onClose}>
    <p class="dialog-text">${t(`install.${kind}`)}</p>
  <//>`;
}

// étapes d'installation de l'extension : téléchargement déjà réglé sur l'adresse de cet Ely
export function ExtensionSteps({ folder }) {
  const zip = `/api/chrome/extension.zip?url=${encodeURIComponent(location.origin)}`;
  const copyAddress = () => navigator.clipboard?.writeText("chrome://extensions").then(() => toast(t("common.copied")));
  return html`<ol class="install-steps">
      <li><a class="btn primary small" href=${zip} download="ely-chrome.zip"><${Icon} name="download" size=${15} /> ${t("ext.download")}</a>
        <span class="step-note">${t("ext.unzip")}</span></li>
      <li>${t("ext.open")} <button class="btn small" onClick=${copyAddress}>${t("ext.copyAddress")}</button></li>
      <li>${t("ext.load")}</li>
      <li>${t("ext.done")}</li>
    </ol>
    ${folder ? html`<p class="desc">${t("ext.folder", { folder })}</p>` : null}`;
}

export function ExtensionDialog({ me, onClose }) {
  const [st, setSt] = useState(null);
  useEffect(() => {
    const load = () => get("/api/chrome").then(setSt).catch(() => {});
    load();
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, []);
  return html`<${Dialog} title=${t("ext.title")} sub=${t("ext.sub")} onClose=${onClose}>
    ${st ? html`<p class=${"ext-status" + (st.connected ? " ok" : "")}>
      ${st.connected ? html`<${Icon} name="check" size=${15} /> ${t("ext.connected", { v: st.version || "" })}` : t("ext.notConnected")}</p>` : null}
    ${browserKind() !== "chrome" ? html`<p class="dialog-text warn">${t("ext.needChrome")}</p>` : null}
    <${ExtensionSteps} folder=${me.role === "admin" ? st?.folder : ""} />
    <p class="desc">${t("ext.whyManual")}</p>
  <//>`;
}
