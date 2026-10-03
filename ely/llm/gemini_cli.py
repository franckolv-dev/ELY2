"""Gemini par l'abonnement Google (AI Pro…), sans clé d'API : Ely pilote le CLI officiel `gemini`.

Connexion une fois pour toutes, dans un terminal du Mac : `npm install -g @google/gemini-cli`, puis `gemini` et
« Sign in with Google ». Le CLI garde sa session dans ~/.gemini/. Chaque appel d'Ely lance ensuite
`gemini -p … -o stream-json` (mode non interactif, flux JSONL : init, message, tool_use, tool_result, error, result) :
- l'abonnement est imposé (GOOGLE_GENAI_USE_GCA=true) et aucune clé Gemini ou Google n'est transmise : le CLI les
  préférerait au compte Google, y compris lues dans un .env d'un dossier parent, qu'il parcourt de lui-même ;
- il tourne dans un dossier vide, hors du dépôt d'Ely (un `@chemin` du texte n'y désigne aucun fichier) ;
- le prompt système d'Ely remplace celui du CLI (GEMINI_SYSTEM_MD), et un fichier de règles refuse tous les outils du
  CLI : les outils sont ceux d'Ely, demandés par un bloc ```tool_calls``` que ce module traduit en appels d'outils.
Comportements vérifiés dans le code du CLI 0.62. L'usage compte dans les limites quotidiennes de l'abonnement.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import tempfile
import uuid
from pathlib import Path

from ..config import settings
from ..db import db
from .base import DeltaCallback, LLMError, LLMResponse, ModelInfo, ToolCall, message_text

log = logging.getLogger("ely.llm")

NAME = "geminicli"
SETTING = "geminicli_enabled"  # activé par l'administrateur (Réglages → Modèles)
MODELS = ["gemini-3.8-flash"]  # autre modèle de l'abonnement : « Autre modèle… » → geminicli:<nom>
CONTEXT = 1_000_000
SILENCE = 300  # secondes sans rien recevoir du CLI : il est figé
AUTH_EXIT = 41  # code de sortie du CLI quand l'authentification échoue
# variables qui feraient passer le CLI par une clé d'API (facturée) ou un autre service que l'abonnement
API_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GEMINI_BASE_URL",
            "GOOGLE_APPLICATION_CREDENTIALS", "GEMINI_CLI_USE_COMPUTE_ADC")
DENY_ALL = """# Ely fournit ses propres outils : ceux du CLI sont tous refusés.
[[rule]]
toolName = "*"
decision = "deny"
priority = 999
denyMessage = "Outil indisponible : utilise le bloc tool_calls décrit dans tes instructions."
"""
INSTRUCTION = "Réponds maintenant, en tant qu'Ely, au dernier message de la conversation ci-dessus."
FENCE = "```tool_calls"
TOOLS_GUIDE = f"""# Outils
Tu disposes des outils ci-dessous, et d'eux seuls. Pour en utiliser, réponds uniquement par ce bloc :
{FENCE}
[{{"name": "nom_de_l_outil", "arguments": {{…}}}}]
```
Plusieurs appels indépendants peuvent figurer dans la même liste. Tu reçois leurs résultats au tour suivant.
Quand tu n'as plus besoin d'outil, réponds normalement, sans bloc."""


def gemini_home() -> Path:
    return Path.home() / ".gemini"


def binary() -> str | None:
    """Le CLI `gemini` : GEMINI_CLI dans .env, sinon le PATH et les dossiers où npm l'installe (le service macOS
    démarre avec un PATH réduit)."""
    if settings.gemini_cli:
        return settings.gemini_cli if Path(settings.gemini_cli).exists() else None
    home = Path.home()
    extra = [home / ".npm-global/bin", home / ".local/bin", home / ".volta/bin", home / ".bun/bin",
             Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    extra += sorted(home.glob(".nvm/versions/node/*/bin"), reverse=True)
    path = os.pathsep.join([os.environ.get("PATH", ""), *map(str, extra)])
    return shutil.which("gemini", path=path)


def auth_type() -> str:
    """Méthode choisie dans le CLI (~/.gemini/settings.json) : prioritaire sur l'environnement."""
    try:
        data = json.loads((gemini_home() / "settings.json").read_text())
    except (OSError, ValueError):
        return ""
    auth = (data.get("security") or {}).get("auth") or {}
    return str(auth.get("selectedType") or data.get("selectedAuthType") or "")


def account() -> str:
    try:
        return str(json.loads((gemini_home() / "google_accounts.json").read_text()).get("active") or "")
    except (OSError, ValueError):
        return ""


def logged_in() -> bool:
    return (gemini_home() / "oauth_creds.json").exists()


def enabled() -> bool:
    return bool(db.get_setting(SETTING, False))


def problem() -> str:
    """Ce qui empêche d'utiliser l'abonnement, ou '' si tout est prêt."""
    if not binary():
        return "CLI gemini introuvable : npm install -g @google/gemini-cli (ou GEMINI_CLI=chemin dans .env)"
    if not logged_in():
        return "CLI gemini pas encore connecté : lancez « gemini » dans un terminal et choisissez « Sign in with Google »"
    if auth_type() not in ("", "oauth-personal"):
        return (f"CLI gemini réglé sur « {auth_type()} » : lancez « gemini », tapez /auth et choisissez "
                "« Sign in with Google »")
    return ""


async def version() -> str:
    path = binary()
    if not path:
        return ""
    try:
        proc = await asyncio.create_subprocess_exec(path, "--version", stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL, env=child_env(path))
        out, _ = await asyncio.wait_for(proc.communicate(), 20)
        return out.decode(errors="replace").strip().splitlines()[-1] if out.strip() else ""
    except Exception:
        return ""


async def status() -> dict:
    return {"installed": bool(binary()), "path": binary() or "", "version": await version(), "logged_in": logged_in(),
            "account": account(), "auth_type": auth_type(), "enabled": enabled(), "problem": problem(),
            "api_key": any(os.environ.get(k) for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
            "models": [f"{NAME}:{m}" for m in MODELS]}


def workdir() -> tuple[Path, Path]:
    """(dossier de travail du CLI, toujours vide ; dossier des fichiers d'Ely pour le CLI), hors du dépôt."""
    base = Path(tempfile.gettempdir()) / "ely-gemini"
    space = base / "espace"
    space.mkdir(parents=True, exist_ok=True)
    policy = base / "regles.toml"
    if not policy.exists() or policy.read_text() != DENY_ALL:
        policy.write_text(DENY_ALL)
    return space, base


def child_env(path: str, system_md: Path | None = None) -> dict[str, str]:
    """Environnement du CLI : celui d'Ely sans aucun secret (clés vides : un .env parent ne les remplace pas),
    abonnement imposé, prompt système d'Ely."""
    from ..tools.files import KEEP, SECRET_NAME

    env = {k: ("" if (SECRET_NAME.search(k) and k not in KEEP) else v) for k, v in os.environ.items()}
    env.update({k: "" for k in API_VARS})
    env.update(GOOGLE_GENAI_USE_GCA="true", NO_COLOR="1", TERM="dumb",
               PATH=os.pathsep.join([str(Path(path).parent), env.get("PATH", "")]))  # node est installé avec gemini
    if system_md:
        env["GEMINI_SYSTEM_MD"] = str(system_md)
    return env


# ---------------------------------------------------------------------- conversation en texte
def tools_section(tools: list[dict]) -> str:
    lines = [TOOLS_GUIDE]
    for t in tools:
        lines.append(f"\n## {t['name']}\n{t.get('description', '')}\n"
                     f"Paramètres (JSON Schema) : {json.dumps(t.get('parameters') or {}, ensure_ascii=False)}")
    return "\n".join(lines)


def transcript(messages: list[dict]) -> str:
    """L'historique d'Ely sous forme de texte, avec les appels d'outils dans le format que le modèle doit employer."""
    out = []
    for m in messages:
        text = message_text(m).strip()
        if isinstance(m.get("content"), list) and any(p.get("type") == "image" for p in m["content"]):
            text += "\n[image jointe : non transmise à Gemini]"
        if m["role"] == "user":
            out.append(f"<<{'Contrôle' if m.get('kind') == 'control' else 'Personne'}>>\n{text}")
        elif m["role"] == "assistant":
            if text:
                out.append(f"<<Ely>>\n{text}")
            if m.get("tool_calls"):
                calls = [{"id": tc["id"], "name": tc["name"], "arguments": tc.get("arguments") or {}} for tc in m["tool_calls"]]
                out.append(f"<<Ely>>\n{FENCE}\n{json.dumps(calls, ensure_ascii=False)}\n```")
        elif m["role"] == "tool":
            state = "erreur" if m.get("is_error") else "résultat"
            out.append(f"<<{state} de l'outil {m.get('name', '')} (appel {m.get('tool_call_id', '')})>>\n{text}"
                       + ("\n[capture d'écran : non transmise à Gemini]" if m.get("images") else ""))
    return "\n\n".join(out)


