# Auto-amélioration : conservation sur GitHub

Cette fonctionnalité a été créée par **Ely**, dans le cadre de son **auto-amélioration**, à la demande de l'administrateur.

## Pourquoi

Le pipeline précédent exécutait les tests, créait un commit sur `ely-self`, puis le fusionnait dans la version active locale. Il ne faisait ni push ni PR. Un retour arrière ou une remise à zéro pouvait rendre les commits inaccessibles depuis les branches habituelles. Le reflog a permis de retrouver le commit `011a5a8847351ff56ffd406ead5242009adde425` du 2 octobre 2026 et de réappliquer ses quatre correctifs, tout en conservant les protections de sécurité plus récentes (`untrusted=True` pour les e-mails).

## Nouveau déroulé

1. Tests obligatoires puis commit dans le worktree.
2. Branche locale durable `ely-improvement/<SHA>` (distincte de la branche de travail réinitialisable).
3. Dépôt déduit de l'URL de push `origin`, uniquement sur github.com, et branche cible lue sur GitHub (pas dans `origin/HEAD`, potentiellement périmé).
4. Push du commit exact sur cette branche dédiée, sans force-push et sans pousser sur la branche principale.
5. Création d'une PR, ou réutilisation d'une PR ouverte sur cette même branche. Description attribuant l'amélioration à Ely, dans le cadre de son auto-amélioration.
6. Vérification de l'état OPEN, du SHA, de la branche source et de la branche cible de la PR distante.
7. Activation locale et redémarrage comme auparavant, seulement après publication confirmée.
8. Compte rendu avec le lien : **l'administrateur relit et fusionne la PR sur GitHub**. Aucune fusion GitHub automatique.

## Compte GitHub d'Ely (facultatif)

Sans réglage, Ely signe ses commits « Ely <ely@localhost> », une adresse rattachée à aucun compte, et ses PR sont
ouvertes par la connexion `gh` de l'administrateur : elles apparaissent à son nom et Ely ne figure pas parmi les
contributeurs du dépôt. Pour qu'elle ait sa propre identité :

1. Créer un compte GitHub pour Ely (GitHub autorise un compte « machine » gratuit par personne), avec une adresse
   e-mail que l'administrateur contrôle et la double authentification.
2. Sur le dépôt : Settings → Collaborators → Add people → ce compte, puis accepter l'invitation depuis ce compte.
3. Depuis ce compte : Settings → Developer settings → Personal access tokens → Tokens (classic), droit `repo`
   (les jetons « fine-grained » ne couvrent pas le dépôt personnel d'un autre compte). Choisir une échéance.
4. Dans `.env` : `ELY_GITHUB_TOKEN=<jeton>`, puis Réglages → Modèles → « Actualiser les modèles » (ou redémarrer).

Ely signe alors ses commits de l'adresse noreply de son compte (`<id>+<identifiant>@users.noreply.github.com`) et `gh`
agit avec son jeton : PR à son nom, que l'administrateur relit et fusionne. Ses commits fusionnés la font figurer
parmi les contributeurs. Le push de la branche passe toujours par l'accès git du Mac. Jeton refusé (expiré,
révoqué) : rien n'est commité ni activé, et le message indique de vérifier `ELY_GITHUB_TOKEN`. Une protection de la
branche principale exigeant une relecture garantit qu'Ely ne fusionne jamais elle-même.

## Prérequis et échecs

`git`, GitHub CLI `gh`, une connexion `gh auth login` et les droits de push/création de PR sur le dépôt origin sont nécessaires. Les tests GitHub sont simulés : ils n'envoient rien sur le réseau.

Si le push ou la création/vérification de PR échoue, le déploiement est refusé, sans activation ni redémarrage. Le commit et sa branche de sauvegarde locale permettent une reprise manuelle après réparation de l'accès. Une PR peut avoir été créée malgré une coupure réseau : vérifier la branche avant de recommencer.

Les leçons, souvenirs, identifiants, fichiers utilisateurs et bases de données ne sont pas publiés : seules les modifications de code et de documentation revues entrent dans une PR.

## Première livraison

Cette PR récupère aussi les correctifs testés du 2 octobre : réponse Telegram validée, outil Gmail `email_manage`, limitation à cinq lectures simultanées par recherche Gmail avec reprise sur 429, date du jour transmise au contrôleur. Elle ne restaure pas automatiquement tous les anciens commits et ne réintroduit pas le recalage navigateur différé.

La première livraison est publiée manuellement et reste à fusionner/mettre à jour sur l'installation ; le nouveau pipeline ne sera actif qu'une fois cette version chargée.
