# Extraction et preprocessing ETICS

Deux étapes transforment les fichiers déjà collectés, orchestrées par `src/pipeline.py` :

```text
data/raw/ -> extraction PDF/HTML -> data/extracted/ -> preprocessing -> data/processed/
```

Les scripts n'écrivent jamais dans `data/raw/` et ne lisent pas `metadata.json` :
les noms de dossiers suffisent ici pour identifier les cas. Les champs métier sont
extraits exclusivement des TXT, séparément pour les deux sources.

## Structure du code

```text
src/
├── __init__.py
├── pipeline.py
├── pipeline_io.py
├── extraction/
│   ├── __init__.py
│   ├── extract_pdf.py
│   ├── extract_html.py
│   └── extract.py
├── preprocessing/
│   ├── __init__.py
│   ├── clean.py
│   ├── extract_fields.py
│   └── preprocess.py
└── scoring/
    ├── __init__.py
    └── score.py
```

`pipeline.py` expose `run_pipeline(case_id, overwrite=False)`, qui appelle dans
l'ordre `extract_case()`, `preprocess_case()` et `score_case()` pour un dossier.
Une erreur empêche l'exécution des étapes suivantes de ce dossier. `main()` gère
les arguments du terminal et la boucle sur tous les cas ou sur la sélection.
`preprocess.py` traite un seul dossier et ne contient plus de point d'entrée en
ligne de commande. `score.py` contient uniquement un point d'appel qui signale
que le scoring reste à implémenter ; aucun score n'est produit.

`pipeline_io.py` regroupe les chemins et les écritures afin de protéger les sources
et d'appliquer les mêmes conventions dans les deux étapes. Les autres modules
préexistants du dépôt sont conservés. Le script de collecte préexistant
`data/extracted/extract.py` n'est pas utilisé par ces commandes.

Le dossier réel `001_Assentus-Food-Sigdal_Sigdal-Herbes-Sel-De-Mer-190G` devient
le cas `001`. Un dossier simplement nommé `001` fonctionne aussi. Les zéros sont
conservés. Deux dossiers donnant le même identifiant provoquent une erreur.

```text
data/extracted/001/
├── rappelconso.txt
└── communication_entreprise.txt

data/processed/
└── 001.json
```

## Installation et commandes PowerShell

Exécuter les commandes depuis la racine du dépôt. Python 3.11 ou supérieur est
recommandé. Si `.venv` n'existe pas encore :

```powershell
python -m venv .venv
```

Installer les deux dépendances nécessaires à cette partie :

```powershell
.\.venv\Scripts\python.exe -m pip install "PyMuPDF>=1.24.11,<2" "beautifulsoup4>=4.12,<5"
```

Elles sont aussi déclarées dans `requirements.txt`. Les autres imports utilisés
par les nouveaux scripts appartiennent à la bibliothèque standard Python.

Pour un dossier :

```powershell
.\.venv\Scripts\python.exe -m src.pipeline --case 001
```

Pour tous les dossiers :

```powershell
.\.venv\Scripts\python.exe -m src.pipeline
```

Pour choisir plusieurs cas, répéter l'option : `--case 001 --case 065`.
Un nom complet de dossier est également accepté.

Un résultat existant est ignoré par défaut. Pour le régénérer explicitement :

```powershell
.\.venv\Scripts\python.exe -m src.pipeline --case 001 --overwrite
```

`--overwrite` régénère les TXT et le JSON du cas. L'extraction seule reste
disponible avec `python -m src.extraction.extract --case 001`. Si les TXT ont été
actualisés séparément, utiliser `--overwrite` pour régénérer aussi le JSON.
Le code de sortie est `1` en cas d'erreur, `0` sinon ; `0` ne signifie pas qu'un
score a été calculé. Les erreurs sont affichées et n'interrompent pas les autres cas.

## Extraction : fonctions et comportement

| Fonction | Rôle |
| --- | --- |
| `extract_pdf(path)` | Ouvre le PDF avec PyMuPDF et lit ses pages dans l'ordre. `sort=True` réordonne le texte selon sa position. Les pages sont séparées par `\f`. Une page sans texte est signalée ; un PDF entièrement vide de texte provoque une erreur. |
| `extract_html(path)` | Lit les octets du HTML avec BeautifulSoup, retire scripts/styles et éléments explicitement cachés, utilise la zone `main` lorsqu'elle existe, puis conserve le texte et les tableaux. Les cellules sont séparées par des tabulations. |
| `clean_reading_text(text)` | Normalise Unicode, fins de ligne et espaces de lecture. Conserve les accents, chiffres, paragraphes, tabulations et séparateurs de pages. |
| `extract_case(case_id, folder, overwrite)` | Parcourt les PDF/HTML du cas et écrit chaque résultat sous le même nom, avec l'extension `.txt`. Détecte les collisions, par exemple un PDF et un HTML portant le même nom. |
| `extraction.extract.main()` | Lit les options du terminal et lance l'extraction sur les cas sélectionnés. |

Les extensions PDF, HTML et HTM sont reconnues sans distinction de casse. Les
images et les JSON sont ignorés. Aucun champ métier n'est calculé à cette étape.
Les PDF supplémentaires sont également extraits ; le preprocessing utilise
uniquement `rappelconso.txt` et `communication_entreprise.txt`.

## Preprocessing : fonctions et comportement

