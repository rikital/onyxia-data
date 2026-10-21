#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eporner_m3u.py
--------------
Génère data/adult.m3u : un seul fichier, deux sources (Eporner + XVideos),
sous le même dossier « Zone 18@ ».

Contraintes connues :
  - Eporner sert un portail « Age verification » aux nouveaux visiteurs et
    étrangle après une centaine de requêtes : on lit les pages HTML sans
    vérifier chaque lien ensuite.
  - XVideos signe ses liens de flux ; ils expirent au bout d'environ 3 heures.
    D'où un passage toutes les 2 heures (voir adult.yml).

Si une source tombe, l'autre est quand même écrite : on ne perd jamais tout.

Usage :
    python eporner_m3u.py
"""

import html as html_mod
import http.cookiejar
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
OUT          = (DOSSIER_DATA if DOSSIER_DATA.is_dir() else RACINE) / "adult.m3u"

GROUPE       = "Zone 18@"            # valeur de group-title, pour les deux sources
DUREE_REELLE = True                  # True = durée réelle | False = -1

# --- partie Eporner ---
SOURCES_EPORNER = [
    "https://www.eporner.com/",                  # les plus récentes
    "https://www.eporner.com/most-viewed/",      # les plus vues
    "https://www.eporner.com/top-rated/",        # les mieux notées
    "https://www.eporner.com/best-videos/",      # la sélection
]
PAGES_EPORNER = 5                    # pages par source
QUALITES      = ("720", "480")       # utilisées seulement si VERIFIER
VERIFIER      = False                # False = on garde tout, sans test des liens
THREADS_EPO   = 8
PAUSE_EPO     = 1.0

# --- partie XVideos ---
PAGES_XVIDEOS = 12                   # pages de listing, 30 vidéos par page
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
RE_HLS  = re.compile(r"setVideoHLS\('([^']+)'\)")


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
            print(f"  [!] {etiquette} HTTP {e.code}")
            return ""
        except Exception as e:
            if n < essais - 1:
                time.sleep(1.5 * (n + 1))
                continue
            print(f"  [!] {etiquette} {type(e).__name__}: {e}")
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


def _portail(texte):
    return "Age verification" in texte[:2000]


def _franchir_portail(page):
    """Tente de passer la page de vérification d'âge. Rend la vraie page ou None."""
    print("  [!] portail d'age detecte, tentative de passage")

    def retenter(etiquette):
        h = _lire("https://www.eporner.com/", "accueil")
        if h and not _portail(h):
            print(f"      {etiquette} -> PORTE FRANCHIE")
            return h
        print(f"      {etiquette} -> toujours bloque")
        return None

    for nom in COOKIES_AGE:
        _poser_cookie(nom, "1")
    h = retenter("cookies d'age")
    if h:
        return h

    liens = [l for l in re.findall(r'href="([^"]+)"', page)
             if not re.search(r"\.(css|js|png|jpe?g|svg|ico|gif)(\?|$)", l)]
    for lien in liens[:6]:
        url = lien if lien.startswith("http") else "https://www.eporner.com" + lien
        _lire(url, "lien portail")
        h = retenter(f"suivi {url}")
        if h:
            return h

    for chemin in ("/ageverify/", "/age-verify/", "/?age=1", "/?ageok=1"):
        _lire("https://www.eporner.com" + chemin, "chemin portail")
        h = retenter(f"chemin {chemin}")
        if h:
            return h

    print("      AUCUN contournement n'a fonctionne")
    return None


def _trouver_videos(html):
    """Rend (hash, id numérique, titre, durée, vignette) pour chaque carte vidéo."""
    morceaux = re.split(r'data-id="(\d+)"', html)
    trouvees = []
    for i in range(1, len(morceaux) - 1, 2):
        numid, bloc = morceaux[i], morceaux[i + 1]
        m_lien = re.search(r'href="[^"]*?/video-([A-Za-z0-9]+)/([^/"]+)/"', bloc)
        if not m_lien:
            continue                       # carte de photo, pas de vidéo
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
    """Utilisé seulement si VERIFIER est vrai."""
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
    """Lit une page en franchissant le portail d'âge si nécessaire."""
    h = _lire(url, url)
    if h and _portail(h):
        if _franchir_portail(h):
            h = _lire(url, url)
    return h


