# Journal des versions

Les évolutions notables d'Ely, de la plus récente à la plus ancienne. Format inspiré de
[Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), numérotation [SemVer](https://semver.org/lang/fr/).

Les versions jusqu'à la 3.1.0 sont celles d'[ElyAgent](https://github.com/franckolv-dev/ElyAgent) (dépôt archivé) :
leur historique est dans [son journal](https://github.com/franckolv-dev/ElyAgent/blob/main/CHANGELOG.md).

> Mainteneur : Franck Ollivier — `franckolv-dev`

---

## [Non publié]

Hormis les corrections de `./ely.sh update` et `./ely.sh service` et les corrections issues de l'audit indépendant du
15 octobre 2026, les améliorations de cette section ont été écrites par Ely elle-même (auto-amélioration), puis relues
et fusionnées.

### Sécurité
- **Le réseau de la maison reste fermé aux comptes de la famille, même par un détour.** Une page publique qui redirige
  vers la box, le script d'une page ouverte dans le navigateur interne, ou une adresse tapée lors d'une prise de main
  ne joignent plus le Mac ni le réseau local depuis un compte ordinaire. L'administrateur garde son accès.
- **Une routine ne s'exécute plus jamais dans la conversation d'un autre compte**, même quand le numéro de sa
  conversation supprimée a été repris.
- **Extensions MCP : aucun secret transmis, outils réservés à l'administrateur.** Un serveur lancé par Ely ne reçoit
  plus les clés du `.env` ; ses outils ne servent qu'à l'administrateur, sauf `"shared": true`. **À vérifier** : un
  serveur qui comptait sur une clé du `.env` (un `GITHUB_TOKEN`, par exemple) doit désormais la recevoir dans son
  `"env"`.
- **Un plugin ne remplace plus un outil du cœur d'Ely** (qui porte les vérifications de droits, de réseau, de
  fichiers) : il en ajoute. Un plugin qui le tenterait est refusé, ou désactivé au démarrage.
- **Mots de passe et jetons chiffrés dans la base**, et jamais recopiés dans le journal des outils ni dans le déroulé
  des tâches, même tapés dans une page. La clé est `data/secret.key` (ou `ELY_SECRET_KEY`) : à garder avec toute copie
  de la base. Les secrets déjà enregistrés sont chiffrés au premier démarrage ; revenir ensuite à une version plus
  ancienne demanderait de reconnecter les services.
- **Les tests d'une auto-modification ne reçoivent plus aucun secret** (jeton GitHub d'Ely, Telegram, Google, clés).
- **Envois de fichiers bornés** : 200 Mo au plus (25 Mo pour la dictée), refusés avant d'être lus, et 2 Go toujours
  gardés libres sur le disque.
- **Nouveaux mots de passe de 8 caractères au moins** (ceux déjà choisis restent valables) ; une session inutilisée
  pendant 90 jours se ferme.
- **Les mots de passe ne sont plus recopiés dans ce qu'Ely lit d'une page.** Dans l'instantané du navigateur interne,
  que le modèle lit et que le journal des tâches conserve, un champ mot de passe ou code à usage unique affiche
  « [masquée] » au lieu de sa valeur. Le champ reste utilisable.

### Corrigé
- **Un site qui bloque les robots n'use plus les sous-agents.** Darty, Fnac et consorts répondent « 403 » à la
  lecture de page (`web_fetch`) ; la veille de prix y renvoyait onze fois une exception brute. L'outil tente d'abord
  le rendu par le navigateur interne, puis explique que le domaine refuse les lectures automatiques et qu'il faut
  ouvrir la page avec `browser`. Les autres erreurs HTTP sont rendues en une ligne lisible.
- **Les sous-agents suivent le modèle de la tâche.** Quand une routine était passée sur le modèle d'escalade, ses
  sous-agents (`delegate`) tournaient encore sur le petit modèle local et rendaient un gabarit vide
  (« [Nom du Modèle A] », « [URL précise du dépôt] ») pris pour un rapport. Ils utilisent désormais le modèle
  d'escalade de la tâche, et un rapport aux valeurs laissées en blanc est signalé comme non constaté.
- **Une routine sur un petit modèle local ne perd plus deux minutes à son premier tour.** Elle répondait d'abord par
  un faux accusé de réception (« c'est noté, je l'exécuterai demain »), relancé puis escaladé à chaque fois. Une
  tâche planifiée dont le modèle principal est local démarre désormais sur le modèle d'escalade.
- **Une facture produite par la page ne reste plus coincée dans le navigateur.** L'agent avait tenté
  `chrome://downloads`, le presse-papiers et l'adresse pendant dix minutes. `eval` enregistre désormais la valeur
  renvoyée (data URL ou texte) dans l'espace de fichiers, et les pages internes du navigateur (`chrome://…`) sont
  refusées avec cette marche à suivre.
- **Une routine qui doit prévenir sur Telegram ne se termine plus sans l'avoir fait.** La veille de prix avait rendu
  un rapport aux liens fictifs (« [Lien de la fiche produit] ») sans jamais appeler `notify`, et le petit modèle du
  contrôleur l'avait accepté. Ces manques sont désormais constatés sans modèle : une tâche planifiée sans aucune
  action, une notification demandée jamais envoyée ou une réponse aux valeurs laissées entre crochets est relancée
  (sur le modèle fort), chaque remarque n'étant faite qu'une fois pour les routines conditionnelles.
- **La copie de travail de l'auto-amélioration repart de la version active quand elle est en retard.** Après des
  améliorations fusionnées sur GitHub puis mises à jour, la copie restait sur l'ancienne base et le déploiement était
  refusé pour « tests disparus » (deux sessions de suite). Un travail en cours n'est jamais écrasé.
- **Un jeton de Claude Code commenté dans `.env` ne sert plus à la mission suivante.** Ely gardait en mémoire les
  identifiants jusqu'à « Actualiser les modèles » ou un redémarrage : la mission de la nuit partait encore sur le forfait
  alors que le `.env` ne laissait que la clé d'API. Le fichier est désormais relu dès qu'il change, avant chaque mission
  de Claude et à l'affichage de Réglages → Modèles.
- **Ely n'ajoute plus un serveur MCP dont la commande n'existe pas.** Elle avait pris son propre outil `ely_code` pour
  un programme : l'erreur revenait à chaque démarrage. L'ajout est refusé en nommant la commande introuvable.
- **Une tâche que le contrôleur ne confirme jamais n'est plus déclarée « terminée ».** Relances épuisées ou contrôleur
  muet : la réponse est rendue avec la réserve du contrôleur (sur Telegram aussi), la tâche est « terminée sans
  confirmation » et Ely n'en tire aucune procédure. Un verdict `"done": "false"` écrit en texte reste un refus.
- **Un e-mail dont l'envoi a été coupé n'est plus renvoyé en double.** Une coupure pendant la transmission (SMTP, ou
  réponse de Gmail ou de l'Agenda jamais reçue) rend un résultat incertain, et Ely ne refait pas la même action sans
  l'accord de la personne.
- **Déploiement d'une auto-modification : seul le code testé est commité.** Une retouche arrivée pendant les tests fait
  refuser le déploiement.
- **Deux tâches qui finissent ensemble n'écrasent plus le profil** : la seconde mise à jour part de la première.
- **Google Agenda : plus de limite à 100 événements** sur une période (toutes les pages sont lues, 1 000 au plus).
- **Lire un e-mail (IMAP) ne le marque plus comme lu.**
- **Un rendez-vous ne peut plus finir avant de commencer**, à l'ajout comme à la modification.
- **Une grande photo jointe ne fige plus le serveur** pendant sa réduction (chat et Telegram).
- **La vérification de syntaxe d'une auto-modification** utilise le Python d'Ely, même sans `python3` dans le PATH.
- **La suite de tests ignore le `.env` de la machine** (jeton de Claude, modèles choisis) : même verdict partout.
- **Un modèle local en panne n'est plus attendu à chaque appel.** LM Studio relancé sans ses modèles (« Model is
  unloaded », erreur 500 après une coupure de courant) : après un échec, il passe dix minutes en fin de chaîne, au lieu
  de faire perdre plusieurs secondes à chaque titre, vérification ou compaction.
- **Une extension MCP dont la commande a disparu le dit en la nommant** (Réglages → Extensions MCP et journal) : sous
  uvloop, l'erreur se limitait à « No such file or directory », sans dire quel fichier manquait.
- **Deux tâches d'un même compte se disputaient la page du navigateur interne.** Une routine pouvait changer la page
  d'une conversation en cours et lui faire cliquer au mauvais endroit. Chaque conversation a désormais sa page,
  conservée d'une réponse à l'autre, et chaque sous-agent a la sienne. Les actions d'un même compte s'exécutent l'une
  après l'autre.
- **Le superviseur et Ely ne lisaient pas toujours le même port.** Avec une valeur mal tapée dans `ELY_PORT` (« 800O »),
  Ely écoutait sur 8000 mais le superviseur contrôlait sa santé ailleurs, et jugeait à tort une mise à jour en échec.
  Le lanceur convertit désormais le port exactement comme Ely.

- **`./ely.sh update` pouvait redémarrer un autre programme que Ely.** Sur un Mac où le nom de service `fr.ely.agent`
  lançait encore une ancienne installation (Node.js), la mise à jour redémarrait ce programme en annonçant « service Ely
  redémarré », et Ely restait sur l'ancienne version. Le lanceur vérifie désormais que le service lance bien son propre
  dossier. Sinon, il n'y touche pas et le dit : `./ely.sh service` le remplace (en gardant une copie de l'ancien dans
  `data/`), et `./ely.sh unservice` ne le supprime jamais.
