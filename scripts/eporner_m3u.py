#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# eporner_m3u.py
# Genere data/adult.m3u : un seul fichier, deux sources (Eporner + XVideos),
# sous le meme dossier « Zone 18@ ».
#
# Eporner : lecture des pages HTML sans verifier chaque lien (le site etrangle
#           au bout de ~100 requetes). Si le site impose sa verification d'age,
#           Eporner est ignore pour ce passage (pas de contournement).
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
def _portail(texte):
    return "Age verification" in texte[:2000]


def _franchir_portail(page):
    # Le portail de verification d'age n'est pas contourne : si le site le
    # presente, on renonce a Eporner pour ce passage (XVideos est quand meme ecrit).
    print("  [!] portail de verification d'age : Eporner ignore pour ce passage", flush=True)
    return None


def _trouver_videos(html):
    morceaux = re.split(r'data-id="(\d+)"', html)
    trouvees = []
    for i in range(1, len(morceaux) - 1, 2):
        numid, bloc = morceaux[i], morceaux[i + 1]
        m_lien = re.search(r'href="[^"]*?/video-([A-Za-z0-9]+)/([^/"]+)/"', bloc)
        if not m_lien:
            continue                       # carte de photo, pas de video
        m_alt = re.search(r'alt="([^"]*)"', bloc)
        m_vig = re.search(r'(?:data-src|src)="(https://[^"]*?\.jpg)"', bloc)
        m_dur = re.search(r'class="mbtim"[^>]*>(\d{1,3}):(\d{2})', bloc)
        titre = html_mod.unescape((m_alt.group(1) if m_alt else "").strip())
        if not titre:
            titre = m_lien.group(2).replace("-", " ")
        duree = int(m_dur.group(1)) * 60 + int(m_dur.group(2)) if m_dur else 0
        trouvees.append((m_lien.group(1), numid, titre, duree,
                         m_vig.group(1) if m_vig else ""))
    return trouvees


def lien_vivant(url):
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


def _pages_eporner(source):
    base = source.rstrip("/")
    urls = [source]
    for n in range(2, PAGES_EPORNER + 1):
        urls.append(f"{base}/{n}/")
    return urls


def _lire_page(url):
    h = _lire(url, url)
    if h and _portail(h):
        if _franchir_portail(h):
            h = _lire(url, url)
    return h


def bloc_eporner():
    print("Eporner :", flush=True)
    accueil = _lire("https://www.eporner.com/", "accueil")
    if not accueil:
        print("  [!] accueil injoignable", flush=True)
        return []
    if _portail(accueil) and not _franchir_portail(accueil):
        print("  [!] portail d'age non franchi", flush=True)
        return []
    print(f"  cookies en session : {len(POT_A_COOKIES)}", flush=True)

    urls = []
    for source in SOURCES_EPORNER:
        urls.extend(_pages_eporner(source))
    print(f"  {len(urls)} pages a lire", flush=True)

    vues = {}
    for debut in range(0, len(urls), THREADS_EPO):
        lot = urls[debut:debut + THREADS_EPO]
        with ThreadPoolExecutor(max_workers=THREADS_EPO) as pool:
            for i, page in enumerate(pool.map(_lire_page, lot), start=debut + 1):
                if not page:
                    print(f"  page {i:2d} : vide", flush=True)
                    continue
                trouvees = _trouver_videos(page)
                print(f"  page {i:2d} : {len(trouvees):3d} videos", flush=True)
                for hash_video, numid, titre, duree, vignette in trouvees:
                    if numid not in vues:
                        vues[numid] = (hash_video, titre, duree, vignette)
        time.sleep(PAUSE_EPO)

    print(f"  {len(vues)} videos distinctes", flush=True)
    lignes = []
    for numid, (hash_video, titre, duree, vignette) in vues.items():
        url = resoudre(hash_video, numid)
        if url:
            lignes.extend(_entree(titre, duree, vignette, url))
    print(f"  {len(lignes) // 2} entrees gardees", flush=True)
    return lignes


# ------------------------------- XVIDEOS --------------------------------
def _pages_xvideos():
    urls = ["https://www.xvideos.com/"]
    for n in range(1, PAGES_XVIDEOS):
        urls.append(f"https://www.xvideos.com/new/{n}/")
    return urls


def _cartes(page):
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
    print("Eporner + XVideos -> playlist adulte", flush=True)
    print(f"  cible : {OUT}", flush=True)

    lignes = ["#EXTM3U"]
    total = 0
    for nom, fonction in (("Eporner", bloc_eporner), ("XVideos", bloc_xvideos)):
        try:
            part = fonction()
        except Exception as e:
            print(f"  [!] {nom} : {type(e).__name__}: {e}", flush=True)
            part = []
        if not part:
            print(f"  [!] {nom} : aucune entree", flush=True)
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
