#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# eporner_m3u.py
# Genere data/adult.m3u : une seule source (XVideos), sous le meme dossier « Zone 18@ ».
#
# XVideos : les liens de flux sont signes et expirent au bout d'environ 3 h.
#           Passage toutes les 2 heures pour rafraichir les liens (voir adult.yml).
#
# Volontairement ecrit en commentaires et non en docstring : si un triple quote
# saute au copier-coller, le fichier ne devient pas un bloc de texte inerte.
# ---------------------------------------------------------------------------

import html as html_mod
import http.cookiejar
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

print("== eporner_m3u.py : module charge ==", flush=True)


def _racine_depot():
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
DUREE_REELLE = True                  # True = duree reelle | False = -1

# --- partie XVideos ---
PAGES_XVIDEOS = 30                   # pages de listing, 30 videos par page
THREADS_XVI   = 8
LOT_XVI       = 30                   # group size, increased with page count
PAUSE_XVI     = 0.4

DELAI = 20
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

POT_A_COOKIES = http.cookiejar.CookieJar()
OUVREUR = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(POT_A_COOKIES)
)

REESSAYABLES = (429, 500, 502, 503, 504)

RE_LIEN = re.compile(r'href="(/video\.([a-z0-9]+)/[^"]*)"')
RE_HAUT = re.compile(r"setVideoUrlHigh\('([^']+)'\)")
RE_BAS  = re.compile(r"setVideoUrlLow\('([^']+)'\)")


def _lire(url, etiquette="", essais=3):
    for n in range(essais):
        try:
            req = urllib.request.Request(url, method="GET", headers=ENTETES)
            with OUVREUR.open(req, timeout=DELAI) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in REESSAYABLES and n < essais - 1:
                time.sleep(2.0 * (n + 1))
                continue
            print(f"  [!] {etiquette} HTTP {e.code}", flush=True)
            return ""
        except Exception as e:
            if n < essais - 1:
                time.sleep(1.5 * (n + 1))
                continue
            print(f"  [!] {etiquette} {type(e).__name__}: {e}", flush=True)
    return ""


def _entree(titre, duree, vignette, url):
    d = duree if DUREE_REELLE else -1
    return [f'#EXTINF:{d} tvg-logo="{vignette}" group-title="{GROUPE}",{titre}',
            url]


# ------------------------------- XVIDEOS --------------------------------
def _pages_xvideos():
    urls = ["https://www.xvideos.com/"]
    for n in range(1, PAGES_XVIDEOS):
        urls.append(f"https://www.xvideos.com/new/{n}/")
    return urls


def _cartes(page):
    """Rend (chemin, eid, titre, duree, vignette) pour chaque carte video."""
    trouves = []
    vus = set()
    for m in re.finditer(r'href="(?:https?://[^"]*?)?(/video\.([a-z0-9]+)/([^"]*))"', page):
        chemin, eid, slug = m.group(1), m.group(2), m.group(3)
        if eid in vus:
            continue
        vus.add(eid)

        # certains liens de vignette portent des segments en trop
        # (/video.<eid>/57468328/THUMBNUM/<slug>) : on ne garde que l'identifiant
        # et le dernier morceau, sinon la page renvoie 404
        bouts = [p for p in chemin.split("/") if p]
        if len(bouts) > 2:
            chemin = "/" + bouts[0] + "/" + bouts[-1]

        bloc = page[m.end():m.end() + 2500]

        m_titre = re.search(r'<p class="title">\s*<a[^>]*>([^<]*)</a>', bloc)
        if not m_titre:
            m_titre = re.search(r'title="([^"]*)"', bloc)
        titre = html_mod.unescape(m_titre.group(1)).strip() if m_titre else ""
        if not titre:
            titre = slug.replace("_", " ").strip()

        m_vig = re.search(r'data-sfwthumb="([^"]*)"', bloc)
        if not m_vig:
            m_vig = re.search(r'data-src="(https://[^"]*?\.jpg)"', bloc)

        duree = 0
        m_dur = re.search(r'class="duration"[^>]*>(.*?)<', bloc, re.S)
        if m_dur:
            h = re.search(r'(\d+)\s*h', m_dur.group(1))
            mn = re.search(r'(\d+)\s*min', m_dur.group(1))
            if h or mn:
                duree = ((int(h.group(1)) if h else 0) * 60
                         + (int(mn.group(1)) if mn else 0)) * 60

        trouves.append((chemin, eid, titre, duree,
                        m_vig.group(1) if m_vig else ""))
    return trouves


def _flux(chemin):
    page = _lire("https://www.xvideos.com" + chemin, chemin)
    if not page:
        return ""
    m = RE_HAUT.search(page) or RE_BAS.search(page)
    return m.group(1) if m else ""


def bloc_xvideos():
    print("XVideos :", flush=True)
    cartes = {}
    pages = _pages_xvideos()
    print(f"  {len(pages)} pages de listing", flush=True)
    for i, url in enumerate(pages, start=1):
        page = _lire(url, f"page {i}")
        if not page:
            print(f"  page {i:2d} : vide", flush=True)
            continue
        trouvees = _cartes(page)
        print(f"  page {i:2d} : {len(trouvees):3d} videos", flush=True)
        for chemin, eid, titre, duree, vignette in trouvees:
            if eid not in cartes:
                cartes[eid] = (chemin, titre, duree, vignette)

    print(f"  {len(cartes)} videos distinctes", flush=True)
    if not cartes:
        return []

    items = list(cartes.values())
    flux = []
    for debut in range(0, len(items), LOT_XVI):
        lot = items[debut:debut + LOT_XVI]
        with ThreadPoolExecutor(max_workers=THREADS_XVI) as pool:
            flux.extend(pool.map(lambda it: _flux(it[0]), lot))
        print(f"  {min(debut + LOT_XVI, len(items))}/{len(items)} resolus", flush=True)
        time.sleep(PAUSE_XVI)

    lignes = []
    for (chemin, titre, duree, vignette), url in zip(items, flux):
        if url:
            lignes.extend(_entree(titre, duree, vignette, url))
    print(f"  {len(lignes) // 2} entrees gardees", flush=True)
    return lignes


# -------------------------------- MAIN ----------------------------------
def main():
    print("XVideos -> playlist adulte", flush=True)
    print(f"  cible : {OUT}", flush=True)

    lignes = ["#EXTM3U"]
    total = 0

    # --- XVideos only ---
    try:
        part = bloc_xvideos()
    except Exception as e:
        print(f"  [!] XVideos : {type(e).__name__}: {e}", flush=True)
        part = []

    total += len(part) // 2
    lignes.extend(part)
    print("", flush=True)

    if total == 0:
        print("  [!] rien a ecrire", flush=True)
        return 1

    tmp = OUT.with_name(OUT.name + ".tmp")
    tmp.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print(f"  ecrit : {OUT} ({total} entrees)", flush=True)
    return 0


print("== lancement ==", flush=True)
try:
    sys.exit(main())
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)