- **Le démarrage automatique sur Mac ne se faisait jamais, sans que rien ne le signale.** launchd lançait `ely.sh`
  directement, et macOS lui refusait l'accès au dossier Documents (code 126, « Operation not permitted ») : le service
  échouait en boucle, et Ely ne repartait pas après une coupure de courant. Le service lance désormais `ely.sh` par
  `/bin/bash`, comme celui de la voix XTTS. `./ely.sh service` vérifie aussi qu'il tourne ; sinon, il le dit, montre la
  fin du journal et indique la marche à suivre. **À faire sur un Mac où le service est déjà installé** : arrêter l'Ely
  lancée à la main (Ctrl+C dans sa fenêtre), puis relancer `./ely.sh service`.

### Ajouté
- **Sauvegardes automatiques de la base** dans `data/sauvegardes/` : une par jour (7 gardées) et une avant chaque
  activation d'une modification du code par Ely (5 gardées). Un retour arrière automatique indique la copie d'avant la
  mise à jour ; la restauration est décrite dans le README.
- **Les longues conversations se chargent par pages** : les 200 derniers messages, puis « Afficher les messages
  précédents ».
- **Intégration continue** : la suite de tests tourne sur chaque pull request et à chaque push sur `main`.

### Modifié
- **Ely fait le ménage dans ses branches locales.** Une fois par jour, elle supprime les copies locales de ses
  améliorations (`ely-improvement/…`) dont la PR est fusionnée sur GitHub. Celles dont la PR attend encore, et sa
  branche de travail `ely-self`, restent.
