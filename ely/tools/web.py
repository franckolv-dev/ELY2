"""Recherche web, lecture de pages et météo."""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import time

import httpx

from ..config import settings
from . import ToolContext, ToolResult, tool

log = logging.getLogger("ely.web")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/140.0.0.0 Safari/537.36")
_cooldown: dict[str, float] = {}  # fournisseur -> timestamp de fin de pause (quota épuisé)


async def _searxng(q: str, n: int, category: str | None) -> list[dict]:
    params = {"q": q, "format": "json", "language": "fr"}
    if category:
        params["categories"] = f"general,{category}"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{settings.searxng_url}/search", params=params)
        r.raise_for_status()
        return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
                for x in r.json().get("results", [])[:n]]


async def _tavily(q: str, n: int, category: str | None) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://api.tavily.com/search", json={
            "api_key": settings.tavily_api_key, "query": q, "max_results": n,
            "topic": "news" if category == "news" else "general"})
        r.raise_for_status()
        return [{"title": x["title"], "url": x["url"], "snippet": x.get("content", "")} for x in r.json().get("results", [])]


async def _brave(q: str, n: int, category: str | None) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("https://api.search.brave.com/res/v1/web/search", params={"q": q, "count": n},
                        headers={"X-Subscription-Token": settings.brave_api_key, "Accept": "application/json"})
        r.raise_for_status()
        return [{"title": x["title"], "url": x["url"], "snippet": x.get("description", "")}
                for x in r.json().get("web", {}).get("results", [])]


def _key(name: str) -> str:
    return os.environ.get(name, "").strip()


async def _serper(q: str, n: int, category: str | None) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"https://google.serper.dev/{'news' if category == 'news' else 'search'}",
                         headers={"X-API-KEY": _key("SERPER_API_KEY")}, json={"q": q, "gl": "fr", "hl": "fr", "num": n})
        r.raise_for_status()
        data = r.json()
    out = []
    kg = data.get("knowledgeGraph") or {}
    if kg.get("description"):
        out.append({"title": kg.get("title", ""), "url": kg.get("website", ""), "snippet": kg["description"]})
    for x in data.get("news" if category == "news" else "organic", []):
        out.append({"title": x.get("title", ""), "url": x.get("link", ""), "snippet": x.get("snippet", ""), "date": x.get("date", "")})
    return out[:n]


async def _exa(q: str, n: int, category: str | None) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://api.exa.ai/search", headers={"x-api-key": _key("EXA_API_KEY")},
                         json={"query": q, "type": "auto", "numResults": max(1, min(n, 25)),
                               "contents": {"highlights": {"maxCharacters": 1000}}})
        r.raise_for_status()
        items = r.json().get("results") or []
    return [{"title": x.get("title") or x["url"], "url": x["url"],
             "snippet": " ".join(x.get("highlights") or []) or (x.get("text") or "")[:500]} for x in items if x.get("url")][:n]


async def _searchcans(q: str, n: int, category: str | None) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post("https://www.searchcans.com/api/v1/search", headers={"Authorization": f"Bearer {_key('SEARCHCANS_API_KEY')}"},
                         json={"t": "google", "s": q, "country": "fr", "language": "fr", "p": 1})
        r.raise_for_status()
        payload = r.json()
    if payload.get("code") not in (None, 0):  # erreurs annoncées dans le corps, en HTTP 200
        raise RuntimeError(str(payload.get("msg") or payload.get("code")))
    items = (payload.get("data") or {}).get("organic") or []
    return [{"title": x.get("title", ""), "url": x.get("link", ""), "snippet": x.get("snippet", "")} for x in items][:n]


async def _google(q: str, n: int, category: str | None) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("https://www.googleapis.com/customsearch/v1", params={
            "key": _key("GOOGLE_SEARCH_API_KEY"), "cx": _key("GOOGLE_SEARCH_CX"), "q": q, "num": min(n, 10), "gl": "fr", "hl": "fr"})
        r.raise_for_status()
        items = r.json().get("items") or []
    return [{"title": x.get("title", ""), "url": x.get("link", ""), "snippet": x.get("snippet", "")} for x in items][:n]


async def _ddgs(q: str, n: int, category: str | None) -> list[dict]:
    from ddgs import DDGS

    def run():
        with DDGS() as d:
            if category == "news":
                return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("body", ""),
                         "date": x.get("date", "")} for x in d.news(q, region="fr-fr", max_results=n)]
            return [{"title": x.get("title", ""), "url": x.get("href", ""), "snippet": x.get("body", "")}
                    for x in d.text(q, region="fr-fr", max_results=n)]

    return await asyncio.to_thread(run)


def _providers():
    out = []
    if settings.searxng_url:
        out.append(("searxng", _searxng))
    if _key("SERPER_API_KEY"):
        out.append(("serper", _serper))
    if _key("EXA_API_KEY"):
        out.append(("exa", _exa))
    if _key("SEARCHCANS_API_KEY"):
        out.append(("searchcans", _searchcans))
    if _key("GOOGLE_SEARCH_API_KEY") and _key("GOOGLE_SEARCH_CX"):
        out.append(("google", _google))
    if settings.tavily_api_key:
        out.append(("tavily", _tavily))
    if settings.brave_api_key:
        out.append(("brave", _brave))
    out.append(("ddgs", _ddgs))
    return out


@tool("web_search", "Recherche sur le web (actualités, faits, prix, horaires, adresses…). Renvoie titres, liens et extraits.",
      {"query": {"type": "string", "description": "Requête de recherche"},
       "category": {"type": "string", "enum": ["general", "news"], "description": "news pour l'actualité"},
       "max_results": {"type": "integer", "description": "Nombre de résultats (défaut 8)"}},
      ["query"], label="Recherche web", icon="🔎", timeout=60)
