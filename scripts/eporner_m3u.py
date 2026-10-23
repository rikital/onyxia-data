#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# eporner_m3u.py
# Genere data/adult.m3u : un seul fichier, trois sources (AdultTV + Eporner +
# XVideos), sous le meme dossier « Zone 18@ ».
#
# AdultTV : les 12 chaines en direct de adult-tv-channels.click. Chaque page de
#           chaine porte un « window.playerConfig » : soit un lien direct
#           (streamType "direct" -> sourceUrl), soit une API (streamType "api") :
#           POST apiUrl/endpoint (avec Referer + Origin du site) -> {"fileUrl": ...},
#           le lien = base64 decode, puis chaine inversee (cf. /assets/js/player.js).
#           Les flux se lisent ensuite sans en-tete particulier.
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

import base64
import html as html_mod
import http.cookiejar
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
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

# --- partie AdultTV (chaines en direct) ---
ADULTTV_BASE   = "https://adult-tv-channels.click"
ADULTTV_EXCLUS = re.compile(r"^/(search|categories|favorites|privacy-policy|contact-us|page\d+)\b")

# --- partie XVideos ---
PAGES_XVIDEOS = 30                   # pages de listing, 30 videos par page
THREADS_XVI   = 8
LOT_XVI       = 30
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
# Secours : n'importe quel lien .m3u8 dans la page (setVideoHLS, JSON avec \/ echappes...)
RE_M3U8 = re.compile(r"(https?:(?:\\?/){2}[^\"'\s<>]+?\.m3u8[^\"'\s<>]*)")
ESSAIS_FLUX = 8                       # essais pour la page video (connexion instable)


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
        return ""                          # verification d'age : page ignoree
    return h


def bloc_eporner():
    print("Eporner :", flush=True)
    accueil = _lire("https://www.eporner.com/", "accueil")
    if not accueil:
        print("  [!] accueil injoignable", flush=True)
        return []
    if _portail(accueil):
        print("  [!] verification d'age : Eporner ignore pour ce passage", flush=True)
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