| Fonction | Rôle |
| --- | --- |
| `clean_text(text)` | Prépare le texte pour les règles : espaces normalisés et pages transformées en limites de paragraphes. Les tabulations des tableaux restent disponibles. |
| `fold(text)` | Fournit une copie sans accents et en minuscules pour comparer les libellés ; les valeurs retournées gardent leur orthographe source. |
| `unique(values)` | Retire les valeurs vides et les doublons en conservant l'ordre. |
| `empty_fields(communication)` | Crée tous les champs du schéma demandé, avec `null` ou `[]`. |
| `single_or_many(values)` | Renvoie `null`, une chaîne, ou une liste si plusieurs valeurs distinctes sont trouvées. |
| `labelled_values(text, labels)` | Lit un libellé connu et ses lignes de contenu jusqu'au champ ou paragraphe suivant. Sa fonction interne `flush()` enregistre le champ en cours. |
| `table_identifiers(text)` | Repère les colonnes GTIN/EAN et lots dans les tableaux du TXT. |
| `extract_gtins(text, labelled)` | Lit les codes explicitement associés à un libellé ou une colonne GTIN/EAN/code-barres. Les conserve en chaînes de 8, 12, 13 ou 14 chiffres, sans perdre les zéros. |
| `extract_lots(text, labelled)` | Lit les lots explicitement désignés. Ne réinterprète pas les dates DLC/DLUO comme des lots. Ignore les renvois tels que « voir liste… ». |
| `matching_paragraphs(text, pattern)` | Retourne les paragraphes source contenant une expression reconnue, en joignant seulement leurs lignes. |
| `extract_rappelconso_fields(text)` | Remplit les champs officiels à partir des libellés et de l'en-tête « entreprise rappelle produit ». |
| `extract_communication_fields(text)` | Analyse uniquement la communication. Complète les libellés par des expressions explicites pour le motif, le risque et les recommandations. |
| `read_text(path)` | Lit un TXT UTF-8 et signale les sources absentes ou vides. |
| `preprocess_case(case_id, folder, overwrite)` | Lit les deux TXT et écrit un JSON réunissant leurs résultats indépendants. |
| `pipeline.run_pipeline(case_id, overwrite)` | Enchaîne extraction, preprocessing et point d'appel du scoring pour un cas. |
| `pipeline.main()` | Lit les options du terminal, parcourt les cas sélectionnés et comptabilise les erreurs. |
| `score_case(case_id)` | Signale que le scoring n'est pas encore implémenté, sans produire de score. |

Les fonctions communes sont les suivantes :

| Fonction | Rôle |
| --- | --- |
| `case_id_from_folder(folder)` | Extrait le préfixe numérique du dossier ou conserve son nom en l'absence de préfixe. |
| `case_folders(root, selected)` | Liste les dossiers, applique la sélection et refuse les identifiants en double ou les sélections inexistantes. |
| `write_output(path, content, root, overwrite)` | Écrit en UTF-8 par fichier temporaire puis remplacement. Vérifie que la destination reste dans la zone de sortie autorisée, même en présence d'un lien symbolique. |

## Règles de données et limites

- Pour suivre le schéma fourni, `gtin`, `lots` et `conduites_a_tenir` sont toujours
  des listes, vides si aucune valeur n'est reconnue. Les autres champs valent
  `null`, une chaîne ou une liste de chaînes lorsqu'il y a plusieurs valeurs.
- Une valeur `null` signifie **non reconnue par les règles** : elle ne prouve pas
  que l'information est absente du document. La rédaction libre demande parfois
  une relecture ou une nouvelle règle.
- Les valeurs viennent du texte de chaque source. Aucun champ de la communication
  n'est recopié depuis RappelConso, et aucune valeur métier de `metadata.json`
  n'est utilisée.
- Les dates sont conservées dans leur forme source. Les numéros de fiche et
  d'entreprise ne sont pas déduits d'une URL ou d'une connaissance extérieure.
- Le découpage des valeurs s'appuie sur les libellés, paragraphes et séparateurs
  explicites. Les paragraphes de motif/risque/recommandations peuvent contenir
  davantage de contexte que le seul champ recherché. Une relecture reste utile.
- Les PDF scannés nécessitent un OCR, non inclus ici. PyMuPDF peut aussi produire
  des défauts de lecture sur des mises en page complexes. Le HTML n'exécute pas
  JavaScript et ne lit pas le texte contenu dans les images.
- Si un seul des deux TXT manque, le JSON est produit avec les champs de cette
  source vides et un avertissement dans le terminal. Si les deux manquent, le cas
  est signalé en erreur sans créer de JSON.

## Exemple réel et vérification

Le fichier [data/processed/001.json](../data/processed/001.json) a été généré par
ces scripts à partir des deux PDF du dossier Sigdal. Les textes intermédiaires
sont dans [data/extracted/001](../data/extracted/001).

Dans cet exemple, `numero_fiche` reste `null` car le numéro n'a pas été trouvé
dans le texte de l'affichette. Le produit de la communication reste `null` car
son titre libre n'est pas reconnu comme un libellé produit par cette première
version des règles. Ses dates DLUO restent dans le TXT, sans devenir des lots.

Les commandes ont été exécutées sur deux cas : `001` (PDF/PDF) et `065`
(PDF/HTML). Les empreintes SHA-256 des huit fichiers bruts de ces dossiers,
dont leurs deux `metadata.json`, étaient identiques avant et après exécution.
Des contrôles ciblés couvrent aussi les champs absents, les tableaux, les valeurs
multiples, les textes multilignes, la restriction des destinations et les relances
sans écrasement. Les autres dossiers n'ont pas été traités pendant cette mise en place.

Références des bibliothèques : [extraction PyMuPDF](https://pymupdf.readthedocs.io/en/latest/recipes-text.html)
et [documentation BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/bs4/doc/).