async def web_search(ctx: ToolContext, query: str, category: str = "general", max_results: int = 8) -> ToolResult:
    cat = None if category == "general" else category
    errors = []
    for name, fn in _providers():
        if _cooldown.get(name, 0) > time.time():
            continue
        try:
            results = await fn(query, max(1, min(max_results, 20)), cat)
            if results:
                lines = [f"{i}. {r['title']}\n   {r['url']}\n   {r.get('date', '')} {r['snippet'][:300]}".rstrip()
                         for i, r in enumerate(results, 1)]
                return ToolResult(f"Résultats ({name}) pour « {query} » :\n" + "\n".join(lines))
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (402, 429, 432):
                _cooldown[name] = time.time() + 1800
            errors.append(f"{name}: {e}")
        except Exception as e:
            errors.append(f"{name}: {e}")
    return ToolResult(f"Aucun résultat pour « {query} ». {' | '.join(errors)[:500]} "
                      "Reformule, ou ouvre directement un site pertinent avec browser.", is_error=bool(errors))


def _extract(html: str, url: str) -> str:
    import trafilatura

    text = trafilatura.extract(html, url=url, include_links=False, include_tables=True, favor_recall=True,
                               output_format="markdown") or ""
    if len(text) < 200:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for s in soup(["script", "style", "noscript", "svg"]):
            s.decompose()
        text = re.sub(r"\n\s*\n+", "\n\n", soup.get_text("\n")).strip()
    return text


async def fetch_text(url: str, max_chars: int = 20000) -> tuple[str, str]:
    """Renvoie (titre, texte) d'une URL ; bascule sur le navigateur si la page exige JavaScript."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"}) as c:
        r = await c.get(url)
        ctype = r.headers.get("content-type", "")
        if "pdf" in ctype or url.lower().endswith(".pdf"):
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(r.content))
            text = "\n\n".join((p.extract_text() or "") for p in reader.pages)
            return url.rsplit("/", 1)[-1], text[:max_chars]
        r.raise_for_status()
        html = r.text
    title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    title = re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else url
    text = await asyncio.to_thread(_extract, html, url)
    if len(text) < 300:
        from ..browser import render_page

        try:
            title, html = await render_page(url)
            text = await asyncio.to_thread(_extract, html, url)
        except Exception as e:
            log.info("rendu navigateur impossible pour %s : %s", url, e)
    return title, text[:max_chars]


@tool("web_fetch", "Lit le contenu d'une page web ou d'un PDF en ligne (texte principal, en markdown).",
      {"url": {"type": "string"}, "max_chars": {"type": "integer", "description": "Longueur max (défaut 15000)"}},
      ["url"], label="Lecture de page", icon="📄", timeout=90)
async def web_fetch(ctx: ToolContext, url: str, max_chars: int = 15000) -> ToolResult:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    title, text = await fetch_text(url, max_chars)
    if not text.strip():
        return ToolResult(f"Page vide ou illisible : {url}. Essaie browser action=open.", is_error=True)
    return ToolResult(f"# {title}\nSource : {url}\n\n{text}")


WMO = {0: "ciel dégagé", 1: "plutôt dégagé", 2: "partiellement nuageux", 3: "couvert", 45: "brouillard", 48: "brouillard givrant",
       51: "bruine légère", 53: "bruine", 55: "bruine forte", 61: "pluie faible", 63: "pluie", 65: "forte pluie",
       66: "pluie verglaçante", 67: "forte pluie verglaçante", 71: "neige faible", 73: "neige", 75: "forte neige",
       77: "grains de neige", 80: "averses", 81: "averses", 82: "violentes averses", 85: "averses de neige",
       86: "fortes averses de neige", 95: "orage", 96: "orage avec grêle", 99: "orage violent avec grêle"}


@tool("weather", "Météo actuelle et prévisions (jusqu'à 14 jours) pour un lieu.",
      {"location": {"type": "string", "description": "Ville ou lieu"}, "days": {"type": "integer", "description": "Jours de prévision (défaut 3)"}},
      ["location"], label="Météo", icon="🌤️", timeout=30)
async def weather(ctx: ToolContext, location: str, days: int = 3) -> ToolResult:
    async with httpx.AsyncClient(timeout=15) as c:
        g = await c.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": location, "count": 1, "language": "fr"})
        res = (g.json().get("results") or [])
        if not res:
            return ToolResult(f"Lieu introuvable : {location}", is_error=True)
        p = res[0]
        f = await c.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": p["latitude"], "longitude": p["longitude"], "timezone": "auto", "forecast_days": max(1, min(days, 14)),
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m,precipitation",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max"})
        d = f.json()
    cur = d["current"]
    lines = [f"Météo à {p['name']} ({p.get('admin1', '')}, {p.get('country', '')}) :",
             f"Maintenant : {cur['temperature_2m']}°C (ressenti {cur['apparent_temperature']}°C), {WMO.get(cur['weather_code'], '?')}, "
             f"vent {cur['wind_speed_10m']} km/h, humidité {cur['relative_humidity_2m']}%"]
    dd = d["daily"]
    for i, day in enumerate(dd["time"]):
        lines.append(f"{day} : {WMO.get(dd['weather_code'][i], '?')}, {dd['temperature_2m_min'][i]}–{dd['temperature_2m_max'][i]}°C, "
                     f"pluie {dd['precipitation_sum'][i]} mm ({dd['precipitation_probability_max'][i]}%), vent max {dd['wind_speed_10m_max'][i]} km/h")
    return ToolResult("\n".join(lines))
