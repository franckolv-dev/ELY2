# Journal des versions

Les évolutions notables d'Ely, de la plus récente à la plus ancienne. Format inspiré de
[Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), numérotation [SemVer](https://semver.org/lang/fr/).

Les versions jusqu'à la 3.1.0 sont celles d'[ElyAgent](https://github.com/franckolv-dev/ElyAgent) (dépôt archivé) :
leur historique est dans [son journal](https://github.com/franckolv-dev/ElyAgent/blob/main/CHANGELOG.md).

> Mainteneur : Franck Ollivier — `franckolv-dev`

---

## [Non publié]

### Ajouté
- **Modèle fort sur demande** : « prenez le modèle fort », « utilise le modèle fort » (ou « use the strong model »)
  font passer la tâche au modèle d'escalade dès le premier appel. « Utilise Opus » ou « utilise Fable » y passent
  aussi, en attendant que Claude soit relié par l'Agent SDK.
- **Rôle « Auto-amélioration »** dans Réglages → Modèles : les sessions d'auto-amélioration, de nuit ou à la
  demande, ont leur propre modèle. En automatique, c'est le modèle d'escalade, et non plus le modèle principal.
- **Auto-amélioration confiée à Claude** (Claude Opus 5.5 ou Fable 5.1, par le Claude Agent SDK, en option :
  `./ely.sh install` l'installe quand une clé Anthropic est dans `.env`). Claude mène toute la session dans la copie
  de travail :
  - il lit et modifie les fichiers, sans terminal ni accès hors de la copie ;
  - il teste, déploie, écrit leçons, compétences et plugins avec les outils d'Ely ;
  - Ely redémarre une fois la mission close ;
  - si Claude ne répond pas (quota du forfait atteint, jeton refusé…), la session continue sur le modèle
    d'escalade, et Ely en donne la raison ;
  - si son quota tombe en pleine mission, Ely s'arrête et consigne ce qu'il a fait dans un fichier markdown
    (Fichiers → `auto-amelioration/`). Elle recommence ensuite la mission avec le modèle d'escalade, en lui disant
    ce qui est déjà en place ;
  - une mission coupée par un redémarrage n'est jamais relancée à l'aveugle.

  Réglages → Modèles → « Claude (Agent SDK) » : état, essai de la connexion, budget par mission. La consommation
  apparaît dans le tableau de bord. Avec une clé d'API, Claude est facturé au token et n'est jamais choisi d'office.

- **Rechargement automatique de l'interface** quand Ely redémarre sur une nouvelle version (mise à jour,
  auto-amélioration). Si un message est en cours d'écriture, Ely le garde et propose de recharger.

### Corrigé
- Réglages → Auto-amélioration → « Lancer » ne démarrait pas la session (erreur « no running event loop » dans le
  terminal). Une erreur de lancement s'affiche désormais au lieu de rien.

### Modifié
- **Escalade dès le premier échec** constaté par le contrôleur d'objectif, au lieu du deuxième. Chaque bascule est
  annoncée dans la conversation, avec sa raison.

## [4.0.0] — 2026-09-29

> **Ely repart de zéro.** Réécriture complète, avec trois mots d'ordre : efficacité, autonomie, performance.
> Environ 8 300 lignes de Python et 2 900 lignes d'interface au lieu de 240 000 lignes et 9 services Docker :
> un seul processus, une seule base SQLite, aucun service à maintenir.
>
> Nouvelle installation : rien n'est repris automatiquement de la 3.1.0. Les clés passent dans `.env` ; la voix
> clonée est reprise dans `voice/xtts`.

### Ajouté
- **Boucle d'agent orientée objectif** : un contrôleur vérifie que la demande est vraiment satisfaite et relance
  Ely avec ce qui manque. Tâches de fond qui survivent à la fermeture de l'application et à un redémarrage. Une
  action au résultat incertain n'est jamais rejouée à l'aveugle. Messages pris en compte en cours de tâche,
  sous-agents en parallèle.
- **Multi-modèles par rôles** (principal, escalade, rapide, local, vecteurs), choisis automatiquement et réglables
  dans Réglages → Modèles avec effet immédiat. En cas de panne, Ely bascule sur un autre modèle et dit pourquoi,
  mais jamais vers un modèle payant que personne n'a choisi. Fournisseurs :
  - Anthropic en natif ;
  - tous les services compatibles OpenAI ;
  - LM Studio et Ollama ;
  - l'abonnement ChatGPT via Codex, GPT-6 compris. Ely partage sa session avec Codex sans que l'un déconnecte
    l'autre.
- **Multi-utilisateurs** : mémoire, connexions, fichiers, navigateur et tâches propres à chaque personne ;
  invitations. Vouvoiement par défaut, tutoiement sur demande.
- **Interface PWA bilingue** français/anglais, installable sur ordinateur et Android, sans étape de compilation.
  Elle montre en direct le navigateur d'Ely.
- **« Ely pour Chrome »** : Ely agit dans votre Chrome, avec vos sessions, dans une fenêtre à part. L'extension se
  télécharge depuis Ely, déjà réglée ; le navigateur interne prend le relais en secours. Ely garde la main malgré
  les extensions qui glissent leurs cadres dans les pages (gestionnaires de mots de passe…). Elle n'est jamais
  prisonnière d'un onglet qu'elle ne peut pas piloter.
- **Mémoire hybride** (profil, souvenirs, historique, compétences partagées) et apprentissage après chaque tâche.
- **Auto-amélioration récursive sur quatre niveaux** :
  1. leçons ;
  2. compétences ;
  3. plugins chargés à chaud ;
  4. modification de son propre code, dans une copie git isolée, avec tests, redéploiement et retour arrière
     automatique par `ely.sh`.
- **Connexions** :
  - e-mail : Gmail ou toute boîte IMAP/SMTP ;
  - agenda et contacts : Google ou intégrés, avec flux iCal ;
  - réseaux et messagerie : LinkedIn, Facebook, Telegram ;
  - outils : serveurs MCP, recherche web multi-moteurs, coffre d'identifiants ;
  - tâches planifiées, dont le résultat complet arrive sur Telegram.
- **Voix** : dictée, et lecture par les voix du navigateur ou par la voix clonée du service local XTTS.
- **Lanceur supervisé `./ely.sh`** : service macOS. `./ely.sh update` met à jour sans perdre les améliorations
  faites par Ely elle-même. Le lanceur et le menu du compte affichent la version et le commit qui tournent.

### Modifié
- Une trentaine d'outils concis : environ 4 000 tokens par tour au lieu de 61 000. Les pages web sont lues sous
  forme d'éléments numérotés, et le contexte des longues missions est élagué puis résumé.
- Configuration en un seul endroit : les secrets dans `.env`, les choix de l'administrateur en base, relus à
  chaque appel.

### Retiré
- Anonymisation, validations humaines (HITL), couches RGPD et « souveraineté ».
- Superviseur multi-agents, environ 200 outils redondants, routeurs de complexité.
- Conteneurs Docker, applications natives, avatar 3D.

Les mesures de l'ancien projet l'ont montré : ces couches coûtaient plus qu'elles ne rapportaient.

[4.0.0]: https://github.com/franckolv-dev/ELY2/releases/tag/v4.0.0