- **La mémoire préfère un modèle d'embeddings multilingue.** En choix automatique, Ely prend
  Qwen3-Embedding quand LM Studio (ou Ollama) l'a installé, puis nomic-embed, puis tout modèle d'embeddings. Les
  souvenirs existants sont recalculés d'eux-mêmes avec le nouveau modèle.
- **Une auto-amélioration qui modifie le lanceur `ely.sh` est publiée sans être activée.** Le superviseur déjà lancé
  garde l'ancien lanceur en mémoire : l'administrateur fusionne la PR, puis relance le superviseur complet
  (`./ely.sh update` avec le service, sinon Ctrl+C puis `./ely.sh`).

## [4.2.0] — 2026-10-04

> **Le bon effort au bon endroit.** L'effort de raisonnement se règle par rôle : les demandes courantes ne font plus
> réfléchir longuement le modèle le plus puissant. Tous les modèles du forfait ChatGPT peuvent être proposés, Ely
> peut publier ses améliorations sous son propre compte GitHub, et elle s'installe aussi sous Linux et Windows (WSL2).
>
> **En mettant à jour depuis la 4.1.0** (`./ely.sh update`) :
> - l'agent principal passe en effort Moyen ; Réglages → Modèles permet de le remettre en Élevé ;
> - pour qu'Ely publie à son nom, suivez [docs/auto-amelioration-github.md](docs/auto-amelioration-github.md)
>   (`ELY_GITHUB_TOKEN`) ; sans ce réglage, rien ne change ;
> - Linux ou Windows : voir les rubriques « Linux » et « Windows (WSL2) » du README.

