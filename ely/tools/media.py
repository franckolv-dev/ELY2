"""Génération d'images (OpenAI ou Gemini selon les clés disponibles)."""
from __future__ import annotations

import base64
import os
import re
import time

import httpx

from . import ToolContext, ToolResult, tool


def _keys() -> tuple[str, str]:
    return os.environ.get("OPENAI_API_KEY", ""), os.environ.get("GEMINI_API_KEY", "")


@tool("image_generate", "Crée une image à partir d'une description (illustration de post, visuel, logo…). L'image est enregistrée dans les fichiers.",
      {"prompt": {"type": "string", "description": "Description détaillée de l'image"},
       "size": {"type": "string", "enum": ["1024x1024", "1536x1024", "1024x1536"]}},
      ["prompt"], label="Création d'image", icon="🎨", timeout=240, available=lambda ctx: any(_keys()))
async def image_generate(ctx: ToolContext, prompt: str, size: str = "1024x1024") -> ToolResult:
    openai_key, gemini_key = _keys()
    data = None
    async with httpx.AsyncClient(timeout=220) as c:
        if openai_key:
            r = await c.post("https://api.openai.com/v1/images/generations", headers={"Authorization": f"Bearer {openai_key}"},
                             json={"model": os.environ.get("ELY_IMAGE_MODEL", "gpt-image-1"), "prompt": prompt, "size": size, "n": 1})
            if r.status_code < 400:
                data = base64.b64decode(r.json()["data"][0]["b64_json"])
            elif not gemini_key:
                return ToolResult(f"Échec OpenAI : {r.text[:300]}", is_error=True)
        if data is None and gemini_key:
            model = os.environ.get("ELY_GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
            r = await c.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                             params={"key": gemini_key}, json={"contents": [{"parts": [{"text": prompt}]}]})
            if r.status_code >= 400:
                return ToolResult(f"Échec Gemini : {r.text[:300]}", is_error=True)
            for part in r.json()["candidates"][0]["content"]["parts"]:
                if part.get("inlineData"):
                    data = base64.b64decode(part["inlineData"]["data"])
                    break
    if not data:
        return ToolResult("Aucune image produite.", is_error=True)
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower())[:40].strip("-") or "image"
    path = ctx.workspace / "Images" / f"{slug}-{int(time.time())}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return ToolResult(f"Image créée : {ctx.rel(path)}", files=[ctx.rel(path)])
