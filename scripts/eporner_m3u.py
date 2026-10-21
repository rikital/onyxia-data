#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eporner_m3u.py
--------------
Génère adult.m3u depuis les pages publiques d'Eporner (sans clé, sans API).

Le site filtre les IP de datacenter : on garde donc une session (cookies
conservés entre les requêtes) et des en-têtes de navigateur complets.

Le fichier est écrit dans data/adult.m3u si le dossier data/ existe,
sinon à la racine du dépôt.

Usage :
    python eporner_m3u.py
"""

import http.cookiejar
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _racine_depot():
    """Remonte jusqu'à la racine du dépôt (.git)."""
    ici = Path(__file__).resolve().parent
    for d in (ici, *ici.parents):
        if (d / ".git").exists():
            return d
    return ici


# ------------------------------- CONFIG --------------------------------
RACINE       = _racine_depot()
DOSSIER_DATA = RACINE / "data"
OUT          = (DOSSIER_DATA if DOSSIER_DATA.is_dir() else RACINE) / "adult.m3u"

GROUPE       = "Zone 18@"            # valeur de group-title
DUREE_REELLE = True                  # True = durée réelle | False = -1
QUALITES     = ("720", "480")        # essai dans l'ordre, 1re qui répond gagne
PAGES        = 4                     # pages par source

SOURCES = [
    "https://www.eporner.com/",                  # les plus récentes
    "https://www.eporner.com/most-viewed/",      # les plus vues
    "https://www.eporner.com/top-rated/",        # les mieux notées
    "https://www.eporner.com/best-videos/",      # la sélection
]

VERIFIER     = True
THREADS      = 8
PAUSE        = 1.0                   # secondes entre deux pages
DELAI        = 15
# -----------------------------------------------------------------------

ENTETES = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    "Accept-Encoding": "identity",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Referer": "https://www.eporner.com/",
    "Connection": "keep-alive",
}

# Session partagée : les cookies reçus au premier appel sont renvoyés ensuite.
POT_A_COOKIES = http.cookiejar.CookieJar()
OUVREUR = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(POT_A_COOKIES)
)

REESSAYABLES = (429, 500, 502, 503, 504)

BLOC_VIDEO = re.compile(
    r'<a[^>]+href="[^"]*?/video-([A-Za-z0-9]+)/([^/"]+)/"[^>]*>(.*?)</a>',
    re.S,
)


def _lire(url, etiquette="", essais=3):
    """GET avec session, en-têtes navigateur et réessais. Rend le texte."""
    for n in range(essais):
        try:
            req = urllib.request.Request(url, method="GET", headers=ENTETES)
            with OUVREUR.open(req, timeout=DELAI) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in REESSAYABLES and n < essais - 1:
                time.sleep(2.0 * (n + 1))
                continue
            print(f"  [!] {etiquette} HTTP {e.code}")
            return ""
        except Exception as e:
            if n < essais - 1:
                time.sleep(1.5 * (n + 1))
                continue
            print(f"  [!] {etiquette} {type(e).__name__}: {e}")
    return ""


def _trouver_videos(html):
    """Rend (hash, id numérique, titre, durée, vignette) pour chaque bloc vidéo."""
    trouvees = []
    for bloc in BLOC_VIDEO.finditer(html):
        hash_video, slug, interieur = bloc.group(1), bloc.group(2), bloc.group(3)
        m_src = re.search(r'src="([^"]+)"', interieur)
        m_alt = re.search(r'alt="([^"]*)"', interieur)
        if not m_src:
            continue
        m_num = re.search(r"/(\d+)/\d+_\d+\.jpg", m_src.group(1))
        if not m_num:
            continue
        titre = (m_alt.group(1) if m_alt else "").strip() or slug.replace("-", " ")
        m_dur = re.search(r"\b(\d{1,3}):(\d{2})\b", html[bloc.end():bloc.end() + 500])
        duree = int(m_dur.group(1)) * 60 + int(m_dur.group(2)) if m_dur else 0
        trouvees.append((hash_video, m_num.group(1), titre, duree, m_src.group(1)))
    return trouvees


def lien_vivant(url):
    """HEAD puis repli GET partiel, avec session et réessais."""
    for methode in ("HEAD", "GET"):
        for essai in range(3):
            try:
                req = urllib.request.Request(url, method=methode, headers=ENTETES)
                if methode == "GET":
                    req.add_header("Range", "bytes=0-2047")
                with OUVREUR.open(req, timeout=DELAI) as r:
                    return 200 <= r.status < 300
            except urllib.error.HTTPError as e:
                if e.code in (405, 501):
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


def resoudre(hash_video, numid):
    for q in QUALITES:
        url = f"https://www.eporner.com/dload/{hash_video}/{q}/{numid}-{q}p.mp4"
        if not VERIFIER or lien_vivant(url):
            return url
    return None


def collecter():
    print("  ouverture de session sur le site...")
    _lire("https://www.eporner.com/", "accueil (session)")
    print(f"  cookies recus : {len(POT_A_COOKIES)}")

    vus, fiches, ok = set(), [], 0
    total = len(SOURCES) * PAGES
    for source in SOURCES:
        for page in range(1, PAGES + 1):
            url = source if page == 1 else f"{source}{page}/"
            etiquette = url.replace("https://www.eporner.com/", "") or "(accueil)"
            html = _lire(url, etiquette)
            if not html:
                continue
            trouvees = _trouver_videos(html)
            if not trouvees:
                extrait = re.sub(r"\s+", " ", html[:300])
                print(f"  [!] {etiquette} : aucun bloc reconnu | {extrait}")
                continue
            ok += 1
            print(f"  [+] {etiquette} : {len(trouvees)} videos")
            for hash_video, numid, titre, duree, logo in trouvees:
                if hash_video in vus:
                    continue
                vus.add(hash_video)
                fiches.append({
                    "hash": hash_video, "numid": numid,
                    "titre": re.sub(r"\s+", " ", titre),
                    "duree": duree, "logo": logo,
                })
            time.sleep(PAUSE)
    print(f"  {ok}/{total} pages exploitables")
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

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.parent / (OUT.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("#EXTM3U\n")
        for f, url in gardees:
            duree = f["duree"] if DUREE_REELLE else -1
            titre = f["titre"].replace('"', "'")
            fh.write(f'#EXTINF:{duree} tvg-logo="{f["logo"]}" '
                     f'group-title="{GROUPE}",{titre}\n{url}\n')
    tmp.replace(OUT)
    print(f"  ecrit : {OUT} ({len(gardees)} entrees)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
