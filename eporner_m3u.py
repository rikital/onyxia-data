#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eporner_m3u.py
--------------
Génère adult.m3u depuis l'API publique Eporner (aucune clé requise).

Le fichier est toujours écrit à la RACINE du dépôt, même si ce script
est placé dans un sous-dossier (scripts/, outils/, ...).
Aucune dépendance externe : bibliothèque standard uniquement.

Usage :
    python eporner_m3u.py
"""

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _racine_depot():
    """Remonte jusqu'à la racine du dépôt (.git) pour que adult.m3u
    tombe toujours au même endroit, quel que soit l'emplacement du script."""
    ici = Path(__file__).resolve().parent
    for d in (ici, *ici.parents):
        if (d / ".git").exists():
            return d
    return ici


# ------------------------------- CONFIG --------------------------------
RACINE       = _racine_depot()
OUT          = RACINE / "adult.m3u"

GROUPE       = "Zone 18@"            # valeur de group-title
DUREE_REELLE = True                  # True = durée en secondes | False = -1
QUALITES     = ("720", "480")        # essai dans l'ordre, 1re qui répond gagne
ORDRE        = "top-weekly"          # top-weekly | top-monthly | top-rated | latest
PAGES        = 2                     # pages par requête
PAR_PAGE     = 100                   # 100 = maximum côté Eporner

REQUETES = [
    "", "milf", "anal", "teen", "lesbian", "amateur", "mature",
    "big-tits", "asian", "latina", "ebony", "creampie",
]

VERIFIER     = True                  # teste chaque lien avant de l'écrire
THREADS      = 8                     # parallélisme de la vérification
PAUSE        = 1.5                   # secondes entre deux requêtes API
DELAI        = 15                    # secondes
# -----------------------------------------------------------------------

API = "https://www.eporner.com/api/v2/video/search/"
ENTETES = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Referer": "https://www.eporner.com/",
    "Accept": "application/json, video/*, */*",
}

REESSAYABLES = (429, 500, 502, 503, 504)


def _lire(url, etiquette="", essais=4):
    for n in range(essais):
        try:
            req = urllib.request.Request(url, method="GET", headers=ENTETES)
            with urllib.request.urlopen(req, timeout=DELAI) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in REESSAYABLES:
                attente = 2.0 * (n + 1)
                print(f"  [~] {etiquette} HTTP {e.code} -> pause {attente:.0f}s")
                time.sleep(attente)
                continue
            print(f"  [!] {etiquette} HTTP {e.code}")
            return b""
        except Exception as e:
            attente = 1.5 * (n + 1)
            print(f"  [~] {etiquette} {type(e).__name__} -> pause {attente:.0f}s")
            time.sleep(attente)
    print(f"  [!] {etiquette} abandon apres {essais} essais")
    return b""


def lien_vivant(url):
    """HEAD puis repli GET partiel. Reessaie sur 429/5xx et incident reseau."""
    for methode in ("HEAD", "GET"):
        for essai in range(3):
            try:
                req = urllib.request.Request(url, method=methode, headers=ENTETES)
                if methode == "GET":
                    req.add_header("Range", "bytes=0-2047")
                with urllib.request.urlopen(req, timeout=DELAI) as r:
                    return 200 <= r.status < 300
            except urllib.error.HTTPError as e:
                if e.code in (405, 501):      # methode refusee -> on passe a GET
                    break
                if e.code in REESSAYABLES and essai < 2:
                    time.sleep(1.0 * (essai + 1))
                    continue
                return False
            except Exception:
                if essai < 2:
                    time.sleep(0.8 * (essai + 1))
                    continue
                break
    return False


def _id_numerique(v):
    """L'id numérique se lit dans le chemin des vignettes : .../<numid>/12_240.jpg"""
    vignettes = [v.get("default_thumb") or {}] + list(v.get("thumbs") or [])
    for t in vignettes:
        m = re.search(r"/(\d+)/\d+_\d+\.jpg", t.get("src") or "")
        if m:
            return m.group(1)
    return None


def resoudre(hash_video, numid):
    """Renvoie la première URL de qualité qui répond, sinon None."""
    for q in QUALITES:
        url = f"https://www.eporner.com/dload/{hash_video}/{q}/{numid}-{q}p.mp4"
        if not VERIFIER or lien_vivant(url):
            return url
    return None


def collecter():
    vus, fiches, ok = set(), [], 0
    total = len(REQUETES) * PAGES
    for requete in REQUETES:
        for page in range(1, PAGES + 1):
            etiquette = f"{requete or '(top)'} p{page}"
            params = urllib.parse.urlencode({
                "query": requete, "per_page": PAR_PAGE, "page": page,
                "order": ORDRE, "lq": 1, "format": "json",
            })
            brut = _lire(f"{API}?{params}", etiquette)
            if not brut:
                continue
            try:
                videos = json.loads(brut).get("videos", [])
            except json.JSONDecodeError:
                print(f"  [!] {etiquette} : JSON illisible")
                continue
            ok += 1
            for v in videos:
                hash_video = (v.get("id") or "").strip()
                numid = _id_numerique(v)
                if not hash_video or not numid or hash_video in vus:
                    continue
                vus.add(hash_video)
                titre = re.sub(r"\s+", " ", v.get("title", "")).strip() or hash_video
                logo = (v.get("default_thumb") or {}).get("src", "") or ""
                if logo.startswith("//"):
                    logo = "https:" + logo
                fiches.append({
                    "hash": hash_video, "numid": numid, "titre": titre,
                    "duree": int(v.get("length_sec") or 0), "logo": logo,
                })
            time.sleep(PAUSE)
    print(f"  {ok}/{total} requetes API exploitables")
    return fiches


def main():
    print(f"Eporner -> {OUT}")
    fiches = collecter()
    print(f"  {len(fiches)} videos distinctes recuperees")
    if not fiches:
        print("  rien a ecrire, adult.m3u laisse intact")
        return 0

    if VERIFIER:
        print(f"  verification des liens ({THREADS} en parallele)...")
        with ThreadPoolExecutor(max_workers=THREADS) as ex:
            urls = list(ex.map(lambda f: resoudre(f["hash"], f["numid"]), fiches))
        gardees = [(f, u) for f, u in zip(fiches, urls) if u]
        print(f"  {len(gardees)}/{len(fiches)} liens valides")
    else:
        gardees = [(f, resoudre(f["hash"], f["numid"])) for f in fiches]

    if not gardees:
        print("  aucun lien valide, adult.m3u laisse intact")
        return 0

    tmp = OUT.parent / (OUT.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("#EXTM3U\n")
        for f, url in gardees:
            duree = f["duree"] if DUREE_REELLE else -1
            titre = f["titre"].replace('"', "'")
            fh.write(f'#EXTINF:{duree} tvg-logo="{f["logo"]}" '
                     f'group-title="{GROUPE}",{titre}\n{url}\n')
    tmp.replace(OUT)
    print(f"  ecrit : {OUT.name} ({len(gardees)} entrees)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
