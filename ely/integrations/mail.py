"""E-mail universel IMAP/SMTP (Gmail, Outlook, iCloud, Free, Orange, OVH… avec mot de passe d'application)."""
from __future__ import annotations

import asyncio
import email
import email.utils
import imaplib
import re
import smtplib
import ssl
import time
from email.header import decode_header, make_header
from email.message import EmailMessage

from . import get

PRESETS = {
    "gmail.com": ("imap.gmail.com", "smtp.gmail.com", 465),
    "googlemail.com": ("imap.gmail.com", "smtp.gmail.com", 465),
    "outlook.com": ("outlook.office365.com", "smtp.office365.com", 587),
    "hotmail.com": ("outlook.office365.com", "smtp.office365.com", 587),
    "hotmail.fr": ("outlook.office365.com", "smtp.office365.com", 587),
    "live.fr": ("outlook.office365.com", "smtp.office365.com", 587),
    "live.com": ("outlook.office365.com", "smtp.office365.com", 587),
    "icloud.com": ("imap.mail.me.com", "smtp.mail.me.com", 587),
    "me.com": ("imap.mail.me.com", "smtp.mail.me.com", 587),
    "mac.com": ("imap.mail.me.com", "smtp.mail.me.com", 587),
    "free.fr": ("imap.free.fr", "smtp.free.fr", 465),
    "orange.fr": ("imap.orange.fr", "smtp.orange.fr", 465),
    "wanadoo.fr": ("imap.orange.fr", "smtp.orange.fr", 465),
    "sfr.fr": ("imap.sfr.fr", "smtp.sfr.fr", 465),
    "laposte.net": ("imap.laposte.net", "smtp.laposte.net", 465),
    "yahoo.fr": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com", 465),
    "yahoo.com": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com", 465),
    "gmx.fr": ("imap.gmx.com", "mail.gmx.com", 587),
    "proton.me": ("127.0.0.1", "127.0.0.1", 1025),
}


def preset_for(address: str) -> dict:
    domain = address.split("@")[-1].lower()
    imap, smtp, port = PRESETS.get(domain, (f"imap.{domain}", f"smtp.{domain}", 465))
    return {"imap_host": imap, "imap_port": 993, "smtp_host": smtp, "smtp_port": port}


def config(user_id: int) -> dict:
    c = get(user_id, "email")
    if not c:
        raise RuntimeError("Aucune boîte mail configurée (Réglages → Connexions → E-mail, ou connexion Google)")
    return c


def _dec(v: str | None) -> str:
    if not v:
        return ""
    try:
        return str(make_header(decode_header(v)))
    except Exception:
        return v


def _imap(c: dict) -> imaplib.IMAP4_SSL:
    m = imaplib.IMAP4_SSL(c["imap_host"], int(c.get("imap_port", 993)), timeout=30)
    m.login(c.get("username") or c["address"], c["password"])
    return m


def _folder(m: imaplib.IMAP4_SSL, flag: str, fallbacks: list[str]) -> str:
    typ, data = m.list()
    for raw in data or []:
        line = raw.decode(errors="replace")
        if flag in line:
            name = line.split(' "/" ')[-1].split(' "." ')[-1].strip()
            return name if name.startswith('"') else f'"{name}"'
    return f'"{fallbacks[0]}"'


def text_of(msg: email.message.Message) -> str:
    if msg.is_multipart():
        html = ""
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.get("Content-Disposition", "").startswith("attachment"):
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            txt = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            if ctype == "text/plain":
                return txt
            if ctype == "text/html" and not html:
                html = txt
        if html:
            from bs4 import BeautifulSoup

            return BeautifulSoup(html, "html.parser").get_text("\n")
        return ""
    payload = msg.get_payload(decode=True) or b""
    txt = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    if msg.get_content_type() == "text/html":
        from bs4 import BeautifulSoup

        return BeautifulSoup(txt, "html.parser").get_text("\n")
    return txt


