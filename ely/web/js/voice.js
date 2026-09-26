// Voix : dictée (reconnaissance du navigateur, sinon transcription côté serveur) et lecture à voix haute.
import { api } from "/static/js/api.js";
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

// voix retenue dans le profil, sinon la meilleure disponible
function pickVoice() {
  const voices = voicesForLang();
  const saved = localStorage.getItem("ely-voice");
  return voices.find((v) => v.name === saved) || voices[0] || null;
}

export function speak(text, onEnd) {
  if (!("speechSynthesis" in window)) { onEnd?.(); return; }
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(plain(text).slice(0, 3000));
  u.lang = speechLang();
  const voice = pickVoice();
  if (voice) u.voice = voice;
  u.rate = parseFloat(localStorage.getItem("ely-rate") || "1.05");
  u.onend = () => onEnd?.();
  u.onerror = () => onEnd?.();
  speechSynthesis.speak(u);
}

export function stopSpeaking() {
  if ("speechSynthesis" in window) speechSynthesis.cancel();
}
