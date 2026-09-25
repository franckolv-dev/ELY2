"""Publication sur les réseaux sociaux."""
from __future__ import annotations

from ..integrations import connected, get
from ..integrations import social as s
from . import ToolContext, ToolResult, tool

BROWSER_HINT = {
    "linkedin": "https://www.linkedin.com/feed/ → bouton « Commencer un post »",
    "facebook": "https://www.facebook.com/ → zone « Que voulez-vous dire ? »",
    "instagram": "https://www.instagram.com/ → « Créer » (image obligatoire, via upload)",
    "x": "https://x.com/compose/post",
    "threads": "https://www.threads.net/",
    "bluesky": "https://bsky.app/",
}


@tool("social_post", """Publie un message sur un réseau social (linkedin, facebook, instagram, x, threads, bluesky).
Utilise l'API si le compte est connecté ; sinon la réponse t'indique comment publier avec l'outil browser
(la session de l'utilisateur y est persistante). Rédige un texte adapté au réseau (ton, longueur, hashtags).""",
      {"platform": {"type": "string", "enum": list(BROWSER_HINT)}, "text": {"type": "string"},
       "image": {"type": "string", "description": "Chemin d'une image de l'espace de fichiers (facultatif)"},
       "link": {"type": "string"}},
      ["platform", "text"], label="Publication", icon="📣", timeout=120)
async def social_post(ctx: ToolContext, platform: str, text: str, image: str = "", link: str = "") -> ToolResult:
    img = ctx.resolve_path(image) if image else None
    if img and not img.exists():
        return ToolResult(f"Image introuvable : {image}", is_error=True)
    body = text + (f"\n\n{link}" if link and platform == "linkedin" else "")
    try:
        if platform == "linkedin" and connected(ctx.user_id, "linkedin"):
            pid = await s.linkedin_post(ctx.user_id, body, img)
            return ToolResult(f"Publié sur LinkedIn (id {pid}).")
        if platform == "facebook" and get(ctx.user_id, "facebook").get("page_token"):
            pid = await s.facebook_post(ctx.user_id, text, img, link or None)
            return ToolResult(f"Publié sur la page Facebook (id {pid}).")
    except Exception as e:
        return ToolResult(f"Échec de la publication par API ({e}). Publie avec l'outil browser : {BROWSER_HINT[platform]}", is_error=True)
    return ToolResult(
        f"Pas d'API connectée pour {platform} : publie avec l'outil browser. Ouvre {BROWSER_HINT[platform]}, "
        "vérifie que l'utilisateur est connecté (sinon identifiants via l'outil credentials, ou ask_user), colle le texte, "
        f"{'joins l image avec action=upload, ' if img else ''}publie, puis vérifie que la publication apparaît. "
        f"Texte à publier :\n---\n{body}\n---")
