"""Google : OAuth + Gmail, Agenda, Contacts (API REST directes, sans SDK)."""
from __future__ import annotations

import base64
import time
from email.message import EmailMessage
from urllib.parse import urlencode

import httpx

from ..config import settings
from . import get, put, sign_state

SCOPES = [
    "openid", "email", "profile",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/contacts",
]


def enabled() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def redirect_uri(base: str) -> str:
    return f"{settings.external_url(base)}/api/integrations/google/callback"


def auth_url(user_id: int, base: str) -> str:
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": settings.google_client_id, "redirect_uri": redirect_uri(base), "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent", "state": sign_state(user_id, "google"),
    })


async def exchange_code(user_id: int, code: str, base: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
            "redirect_uri": redirect_uri(base), "grant_type": "authorization_code"})
        r.raise_for_status()
        tok = r.json()
        info = (await c.get("https://openidconnect.googleapis.com/v1/userinfo",
                            headers={"Authorization": f"Bearer {tok['access_token']}"})).json()
    data = {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token", ""),
            "expires_at": time.time() + int(tok.get("expires_in", 3600)) - 60, "email": info.get("email", ""),
            "name": info.get("name", "")}
    put(user_id, "google", data)
    return data


async def _token(user_id: int) -> str:
    data = get(user_id, "google")
    if not data:
        raise RuntimeError("Compte Google non connecté (Réglages → Connexions)")
    if data["expires_at"] > time.time():
        return data["access_token"]
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://oauth2.googleapis.com/token", data={
            "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
            "refresh_token": data["refresh_token"], "grant_type": "refresh_token"})
        if r.status_code >= 400:
            raise RuntimeError(f"Reconnexion Google nécessaire : {r.text[:200]}")
        tok = r.json()
    data.update(access_token=tok["access_token"], expires_at=time.time() + int(tok.get("expires_in", 3600)) - 60)
    put(user_id, "google", data)
    return data["access_token"]


async def api(user_id: int, method: str, url: str, **kw) -> dict:
    token = await _token(user_id)
    async with httpx.AsyncClient(timeout=40) as c:
        r = await c.request(method, url, headers={"Authorization": f"Bearer {token}"}, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"Google API {r.status_code} : {r.text[:400]}")
    return r.json() if r.content else {}


# ---------------------------------------------------------------------- Gmail
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"


def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _body(part: dict) -> tuple[str, str]:
    """Renvoie (texte, html) d'une partie MIME Gmail, récursivement."""
    mime = part.get("mimeType", "")
    data = part.get("body", {}).get("data")
    if data and mime == "text/plain":
        return base64.urlsafe_b64decode(data + "==").decode(errors="replace"), ""
    if data and mime == "text/html":
        return "", base64.urlsafe_b64decode(data + "==").decode(errors="replace")
    text, html = "", ""
    for p in part.get("parts", []) or []:
        t, h = _body(p)
        text, html = text or t, html or h
    return text, html


async def gmail_search(user_id: int, query: str, limit: int) -> list[dict]:
    import asyncio

    res = await api(user_id, "GET", f"{GMAIL}/messages", params={"q": query, "maxResults": limit})
    ids = [m["id"] for m in res.get("messages", [])]

    async def meta(mid):
        m = await api(user_id, "GET", f"{GMAIL}/messages/{mid}", params={
            "format": "metadata", "metadataHeaders": ["From", "To", "Subject", "Date"]})
        return {"id": mid, "from": _header(m, "From"), "to": _header(m, "To"), "subject": _header(m, "Subject"),
                "date": _header(m, "Date"), "snippet": m.get("snippet", ""), "unread": "UNREAD" in m.get("labelIds", [])}

    return list(await asyncio.gather(*(meta(i) for i in ids)))


async def gmail_read(user_id: int, mid: str) -> dict:
    m = await api(user_id, "GET", f"{GMAIL}/messages/{mid}", params={"format": "full"})
    text, html = _body(m.get("payload", {}))
    if not text and html:
        from bs4 import BeautifulSoup

        text = BeautifulSoup(html, "html.parser").get_text("\n")
    atts = []

    def walk(p):
        if p.get("filename"):
            atts.append(p["filename"])
        for q in p.get("parts", []) or []:
            walk(q)

    walk(m.get("payload", {}))
    return {"id": mid, "thread_id": m.get("threadId"), "from": _header(m, "From"), "to": _header(m, "To"),
            "cc": _header(m, "Cc"), "subject": _header(m, "Subject"), "date": _header(m, "Date"),
            "message_id": _header(m, "Message-ID"), "body": text.strip(), "attachments": atts}


async def gmail_send(user_id: int, msg: EmailMessage, draft: bool = False, thread_id: str | None = None) -> dict:
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    body: dict = {"raw": raw}
    if thread_id:
        body["threadId"] = thread_id
    if draft:
        return await api(user_id, "POST", f"{GMAIL}/drafts", json={"message": body})
    return await api(user_id, "POST", f"{GMAIL}/messages/send", json=body)


# ---------------------------------------------------------------------- Agenda
CAL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


def _when(v: str, all_day: bool, tz: str) -> dict:
    return {"date": v[:10]} if all_day else {"dateTime": v if len(v) > 16 else v + ":00", "timeZone": tz}


