# Fiche Chrome Web Store : Ely pour Chrome

> Dernière mise à jour : 2026-09-29

Source unique des informations à saisir dans le tableau de bord développeur du Chrome Web Store
(gabarit de la compétence `chrome-extensions` de Modern Web Guidance). À tenir à jour à chaque
modification de l'extension : permissions, données, version. Ce fichier n'est pas inclus dans le
zip téléchargé depuis Ely.

Distribution actuelle : non publiée. L'extension se télécharge depuis Ely (menu du compte,
Extension Chrome) et se charge décompressée.

## Fiche du Store

**Nom** : Ely pour Chrome (identique à `manifest.json`)

**Description courte** (132 caractères max) :
Permet à votre assistant Ely d'ouvrir et d'utiliser des onglets dans votre Chrome, avec vos sessions, dans une fenêtre à part.

**Description détaillée** :
Ely pour Chrome relie ce Chrome à votre assistant Ely, installé sur votre propre machine ou serveur.

Quand vous demandez à Ely une démarche en ligne (prendre un rendez-vous, retrouver un code reçu par e-mail, remplir un formulaire), Ely ouvre ses propres onglets dans une fenêtre séparée de ce Chrome et y agit comme vous le feriez : clics, saisie, lecture de la page. Les sites où vous êtes déjà connecté le restent pour Ely, sans lui confier vos mots de passe.

Utilisation : installez l'extension, ouvrez Ely dans ce Chrome et connectez-vous. L'icône de l'extension indique si la liaison est active et permet de changer l'adresse d'Ely.

Confidentialité : l'extension ne parle qu'à l'adresse d'Ely que vous avez choisie. Elle n'envoie rien à un tiers, n'affiche aucune publicité et ne mesure pas votre navigation. Quand Ely ne s'en sert plus, elle libère ses onglets.

**Catégorie** : Productivity

**Objectif unique** : Permettre à l'assistant Ely de l'utilisateur d'agir dans des onglets de son Chrome, dans une fenêtre dédiée.

