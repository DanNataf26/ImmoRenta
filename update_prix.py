"""
Télécharge :
- le fichier officiel "Statistiques DVF" (data.gouv.fr / Etalab) : prix par
  commune et par département
- le fichier LOVAC (logements vacants, data.gouv.fr / Cerema) : vacance par
  commune et par département
- les 4 fichiers "Carte des loyers" (data.gouv.fr / ANIL), un par typologie
  (toutes tailles, T1-T2, T3+, maison) : loyer par commune. L'édition la plus
  récente est recherchée automatiquement chaque année (le jeu de données
  change d'adresse à chaque nouvelle édition annuelle) ; à défaut, on retombe
  sur l'édition 2025 dont les URLs sont connues et fiables.

... et régénère prix-communes.json avec toutes ces données, par commune ET
par département (moyenne pondérée par nombre d'observations pour les loyers).

Ce script est destiné à être exécuté automatiquement par GitHub Actions
(voir .github/workflows/update-prix.yml), mais peut aussi être lancé
manuellement : python update_prix.py
"""

import csv
import datetime
import gzip
import json
import math
import os
import statistics
import urllib.request
from collections import defaultdict

SOURCE_URL = "https://www.data.gouv.fr/api/1/datasets/r/851d342f-9c96-41c1-924a-11a7a7aae8a6"
TMP_CSV = "statistiques_dvf.csv"
LOVAC_URL = "https://www.data.gouv.fr/api/1/datasets/r/2e0417b4-902d-4c60-90e7-bf5df148cb87"
TMP_LOVAC = "lovac.csv"
DEST_JSON = "prix-communes.json"

# Repli garanti : URLs de l'édition 2025, vérifiées manuellement (voir
# historique du projet - typologies confirmées sur Bourg-en-Bresse, 01053).
LOYERS_FICHIERS_2025 = {
    "toutes": "https://www.data.gouv.fr/api/1/datasets/r/55b34088-0964-415f-9df7-d87dd98a09be",
    "t1t2": "https://www.data.gouv.fr/api/1/datasets/r/14a1fe11-b2d1-49b3-9f6b-83d12df9482c",
    "t3plus": "https://www.data.gouv.fr/api/1/datasets/r/5e3b28a4-cf56-43a3-ae79-43cceeb27f8c",
    "maison": "https://www.data.gouv.fr/api/1/datasets/r/129f764d-b613-44e4-952c-5ff50a8c9b73",
}
COMMUNE_REFERENCE = "01053"  # Bourg-en-Bresse : sert à identifier les typologies automatiquement

# Prix par typologie (T1-T2/T3+) calculé sur données DVF BRUTES, avec fenêtre
# adaptative : 1 an par défaut, élargie jusqu'à MAX_ANNEES si le nombre de
# ventes est insuffisant pour une commune/typologie donnée.
SEUIL_VENTES_TYPOLOGIE = 20
MAX_ANNEES_TYPOLOGIE = 5

# Résolutions de la grille géographique fine (secteur), en mètres, du plus
# fin au plus large - cascade utilisée par le simulateur au moment de la
# consultation (300m -> 500m -> 1km -> commune -> département).
RESOLUTIONS_SECTEUR = [300, 500, 1000]
REF_LAT_FRANCE = 46.5  # latitude de référence pour la conversion degrés/mètres


def grid_id(lat, lon, taille_m):
    """Identifiant de case de grille (cohérent avec l'équivalent JS côté
    simulateur - même latitude de référence, mêmes formules)."""
    lat_deg = taille_m / 111320.0
    lon_deg = taille_m / (111320.0 * math.cos(math.radians(REF_LAT_FRANCE)))
    return f"{math.floor(lat / lat_deg)}_{math.floor(lon / lon_deg)}"


def url_existe(url):
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "immo-renta/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except Exception:
        return False


def annees_dvf_disponibles(max_annees=MAX_ANNEES_TYPOLOGIE):
    """Détecte les années DVF brutes disponibles (adresse stable, mise à jour
    en continu), de la plus récente à la plus ancienne."""
    annee_courante = datetime.date.today().year
    trouvees = []
    for annee in range(annee_courante, annee_courante - 8, -1):
        url = f"https://files.data.gouv.fr/geo-dvf/latest/csv/{annee}/full.csv.gz"
        if url_existe(url):
            trouvees.append(annee)
        if len(trouvees) >= max_annees:
            break
    return trouvees