def _search_sync(c: dict, query: str, limit: int, unread: bool, folder: str) -> list[dict]:
    m = _imap(c)
    try:
        m.select(folder, readonly=True)
        crit: list[str] = ["UNSEEN"] if unread else []
        q = query.replace('"', "").strip()
        if q and not q.isascii():
            m.literal = q.encode()  # recherche UTF-8 via littéral IMAP
            typ, data = m.uid("SEARCH", "CHARSET", "UTF-8", *crit, "TEXT")
        else:
            if q:
                crit.append(f'OR OR FROM "{q}" SUBJECT "{q}" BODY "{q}"')
            typ, data = m.uid("SEARCH", *(crit or ["ALL"]))
        uids = (data[0] or b"").split()[-limit:][::-1]
        out = []
        for uid in uids:
            typ, d = m.uid("FETCH", uid, "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)] BODY.PEEK[TEXT]<0.300>)")
            headers, preview, flags = b"", b"", ""
            for part in d:
                if isinstance(part, tuple):
                    if b"HEADER" in part[0]:
                        headers = part[1]
                        flags = part[0].decode(errors="replace")
                    else:
                        preview = part[1]
            h = email.message_from_bytes(headers)
            out.append({"id": uid.decode(), "from": _dec(h["From"]), "to": _dec(h["To"]), "subject": _dec(h["Subject"]),
                        "date": h["Date"] or "", "unread": "\\Seen" not in flags,
                        "snippet": re.sub(r"\s+", " ", preview.decode(errors="replace"))[:200]})
        return out
    finally:
        m.logout()


def _read_sync(c: dict, uid: str, folder: str) -> dict:
    m = _imap(c)
    try:
        m.select(folder)
        typ, d = m.uid("FETCH", uid, "(RFC822)")
        raw = next((p[1] for p in d if isinstance(p, tuple)), None)
        if not raw:
            raise RuntimeError(f"message {uid} introuvable")
        msg = email.message_from_bytes(raw)
        atts = [_dec(p.get_filename()) for p in msg.walk() if p.get_filename()]
        return {"id": uid, "from": _dec(msg["From"]), "to": _dec(msg["To"]), "cc": _dec(msg["Cc"]),
                "subject": _dec(msg["Subject"]), "date": msg["Date"], "message_id": msg["Message-ID"] or "",
                "body": text_of(msg).strip(), "attachments": atts}
    finally:
        m.logout()


def _send_sync(c: dict, msg: EmailMessage) -> None:
    host, port = c["smtp_host"], int(c.get("smtp_port", 465))
    user = c.get("username") or c["address"]
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as s:
            s.login(user, c["password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(user, c["password"])
            s.send_message(msg)
    if "gmail" not in host:  # Gmail classe tout seul dans « Envoyés »
        try:
            m = _imap(c)
            m.append(_folder(m, "\\Sent", ["Sent"]), "\\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            m.logout()
        except Exception:
            pass


def _draft_sync(c: dict, msg: EmailMessage) -> None:
    m = _imap(c)
    try:
        m.append(_folder(m, "\\Drafts", ["Drafts"]), "\\Draft", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    finally:
        m.logout()


async def search(user_id: int, query: str, limit: int, unread: bool, folder: str = "INBOX") -> list[dict]:
    return await asyncio.to_thread(_search_sync, config(user_id), query, limit, unread, folder)


async def read(user_id: int, uid: str, folder: str = "INBOX") -> dict:
    return await asyncio.to_thread(_read_sync, config(user_id), uid, folder)


async def send(user_id: int, msg: EmailMessage, draft: bool = False) -> None:
    c = config(user_id)
    await asyncio.to_thread(_draft_sync if draft else _send_sync, c, msg)


def test_login(c: dict) -> None:
    m = _imap(c)
    m.logout()


def from_header(c: dict, fallback_name: str = "") -> str:
    return email.utils.formataddr((c.get("from_name") or fallback_name, c["address"]))
