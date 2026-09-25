"""Publication sur les réseaux sociaux par API (LinkedIn, pages Facebook).

Quand l'API n'est pas connectée, l'agent publie avec le navigateur et la session
de l'utilisateur : ça marche partout, sans créer d'application développeur.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from ..config import settings
from . import get, put, sign_state

FB_VERSION = "v23.0"


# ---------------------------------------------------------------------- LinkedIn
def linkedin_enabled() -> bool:
    return bool(settings.linkedin_client_id and settings.linkedin_client_secret)


def linkedin_redirect(base: str) -> str:
    return f"{settings.external_url(base)}/api/integrations/linkedin/callback"


def linkedin_auth_url(user_id: int, base: str) -> str:
    return "https://www.linkedin.com/oauth/v2/authorization?" + urlencode({
        "response_type": "code", "client_id": settings.linkedin_client_id, "redirect_uri": linkedin_redirect(base),
        "scope": "openid profile email w_member_social", "state": sign_state(user_id, "linkedin")})


async def linkedin_exchange(user_id: int, code: str, base: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://www.linkedin.com/oauth/v2/accessToken", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": linkedin_redirect(base),
            "client_id": settings.linkedin_client_id, "client_secret": settings.linkedin_client_secret})
        r.raise_for_status()
        tok = r.json()
        me = (await c.get("https://api.linkedin.com/v2/userinfo", headers={"Authorization": f"Bearer {tok['access_token']}"})).json()
    data = {"access_token": tok["access_token"], "expires_at": time.time() + int(tok.get("expires_in", 5184000)),
            "person": f"urn:li:person:{me['sub']}", "name": me.get("name", "")}
    put(user_id, "linkedin", data)
    return data


def _li_version() -> str:
    d = dt.date.today().replace(day=1) - dt.timedelta(days=45)  # version publiée il y a ~2 mois
    return d.strftime("%Y%m")


async def linkedin_post(user_id: int, text: str, image: Path | None = None, visibility: str = "PUBLIC") -> str:
    data = get(user_id, "linkedin")
    if not data or data.get("expires_at", 0) < time.time():
        raise RuntimeError("LinkedIn non connecté ou jeton expiré")
    h = {"Authorization": f"Bearer {data['access_token']}", "LinkedIn-Version": _li_version(),
         "X-Restli-Protocol-Version": "2.0.0", "Content-Type": "application/json"}
    body: dict = {"author": data["person"], "commentary": text, "visibility": visibility,
                  "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
                  "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
    async with httpx.AsyncClient(timeout=60) as c:
        if image:
            init = await c.post("https://api.linkedin.com/rest/images?action=initializeUpload", headers=h,
                                json={"initializeUploadRequest": {"owner": data["person"]}})
            init.raise_for_status()
            v = init.json()["value"]
            up = await c.put(v["uploadUrl"], content=image.read_bytes(), headers={"Authorization": h["Authorization"]})
            up.raise_for_status()
            body["content"] = {"media": {"id": v["image"]}}
        r = await c.post("https://api.linkedin.com/rest/posts", headers=h, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"LinkedIn {r.status_code} : {r.text[:300]}")
        return r.headers.get("x-restli-id", "publié")


# ---------------------------------------------------------------------- Facebook (pages)
async def facebook_post(user_id: int, text: str, image: Path | None = None, link: str | None = None) -> str:
    data = get(user_id, "facebook")
    if not data.get("page_id") or not data.get("page_token"):
        raise RuntimeError("Page Facebook non configurée")
    base = f"https://graph.facebook.com/{FB_VERSION}/{data['page_id']}"
    async with httpx.AsyncClient(timeout=60) as c:
        if image:
            r = await c.post(f"{base}/photos", data={"caption": text, "access_token": data["page_token"]},
                             files={"source": (image.name, image.read_bytes())})
        else:
            payload = {"message": text, "access_token": data["page_token"]}
            if link:
                payload["link"] = link
            r = await c.post(f"{base}/feed", data=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"Facebook {r.status_code} : {r.text[:300]}")
        return r.json().get("post_id") or r.json().get("id", "publié")
