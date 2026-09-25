"""Délégation : plusieurs sous-agents travaillent en parallèle sur des sous-tâches indépendantes."""
from __future__ import annotations

import asyncio

from . import ToolContext, ToolResult, tool


@tool("delegate", """Lance jusqu'à 6 sous-agents EN PARALLÈLE, chacun avec tes outils, sur des sous-tâches indépendantes
(ex. comparer 5 fournisseurs, rechercher plusieurs sujets, vérifier plusieurs sites). Chaque sous-tâche doit être
autonome et précise (inclure le contexte nécessaire). Tu reçois leurs rapports.""",
      {"tasks": {"type": "array", "items": {"type": "string"}}, "context": {"type": "string", "description": "Contexte commun"}},
      ["tasks"], label="Sous-agents", icon="🧩", timeout=3600, subagent=False)
async def delegate(ctx: ToolContext, tasks: list[str], context: str = "") -> ToolResult:
    from ..agent.loop import run_subagent

    tasks = [t for t in tasks if t.strip()][:6]
    results = await asyncio.gather(*(run_subagent(ctx, t, context, i + 1) for i, t in enumerate(tasks)), return_exceptions=True)
    parts = []
    for i, (t, r) in enumerate(zip(tasks, results), 1):
        body = f"ÉCHEC : {r}" if isinstance(r, Exception) else r
        parts.append(f"## Sous-tâche {i} : {t}\n{body}")
    return ToolResult("\n\n".join(parts))