def traiter_annee_dvf_brut(annee, communes_prix, dept_prix, locked_communes, locked_dept, secteurs_prix, locked_secteurs):
    """Télécharge et traite une année de DVF brut national, en ne retenant
    que les ventes d'un seul appartement (dépendances tolérées à part), et
    alimente les compteurs par (commune/département, typologie) ET par
    secteur géographique fin (300m/500m/1km, typologie)."""
    url = f"https://files.data.gouv.fr/geo-dvf/latest/csv/{annee}/full.csv.gz"
    tmp = f"dvf_full_{annee}.csv.gz"
    print(f"Téléchargement DVF brut national {annee}...")
    urllib.request.urlretrieve(url, tmp)
    print("Téléchargé.")

    # Passe 1 : compter les lots d'habitation par mutation (léger en mémoire)
    compte = {}
    with gzip.open(tmp, "rt", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["type_local"] in ("Appartement", "Maison"):
                mid = row["id_mutation"]
                compte[mid] = compte.get(mid, 0) + 1

    # Passe 2 : ne garder que les appartements en mutation à un seul lot
    with gzip.open(tmp, "rt", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["type_local"] != "Appartement":
                continue
            if compte.get(row["id_mutation"], 0) != 1:
                continue
            try:
                surface = float(row["surface_reelle_bati"] or 0)
                valeur = float(row["valeur_fonciere"] or 0)
                pieces = int(float(row["nombre_pieces_principales"] or 0))
                lat = float(row["latitude"] or 0)
                lon = float(row["longitude"] or 0)
            except ValueError:
                continue
            if surface <= 0 or valeur <= 0 or pieces <= 0:
                continue
            prix_m2 = valeur / surface
            if prix_m2 < 500 or prix_m2 > 25000:
                continue

            typo = "t1t2" if pieces <= 2 else "t3plus"
            code = row["code_commune"]

            key_c = (code, typo)
            if key_c not in locked_communes:
                communes_prix[key_c].append(prix_m2)

            dept = code_to_dept(code)
            if dept:
                key_d = (dept, typo)
                if key_d not in locked_dept:
                    dept_prix[key_d].append(prix_m2)

            if lat and lon:
                for taille_m in RESOLUTIONS_SECTEUR:
                    cell = grid_id(lat, lon, taille_m)
                    key_s = (taille_m, cell, typo)
                    if key_s not in locked_secteurs:
                        secteurs_prix[key_s].append(prix_m2)

    os.remove(tmp)


def to_int(v):
    return int(v) if v not in (None, "", "0") else None


def to_int_lovac(v):
    v = (v or "").strip()
    if v in ("", "s"):
        return None
    try:
        return int(v)
    except ValueError:
        return None


def to_float_fr(v):
    """Nombre au format français (virgule décimale) -> float, ou None."""
    v = (v or "").strip()
    if not v:
        return None
    try:
        return float(v.replace(",", "."))
    except ValueError:
        return None


def code_to_dept(code):
    """Déduit le code département à partir d'un code commune INSEE."""
    if not code:
        return None
    if code.startswith("97") or code.startswith("98"):
        return code[:3]
    return code[:2]


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "immo-renta-simulateur/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def lire_commune_reference(url):
    """Télécharge un fichier de loyer et renvoie (loyer, nbobs_com) pour la
    commune de référence, ou None si absent/illisible."""
    tmp = "_tmp_ref.csv"
    urllib.request.urlretrieve(url, tmp)
    with open(tmp, encoding="latin-1") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            if row.get("INSEE_C") == COMMUNE_REFERENCE:
                loyer = to_float_fr(row.get("loypredm2"))
                nb = to_int(row.get("nbobs_com")) or 0
                if loyer is not None:
                    return loyer, nb
    return None


def identifier_typologies(urls):
    """À partir de 4 URLs de fichiers "Carte des loyers", détermine
    automatiquement laquelle correspond à quelle typologie, en se basant sur
    la commune de référence :
    - le fichier avec le MOINS d'observations = maison
    - parmi les 3 restants (appartements), celui avec le PLUS d'observations
      = toutes tailles confondues (jeu de données le plus large)
    - parmi les 2 restants, le loyer le plus élevé = T1-T2, le plus bas = T3+
    Renvoie {typologie: url} ou None si l'identification échoue.
    """
    mesures = []
    for url in urls:
        r = lire_commune_reference(url)
        if r is None:
            return None
        loyer, nb = r
        mesures.append({"url": url, "loyer": loyer, "nb": nb})

    if len(mesures) != 4:
        return None

    mesures.sort(key=lambda m: m["nb"])
    maison = mesures[0]
    reste = mesures[1:]
    reste.sort(key=lambda m: m["nb"], reverse=True)
    toutes = reste[0]
    appts = reste[1:]
    appts.sort(key=lambda m: m["loyer"], reverse=True)
    t1t2, t3plus = appts[0], appts[1]

    return {
        "toutes": toutes["url"],
        "t1t2": t1t2["url"],
        "t3plus": t3plus["url"],
        "maison": maison["url"],
    }


def trouver_edition_loyers():
    """Cherche la dernière édition "Carte des loyers" disponible sur
    data.gouv.fr (nouvelle adresse à chaque nouvelle édition annuelle), et
    identifie automatiquement les 4 fichiers par typologie. Retombe sur
    l'édition 2025 (connue et fiable) si rien de plus récent n'est trouvé ou
    reconnu.
    """
    annee_courante = datetime.date.today().year
    for annee in range(annee_courante + 1, 2024, -1):
        slug = f"carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-{annee}"
        try:
            data = get_json(f"https://www.data.gouv.fr/api/1/datasets/{slug}/")
        except Exception:
            continue

        resources = [
            r for r in data.get("resources", [])
            if (r.get("format") or "").lower() == "csv"
            and (r.get("filesize") or 0) > 1_000_000  # écarte d'éventuels petits fichiers annexes
        ]
        if len(resources) != 4:
            continue

        urls = [r["url"] for r in resources]
        typologies = identifier_typologies(urls)
        if typologies:
            print(f"Édition {annee} de la Carte des loyers trouvée et identifiée automatiquement.")
            return typologies, annee

    print("Aucune édition plus récente trouvée/reconnue : repli sur l'édition 2025 (connue).")
    return LOYERS_FICHIERS_2025, 2025


result = {}
departements = {}

# ---------------------------------------------------------------------------
# 1) DVF : prix par commune ET par département (déjà agrégé dans le fichier)
# ---------------------------------------------------------------------------
print("Téléchargement du fichier source DVF...")
urllib.request.urlretrieve(SOURCE_URL, TMP_CSV)
print("Téléchargé.")

with open(TMP_CSV, encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        echelle = row["echelle_geo"]
        if echelle not in ("commune", "departement"):
            continue

        code = row["code_geo"]
        entry = {"nom": row["libelle_geo"]}

        nb_appt = to_int(row["nb_ventes_whole_appartement"])
        if nb_appt:
            entry["appartement"] = {
                "nb": nb_appt,
                "moyenne": to_int(row["moy_prix_m2_whole_appartement"]),
                "mediane": to_int(row["med_prix_m2_whole_appartement"]),
            }

        nb_maison = to_int(row["nb_ventes_whole_maison"])
        if nb_maison:
            entry["maison"] = {
                "nb": nb_maison,
                "moyenne": to_int(row["moy_prix_m2_whole_maison"]),
                "mediane": to_int(row["med_prix_m2_whole_maison"]),
            }

        nb_local = to_int(row["nb_ventes_whole_local"])
        if nb_local:
            entry["local"] = {
                "nb": nb_local,
                "moyenne": to_int(row["moy_prix_m2_whole_local"]),
                "mediane": to_int(row["med_prix_m2_whole_local"]),
            }

        if "appartement" in entry or "maison" in entry or "local" in entry:
            if echelle == "commune":
                result[code] = entry
            else:
                departements[code] = entry

print(f"{len(result)} communes et {len(departements)} départements (prix DVF) traités.")

# ---------------------------------------------------------------------------
# 2) LOVAC : vacance par commune, agrégée aussi par département (somme des
#    numérateurs/dénominateurs, plus correct statistiquement qu'une moyenne
#    de pourcentages)
# ---------------------------------------------------------------------------
print("Téléchargement du fichier source LOVAC (vacance locative)...")
urllib.request.urlretrieve(LOVAC_URL, TMP_LOVAC)
print("Téléchargé.")

dept_vacants = {}
dept_total = {}

with open(TMP_LOVAC, encoding="latin-1") as f:
    reader = csv.DictReader(f, delimiter=";")
    for row in reader:
        code = row.get("CODGEO_26")
        vacants = to_int_lovac(row.get("pp_vacant_25"))
        total = to_int_lovac(row.get("ff_pp_total_25"))
        if vacants is None or not total:
            continue

        taux = round(vacants / total * 100, 1)
        if code not in result:
            result[code] = {"nom": row["LIBGEO_26"]}
        result[code]["vacance"] = {"taux": taux, "millesime": 2025}

        dept = code_to_dept(code)
        if dept:
            dept_vacants[dept] = dept_vacants.get(dept, 0) + vacants
            dept_total[dept] = dept_total.get(dept, 0) + total

for dept, total in dept_total.items():
    if total > 0 and dept in departements:
        taux = round(dept_vacants[dept] / total * 100, 1)
        departements[dept]["vacance"] = {"taux": taux, "millesime": 2025}

print(f"Vacance agrégée pour {len(dept_total)} départements.")

# ---------------------------------------------------------------------------
# 3) Carte des loyers, PAR COMMUNE, une fois par typologie
#    (toutes tailles / T1-T2 / T3+ / maison). Édition la plus récente trouvée
#    automatiquement. Agrégation par département en moyenne pondérée par
#    nombre d'observations.
# ---------------------------------------------------------------------------
LOYERS_FICHIERS, millesime_loyers = trouver_edition_loyers()

dept_loyer_pondere = {t: {} for t in LOYERS_FICHIERS}
dept_loyer_poids = {t: {} for t in LOYERS_FICHIERS}

for typologie, url in LOYERS_FICHIERS.items():
    print(f"Téléchargement Carte des loyers {millesime_loyers} - {typologie}...")
    tmp = f"loyers_{typologie}.csv"
    urllib.request.urlretrieve(url, tmp)
    print("Téléchargé.")

    with open(tmp, encoding="latin-1") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            code = row.get("INSEE_C")
            loyer = to_float_fr(row.get("loypredm2"))
            nb = to_int(row.get("nbobs_com")) or 0
            r2 = to_float_fr(row.get("R2_adj"))
            if not code or loyer is None:
                continue

            if code not in result:
                result[code] = {"nom": row.get("LIBGEO", "")}
            result[code].setdefault("loyer", {})
            result[code]["loyer"][typologie] = {
                "valeur": round(loyer, 2),
                "nb": nb,
                "r2": round(r2, 3) if r2 is not None else None,
            }

            dept = code_to_dept(code)
            if dept:
                poids = nb if nb > 0 else 1
                dept_loyer_pondere[typologie][dept] = (
                    dept_loyer_pondere[typologie].get(dept, 0) + loyer * poids
                )
                dept_loyer_poids[typologie][dept] = (
                    dept_loyer_poids[typologie].get(dept, 0) + poids
                )

for typologie in LOYERS_FICHIERS:
    for dept, poids in dept_loyer_poids[typologie].items():
        if poids > 0 and dept in departements:
            departements[dept].setdefault("loyer", {})
            departements[dept]["loyer"][typologie] = round(
                dept_loyer_pondere[typologie][dept] / poids, 2
            )

print(f"Loyers {millesime_loyers} par typologie intégrés (commune + département).")

# ---------------------------------------------------------------------------
# 4) Prix par typologie (T1-T2 / T3+), calculé sur DVF BRUT (transaction par
#    transaction), avec fenêtre adaptative : 1 an par défaut, élargie jusqu'à
#    5 ans si moins de 20 ventes pour une commune/typologie donnée.
# ---------------------------------------------------------------------------
communes_prix_typo = defaultdict(list)
dept_prix_typo = defaultdict(list)
secteurs_prix_typo = defaultdict(list)  # (taille_m, cell, typo) -> [prix_m2, ...]
locked_communes = set()
locked_dept = set()
locked_secteurs = set()
fenetre_communes = {}
fenetre_dept = {}
fenetre_secteurs = {}

annees_dvf = annees_dvf_disponibles()
print("Années DVF brutes disponibles utilisées :", annees_dvf)

for i, annee in enumerate(annees_dvf, start=1):
    traiter_annee_dvf_brut(
        annee, communes_prix_typo, dept_prix_typo, locked_communes, locked_dept,
        secteurs_prix_typo, locked_secteurs,
    )

    nouveaux_c = 0
    for key, prix in communes_prix_typo.items():
        if key not in locked_communes and len(prix) >= SEUIL_VENTES_TYPOLOGIE:
            locked_communes.add(key)
            fenetre_communes[key] = i
            nouveaux_c += 1
    nouveaux_d = 0
    for key, prix in dept_prix_typo.items():
        if key not in locked_dept and len(prix) >= SEUIL_VENTES_TYPOLOGIE:
            locked_dept.add(key)
            fenetre_dept[key] = i
            nouveaux_d += 1
    nouveaux_s = 0
    for key, prix in secteurs_prix_typo.items():
        if key not in locked_secteurs and len(prix) >= SEUIL_VENTES_TYPOLOGIE:
            locked_secteurs.add(key)
            fenetre_secteurs[key] = i
            nouveaux_s += 1
    print(
        f"Après {i} an(s) : {len(locked_communes)} paires commune/typologie fiables "
        f"({nouveaux_c} nouvelles), {len(locked_dept)} départements/typologie fiables, "
        f"{len(locked_secteurs)} secteurs/typologie fiables ({nouveaux_s} nouveaux)."
    )

for key in communes_prix_typo:
    fenetre_communes.setdefault(key, len(annees_dvf))
for key in dept_prix_typo:
    fenetre_dept.setdefault(key, len(annees_dvf))
for key in secteurs_prix_typo:
    fenetre_secteurs.setdefault(key, len(annees_dvf))

for (code, typo), prix in communes_prix_typo.items():
    if not prix:
        continue
    if code not in result:
        result[code] = {"nom": ""}
    result[code].setdefault("appartement_typologie", {})
    result[code]["appartement_typologie"][typo] = {
        "mediane": round(statistics.median(prix)),
        "nb": len(prix),
        "fenetre_annees": fenetre_communes[(code, typo)],
        "fiable": len(prix) >= SEUIL_VENTES_TYPOLOGIE,
    }

for (dept, typo), prix in dept_prix_typo.items():
    if not prix or dept not in departements:
        continue
    departements[dept].setdefault("appartement_typologie", {})
    departements[dept]["appartement_typologie"][typo] = {
        "mediane": round(statistics.median(prix)),
        "nb": len(prix),
        "fenetre_annees": fenetre_dept[(dept, typo)],
        "fiable": len(prix) >= SEUIL_VENTES_TYPOLOGIE,
    }

# Secteurs : seules les cases FIABLES (>= seuil) sont exportées, pour ne pas
# alourdir le fichier avec des cases à l'échantillon trop faible (le repli
# sur la commune/département prend alors le relais côté simulateur).
secteurs = {str(t): {} for t in RESOLUTIONS_SECTEUR}
nb_secteurs_fiables = 0
for (taille_m, cell, typo), prix in secteurs_prix_typo.items():
    if len(prix) < SEUIL_VENTES_TYPOLOGIE:
        continue
    secteurs[str(taille_m)].setdefault(cell, {})
    secteurs[str(taille_m)][cell][typo] = {
        "mediane": round(statistics.median(prix)),
        "nb": len(prix),
        "fenetre_annees": fenetre_secteurs[(taille_m, cell, typo)],
    }
    nb_secteurs_fiables += 1

print(
    f"Prix par typologie calculé pour {len(communes_prix_typo)} paires commune/typologie, "
    f"{len(dept_prix_typo)} paires département/typologie, "
    f"{nb_secteurs_fiables} paires secteur/typologie fiables sur {len(secteurs_prix_typo)} calculées "
    f"({sum(len(v) for v in secteurs.values())} cases de secteur au total)."
)

# ---------------------------------------------------------------------------
result["departements"] = departements
result["secteurs"] = secteurs
result["_millesime_loyers"] = millesime_loyers

with open(DEST_JSON, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

print(f"{len(result) - 2} communes + {len(departements)} départements exportés dans {DEST_JSON}")
