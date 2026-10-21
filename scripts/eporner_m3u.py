#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# eporner_m3u.py
# Genere data/adult.m3u : un seul fichier, deux sources (Eporner + XVideos),
# sous le meme dossier « Zone 18@ ».
#
# Eporner : portail « Age verification » a franchir, puis lecture des pages HTML
#           sans verifier chaque lien (le site etrangle au bout de ~100 requetes).
# XVideos : les liens de flux sont signes et expirent au bout d'environ 3 h,
#           d'ou un passage toutes les 2 heures (voir adult.yml).
#
# Si une source tombe, l'autre est quand meme ecrite.
#
# Volontairement ecrit en commentaires et non en docstring : si un triple quote
# saute au copier-coller, le fichier ne devient pas un bloc de texte inerte.
# ---------------------------------------------------------------------------

import html as html_mod
import http.cookiejar
import re
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

GROUPE       = "Zone 18@"            # valeur de group-title, pour les deux sources
DUREE_REELLE = True                  # True = duree reelle | False = -1

# --- partie Eporner ---
SOURCES_EPORNER = [
    "https://www.eporner.com/",                  # les plus recentes
    "https://www.eporner.com/most-viewed/",      # les plus vues
    "https://www.eporner.com/top-rated/",        # les mieux notees
    "https://www.eporner.com/best-videos/",      # la selection
]
PAGES_EPORNER = 5                    # pages par source
QUALITES      = ("720", "480")       # utilisees seulement si VERIFIER
VERIFIER      = False                # False = on garde tout, sans test des liens
THREADS_EPO   = 8
PAUSE_EPO     = 1.0

# --- partie XVideos ---
PAGES_XVIDEOS = 12                   # pages de listing, 30 videos par page
THREADS_XVI   = 8
LOT_XVI       = 24
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

COOKIES_AGE = ("age_verified", "age_ok", "ageok", "ep_age",
               "ep_age_verified", "age", "adult", "nv_age")

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


# ------------------------------- EPORNER --------------------------------
def _poser_cookie(nom, valeur):
    POT_A_COOKIES.set_cookie(http.cookiejar.Cookie(
        version=0, name=nom, value=valeur, port=None, port_specified=False,
        domain=".eporner.com", domain_specified=True, domain_initial_dot=True,
        path="/", path_specified=True, secure=True, expires=None,
        discard=False, comment=None, comment_url=None, rest={}, rfc2109=False,
    ))
