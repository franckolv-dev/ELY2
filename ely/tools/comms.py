"""E-mails : envoi, brouillon, réponse, recherche, lecture (Gmail API ou IMAP/SMTP)."""
from __future__ import annotations

import mimetypes
from email.message import EmailMessage

from ..integrations import connected, get
from ..integrations import google as g
from ..integrations import mail
from . import ToolContext, ToolResult, tool


def _backend(ctx: ToolContext) -> str:
    if connected(ctx.user_id, "email"):
        return "imap"
    if connected(ctx.user_id, "google"):
        return "gmail"
    return ""


NO_MAIL = ("Aucune boîte mail connectée. Options : 1) propose à l'utilisateur de connecter Google ou son e-mail "
           "dans Réglages → Connexions ; 2) en attendant, utilise son webmail avec l'outil browser (session déjà ouverte ?).")


def _build(ctx: ToolContext, to: str, subject: str, body: str, cc: str = "", bcc: str = "", attachments: list[str] | None = None,
           html: bool = False, in_reply_to: str = "") -> EmailMessage:
    msg = EmailMessage()
    backend = _backend(ctx)
    if backend == "imap":
        msg["From"] = mail.from_header(mail.config(ctx.user_id), ctx.user.get("name", ""))
    elif backend == "gmail":
        gdata = get(ctx.user_id, "google")
        msg["From"] = f"{gdata.get('name') or ctx.user.get('name', '')} <{gdata.get('email', '')}>"
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    if html:
        from bs4 import BeautifulSoup

        msg.set_content(BeautifulSoup(body, "html.parser").get_text("\n"))
        msg.add_alternative(body, subtype="html")
    else:
        msg.set_content(body)
    for a in attachments or []:
        path = ctx.resolve_path(a)
        if not path.exists():
            raise FileNotFoundError(f"pièce jointe introuvable : {a}")
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        main, sub = ctype.split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=main, subtype=sub, filename=path.name)
    return msg


@tool("email_send", """Envoie un e-mail depuis la boîte de l'utilisateur (ou l'enregistre en brouillon avec draft=true).
Pour répondre à un message, passe reply_to_id (id obtenu via email_search) : destinataire et sujet sont alors facultatifs.""",
      {"to": {"type": "string", "description": "Destinataire(s), séparés par des virgules"},
       "subject": {"type": "string"}, "body": {"type": "string", "description": "Corps du message"},
       "cc": {"type": "string"}, "bcc": {"type": "string"},
       "attachments": {"type": "array", "items": {"type": "string"}, "description": "Chemins de fichiers de l'espace de fichiers"},
       "html": {"type": "boolean", "description": "Corps en HTML"},
       "draft": {"type": "boolean", "description": "Enregistrer en brouillon au lieu d'envoyer"},
       "reply_to_id": {"type": "string"}},
      ["body"], label="E-mail", icon="✉️", timeout=90)
async def email_send(ctx: ToolContext, body: str, to: str = "", subject: str = "", cc: str = "", bcc: str = "",
                     attachments: list[str] | None = None, html: bool = False, draft: bool = False, reply_to_id: str = "") -> ToolResult:
    backend = _backend(ctx)
    if not backend:
        return ToolResult(NO_MAIL, is_error=True)
    thread_id, in_reply_to = None, ""
    if reply_to_id:
        orig = await (g.gmail_read(ctx.user_id, reply_to_id) if backend == "gmail" else mail.read(ctx.user_id, reply_to_id))
        to = to or orig["from"]
        subject = subject or (orig["subject"] if orig["subject"].lower().startswith("re:") else f"Re: {orig['subject']}")
        in_reply_to = orig.get("message_id", "")
        thread_id = orig.get("thread_id")
    if not to:
        return ToolResult("Destinataire manquant (to).", is_error=True)
    msg = _build(ctx, to, subject or "(sans objet)", body, cc, bcc, attachments, html, in_reply_to)
    if backend == "gmail":
        await g.gmail_send(ctx.user_id, msg, draft=draft, thread_id=thread_id)
    else:
        await mail.send(ctx.user_id, msg, draft=draft)
    what = "Brouillon enregistré" if draft else "E-mail envoyé"
    return ToolResult(f"{what} à {to} — objet : « {subject} »" + (f" ({len(attachments)} pièce(s) jointe(s))" if attachments else ""))


@tool("email_search", """Cherche des e-mails dans la boîte de l'utilisateur. Sans requête : les plus récents.
Avec Gmail, la requête suit la syntaxe Gmail (from:, subject:, is:unread, newer_than:7d, has:attachment…).""",
      {"query": {"type": "string"}, "limit": {"type": "integer", "description": "défaut 15"},
       "unread_only": {"type": "boolean"}, "folder": {"type": "string", "description": "Dossier IMAP (défaut INBOX)"}},
      [], label="Recherche e-mail", icon="📬", timeout=90, untrusted=True, effects=False)
async def email_search(ctx: ToolContext, query: str = "", limit: int = 15, unread_only: bool = False, folder: str = "INBOX") -> ToolResult:
    backend = _backend(ctx)
    if not backend:
        return ToolResult(NO_MAIL, is_error=True)
    limit = max(1, min(limit, 50))
    if backend == "gmail":
        q = (query + (" is:unread" if unread_only else "")).strip() or "in:inbox"
        items = await g.gmail_search(ctx.user_id, q, limit)
    else:
        items = await mail.search(ctx.user_id, query, limit, unread_only, folder)
    if not items:
        return ToolResult("Aucun e-mail trouvé.")
    lines = [f"[{m['id']}] {'● ' if m.get('unread') else ''}{m['date'][:25]} — {m['from']}\n   {m['subject']}\n   {m['snippet'][:160]}"
             for m in items]
    return ToolResult(f"{len(items)} e-mail(s) :\n" + "\n".join(lines))


@tool("email_read", "Lit un e-mail complet (expéditeur, destinataires, corps, pièces jointes) à partir de son id.",
      {"id": {"type": "string"}, "folder": {"type": "string"}}, ["id"], label="Lecture e-mail", icon="📨", timeout=60, untrusted=True, effects=False)
async def email_read(ctx: ToolContext, id: str, folder: str = "INBOX") -> ToolResult:
    backend = _backend(ctx)
    if not backend:
        return ToolResult(NO_MAIL, is_error=True)
    m = await (g.gmail_read(ctx.user_id, id) if backend == "gmail" else mail.read(ctx.user_id, id, folder))
    atts = f"\nPièces jointes : {', '.join(m['attachments'])}" if m["attachments"] else ""
    return ToolResult(f"De : {m['from']}\nÀ : {m['to']}\nCc : {m.get('cc', '')}\nDate : {m['date']}\nObjet : {m['subject']}{atts}\n\n{m['body'][:15000]}")
