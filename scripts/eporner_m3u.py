#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xvideos_m3u.py
--------------
Génère data/xvideos.m3u depuis les pages publiques de XVideos (sans clé, sans API).

Différence avec Eporner : les liens de flux sont signés et expirent au bout
d'environ 3 heures. Le workflow doit donc tourner toutes les 2 heures pour que
la playlist reste jouable en permanence.

Usage :
    python xvideos_m3u.py
"""

import html as html_mod
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _racine_depot():
    ici = Path(__file__).resolve().parent
    for d in (ici, *ici.parents):
        if (d / ".git").exists():
            return d
    return ici


# ------------------------------- CONFIG --------------------------------
RACINE       = _racine_depot()
DOSSIER_DATA = RACINE / "data"
OUT          = (DOSSIER_DATA if DOSSIER_DATA.is_dir() else RACINE) / "xvideos.m3u"

GROUPE       = "Zone 18@"            # valeur de group-title
PAGES        = 12                    # pages de listing, 30 vidéos par page
FORMAT       = "mp4"                 # "mp4" = fichier direct | "hls" = flux adaptatif
DUREE_REELLE = True                  # True = durée réelle | False = -1
THREADS      = 8
LOT          = 24                    # vidéos traitées avant une petite pause
PAUSE        = 0.4                   # secondes entre deux lots
DELAI        = 20
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
    "Connection": "keep-alive",
}

REESSAYABLES = (429, 500, 502, 503, 504)
RE_LIEN = re.compile(r'href="(/video\.([a-z0-9]+)/[^"]*)"')
RE_HAUT = re.compile(r"setVideoUrlHigh\('([^']+)'\)")
RE_BAS  = re.compile(r"setVideoUrlLow\('([^']+)'\)")
RE_HLS  = re.compile(r"setVideoHLS\('([^']+)'\)")


def _lire(url, essais=3):
    for n in range(essais):
        try:
            req = urllib.request.Request(url, method="GET", headers=ENTETES)
            with urllib.request.urlopen(req, timeout=DELAI) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in REESSAYABLES and n < essais - 1:
                time.sleep(1.5 * (n + 1))
                continue
            return ""
        except Exception:
            if n < essais - 1:
                time.sleep(1.5 * (n + 1))
                continue
    return ""


def _pages():
    urls = ["https://www.xvideos.com/"]
    for n in range(1, PAGES):
        urls.append(f"https://www.xvideos.com/new/{n}/")
    return urls


def _cartes(page):
    """Rend (chemin, eid, titre, durée, vignette) pour chaque carte vidéo."""
    trouves = []
    for bloc in page.split('class="thumb-block')[1:]:
        m_lien = RE_LIEN.search(bloc)
        if not m_lien:
            continue
        m_titre = re.search(r'title="([^"]*)"', bloc)
        m_dur = re.search(r'class="duration">\s*(\d+)\s*min', bloc)
        m_vig = re.search(r'data-sfwthumb="([^"]*\.jpg)"', bloc)
        if not m_vig:
            m_vig = re.search(r'src="(https://[^"]*?\.(?:jpg|avif))"', bloc)
        titre = html_mod.unescape((m_titre.group(1) if m_titre else "").strip())
        duree = int(m_dur.group(1)) * 60 if m_dur else 0
        trouves.append((m_lien.group(1), m_lien.group(2), titre, duree,
                        m_vig.group(1) if m_vig else ""))
    return trouves


def _flux(chemin):
    page = _lire("https://www.xvideos.com" + chemin)
    if not page:
        return ""
    if FORMAT == "hls":
        m = RE_HLS.search(page)
        if m:
            return m.group(1)
    m = RE_HAUT.search(page) or RE_BAS.search(page)
    return m.group(1) if m else ""


def main():
    print("XVideos -> playlist adulte")
    print(f"  cible : {OUT}")
    print(f"  format : {FORMAT}")

    cartes = {}
    pages = _pages()
    print(f"  {len(pages)} pages de listing")
    for i, url in enumerate(pages, start=1):
        page = _lire(url)
        if not page:
            print(f"  page {i:2d} : vide")
            continue
        trouvees = _cartes(page)
        print(f"  page {i:2d} : {len(trouvees):3d} videos")
        for chemin, eid, titre, duree, vignette in trouvees:
            if eid not in cartes:
                cartes[eid] = (chemin, titre, duree, vignette)

    print(f"  {len(cartes)} videos distinctes")
    if not cartes:
        print("  [!] rien a ecrire")
        return 1

    items = list(cartes.values())
    flux = []
    for debut in range(0, len(items), LOT):
        lot = items[debut:debut + LOT]
        with ThreadPoolExecutor(max_workers=THREADS) as pool:
            flux.extend(pool.map(lambda it: _flux(it[0]), lot))
        print(f"  {min(debut + LOT, len(items))}/{len(items)} resolus")
        time.sleep(PAUSE)

    lignes = ["#EXTM3U"]
    gardees = 0
    for (chemin, titre, duree, vignette), url in zip(items, flux):
        if not url:
            continue
        d = duree if DUREE_REELLE else -1
        lignes.append(f'#EXTINF:{d} tvg-logo="{vignette}" '
                      f'group-title="{GROUPE}",{titre}')
        lignes.append(url)
        gardees += 1

    if gardees == 0:
        print("  [!] aucune url de flux recuperee")
        return 1

    tmp = OUT.with_name(OUT.name + ".tmp")
    tmp.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print(f"  ecrit : {OUT} ({gardees} entrees)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