async def cal_list(user_id: int, start: str, end: str, query: str | None) -> list[dict]:
    params = {"timeMin": start, "timeMax": end, "singleEvents": "true", "orderBy": "startTime", "maxResults": 100}
    if query:
        params["q"] = query
    res = await api(user_id, "GET", CAL, params=params)
    out = []
    for e in res.get("items", []):
        out.append({"id": e["id"], "title": e.get("summary", "(sans titre)"),
                    "start": e["start"].get("dateTime") or e["start"].get("date"),
                    "end": e["end"].get("dateTime") or e["end"].get("date"), "location": e.get("location", ""),
                    "description": (e.get("description") or "")[:300],
                    "attendees": [a.get("email") for a in e.get("attendees", [])]})
    return out


async def cal_add(user_id: int, ev: dict, tz: str) -> dict:
    body = {"summary": ev["title"], "start": _when(ev["start"], ev.get("all_day", False), tz),
            "end": _when(ev["end"], ev.get("all_day", False), tz)}
    if ev.get("location"):
        body["location"] = ev["location"]
    if ev.get("description"):
        body["description"] = ev["description"]
    if ev.get("attendees"):
        body["attendees"] = [{"email": a} for a in ev["attendees"]]
    if ev.get("reminder_minutes") is not None:
        body["reminders"] = {"useDefault": False, "overrides": [{"method": "popup", "minutes": int(ev["reminder_minutes"])}]}
    return await api(user_id, "POST", CAL, json=body, params={"sendUpdates": "all" if ev.get("attendees") else "none"})


async def cal_update(user_id: int, eid: str, fields: dict, tz: str) -> dict:
    body: dict = {}
    if "title" in fields:
        body["summary"] = fields["title"]
    for k in ("location", "description"):
        if k in fields:
            body[k] = fields[k]
    if "start" in fields:
        body["start"] = _when(fields["start"], fields.get("all_day", False), tz)
    if "end" in fields:
        body["end"] = _when(fields["end"], fields.get("all_day", False), tz)
    return await api(user_id, "PATCH", f"{CAL}/{eid}", json=body)


async def cal_delete(user_id: int, eid: str) -> None:
    await api(user_id, "DELETE", f"{CAL}/{eid}")


# ---------------------------------------------------------------------- Contacts
PEOPLE = "https://people.googleapis.com/v1"
FIELDS = "names,emailAddresses,phoneNumbers,organizations,addresses,birthdays,biographies"


def _person(p: dict) -> dict:
    first = lambda k, f: (p.get(k) or [{}])[0].get(f, "")  # noqa: E731
    b = (p.get("birthdays") or [{}])[0].get("date", {})
    return {"id": p["resourceName"], "name": first("names", "displayName"), "email": first("emailAddresses", "value"),
            "phone": first("phoneNumbers", "value"), "company": first("organizations", "name"),
            "address": first("addresses", "formattedValue"),
            "birthday": f"{b.get('year', '????')}-{b.get('month', 0):02d}-{b.get('day', 0):02d}" if b else "",
            "notes": first("biographies", "value")}


async def contacts_search(user_id: int, query: str) -> list[dict]:
    await api(user_id, "GET", f"{PEOPLE}/people:searchContacts", params={"query": "", "readMask": FIELDS})  # préchauffage
    res = await api(user_id, "GET", f"{PEOPLE}/people:searchContacts", params={"query": query, "readMask": FIELDS, "pageSize": 20})
    return [_person(r["person"]) for r in res.get("results", [])]


def _person_body(c: dict) -> dict:
    body: dict = {}
    if c.get("name"):
        parts = c["name"].split(" ", 1)
        body["names"] = [{"givenName": parts[0], "familyName": parts[1] if len(parts) > 1 else ""}]
    if c.get("email"):
        body["emailAddresses"] = [{"value": c["email"]}]
    if c.get("phone"):
        body["phoneNumbers"] = [{"value": c["phone"]}]
    if c.get("company"):
        body["organizations"] = [{"name": c["company"]}]
    if c.get("address"):
        body["addresses"] = [{"formattedValue": c["address"]}]
    if c.get("notes"):
        body["biographies"] = [{"value": c["notes"]}]
    if c.get("birthday") and len(c["birthday"]) >= 10:
        y, m, d = c["birthday"][:10].split("-")
        date = {"month": int(m), "day": int(d)}
        if y.isdigit():
            date["year"] = int(y)
        body["birthdays"] = [{"date": date}]
    return body


async def contacts_create(user_id: int, c: dict) -> dict:
    return _person(await api(user_id, "POST", f"{PEOPLE}/people:createContact", json=_person_body(c),
                             params={"personFields": FIELDS}))


async def contacts_update(user_id: int, resource: str, c: dict) -> dict:
    cur = await api(user_id, "GET", f"{PEOPLE}/{resource}", params={"personFields": FIELDS})
    body = _person_body(c)
    body["etag"] = cur["etag"]
    fields = ",".join(k for k in body if k != "etag")
    return _person(await api(user_id, "PATCH", f"{PEOPLE}/{resource}:updateContact",
                             params={"updatePersonFields": fields, "personFields": FIELDS}, json=body))