### Ajouté
- **Effort de raisonnement par rôle** (Réglages → Modèles) : Moyen ou Élevé pour l'agent principal, l'escalade et
  l'auto-amélioration. Il est transmis à chaque modèle qui le prend en charge (abonnement ChatGPT, OpenAI, Gemini,
  Claude). Inutile de faire réfléchir longuement le modèle le plus puissant pour des demandes courantes (actualités,
  e-mails…), qui consomment alors moins de quota.
- **Autres modèles de l'abonnement ChatGPT** (GPT-6 Sol, Luna…) : il suffit de les lister dans `CHATGPT_MODELS`
  puis de cliquer « Actualiser les modèles », comme l'explique désormais la ligne « Abonnement ChatGPT ».
- **Compte GitHub propre à Ely** (`ELY_GITHUB_TOKEN`, compte invité comme collaborateur sur le dépôt). Ses commits
  d'auto-amélioration portent l'adresse de ce compte, et `gh` ouvre ses PR avec son jeton : elles sont à son nom, et Ely
  figure parmi les contributeurs une fois ses PR fusionnées. Un jeton refusé arrête tout avant le commit, sans rien
  activer. Sans ce jeton, rien ne change.
- **Linux et Windows (WSL2)** : `./ely.sh service` crée un service systemd de l'utilisateur, au lieu d'un simple
  message. Ely démarre alors avec la session, et `./ely.sh update` et `./ely.sh unservice` le prennent en charge.
  L'installation signale les bibliothèques système qui manquent à Chromium, avec la commande à lancer. Le README
  explique l'installation sous Linux et sous Windows par WSL2.

### Modifié
- **L'agent principal réfléchit en effort Moyen par défaut**, au lieu d'Élevé. L'escalade et l'auto-amélioration
  restent en Élevé.

### Corrigé
- **Gmail refusait des requêtes quand plusieurs tâches d'une même personne l'interrogeaient en même temps** (« Too
  many concurrent requests »). La limite de cinq requêtes simultanées est désormais partagée entre recherches, lectures
  et tris, sous-agents compris ; Agenda et Contacts gardent leur propre débit. Amélioration écrite par Ely elle-même.
- **Une clé retirée ou commentée dans `.env` restait active jusqu'au redémarrage.** « Actualiser les modèles »
  ajoutait les nouvelles clés mais n'oubliait jamais les anciennes : une clé Anthropic commentée pouvait encore servir
  à Claude. Elle est maintenant abandonnée dès l'actualisation.

## [4.1.0] — 2026-10-03

> **Ely s'ouvre à Internet et à la famille.** Revue complète de sécurité et de stabilité ; auto-amélioration
> confiée à Claude et publiée sur GitHub en pull request.
>
> **En mettant à jour depuis la 4.0.0** (`./ely.sh update`) :
> - retéléchargez l'extension Chrome (1.3.0) depuis Ely : l'ancienne reste reliée, mais met la session dans
>   l'adresse de sa connexion ;
> - `ELY_ALLOW_SHELL_FOR_ALL` n'a plus d'effet : Python et le terminal sont réservés à l'administrateur, sauf
>   `ELY_ALLOW_CODE_FOR_ALL=true` ;
> - proxy HTTPS sur une autre machine que le Mac : déclarez son adresse dans `ELY_TRUSTED_PROXIES` ;
> - auto-amélioration du code : connectez le CLI GitHub sur le Mac (`gh auth login`), sans quoi le déploiement est
>   refusé.

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