def split_tool_calls(text: str, known: set[str]) -> tuple[str, list[ToolCall]]:
    """Sépare le texte de la réponse et les appels d'outils demandés dans un bloc ```tool_calls```."""
    m = re.search(r"```tool_calls\s*(.*?)(?:```|$)", text, flags=re.S)
    if not m:
        return text.strip(), []
    try:
        raw = json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return text.strip(), []
    raw = raw if isinstance(raw, list) else [raw]
    calls = []
    for c in raw:
        if isinstance(c, dict) and c.get("name") in known:
            args = c.get("arguments") if isinstance(c.get("arguments"), dict) else {}
            calls.append(ToolCall(id=f"gem_{uuid.uuid4().hex[:10]}", name=c["name"], arguments=args))
    rest = (text[:m.start()] + text[m.end():]).strip()
    return rest, calls


def _visible(text: str) -> str:
    """Partie de la réponse à afficher pendant qu'elle s'écrit : jamais le bloc d'appels d'outils."""
    cut = text.find("```tool")
    if cut >= 0:
        return text[:cut]
    for n in range(min(len(FENCE), len(text)), 0, -1):  # début possible du bloc : on attend la suite
        if FENCE.startswith(text[-n:]):
            return text[:-n]
    return text


def _error(message: str, code: int) -> LLMError:
    low = message.lower()
    if code == AUTH_EXIT or "authenticat" in low or "auth method" in low or "manual authorization" in low:
        return LLMError("Gemini (abonnement) : le CLI n'est pas connecté à votre compte Google. Lancez « gemini » dans "
                        "un terminal du Mac et choisissez « Sign in with Google ».", kind="auth")
    if "quota" in low or "resource_exhausted" in low or "429" in low or "rate limit" in low:
        return LLMError(f"Gemini (abonnement) : limite de l'abonnement atteinte ({message[:200]})", status=429)
    if "not found" in low or "404" in low:
        return LLMError(f"Gemini (abonnement) : modèle introuvable ({message[:200]})", kind="not_found")
    return LLMError(f"Gemini (abonnement) : {message[:300] or f'arrêt du CLI (code {code})'}", retryable=True)


