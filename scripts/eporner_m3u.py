#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eporner_m3u.py
--------------
Génère adult.m3u depuis les pages publiques d'Eporner (sans clé, sans API).

Deux particularités du site sont gérées :
  - un portail « Age verification » servi aux nouveaux visiteurs, qu'on franchit
    en posant des cookies et en suivant les liens de la page ;
  - les vignettes en chargement différé (data-src), qu'il faut lire à la place
    de src qui contient un gif transparent.

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

GROUPE       = "Zone 18@"
DUREE_REELLE = True
QUALITES     = ("720", "480")
PAGES        = 4

SOURCES = [
    "https://www.eporner.com/",
    "https://www.eporner.com/most-viewed/",
    "https://www.eporner.com/top-rated/",
    "https://www.eporner.com/best-videos/",
]

VERIFIER     = True
THREADS      = 8
PAUSE        = 1.0
DELAI        = 15

# Noms de cookies plausibles pour le portail d'age : on les pose tous,
# le serveur n'en a besoin que d'un seul.
COOKIES_AGE = ("age_verified", "age_ok", "ageok", "ep_age",
               "ep_age_verified", "age", "adult", "nv_age")
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
    "Referer": "https://www.eporner.com/",
    "Connection": "keep-alive",
}

POT_A_COOKIES = http.cookiejar.CookieJar()
OUVREUR = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(POT_A_COOKIES)
)

REESSAYABLES = (429, 500, 502, 503, 504)


def _poser_cookie(nom, valeur):
    POT_A_COOKIES.set_cookie(http.cookiejar.Cookie(
        version=0, name=nom, value=valeur, port=None, port_specified=False,
        domain=".eporner.com", domain_specified=True, domain_initial_dot=True,
        path="/", path_specified=True, secure=True, expires=None,
        discard=False, comment=None, comment_url=None, rest={}, rfc2109=False,
    ))


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


def _portail(texte):
    return "Age verification" in texte[:2000]


def _franchir_portail(page):
    """Tente de passer la page de verification d'age. Rend la vraie page ou None."""
    print("  [!] portail d'age detecte, tentative de passage")
    print("      extrait : " + re.sub(r"\s+", " ", page[:500]))

    def retenter(etiquette):
        h = _lire("https://www.eporner.com/", "accueil")
        if h and not _portail(h):
            print(f"      {etiquette} -> PORTE FRANCHIE")
            return h
        print(f"      {etiquette} -> toujours bloque")
        return None

    # 1) cookies classiques
    for nom in COOKIES_AGE:
        _poser_cookie(nom, "1")
    h = retenter("cookies d'age")
    if h:
        return h

    # 2) suivre les liens presents sur le portail
    liens = [l for l in re.findall(r'href="([^"]+)"', page)
             if not re.search(r"\.(css|js|png|jpe?g|svg|ico|gif)(\?|$)", l)]
    for lien in liens[:6]:
        url = lien if lien.startswith("http") else "https://www.eporner.com" + lien
        _lire(url, "lien portail")
        h = retenter(f"suivi {url}")
        if h:
            return h

    # 3) chemins classiques de validation
    for chemin in ("/ageverify/", "/age-verify/", "/?age=1", "/?ageok=1"):
        _lire("https://www.eporner.com" + chemin, "chemin portail")
        h = retenter(f"chemin {chemin}")
        if h:
            return h

    print("      AUCUN contournement n'a fonctionne")
    return None


def _trouver_videos(html):
    """Rend (hash, id numérique, titre, durée, vignette) pour chaque carte vidéo.

    Chaque carte porte un attribut data-id qui contient justement l'id numérique
    nécessaire aux liens de téléchargement : on découpe le HTML dessus.
    """
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
    print("  ouverture de session...")
    page = _lire("https://www.eporner.com/", "accueil")
    if page and _portail(page):
        page = _franchir_portail(page)
    print(f"  cookies en session : {len(POT_A_COOKIES)}")

    vus, fiches, ok = set(), [], 0
    total = len(SOURCES) * PAGES
    for source in SOURCES:
        for page_num in range(1, PAGES + 1):
            url = source if page_num == 1 else f"{source}{page_num}/"
            etiquette = url.replace("https://www.eporner.com/", "") or "(accueil)"
            texte = _lire(url, etiquette)
            if not texte:
                continue
            if _portail(texte):
                print(f"  [!] {etiquette} : encore le portail d'age")
                continue
            trouvees = _trouver_videos(texte)
            if not trouvees:
                print(f"  [!] {etiquette} : aucun bloc reconnu | "
                      f"{re.sub(r'[[:space:]]+', ' ', texte[:200])}")
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