def bloc_eporner():
    """Rend les lignes M3U de la partie Eporner."""
    print("Eporner :")
    accueil = _lire("https://www.eporner.com/", "accueil")
    if not accueil:
        print("  [!] accueil injoignable")
        return []
    if _portail(accueil) and not _franchir_portail(accueil):
        print("  [!] portail d'age non franchi")
        return []
    print(f"  cookies en session : {len(POT_A_COOKIES)}")

    urls = []
    for source in SOURCES_EPORNER:
        urls.extend(_pages_eporner(source))
    print(f"  {len(urls)} pages a lire")

    vues = {}
    for debut in range(0, len(urls), THREADS_EPO):
        lot = urls[debut:debut + THREADS_EPO]
        with ThreadPoolExecutor(max_workers=THREADS_EPO) as pool:
            for i, page in enumerate(pool.map(_lire_page, lot), start=debut + 1):
                if not page:
                    print(f"  page {i:2d} : vide")
                    continue
                trouvees = _trouver_videos(page)
                print(f"  page {i:2d} : {len(trouvees):3d} videos")
                for hash_video, numid, titre, duree, vignette in trouvees:
                    if numid not in vues:
                        vues[numid] = (hash_video, titre, duree, vignette)
        time.sleep(PAUSE_EPO)

    print(f"  {len(vues)} videos distinctes")
    lignes = []
    for numid, (hash_video, titre, duree, vignette) in vues.items():
        url = resoudre(hash_video, numid)
        if url:
            lignes.extend(_entree(titre, duree, vignette, url))
    print(f"  {len(lignes) // 2} entrees gardees")
    return lignes


# ------------------------------- XVIDEOS --------------------------------
def _pages_xvideos():
    urls = ["https://www.xvideos.com/"]
    for n in range(1, PAGES_XVIDEOS):
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
    page = _lire("https://www.xvideos.com" + chemin, chemin)
    if not page:
        return ""
    m = RE_HAUT.search(page) or RE_BAS.search(page)
    return m.group(1) if m else ""


def bloc_xvideos():
    """Rend les lignes M3U de la partie XVideos."""
    print("XVideos :")
    cartes = {}
    pages = _pages_xvideos()
    print(f"  {len(pages)} pages de listing")
    for i, url in enumerate(pages, start=1):
        page = _lire(url, f"page {i}")
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
        return []

    items = list(cartes.values())
    flux = []
    for debut in range(0, len(items), LOT_XVI):
        lot = items[debut:debut + LOT_XVI]
        with ThreadPoolExecutor(max_workers=THREADS_XVI) as pool:
            flux.extend(pool.map(lambda it: _flux(it[0]), lot))
        print(f"  {min(debut + LOT_XVI, len(items))}/{len(items)} resolus")
        time.sleep(PAUSE_XVI)

    lignes = []
    for (chemin, titre, duree, vignette), url in zip(items, flux):
        if url:
            lignes.extend(_entree(titre, duree, vignette, url))
    print(f"  {len(lignes) // 2} entrees gardees")
    return lignes


# -------------------------------- MAIN ----------------------------------
def main():
    print("Eporner + XVideos -> playlist adulte")
    print(f"  cible : {OUT}")

    lignes = ["#EXTM3U"]
    total = 0
    for nom, fonction in (("Eporner", bloc_eporner), ("XVideos", bloc_xvideos)):
        try:
            part = fonction()
        except Exception as e:
            print(f"  [!] {nom} : {type(e).__name__}: {e}")
            part = []
        if not part:
            print(f"  [!] {nom} : aucune entree")
        total += len(part) // 2
        lignes.extend(part)
        print()

    if total == 0:
        print("  [!] rien a ecrire")
        return 1

    tmp = OUT.with_name(OUT.name + ".tmp")
    tmp.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print(f"  ecrit : {OUT} ({total} entrees)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