- **Journal des tâches pour l'auto-amélioration** (`ely_journal`, pour Claude comme pour le modèle d'escalade).
  Il retrouve une tâche passée par quelques mots, puis en donne le déroulé complet : demandes, actions avec leurs
  arguments et résultats, erreurs exactes, refus du contrôleur. Les mots de passe restent masqués. Une session peut
  ainsi diagnostiquer précisément un échec, par exemple une commande en ligne qui n'a pas abouti.
- **Auto-amélioration publiée sur GitHub** (amélioration écrite par Ely elle-même). Chaque modification de son code
  testée est poussée sur une branche `ely-improvement/<commit>` avec une pull request attribuée à Ely, que
  l'administrateur relit et fusionne ; Ely ne fusionne jamais elle-même. L'activation locale n'a lieu qu'une fois la
  publication confirmée. Voir [docs/auto-amelioration-github.md](docs/auto-amelioration-github.md).
- **Tri des e-mails Gmail** (`email_manage`) : corbeille, archives, libellés (créés s'ils manquent), lu ou non lu, sur
  plusieurs e-mails en un appel.
- **Rechargement automatique de l'interface** quand Ely redémarre sur une nouvelle version (mise à jour,
  auto-amélioration). Si un message est en cours d'écriture, Ely le garde et propose de recharger.

### Sécurité
Ce lot fait suite à une revue complète. Ely est souvent joignable depuis Internet et partagée en famille : ces failles
permettaient de prendre la main sur la machine.
- **Droits vérifiés à l'exécution de chaque outil.** Jusqu'ici, un outil réservé n'était que retiré de la liste
  proposée au modèle. Un compte ordinaire, ou un contenu piégé lu par l'agent, pouvait encore appeler par leur nom
  `ely_plugin`, `ely_guidelines` ou `ely_deploy`.
- **Python et terminal réservés à l'administrateur par défaut.** Le nouveau réglage `ELY_ALLOW_CODE_FOR_ALL=false`
  remplace `ELY_ALLOW_SHELL_FOR_ALL`, qui laissait `run_python` à tous. Le code lancé ne reçoit plus les secrets
  d'Ely (`.env`, clés, jetons).
- **Fichiers de l'espace personnel servis sans pouvoir s'exécuter.** HTML, SVG et XML se téléchargent ; tout est
  servi avec `nosniff` et dans un bac à sable. Une page déposée par Telegram ou un téléchargement ne peut plus agir
  avec la session.
- **Contenu de tiers encadré pour le modèle.** Pages web, e-mails, fichiers lus, résultats de recherche et extensions
  MCP arrivent marqués comme des informations, jamais des consignes. Le prompt de base pose la règle.
- **Compétences partagées réservées à l'administrateur.** Une compétence personnelle du même nom, apprise ou
  enregistrée, n'écrase plus celle de toute la famille.
- **Extension Chrome téléchargée liée à Ely.** Le lien `extension.zip?url=` refuse une autre adresse que celle
  d'Ely.
- **Premier compte (administrateur) créé seulement sur la machine d'Ely.** Une installation neuve exposée ne
  revient plus au premier venu. Les inscriptions simultanées ne créent plus plusieurs administrateurs.
- **Invitations à usage unique, même en cas d'envois simultanés, et valables 7 jours.**
- **Retour OAuth (Google, LinkedIn) lié à la session de la personne qui l'a lancé.** Transmis à un autre, le lien
  ne rattache plus ses comptes à l'expéditeur.
- **Telegram.**
  - Seuls les chats privés sont écoutés : relié à un groupe, chacun y aurait parlé au nom du compte.
  - Le nom des fichiers reçus est nettoyé : il n'écrit plus hors de `Reçus/`, et un fichier existant n'est plus
    écrasé.
  - Le code de liaison expire après 30 minutes, et un chat n'est relié qu'à un seul compte.

Second lot, durcissement :
- **Jeton de session jamais lu dans l'adresse.** Seuls le cookie et l'en-tête `Authorization` comptent. L'extension
  Chrome (1.3.0) envoie la session dans le premier message de sa connexion, et non plus dans l'adresse du
  WebSocket, qui finit dans les journaux des proxys. Les extensions déjà installées restent reliées.