# ------------------------------- ADULTTV --------------------------------
def _lire_post(url, referer):
    entetes = dict(ENTETES, **{
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json, text/plain, */*",
        "Referer": referer,
        "Origin": ADULTTV_BASE,
    })
    for n in range(3):
        try:
            req = urllib.request.Request(url, data=b"", method="POST", headers=entetes)
            with OUVREUR.open(req, timeout=DELAI) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in REESSAYABLES and n < 2:
                time.sleep(2.0 * (n + 1))
                continue
            print(f"  [!] API {url} HTTP {e.code}", flush=True)
            return ""
        except Exception as e:
            if n < 2:
                time.sleep(1.5 * (n + 1))
                continue
            print(f"  [!] API {url} {type(e).__name__}: {e}", flush=True)
    return ""


def _chaines_adulttv():
    # Rend la liste des chemins de chaines (/dorcel-tv-online/ ...) de l'accueil.
    accueil = _lire(ADULTTV_BASE + "/", "AdultTV accueil")
    chemins = []
    for m in re.finditer(r'href="(?:https://adult-tv-channels\.click)?(/[a-z0-9-]+/)"', accueil):
        p = m.group(1)
        if ADULTTV_EXCLUS.match(p) or p in chemins:
            continue
        chemins.append(p)
    return chemins


def _flux_adulttv(chemin):
    # Rend (titre, logo, lien) pour une chaine, ou None.
    page = _lire(ADULTTV_BASE + chemin, chemin)
    m_cfg = re.search(r"window\.playerConfig\s*=\s*\{(.*?)\};", page, re.S)
    if not m_cfg:
        print(f"  {chemin} : pas de playerConfig", flush=True)
        return None
    cfg = dict(re.findall(r'(\w+)\s*:\s*"([^"]*)"', m_cfg.group(1)))

    lien = ""
    if cfg.get("streamType") == "api" and cfg.get("apiUrl") and cfg.get("endpoint"):
        rep_api = _lire_post(cfg["apiUrl"].rstrip("/") + "/" + cfg["endpoint"], ADULTTV_BASE + chemin)
        try:
            code = json.loads(rep_api).get("fileUrl") if rep_api else None
            if code:
                lien = base64.b64decode(code).decode("utf-8", "replace")[::-1]
        except Exception as e:
            print(f"  {chemin} : reponse API illisible ({type(e).__name__})", flush=True)
    elif cfg.get("streamType") == "direct":
        lien = cfg.get("sourceUrl", "")
    if not lien.startswith("http"):
        print(f"  {chemin} : aucun lien", flush=True)
        return None
    # 2026-10-23 (user : « les réparer ») : les liens de type « api » (stream.xlivetv.com)
    #   ne marchent QUE pour l'adresse IP qui les a demandés -> generes ici, ils etaient
    #   refuses (403) chez tout le monde. On publie adulttv://<chemin> et l'appli demande
    #   le lien au moment de la lecture (AdultTvResolver), depuis l'IP de l'utilisateur.
    if cfg.get("streamType") == "api":
        lien = "adulttv://" + chemin.strip("/")

    m_titre = re.search(r"<h1[^>]*>\s*([^<]+?)\s*</h1>", page) or re.search(r"<title>\s*([^<|]+)", page)
    titre = html_mod.unescape(m_titre.group(1)).strip() if m_titre else chemin.strip("/").replace("-", " ").title()
    m_logo = re.search(r'poster="([^"]+)"', page)
    logo = urllib.parse.urljoin(ADULTTV_BASE + "/", m_logo.group(1)) if m_logo else ""
    return titre, logo, lien


def _trouver_adulttv():
    # Scanne les chaines listees sur l'accueil et rend leurs entrees M3U (en direct).
    print("AdultTV :", flush=True)
    chemins = _chaines_adulttv()
    print(f"  {len(chemins)} chaines sur l'accueil", flush=True)
    lignes = []
    for chemin in chemins:
        try:
            r = _flux_adulttv(chemin)
        except Exception as e:
            print(f"  {chemin} : {type(e).__name__}: {e}", flush=True)
            r = None
        if r:
            titre, logo, lien = r
            lignes.extend([f'#EXTINF:-1 tvg-logo="{logo}" group-title="{GROUPE}",{titre}', lien])
            print(f"  {chemin} : ok ({lien.split('/')[2]})", flush=True)
        time.sleep(0.5)
    print(f"  {len(lignes) // 2} chaines gardees", flush=True)
    return lignes


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


_DIAG_FLUX = 0


def _flux(chemin):
    page = _lire("https://www.xvideos.com" + chemin, chemin, essais=ESSAIS_FLUX)
    if not page:
        return ""
    m = RE_HAUT.search(page) or RE_BAS.search(page)
    if m:
        return m.group(1)
    # Secours : un lien HLS (.m3u8) present ailleurs dans la page
    s = RE_M3U8.search(page)
    if s:
        return s.group(1).replace("\\/", "/")
    if not m:
        global _DIAG_FLUX
        _DIAG_FLUX += 1
        # Diagnostic limité aux 3 premiers échecs (évite d'inonder le journal)
        if _DIAG_FLUX <= 3:
            print(f"  [diag] aucun lien pour {chemin} — début de la page :\n{page[:1000]}", flush=True)
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


# ------------------------------- REPRISE --------------------------------
# Si une source ne rend rien pendant un passage, on remet ses entrees deja en
# ligne (fichier actuel), et a defaut celles du dernier fichier de l'historique
# git qui en contenait. Seulement pour les sources dont les liens n'expirent
# pas : les liens XVideos sont signes pour ~3 h, les reprendre ne servirait a rien.
# 2026-10-22 : Eporner retire de la reprise — ses anciens liens « dload » ne rendent plus
#   la video mais la page de verification d'age (text/html) : les remettre ne fait que
#   remplir la playlist d'entrees mortes. Pas de reprise tant que le site bloque.
HOTES_DURABLES = {}

# 2026-10-22 : XVideos repris AUSSI, mais seulement les liens encore valides. Un passage
#   a rendu 0 lien (pages video sans lien selon le serveur GitHub tire au sort) : sans
#   reprise, la playlist perdait toutes ses videos pendant 2 h. Chaque lien porte sa date
#   d'expiration (« secure=<signature>,<horodatage> ») : on ne garde que ceux qui ont
#   encore au moins MARGE_EXPIRATION secondes devant eux.
HOTES_EXPIRANTS  = {"XVideos": "xvideos-cdn.com"}
MARGE_EXPIRATION = 15 * 60
RE_EXPIRATION    = re.compile(r"[?&]secure=[^,&]*,(\d{9,11})")


def _encore_valide(url):
    m = RE_EXPIRATION.search(url)
    return bool(m) and int(m.group(1)) > time.time() + MARGE_EXPIRATION


def _entrees_de(texte, hote, filtre=None):
    lignes = texte.splitlines()
    out = []
    for i, ligne in enumerate(lignes):
        if (ligne.startswith("#EXTINF") and i + 1 < len(lignes)
                and hote in lignes[i + 1]
                and (filtre is None or filtre(lignes[i + 1]))):
            out.extend([ligne, lignes[i + 1]])
    return out


def _git(*args):
    try:
        return subprocess.run(["git", *args], cwd=RACINE, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=180).stdout
    except Exception:
        return ""


def _reprise(nom):
    hote = HOTES_DURABLES.get(nom) or HOTES_EXPIRANTS.get(nom)
    if not hote:
        return []
    filtre = _encore_valide if nom in HOTES_EXPIRANTS else None
    if OUT.exists():
        e = _entrees_de(OUT.read_text(encoding="utf-8", errors="replace"), hote, filtre)
        if e:
            print(f"  {nom} : reprise de {len(e) // 2} entrees deja en ligne", flush=True)
            return e
    # le checkout d'Actions est superficiel : on recupere quelques jours
    # d'historique (sans le contenu des fichiers, telecharge a la demande)
    if _git("rev-parse", "--is-shallow-repository").strip() == "true":
        _git("fetch", "-q", "--filter=blob:none", "--shallow-since=7 days ago", "origin")
    chemins = ["data/adult.m3u", "adult.m3u"]
    for commit in _git("log", "--format=%H", "-n", "300", "--", *chemins).split():
        for c in chemins:
            texte = _git("show", f"{commit}:{c}")
            e = _entrees_de(texte, hote, filtre) if texte else []
            if e:
                print(f"  {nom} : reprise de {len(e) // 2} entrees depuis "
                      f"l'historique ({commit[:8]})", flush=True)
                return e
    print(f"  [!] {nom} : rien a reprendre", flush=True)
    return []


# 2026-10-23 (user : « rester bien couvert ») : echec PARTIEL. Si une source a liens
#   expirants ramene nettement moins d'entrees que la playlist en ligne (connexion
#   instable, pages bloquees...), on complete avec les anciennes entrees encore valides
#   qui manquent, sans depasser le volume precedent.
SEUIL_COMPLEMENT = 0.8


def _completer(nom, part):
    hote = HOTES_EXPIRANTS.get(nom)
    if not hote or not part or not OUT.exists():
        return part
    anciennes = _entrees_de(OUT.read_text(encoding="utf-8", errors="replace"),
                            hote, _encore_valide)
    n_neuf, n_ancien = len(part) // 2, len(anciennes) // 2
    if n_neuf >= SEUIL_COMPLEMENT * n_ancien:
        return part
    deja = set(part[0::2])
    ajout = []
    for i in range(0, len(anciennes) - 1, 2):
        if n_neuf + len(ajout) // 2 >= n_ancien:
            break
        if anciennes[i] not in deja:
            ajout.extend(anciennes[i:i + 2])
            deja.add(anciennes[i])
    if ajout:
        print(f"  {nom} : {n_neuf} entrees seulement (avant : {n_ancien}) -> "
              f"complement de {len(ajout) // 2} entrees encore valides", flush=True)
    return part + ajout


# -------------------------------- MAIN ----------------------------------
def main():
    print("AdultTV + XVideos -> playlist adulte (Eporner desactive)", flush=True)
    print(f"  cible : {OUT}", flush=True)

    lignes = ["#EXTM3U"]
    total = 0
    # 2026-10-23 (user : « je croyais que tu l'avais retire, on tombait sur un mur ») : Eporner
    #   DESACTIVE. Ses liens « dload » menent a la verification d'age pour les spectateurs en
    #   France, meme quand le serveur GitHub, lui, n'y tombe pas. Le code reste (bloc_eporner),
    #   il suffit de remettre ("Eporner", bloc_eporner) dans la liste pour le reactiver.
    for nom, fonction in (("AdultTV", _trouver_adulttv), ("XVideos", bloc_xvideos)):
        try:
            part = fonction()
        except Exception as e:
            print(f"  [!] {nom} : {type(e).__name__}: {e}", flush=True)
            part = []
        if not part:
            print(f"  [!] {nom} : aucune entree", flush=True)
            part = _reprise(nom)
        else:
            part = _completer(nom, part)
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