**Langue principale** : Français (fenêtre de l'extension en français ou en anglais selon Chrome)

## Visuels

| Visuel | Dimensions | État | Fichier |
|--------|-----------|------|---------|
| Icône du Store (obligatoire) | 128x128 PNG | Prêt | `icons/icon-128.png` |
| Capture 1 (obligatoire) | 1280x800 ou 640x400 | À faire | |
| Capture 2 | 1280x800 ou 640x400 | À faire | |
| Petite vignette promo | 440x280 | À faire | |

Captures à prévoir : la fenêtre de l'icône en état « Connecté » ; la fenêtre d'Ely remplissant un
formulaire de rendez-vous à côté de la conversation avec Ely.

## Justification des permissions

| Permission | Type | Justification |
|------------|------|---------------|
| `debugger` | permissions | Ely agit dans les onglets qu'elle ouvre (clics et saisie réels, lecture du contenu, captures d'écran, envoi d'un fichier choisi par l'utilisateur). Les simples scripts de contenu ne produisent pas de clics reconnus par les sites de rendez-vous et de messagerie. L'onglet est libéré 1 min 30 après la dernière action d'Ely. |
| `scripting` | permissions | Dans les onglets d'Ely seulement, avant d'en reprendre le pilotage : retirer les cadres qu'une autre extension y a insérés (menu d'un gestionnaire de mots de passe dans un champ, par exemple). Si Chrome refuse encore, retirer aussi ceux chargés par script, reconnus à ce qu'ils sont opaques et sans adresse visible. Chrome interdit le protocole DevTools sur un onglet qui contient une page d'une autre extension ; sans ce retrait, Ely ne peut plus ni lire ni remplir la page. Aucun script n'est injecté dans les onglets de l'utilisateur. |
| `tabs` | permissions | Lister les onglets de la fenêtre d'Ely avec leur titre et leur adresse, pour qu'Ely sache sur quelle page elle se trouve et puisse passer d'un onglet à l'autre (par exemple d'un site de rendez-vous à la messagerie qui a reçu le code). |
| `cookies` | permissions | Lire uniquement le cookie de session d'Ely (`ely_token`) à l'adresse d'Ely choisie, pour relier l'extension au compte connecté sans redemander d'identifiants, et se reconnecter quand ce cookie change. |
| `storage` | permissions | Retenir l'adresse d'Ely saisie dans la fenêtre de l'extension et l'identifiant de la fenêtre d'Ely pendant la session. |
| `alarms` | permissions | Relancer la liaison avec Ely chaque minute après une coupure, et libérer les onglets après une période sans action. |
| `<all_urls>` | host_permissions | L'adresse d'Ely est choisie par l'utilisateur (ordinateur local, réseau ou nom de domaine) : l'extension doit pouvoir joindre cette adresse et lire son cookie de session. Ely agit ensuite sur les sites que l'utilisateur lui demande, qui ne sont pas connus d'avance. |

## Confidentialité et données

### Collecte

**L'extension collecte-t-elle des données ?** Oui : elles vont uniquement au serveur Ely de l'utilisateur, à l'adresse qu'il a choisie.

| Type de donnée | Collectée ? | Envoyée hors de l'appareil ? | Usage | Partagée avec des tiers ? |
|----------------|-------------|------------------------------|-------|---------------------------|
| Informations d'identification personnelle | Non | | | |
| Santé | Selon les pages qu'Ely ouvre à la demande de l'utilisateur (ex. rendez-vous médical) | Vers le serveur Ely de l'utilisateur | Accomplir la démarche demandée | Non |
| Finances | Non | | | |
| Authentification | Cookie de session d'Ely | Vers le serveur Ely de l'utilisateur | Relier l'extension au compte | Non |
| Communications personnelles | Selon les pages qu'Ely ouvre (ex. messagerie pour lire un code) | Vers le serveur Ely de l'utilisateur | Accomplir la démarche demandée | Non |
| Localisation | Non | | | |
| Historique web | Adresses et titres des onglets de la fenêtre d'Ely | Vers le serveur Ely de l'utilisateur | Savoir où en est la démarche | Non |
| Activité | Non | | | |
| Contenu des sites | Contenu et captures des onglets de la fenêtre d'Ely | Vers le serveur Ely de l'utilisateur | Lire la page pour agir | Non |

### Engagements
- [x] Les données ne sont pas vendues à des tiers
- [x] Les données ne servent qu'à la fonction de l'extension
- [x] Les données ne servent pas à évaluer une solvabilité

## Politique de confidentialité

**URL** (obligatoire, requise par `cookies` et `<all_urls>`) : à créer et publier avant tout envoi.

## Diffusion

**Visibilité** : non publiée (téléchargement depuis Ely)
**Régions** : toutes

## Éditeur

**Nom** : Franck Ollivier
**E-mail de contact** : à compléter
**Assistance** : https://github.com/franckolv-dev/ELY2/issues
**Site** : https://github.com/franckolv-dev/ELY2

## Historique des versions

| Version | Date | Changements | État |
|---------|------|-------------|------|
| 1.2.3 | 2026-09-29 | Cadres d'extension chargés par script (sans adresse visible) retirés aussi ; Ely n'est plus bloquée sur une page qu'elle ne peut pas piloter (PDF, cadre qui revient sans cesse) : elle peut toujours ouvrir une autre page ; messages clairs à la place de l'erreur de Chrome | Brouillon |
| 1.2.2 | 2026-09-29 | Ely garde la main sur ses onglets quand une autre extension (gestionnaire de mots de passe…) y glisse son menu : les cadres de cette extension sont retirés et l'onglet repris ; message clair si Chrome refuse malgré tout | Brouillon |
| 1.2.1 | 2026-09-26 | Onglets libérés de façon fiable après une période sans action, même si Chrome a mis l'extension en veille ; focus clavier visible dans la fenêtre de l'icône | Brouillon |
| 1.2.0 | 2026-09-26 | Extension téléchargeable depuis Ely, déjà réglée sur son adresse | Brouillon |

## Notes pour l'examen

### Points que l'examen relèvera
- `debugger` et `<all_urls>` sont des permissions larges, examinées de près. L'objectif unique et
  les justifications ci-dessus doivent être repris tels quels.
- Les actions sont décidées par le serveur Ely de l'utilisateur (commandes du protocole DevTools
  envoyées par WebSocket). Aucun code distant n'est exécuté dans l'extension elle-même, mais le
  Store peut assimiler ce pilotage à distance à du code distant : risque de refus à anticiper,
  d'où la distribution actuelle hors Store.
- Les boîtes de dialogue JavaScript des pages ouvertes par Ely sont acceptées automatiquement
  (sinon la page resterait bloquée).
- Dans les onglets d'Ely, l'extension retire les cadres insérés par d'autres extensions quand ils
  empêchent le pilotage (`scripting`). Elle ne touche ni aux autres extensions ni aux onglets de
  l'utilisateur ; l'examen peut y voir une interférence avec d'autres extensions, à expliquer.

### Refus
Aucun envoi pour l'instant.