- **Changer de mot de passe ferme les autres sessions**, celle qui fait le changement exceptée (téléphone perdu,
  jeton volé), et coupe un Chrome relié avec une ancienne session. Un mot de passe réinitialisé par l'administrateur
  ferme toutes les sessions du compte.
- **Essais de mot de passe.** Un robot est bloqué un quart d'heure après 5 essais sur un compte (20 sur l'ensemble des
  comptes) depuis son adresse, mais il ne peut plus enfermer Franck dehors : depuis une autre adresse, le bon mot de
  passe passe. La personne est prévenue des essais (téléphone, Telegram). Un e-mail inconnu coûte le même calcul
  qu'un compte existant : le temps de réponse ne dit plus qui a un compte.
- **Adresse réelle des visiteurs.** `X-Forwarded-For` n'est plus cru que d'un proxy de confiance : un proxy sur ce
  Mac par défaut, sinon `ELY_TRUSTED_PROXIES`. Avant, n'importe quel visiteur pouvait l'écrire et contourner la limite
  d'essais. Un proxy non déclaré est signalé dans le journal.
- **En-têtes de sécurité.** La page de l'interface n'exécute que ses propres scripts, ne charge aucune image d'un
  autre site et ne s'affiche pas dans le cadre d'un autre site (CSP, `frame-ancestors`) ; toutes les réponses sont en
  `nosniff`. Dans les réponses d'Ely, une image d'un autre site devient un lien : son adresse ne peut plus emporter
  d'informations à l'affichage.
