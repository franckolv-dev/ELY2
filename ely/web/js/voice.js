// Voix : dictée (reconnaissance du navigateur, sinon transcription côté serveur) et lecture à voix haute.
import { api, get } from "/static/js/api.js";
import { getLang, speechLang, t } from "/static/js/i18n.js";

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

export function voiceSupported() {
  return !!SR || !!(navigator.mediaDevices && window.MediaRecorder);
}

// Démarre une dictée. Renvoie une fonction stop(). onText(texte, final).
export function listen({ onText, onEnd, onError, lang = speechLang() }) {
  if (SR) {
    const rec = new SR();
    rec.lang = lang;
    rec.interimResults = true;
    rec.continuous = !/Android/i.test(navigator.userAgent);
    let finalText = "";
    let stopped = false;
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) finalText += r[0].transcript + " ";
        else interim += r[0].transcript;
      }
      onText((finalText + interim).trim(), false);
    };
    rec.onerror = (e) => { if (e.error !== "no-speech" && e.error !== "aborted") onError?.(e.error); };
    rec.onend = () => { onText(finalText.trim(), true); if (!stopped) onEnd?.(); };
    rec.start();
    return () => { stopped = true; rec.stop(); onEnd?.(); };
  }
  // Repli : enregistrement puis transcription par le serveur (Groq / OpenAI Whisper)
  let recorder, chunks = [], stream;
  navigator.mediaDevices.getUserMedia({ audio: true }).then((s) => {
    stream = s;
    recorder = new MediaRecorder(s);
    recorder.ondataavailable = (e) => chunks.push(e.data);
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
      const form = new FormData();
      form.append("file", blob, "dictee.webm");
      try {
        const res = await api("/api/transcribe", { method: "POST", form });
        onText(res.text || "", true);
      } catch (err) { onError?.(err.message); }
      onEnd?.();
    };
    recorder.start();
  }).catch((err) => { onError?.(err.message); onEnd?.(); });
  return () => { if (recorder && recorder.state !== "inactive") recorder.stop(); };
}

function plain(text) {
  return (text || "")
    .replace(/```[\s\S]*?```/g, t("voice.code"))
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_`#>|]/g, "")
    .replace(/https?:\/\/\S+/g, t("voice.link"))
    .replace(/\n{2,}/g, ". ");
}

// Voix « fantaisie » de macOS (synthèse Eloquence et effets sonores), déclinées en français de France et du Canada :
// robotiques, inutilisables pour lire des réponses. On ne les propose pas.
const POOR = /^(Eddy|Flo|Grandma|Grandpa|Reed|Rocko|Sandy|Shelley|Albert|Bad News|Bahh|Bells|Boing|Bubbles|Cellos|Good News|Jester|Organ|Superstar|Trinoids|Whisper|Wobble|Zarvox)\b/i;
// voix de bonne qualité : en ligne (Google) ou téléchargées dans macOS (Premium, Améliorée)
const GOOD = /google|natural|neural|premium|enhanced|amélior|qualité supérieure/i;

// voix utilisables dans la langue de l'interface, les meilleures d'abord
export function voicesForLang() {
  if (!("speechSynthesis" in window)) return [];
  const lang = getLang();
  return speechSynthesis.getVoices().filter((v) => v.lang?.startsWith(lang) && !POOR.test(v.name))
    .sort((a, b) => GOOD.test(b.name) - GOOD.test(a.name) || a.name.localeCompare(b.name));
}

// Voix enregistrées (voix clonée) : service vocal XTTS du Mac, relayé par Ely. Choix gardé sous « xtts:<nom> ».
export const RECORDED = "xtts:";
let recorded = { voices: [], default: "" };

export async function loadRecordedVoices() {
  try { recorded = await get("/api/tts/voices"); } catch { recorded = { voices: [], default: "" }; }
  return recorded.voices;
}

// voix retenue dans le profil ; sinon la voix enregistrée, puis la meilleure voix du navigateur
function pickVoice() {
  const saved = localStorage.getItem("ely-voice") || "";
  if (saved.startsWith(RECORDED) && recorded.voices.includes(saved.slice(RECORDED.length))) return saved;
  const voices = voicesForLang();
  return voices.find((v) => v.name === saved) || (!saved && recorded.voices.length ? RECORDED + (recorded.default || recorded.voices[0]) : null)
    || voices[0] || null;
}

// phrases à synthétiser une à une : la première se fait entendre sans attendre la réponse entière
function sentences(text) {
  const out = [];
  for (const part of text.replace(/\s+/g, " ").trim().split(/(?<=[.!?…:;])\s+/)) {
    if (out.length && out[out.length - 1].length < 25) out[out.length - 1] += " " + part;
    else if (part) out.push(part);
  }
  return out;
}

let session = 0; // chaque lecture a son numéro : stopSpeaking() ou une nouvelle lecture arrête la précédente
let audio = null;

async function speakRecorded(text, name, onEnd, fallback) {
  const id = ++session;
  const fetchOne = (s) => fetch("/api/tts", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: s, voice: name, language: getLang() }) }).then((r) => (r.ok ? r.blob() : Promise.reject(r.status)));
  const queue = sentences(text);
  let next = queue.length ? fetchOne(queue.shift()) : null;
  try {
    while (next && id === session) {
      const blob = await next;
      next = queue.length ? fetchOne(queue.shift()) : null; // la phrase suivante se calcule pendant la lecture
      if (id !== session) return;
      const url = URL.createObjectURL(blob);
      audio = new Audio(url);
      await new Promise((resolve) => { audio.onended = resolve; audio.onerror = resolve; audio.play().catch(resolve); });
      URL.revokeObjectURL(url);
    }
    if (id === session) onEnd?.();
  } catch {
    if (id === session) fallback(); // service arrêté : on continue avec la voix du navigateur
  }
}

function speakBrowser(text, voice, onEnd) {
  if (!("speechSynthesis" in window)) { onEnd?.(); return; }
  const u = new SpeechSynthesisUtterance(text);
  u.lang = speechLang();
  if (voice) u.voice = voice;
  u.rate = parseFloat(localStorage.getItem("ely-rate") || "1.05");
  u.onend = () => onEnd?.();
  u.onerror = () => onEnd?.();
  speechSynthesis.speak(u);
}

export function speak(text, onEnd) {
  stopSpeaking();
  const clean = plain(text).slice(0, 3000);
  const voice = pickVoice();
  if (typeof voice === "string") {
    speakRecorded(clean, voice.slice(RECORDED.length), onEnd,
      () => speakBrowser(clean, voicesForLang()[0] || null, onEnd));
  } else speakBrowser(clean, voice, onEnd);
}

export function stopSpeaking() {
  session++;
  if (audio) { audio.pause(); audio = null; }
  if ("speechSynthesis" in window) speechSynthesis.cancel();
}
