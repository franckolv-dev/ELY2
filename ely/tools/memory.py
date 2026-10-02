"""Outils de mémoire : retenir, retrouver, oublier, apprendre une procédure."""
from __future__ import annotations

import time

from ..memory import store
from . import ToolContext, ToolResult, tool


@tool("remember", """Mémorise durablement une information utile sur l'utilisateur ou son entourage
(préférence, proche, adresse, habitude, compte utilisé, médecin, décision…). Une phrase autonome et précise.""",
      {"fact": {"type": "string"}, "category": {"type": "string", "enum": ["identité", "proche", "préférence", "habitude", "travail",
                                                                          "santé", "lieu", "compte", "projet", "fait"]}},
      ["fact"], label="Mémorisation", icon="🧠", timeout=60)
async def remember(ctx: ToolContext, fact: str, category: str = "fait") -> ToolResult:
    mid, new = await store.add_memory(ctx.user_id, fact, category, source="agent")
    return ToolResult(f"{'Mémorisé' if new else 'Souvenir mis à jour'} (#{mid}) : {fact}")


@tool("recall", """Cherche dans la mémoire d'Ely : souvenirs sur l'utilisateur ET conversations passées.
À utiliser dès qu'une information personnelle manque (adresse, médecin, proches, préférences, ce qui a été dit/fait avant).""",
      {"query": {"type": "string"}}, ["query"], label="Souvenirs", icon="💭", timeout=60, effects=False)
async def recall(ctx: ToolContext, query: str) -> ToolResult:
    mems = await store.search_memories(ctx.user_id, query, k=10)
    hist = store.search_history(ctx.user_id, query, limit=8, exclude_conversation=ctx.conversation_id)
    parts = []
    if mems:
        parts.append("## Souvenirs\n" + "\n".join(f"#{m['id']} [{m['category']}] {m['content']}" for m in mems))
    if hist:
        parts.append("## Conversations passées\n" + "\n".join(
            f"- {time.strftime('%d/%m/%Y', time.localtime(h['created_at']))} « {h['title']} » ({h['role']}) : {h['extract']}"
            for h in hist))
    return ToolResult("\n\n".join(parts) if parts else f"Rien trouvé en mémoire pour « {query} ».")


@tool("forget", "Supprime un souvenir erroné ou obsolète (id obtenu via recall).",
      {"memory_id": {"type": "integer"}}, ["memory_id"], label="Oubli", icon="🧽", timeout=20)
async def forget(ctx: ToolContext, memory_id: int) -> ToolResult:
    ok = store.delete_memory(ctx.user_id, memory_id)
    return ToolResult(f"Souvenir #{memory_id} supprimé." if ok else f"Souvenir #{memory_id} introuvable.", is_error=not ok)


@tool("skill_save", """Enregistre (ou améliore) une compétence : une procédure réutilisable qui a fonctionné,
pour réussir plus vite la prochaine fois (ex. « Prendre RDV sur Doctolib », « Publier sur LinkedIn »).
Contenu : étapes concrètes, URLs, pièges rencontrés et solutions. Décris comment RÉUSSIR, jamais d'interdiction.""",
      {"name": {"type": "string"}, "description": {"type": "string", "description": "Quand l'utiliser (une phrase)"},
       "content": {"type": "string", "description": "Procédure en markdown"},
       "shared": {"type": "boolean", "description": "Utile à tous les utilisateurs (pas de données personnelles)"}},
      ["name", "description", "content"], label="Nouvelle compétence", icon="🎓", timeout=20)
async def skill_save(ctx: ToolContext, name: str, description: str, content: str, shared: bool = False) -> ToolResult:
    shared = shared and ctx.is_admin  # une compétence partagée entre dans le contexte de toute la famille
    sid, created = store.save_skill(None if shared else ctx.user_id, name, description, content)
    return ToolResult(f"Compétence « {name} » {'créée' if created else 'mise à jour'} (#{sid})"
                      + (" pour tous." if shared else " pour cette personne."))
