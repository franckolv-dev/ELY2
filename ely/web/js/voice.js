// Voix : dictée (reconnaissance du navigateur, sinon transcription côté serveur) et lecture à voix haute.
import { api } from "/static/js/api.js";

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

export function voiceSupported() {
  return !!SR || !!(navigator.mediaDevices && window.MediaRecorder);
}

// Démarre une dictée. Renvoie une fonction stop(). onText(texte, final).
export function listen({ onText, onEnd, onError, lang = "fr-FR" }) {
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
    .replace(/```[\s\S]*?```/g, " (bloc de code) ")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_`#>|]/g, "")
    .replace(/https?:\/\/\S+/g, "le lien")
    .replace(/\n{2,}/g, ". ");
}

let frenchVoice = null;
function pickVoice() {
  const voices = speechSynthesis.getVoices();
  const saved = localStorage.getItem("ely-voice");
  frenchVoice = voices.find((v) => v.name === saved) ||
    voices.find((v) => v.lang?.startsWith("fr") && /natural|neural|premium|enhanced|google/i.test(v.name)) ||
    voices.find((v) => v.lang?.startsWith("fr")) || null;
}
if ("speechSynthesis" in window) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }

export function frenchVoices() {
  return "speechSynthesis" in window ? speechSynthesis.getVoices().filter((v) => v.lang?.startsWith("fr")) : [];
}

export function speak(text, onEnd) {
  if (!("speechSynthesis" in window)) { onEnd?.(); return; }
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(plain(text).slice(0, 3000));
  u.lang = "fr-FR";
  if (frenchVoice) u.voice = frenchVoice;
  u.rate = parseFloat(localStorage.getItem("ely-rate") || "1.05");
  u.onend = () => onEnd?.();
  u.onerror = () => onEnd?.();
  speechSynthesis.speak(u);
}

export function stopSpeaking() {
  if ("speechSynthesis" in window) speechSynthesis.cancel();
}