class GeminiCLIProvider:
    kind = NAME
    name = NAME

    def __init__(self) -> None:
        self.models = [ModelInfo(id=m, provider=NAME, context=CONTEXT) for m in MODELS]

    async def list_models(self) -> list[ModelInfo]:
        if why := problem():
            raise LLMError(why, kind="auth")
        return self.models

    def info(self, model: str) -> ModelInfo:
        return next((m for m in self.models if m.id == model), ModelInfo(id=model, provider=NAME, context=CONTEXT))

    async def chat(self, model: str, system: list[str], messages: list[dict], tools: list[dict] | None = None,
                   on_delta: DeltaCallback = None, max_tokens: int = 0, effort: str = "high") -> LLMResponse:
        path = binary()
        if not path:
            raise LLMError(problem(), kind="not_found")
        if auth_type() not in ("", "oauth-personal"):
            # CLI réglé sur une clé d'API : ce réglage passe avant l'environnement, et il irait chercher la clé dans le
            # trousseau du Mac. L'abonnement ne serait pas utilisé : l'API serait facturée.
            raise LLMError(problem(), kind="auth")
        space, base = workdir()
        system_md = base / f"systeme-{uuid.uuid4().hex}.md"
        system_md.write_text("\n\n".join([*system, tools_section(tools)] if tools else system) or "Tu es Ely.")
        args = [path, "-p", INSTRUCTION, "-o", "stream-json", "--skip-trust", "-m", model,
                "--approval-mode", "plan", "--policy", str(base / "regles.toml")]
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=str(space), env=child_env(path, system_md), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=2**24)
        try:
            return await self._read(proc, model, transcript(messages), {t["name"] for t in tools or []}, on_delta)
        finally:
            if proc.returncode is None:  # annulation, délai dépassé : le CLI et ses enfants s'arrêtent avec Ely
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
            system_md.unlink(missing_ok=True)

    async def _read(self, proc, model: str, prompt: str, known: set[str], on_delta: DeltaCallback) -> LLMResponse:
        proc.stdin.write(prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()
        stderr = asyncio.ensure_future(proc.stderr.read())
        text, shown, errors, stats, failed = "", 0, [], {}, False
        while True:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), SILENCE)
            except asyncio.TimeoutError:
                raise LLMError(f"Gemini (abonnement) : aucune réponse du CLI en {SILENCE // 60} min", retryable=True,
                               kind="timeout") from None
            if not line:
                break
            try:
                ev = json.loads(line)
            except ValueError:  # ligne qui n'est pas un événement (avertissement du CLI)
                continue
            kind = ev.get("type")
            if kind == "message" and ev.get("role") == "assistant" and isinstance(ev.get("content"), str):
                text += ev["content"]
                visible = _visible(text)
                if on_delta and len(visible) > shown:
                    await on_delta("text", visible[shown:])
                    shown = len(visible)
            elif kind == "error":
                errors.append(str(ev.get("message") or ""))
                failed = failed or ev.get("severity") == "error"
            elif kind == "result":  # fin : statistiques, et l'erreur de l'API s'il y en a une (quota, modèle…)
                stats = ev.get("stats") or {}
                if ev.get("status") == "error":
                    failed = True
                    errors.append(str((ev.get("error") or {}).get("message") or ""))
        code = await proc.wait()
        err = (await stderr).decode(errors="replace")
        if code != 0 or (failed and not text.strip()):
            detail = next((e for e in reversed(errors) if e), "") or _last_line(err)
            raise _error(detail, code)
        content, calls = split_tool_calls(text, known)
        cached = int(stats.get("cached") or 0)
        return LLMResponse(text=content, tool_calls=calls, stop_reason="tool_use" if calls else "end_turn",
                           model=f"{NAME}:{model}", input_tokens=int(stats.get("input_tokens") or 0),
                           output_tokens=int(stats.get("output_tokens") or 0), cached_tokens=cached)

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        raise LLMError("Gemini (abonnement) ne calcule pas de vecteurs", kind="not_found")


def _last_line(stderr: str) -> str:
    """Dernière ligne utile du journal d'erreurs du CLI (sans la pile d'appels ni les avertissements de terminal)."""
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip() and not ln.strip().startswith("at ")
             and "color support" not in ln]
    return lines[-1] if lines else ""


async def ping(model: str = MODELS[0]) -> dict:
    """Petit appel pour vérifier la connexion à l'abonnement."""
    try:
        r = await GeminiCLIProvider().chat(model, ["Tu es Ely."], [{"role": "user", "content": "Réponds uniquement : OK"}])
        return {"ok": True, "text": r.text[:200], "model": r.model}
    except LLMError as e:
        return {"ok": False, "error": str(e)}
