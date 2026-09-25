"""Compétences de départ, partagées par tous les utilisateurs.

Ely les ressort automatiquement quand une demande s'y rapporte, puis les améliore
elle-même (skill_save) au fil des réussites.
"""
from __future__ import annotations

from ..db import db
from .store import save_skill

SKILLS = [
    ("Prendre un rendez-vous médical sur Doctolib",
     "Réserver un rendez-vous chez un médecin, dentiste, kiné ou spécialiste via Doctolib",
     """1. recall « médecin », « Doctolib », « adresse » : praticien habituel, ville, préférences d'horaires.
2. browser open https://www.doctolib.fr — le bandeau cookies est fermé automatiquement.
3. Si le praticien est connu : chercher son nom ; sinon chercher la spécialité + la ville et privilégier la proximité.
4. Vérifier la connexion (« Se connecter » visible ?) : credentials get doctolib, remplir, valider.
   Code reçu par SMS ou e-mail → ask_user (c'est le seul cas où demander).
5. Choisir le motif (souvent « Consultation » / « Première consultation »), puis le PREMIER créneau compatible avec
   l'agenda (calendar_list) et les préférences connues. Pas de demande de confirmation à l'utilisateur.
6. Cocher les conditions si demandé, confirmer, puis LIRE la page de confirmation (date, heure, adresse).
7. calendar_add avec le nom du praticien, l'adresse exacte et un rappel 60 min avant.
8. remember le praticien et son adresse si c'est nouveau.
Pièges : les créneaux se chargent après un court délai (wait 2 s puis snapshot) ; « Voir plus de disponibilités »
et la navigation semaine suivante sont des boutons ; certains praticiens exigent d'être déjà patient."""),
    ("Publier un post sur LinkedIn avec le navigateur",
     "Publier sur LinkedIn quand l'API n'est pas connectée",
     """1. Rédiger le post : accroche en 1re ligne, paragraphes courts, 3 à 5 hashtags à la fin, ton professionnel.
2. browser open https://www.linkedin.com/feed/ ; si page de connexion : credentials get linkedin (2FA → ask_user).
3. Cliquer « Commencer un post » (ou « Start a post »), taper le texte dans la zone d'édition.
4. Image : bouton média puis upload avec le chemin du fichier (image_generate si une image est demandée).
5. Cliquer « Publier » ; vérifier avec snapshot que le post apparaît dans le fil ou qu'un message de succès s'affiche.
Piège : la zone de texte est un éditeur riche (zone-texte) : type sans submit ; ne pas appuyer sur Entrée pour publier."""),
    ("Publier sur Facebook avec le navigateur",
     "Publier sur son profil ou une page Facebook quand l'API n'est pas configurée",
     """1. browser open https://www.facebook.com/ (ou l'URL de la page) ; connexion : credentials get facebook (2FA → ask_user).
2. Cliquer la zone « Que voulez-vous dire ? » / « Exprimez-vous », attendre l'ouverture de la fenêtre de publication.
3. Taper le texte ; photo : bouton « Photo/vidéo » puis upload.
4. Cliquer « Publier » ; vérifier que la publication apparaît en haut du profil ou de la page."""),
    ("Remplir une démarche administrative en ligne",
     "Formulaires administratifs : impots.gouv, ameli, CAF, ANTS, service-public, mutuelle, banque",
     """1. recall les informations personnelles utiles (adresse, numéros, situation) avant d'ouvrir le site.
2. Connexion via credentials (FranceConnect possible : choisir le fournisseur enregistré). Code SMS → ask_user.
3. Avancer étape par étape : lire chaque page (snapshot), remplir tous les champs obligatoires, joindre les
   justificatifs de l'espace de fichiers (upload), relire le récapitulatif avant de valider.
4. Télécharger l'accusé de réception ou le récapitulatif (il arrive dans Téléchargements) et donner le lien du fichier.
5. remember la référence du dossier."""),
]


def seed_skills() -> int:
    n = 0
    for name, desc, content in SKILLS:
        if not db.one("SELECT id FROM skills WHERE lower(name) = lower(?)", (name,)):
            save_skill(None, name, desc, content)
            n += 1
    return n
