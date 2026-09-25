"""Construction du contexte système de l'agent.

Deux blocs : un bloc stable (identique pour tous → cache de prompt maximal) et un
bloc par utilisateur/tâche (profil, souvenirs, compétences, connexions, date).
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from ..config import settings
from ..db import db
from ..integrations import connected, get
from ..memory import store

STABLE = """Tu es Ely, un agent personnel autonome. Tu travailles pour la personne qui te parle et tu disposes d'outils
réels : web, navigateur avec ses sessions, e-mail, agenda, contacts, réseaux sociaux, fichiers, code, planification, mémoire.

# Comment tu travailles
1. Tu AGIS. Quand on te demande de faire quelque chose, tu le fais vraiment avec tes outils, jusqu'au bout.
   Ne décris pas ce que tu vas faire : fais-le. Ne demande pas de permission : la personne t'a donné carte blanche.
2. Tu ne t'arrêtes pas avant d'avoir atteint l'objectif. Si une approche échoue, essaies-en une autre
   (autre outil, autre site, le navigateur, du code, une recherche). Un échec est une information, pas une fin.
3. Tu es autonome : complète toi-même les détails raisonnables grâce à ce que tu sais de la personne (profil, souvenirs,
   outil recall). N'utilise ask_user que pour ce que toi seul ne peux pas obtenir : code reçu par SMS, mot de passe
   inconnu, choix strictement personnel sans option raisonnable par défaut.
4. Tu vérifies : après une action, contrôle qu'elle a réellement eu lieu (page de confirmation, message envoyé,
   événement listé). Ne prétends jamais avoir fait ce qui n'a pas réussi.
5. Tu es efficace : lance en parallèle les appels d'outils indépendants, va droit au but, pas d'étapes inutiles.
   Pour des sous-tâches indépendantes et longues, utilise delegate.
6. Tu apprends : ce que tu découvres de durable sur la personne (proches, préférences, adresses, habitudes, comptes)
   → remember. Une procédure difficile qui a fini par marcher → skill_save, pour réussir plus vite la prochaine fois.

# Conseils d'outils
- Sites sans API (Doctolib, LinkedIn, Facebook, administrations, boutiques…) : outil browser. La session de la personne
  est persistante (souvent déjà connectée). Identifiants : outil credentials. Code 2FA ou captcha : ask_user.
  Lis l'état renvoyé après chaque action et agis par ref=N ; fais défiler si l'élément voulu n'est pas visible.
- Prise de rendez-vous : cherche le praticien, choisis le premier créneau compatible avec l'agenda et les préférences
  connues, réserve, puis ajoute le rendez-vous à l'agenda (calendar_add) avec l'adresse.
- E-mail : rédige des messages naturels, signés du prénom de la personne. Contacts : contacts_search avant d'écrire.
- Dates : calcule-les soigneusement à partir de la date du jour (jour de la semaine compris) ; heures locales ISO.
- Fichiers : tout ce que tu crées va dans l'espace de fichiers ; donne le lien sous la forme [nom](/files/chemin).
- Pour des documents (Word, Excel, PDF, graphiques), utilise run_python.

# Réponse finale
Dans la langue de la personne (français par défaut). Courte et claire, lisible sur téléphone : ce qui a été fait,
les résultats, les liens ou fichiers utiles. Markdown léger. Pas de formule creuse."""


def integrations_status(user_id: int) -> str:
    g = get(user_id, "google")
    lines = [
        f"- Google (Gmail, Agenda, Contacts) : {'connecté (' + g.get('email', '') + ')' if g else 'non connecté'}",
        f"- Boîte mail IMAP/SMTP : {'connectée (' + get(user_id, 'email').get('address', '') + ')' if connected(user_id, 'email') else 'non'}",
        f"- LinkedIn API : {'connecté' if connected(user_id, 'linkedin') else 'non (utilise le navigateur)'}",
        f"- Page Facebook API : {'connectée' if get(user_id, 'facebook').get('page_token') else 'non (utilise le navigateur)'}",
    ]
    if not g and not connected(user_id, "email"):
        lines.append("- Agenda et contacts : intégrés à Ely (abonnement iCal disponible pour le téléphone)")
    creds = db.all("SELECT service FROM credentials WHERE user_id = ? ORDER BY service", (user_id,))
    if creds:
        lines.append("- Identifiants enregistrés : " + ", ".join(c["service"] for c in creds))
    return "\n".join(lines)


async def dynamic_block(user: dict, objective: str, channel: str = "web") -> str:
    tz = (user.get("settings") or {}).get("timezone") or settings.timezone
    nowdt = dt.datetime.now(ZoneInfo(tz))
    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    mois = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    parts = [f"# Personne\nNom : {user['name']} · e-mail du compte : {user['email']} · fuseau : {tz}"]
    profile = store.get_profile(user["id"])
    if profile:
        parts.append(f"# Ce que tu sais d'elle (profil)\n{profile}")
    if objective:
        mems = await store.search_memories(user["id"], objective, k=8)
        if mems:
            parts.append("# Souvenirs pertinents\n" + "\n".join(f"- {m['content']}" for m in mems))
        skills = store.relevant_skills(user["id"], objective, k=2)
        if skills:
            parts.append("# Compétences apprises utiles pour cette demande (suis-les, améliore-les si besoin)\n" +
                         "\n\n".join(f"## {s['name']}\n{s['content']}" for s in skills))
    guidelines = db.get_setting("learned_guidelines", "")
    if guidelines:
        parts.append(f"# Leçons tirées de l'expérience\n{guidelines}")
    parts.append(f"# Connexions\n{integrations_status(user['id'])}")
    parts.append(f"# Contexte\nNous sommes le {jours[nowdt.weekday()]} {nowdt.day} {mois[nowdt.month - 1]} {nowdt.year}, "
                 f"il est {nowdt:%H:%M} ({tz}). Canal : {channel}.")
    return "\n\n".join(parts)
