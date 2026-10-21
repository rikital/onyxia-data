#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eporner_m3u.py
--------------
Génère adult.m3u depuis l'API publique Eporner (aucune clé requise).

Le fichier est écrit à la racine du dépôt, à côté des autres .m3u.
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

# ------------------------------- CONFIG --------------------------------
RACINE       = Path(__file__).resolve().parent
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
THREADS      = 12                    # parallélisme de la vérification
DELAI        = 10                    # secondes
# -----------------------------------------------------------------------

API = "https://www.eporner.com/api/v2/video/search/"
ENTETES = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Referer": "https://www.eporner.com/",
}


def _lire(url, essais=3):
    for n in range(essais):
        try:
            req = urllib.request.Request(url, method="GET", headers=ENTETES)
            with urllib.request.urlopen(req, timeout=DELAI) as r:
                return r.read()
        except urllib.error.HTTPError:
            return b""
        except Exception:
            time.sleep(0.7 * (n + 1))
    return b""


def lien_vivant(url):
    """HEAD puis repli GET partiel. True si le serveur accepte le lien."""
    for methode in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=methode, headers=ENTETES)
            if methode == "GET":
                req.add_header("Range", "bytes=0-2047")
            with urllib.request.urlopen(req, timeout=DELAI) as r:
                return 200 <= r.status < 300
        except urllib.error.HTTPError as e:
            if e.code in (405, 501):     # HEAD refusé -> on retente en GET
                continue
            return False
        except Exception:
            continue
    return False


def resoudre(vid, slug):
    """Renvoie la première URL de qualité qui répond, sinon None."""
    for q in QUALITES:
        url = f"https://www.eporner.com/dload/{slug}/{q}/{vid}-{q}p.mp4"
        if not VERIFIER or lien_vivant(url):
            return url
    return None


def collecter():
    vus, fiches = set(), []
    for requete in REQUETES:
        for page in range(1, PAGES + 1):
            params = urllib.parse.urlencode({
                "query": requete, "per_page": PAR_PAGE, "page": page,
                "order": ORDRE, "lq": 1, "format": "json",
            })
            brut = _lire(f"{API}?{params}")
            if not brut:
                print(f"  [!] {requete or '(top)'} p{page} : pas de reponse")
                continue
            try:
                videos = json.loads(brut).get("videos", [])
            except json.JSONDecodeError:
                print(f"  [!] {requete or '(top)'} p{page} : JSON illisible")
                continue
            for v in videos:
                m = re.search(r"video-(\d+)/([^/]+)", v.get("url", ""))
                if not m:
                    continue
                vid, slug = m.group(1), m.group(2)
                if vid in vus:
                    continue
                vus.add(vid)
                titre = re.sub(r"\s+", " ", v.get("title", "")).strip() or vid
                logo = (v.get("thumbs") or [{}])[0].get("src", "") or ""
                if logo.startswith("//"):
                    logo = "https:" + logo
                fiches.append({
                    "vid": vid, "slug": slug, "titre": titre,
                    "duree": int(v.get("length_sec") or 0), "logo": logo,
                })
            time.sleep(0.3)
    return fiches


def main():
    print("Eporner -> adult.m3u")
    fiches = collecter()
    print(f"  {len(fiches)} videos distinctes recuperees")
    if not fiches:
        print("  rien a ecrire, adult.m3u laisse intact")
        return 0

    if VERIFIER:
        print(f"  verification des liens ({THREADS} en parallele)...")
        with ThreadPoolExecutor(max_workers=THREADS) as ex:
            urls = list(ex.map(lambda f: resoudre(f["vid"], f["slug"]), fiches))
        gardees = [(f, u) for f, u in zip(fiches, urls) if u]
        print(f"  {len(gardees)}/{len(fiches)} liens valides")
    else:
        gardees = [(f, resoudre(f["vid"], f["slug"])) for f in fiches]

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