- **Moins d'informations sans compte.** `/api/health` dit seulement qu'Ely est en vie (l'état des fournisseurs est
  réservé à l'administrateur) ; la documentation de l'API (`/api/docs`) n'est plus publiée ; `/api/tools` demande
  une session.
- **Réseau de la maison fermé aux comptes ordinaires.** Ni leur navigateur, ni la lecture de pages, ni leur serveur
  de messagerie ne peuvent viser le Mac, la box, le NAS ou LM Studio (`file:` compris) ; un abonnement aux
  notifications doit viser un vrai service de notification. L'administrateur garde l'accès à son réseau.
- **Claude ne reçoit plus les autres clés d'Ely** (OpenAI, Telegram, Google…) : le SDK lui transmettait tout
  l'environnement.
- **Un compte supprimé ne laisse rien derrière lui** : fichiers, profil du navigateur interne (et ses cookies),
  compétences, statistiques, Chrome relié et tâches en cours. Un compte créé ensuite pouvait reprendre son numéro et
  en hériter.

### Corrigé
- **Stabilité, suite de la revue** :
  - un outil qui agit (envoi d'e-mail, clic, publication…) et dépasse son délai n'est plus présenté comme un échec
    (« essaie autrement ») mais comme un résultat incertain à vérifier : l'action a souvent eu lieu, et le modèle la
    refaisait. Chrome qui se déconnecte en plein clic : même règle, et plus de second clic par JavaScript ;
  - un modèle resté muet passe dix minutes en fin de chaîne : l'étape suivante ne l'attend plus ;
  - un modèle de secours au contexte trop court passe la main au suivant ; s'il n'y en a pas, l'historique est
    condensé à sa taille, et non à celle du modèle principal ;
  - Ely démarrée avant LM Studio ne répond plus « aucun modèle » pendant 30 minutes : LM Studio est revérifié chaque
    minute et la tâche patiente ;
  - les photos jointes (Telegram, pièces jointes) ne sont plus renvoyées au modèle à chaque appel, et les captures de
    plus de 30 jours quittent la base (une photo garde sa vignette) ;
  - l'arrêt est borné : une tâche qui refuse de s'arrêter, ou un fil bloqué, ne retient plus le redémarrage après une
    mise à jour (arrêt forcé au bout de 20 s) ;
  - une mise à jour d'Ely attend que les tâches en cours des autres membres de la famille soient finies (15 min au
    plus) avant de redémarrer ;
  - une auto-amélioration qui plante trois fois en dix minutes est annulée par le lanceur, comme celle qui ne démarre
    pas. Ce retour arrière met de côté les modifications locales (`git stash`) au lieu de les écraser. Un démarrage en
    échec n'est plus confondu avec un port occupé ;
  - un déploiement qui supprime ou renomme un test est refusé ;
  - « Annuler » une amélioration modifiée depuis ne laisse plus de marqueurs de conflit dans le code ;
  - un plugin qui quitte (`sys.exit`) ou ne finit pas de se charger est refusé avant d'être installé ; présent au
    démarrage, il est désactivé au lieu d'empêcher Ely de démarrer ;
  - une routine illisible (horaire invalide, fuseau inconnu) ne retient plus celles des autres ; un fuseau mal saisi
    est refusé ; une routine en échec prévient la personne et garde son vrai résultat ;
  - un serveur MCP qui s'arrête est détecté et reconnecté ; le navigateur interne planté est relancé ;
  - supprimer une conversation pendant qu'Ely y travaille arrête proprement la tâche ;
  - deux index manquants faisaient parcourir toute la base à chaque vérification d'objectif.
- **Notifications push jamais envoyées.** La clé VAPID, enregistrée en PEM, était illisible pour `pywebpush`. Aucune
  notification ne partait, sans erreur visible : les questions d'Ely, les résultats des tâches planifiées et les
  rappels d'agenda n'arrivaient que par Telegram. Un service push muet est désormais abandonné au bout de 10 s.
- **LM Studio (ou ChatGPT) qui ne répond plus du tout.** Ely passe au modèle suivant dès le premier délai réseau,
  au lieu de trois essais de 15 minutes, soit 45 minutes perdues à chaque étape.
- **Sécurité, pour une Ely joignable depuis Internet** :
  - la session des extensions Chrome d'avant la 1.3, qui passe dans l'adresse de leur WebSocket, ne s'écrit plus en
    clair dans la console. Il en va de même pour le jeton du flux iCal ;
  - les mots de passe essayés en boucle sont freinés (voir Sécurité).
- **Une tâche pouvait rester bloquée des heures sur un seul appel de modèle.** C'est arrivé le 30/09 : la routine du
  matin est restée dix heures sans réponse de Gemma (LM Studio).
  - Les modèles locaux reçoivent désormais, eux aussi, une longueur maximale de réponse.
  - Un appel qui dépasse 20 minutes est abandonné, et Ely passe au modèle suivant (l'escalade, par exemple) en le
    disant.
- **Telegram : la réponse n'est envoyée qu'en fin de tâche.** Un brouillon refusé par le contrôleur d'objectif
  (« je m'en occupe » sans rien faire) n'atteint plus la personne.
- **Le contrôleur d'objectif connaît la date du jour** : il juge les dates par rapport à aujourd'hui, et non d'après
  ses connaissances.
- **Recherche Gmail** : cinq lectures simultanées au plus, avec reprise sur une limite de débit (429).
- **Une tâche planifiée qui se déclenche pendant qu'une autre tourne encore** démarre maintenant à part, au lieu de se
  greffer sur la tâche en cours. Le 30/09, la routine de midi attendait derrière celle du matin, bloquée.
- Réglages → Auto-amélioration → « Lancer » ne démarrait pas la session (erreur « no running event loop » dans le
  terminal). Une erreur de lancement s'affiche désormais au lieu de rien.
- **Le menu de modèle de la conversation suit les réglages.** Une clé ajoutée dans `.env` puis « Actualiser les
  modèles » : ses modèles sont proposés dès la fermeture des réglages, sans recharger la page. Un fournisseur retiré
  (clé enlevée, abonnement déconnecté) n'y figure plus et n'apparaît plus « ok » parmi les fournisseurs.

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

[4.2.0]: https://github.com/franckolv-dev/ELY2/releases/tag/v4.2.0
[4.1.0]: https://github.com/franckolv-dev/ELY2/releases/tag/v4.1.0
[4.0.0]: https://github.com/franckolv-dev/ELY2/releases/tag/v4.0.0
