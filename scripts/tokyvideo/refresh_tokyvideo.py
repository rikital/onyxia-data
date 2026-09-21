#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tokyvideo — index des « séries » (playlists) FR : vieilles séries complètes en VF (Columbo,
X-Files, Code Quantum, Magnum, Goldorak…) et collections de films.

2026-09-06 (user : « un site avec des vieilles séries, ça vaut vraiment le coup… juste un
backup : quand les gens cherchent une série sur Movix, ça matche en tant que serveur
supplémentaire »). Pourquoi un index : la recherche du site n'indexe PAS ces vidéos
(« columbo » ne rend que le générique), donc l'app ne peut rien trouver à la volée. En
revanche chaque playlist a une API JSON : api.tokyvideo.com/videos/serie/<id>?page=N.

Sortie : data/tokyvideo/index.json
  {"updated": "...", "series": [{"id", "slug", "t", "y", "img", "s": {"1": [[ep, vid, slug, titre], …]}}],
   "films": [{"vid", "slug", "t", "y", "img", "dur", "col"}]}
  - vid = identifiant vidéo Tokyvideo ; slug = fin de l'URL /fr/video/<slug> (le MP4 signé,
    valable ~24 h, se lit dans la page de la vidéo au moment de la lecture : <source src=…mp4?secure=…>).
"""
import html
import json
import os
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

SITE = "https://www.tokyvideo.com"
API = "https://api.tokyvideo.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
OUT = os.environ.get("TOKY_OUT", "data/tokyvideo/index.json")
LIMIT = int(os.environ.get("TOKY_LIMIT", "0"))  # 0 = toutes les playlists (test : ex. 5)
WORKERS = int(os.environ.get("TOKY_WORKERS", "2"))
MAX_PAGES_LIST = 40

S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"})


# Le site limite le débit (429 « Too Many Requests » dès ~8 requêtes parallèles) : on
#   sérialise à ~2 requêtes/s au total et on patiente franchement sur un 429.
_verrou_debit = __import__("threading").Lock()
_dernier = [0.0]
DELAI = [float(os.environ.get("TOKY_DELAI", "1.5"))]   # s entre deux requêtes, ADAPTATIF (×1.5 à chaque 429, max 8 s)


def _cadence():
    with _verrou_debit:
        attente = _dernier[0] + DELAI[0] - time.time()
        if attente > 0:
            time.sleep(attente)
        _dernier[0] = time.time()


DELAI_BASE = DELAI[0]
_succes = [0]


def _ralentir():
    with _verrou_debit:
        _succes[0] = 0
        if DELAI[0] < 8:
            DELAI[0] = min(8.0, DELAI[0] * 1.5)
            print(f"  cadence ralentie → {DELAI[0]:.1f} s entre requêtes", flush=True)


def _accelerer():
    """Après 25 requêtes sans 429, on resserre doucement vers la cadence de base."""
    with _verrou_debit:
        _succes[0] += 1
        if _succes[0] >= 25 and DELAI[0] > DELAI_BASE:
            _succes[0] = 0
            DELAI[0] = max(DELAI_BASE, DELAI[0] * 0.8)
            print(f"  cadence resserrée → {DELAI[0]:.1f} s", flush=True)


def get(url, retries=6, **kw):
    for i in range(retries):
        _cadence()
        try:
            r = S.get(url, timeout=40, **kw)
            if r.status_code == 200:
                _accelerer()
                return r
            if r.status_code in (404, 410):
                return None
            if r.status_code == 429:
                _ralentir()
                pause = 15 * (i + 1)
                print(f"  429 sur {url[-60:]} → pause {pause}s", flush=True)
                time.sleep(pause)
                continue
        except requests.RequestException:
            pass
        time.sleep(2 + 2 * i)
    print(f"  !! abandon {url[-70:]}", flush=True)
    return None


def norm(s):
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]", "", s)


# ───────────────────────────────────────────── liste des playlists
RE_SERIE_LINK = re.compile(r'href="/fr/serie/([^"]+)"[^>]*>\s*([^<]{2,120})<')


def lister_playlists():
    vus, out = set(), []
    for p in range(1, MAX_PAGES_LIST + 1):
        url = f"{SITE}/fr/series" + ("" if p == 1 else f"/{p}")
        r = get(url)
        if not r:
            break
        nouveaux = 0
        for m in RE_SERIE_LINK.finditer(r.text):
            slug, titre = m.group(1), html.unescape(m.group(2)).strip()
            if not titre or slug in vus:
                continue
            vus.add(slug)
            nouveaux += 1
            # « Vidéos tendances » et consorts : playlists automatiques du site, fourre-tout.
            if slug.startswith("trends") or titre.lower().startswith("vidéos tendances"):
                continue
            out.append((slug, titre))
        print(f"  liste page {p}: +{nouveaux} (total {len(out)})", flush=True)
        if nouveaux == 0:
            break
    return out


# ───────────────────────────────────────────── analyse des titres
RE_SE = re.compile(r"(?<![a-z0-9])s\s*(\d{1,2})\s*[ ._\-]*(?:e|ep|episode|épisode)\s*[ ._\-]*(\d{1,3})(?![0-9])", re.I)
RE_X = re.compile(r"(?<![0-9])(\d{1,2})\s*x\s*(\d{2,3})(?![0-9])", re.I)
RE_SAISON_EP = re.compile(r"saison\s*(\d{1,2})[^0-9]{0,20}(?:episode|épisode|ep)\s*(\d{1,3})", re.I)
RE_EP_SEUL = re.compile(r"(?:episode|épisode|ep)\s*[.\-]?\s*(\d{1,3})(?![0-9])", re.I)
RE_E_SEUL = re.compile(r"(?<![a-z0-9])e\s*(\d{2,3})(?![0-9])", re.I)
RE_ANNEE = re.compile(r"(?<!\d)(19[3-9]\d|20[0-3]\d)(?!\d)")
RE_QUAL = re.compile(r"\b(dvdrip|hdtv|webrip|web-dl|bluray|brrip|x264|x265|h264|hevc|720p|1080p|480p|vf|vff|vfq|vostfr|multi|truefrench|french|complet|complete|hd|4k)\b", re.I)
RE_NB_SAISONS = re.compile(r"(\d{1,2})\s*saisons?", re.I)


def saison_ep(titre, saison_defaut):
    """→ (saison, episode) ou None."""
    t = titre.replace("‧", " ")
    m = RE_SE.search(t) or RE_X.search(t) or RE_SAISON_EP.search(t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = RE_EP_SEUL.search(t) or RE_E_SEUL.search(t)
    if m:
        return (saison_defaut or 1), int(m.group(1))
    return None


def nettoyer_nom_serie(nom):
    """« Columbo 1968 ‧ Mystère ‧ 10 saisons. » → ("Columbo", 1968, 10)."""
    brut = html.unescape(nom).replace("‧", "|").replace("·", "|")
    partie = brut.split("|")[0]
    y = RE_ANNEE.search(brut)
    annee = int(y.group(1)) if y else 0
    nb = RE_NB_SAISONS.search(brut)
    nb_saisons = int(nb.group(1)) if nb else 0
    saison_seule = re.search(r"saison\s*(\d{1,2})", brut, re.I)
    saison_defaut = int(saison_seule.group(1)) if saison_seule and not nb else 0
    t = RE_ANNEE.sub(" ", partie)
    t = t.replace('"', " ").replace("_", " ").replace("«", " ").replace("»", " ")
    t = re.sub(r"\(\s*\)", " ", t)
    t = re.sub(r"\(\s*archives?\s*\)", " ", t, flags=re.I)
    # « Saison II », « Saison III » (chiffres romains) ; « Saison 2 d'Au-delà du réel » → reste « Au-delà du réel »
    t = re.sub(r"saisons?\s+[ivx]+\b", " ", t, flags=re.I)
    t = re.sub(r"[&]\s*\d+", " ", t)
    t = re.sub(r"\b(vostvf|vostfr|vf|mini|complète|complete|version longue)\b", " ", t, flags=re.I)
    t = re.sub(r"saisons?\s*\d{1,2}(\s*(?:de|a|à|-|et)\s*\d{1,2}){0,2}", " ", t, flags=re.I)
    t = re.sub(r"\d{1,2}\s*sai\w*", " ", t, flags=re.I)          # « 6 saisons », « 6 saiaons » (coquille vue)
    # « Saison 2 d'Au-delà du réel » → une fois « Saison 2 » retiré, on enlève le « de / d' » de tête
    t = re.sub(r"^[\s.\-–:]*(?:de\s+|d')", "", t, flags=re.I)
    # ⚠ pas de mots de genre courants ici (« aventure », « action »… font partie de vrais titres) ;
    #   les genres du site sont après le « ‧ », déjà coupés plus haut.
    t = re.sub(r"\b(serie tv|série tv|la serie|la série|série|serie|episodes?|complet|complete|vf|integrale|intégrale"
               r"|sitcom|science-fiction)\b", " ", t, flags=re.I)
    t = re.sub(r"[\s.\-–,:;|]+$", "", t)
    t = re.sub(r"^[\s.\-–,:;|]+", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" .-–:")
    return t, annee, nb_saisons, saison_defaut


def nettoyer_titre_film(titre):
    t = html.unescape(titre)
    y = RE_ANNEE.search(t)
    annee = int(y.group(1)) if y else 0
    t = t.split("‧")[0].split("·")[0]                        # « Pale Rider ‧ Western ‧ 1h 56m »
    t = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", t)          # (1968) [VF]
    t = RE_ANNEE.sub(" ", t)
    t = RE_QUAL.sub(" ", t)
    t = re.sub(r"\b(streaming|en fran[cç]ais|film complet|film entier|en vost(?:fr)?|vost(?:fr)?|film en|en entier|fr)\b", " ", t, flags=re.I)
    t = t.replace(".", " ").replace("_", " ").replace('"', " ").replace("«", " ").replace("»", " ")
    t = re.sub(r"\s*[-–|;]\s*(film complet|complet|vf|en francais|en français).*$", " ", t, flags=re.I)
    # « Carambolages - Louis de Funès NB » : on coupe le suffixe acteur/qualité quand il reste un vrai titre devant
    for sep in (" - ", " – ", " ; ", " | "):
        if sep in t and len(t.split(sep)[0].strip()) >= 3:
            t = t.split(sep)[0]
    t = re.sub(r"\b(louis de fun[eéè]s|fernandel|bourvil|de fun[eéè]s|nb|noir et blanc|colori[sz][ée]e?|collor)\b.*$", " ", t, flags=re.I)
    t = re.sub(r"\s{2,}", " ", t).strip(" .-–:|;,")
    return t, annee


def secondes(dur):
    try:
        p = [int(x) for x in (dur or "0").split(":")]
    except ValueError:
        return 0
    while len(p) < 3:
        p.insert(0, 0)
    return p[0] * 3600 + p[1] * 60 + p[2]


# ───────────────────────────────────────────── une playlist
RE_ID = re.compile(r'global_tokyvideo_serie_id\s*=\s*"(\d+)"')
RE_NOM = re.compile(r"d\.nameSerie\s*=\s*'((?:[^'\\]|\\.)*)'")


def lire_playlist(slug, titre_liste):
    r = get(f"{SITE}/fr/serie/{slug}")
    if not r:
        return None
    m = RE_ID.search(r.text)
    if not m:
        return None
    sid = m.group(1)
    nom = titre_liste
    mn = RE_NOM.search(r.text)
    if mn:
        nom = html.unescape(mn.group(1).replace("\\'", "'"))
    videos, page = [], 1
    while page <= 200:
        rr = get(f"{API}/videos/serie/{sid}", params={"page": page},
                 headers={"Referer": f"{SITE}/fr/serie/{slug}", "Origin": SITE, "Accept": "application/json"})
        if not rr:
            if page == 1:
                return None      # échec dès la 1re page → playlist inconnue, pas « vide »
            break
        try:
            j = rr.json()
        except ValueError:
            break
        vids = j.get("videos") or []
        videos.extend(vids)
        if not j.get("showMore") or not vids:
            break
        page += 1
    return sid, nom, videos


def slug_de(url):
    return (url or "").rstrip("/").split("/")[-1]


def construire(slug, titre_liste):
    lu = lire_playlist(slug, titre_liste)
    if not lu:
        return None
    sid, nom, videos = lu
    t, annee, nb_saisons, saison_defaut = nettoyer_nom_serie(nom)
    if not t:
        return None
    eps, films = {}, []
    for v in videos:
        vt = html.unescape(v.get("title") or "")
        vid = str(v.get("id") or "")
        vslug = slug_de(v.get("url"))
        if not vid or not vslug:
            continue
        dur = secondes(v.get("duration"))
        se = saison_ep(vt, saison_defaut)
        if se:
            if dur < 12 * 60:      # génériques, extraits
                continue
            s, e = se
            eps.setdefault(str(s), []).append([e, vid, vslug, vt[:80]])
        else:
            if dur < 55 * 60:      # un film dure plus de 55 min ; le reste = clips, tendances, chats…
                continue
            ft, fy = nettoyer_titre_film(vt)
            if len(ft) >= 2:
                films.append({"vid": vid, "slug": vslug, "t": ft, "y": fy,
                              "img": v.get("thumb") or "", "dur": v.get("duration") or "", "col": t})
    # Une playlist est une « série » si la majorité de ses vidéos ont un SxxExx.
    n_eps = sum(len(l) for l in eps.values())
    serie = None
    if n_eps >= 2 and n_eps >= max(1, len(videos)) * 0.4:
        for s in eps:
            # dédoublonnage par numéro d'épisode (garde le premier = le plus récent côté API)
            vus, liste = set(), []
            for it in sorted(eps[s], key=lambda x: x[0]):
                if it[0] in vus:
                    continue
                vus.add(it[0])
                liste.append(it)
            # 2026-09-06 (user : « saison 2 épisode 1, le serveur n'est pas apparu ») : certains
            #   uploadeurs numérotent EN CONTINU (Columbo : S02.E10…E17, S03.E18…). Une saison
            #   qui ne commence pas à 1 est renumérotée à partir de 1 (numéro d'origine conservé
            #   en 5e position, pour information).
            eps[s] = liste
        # Renumérotation SEULEMENT si la saison enchaîne exactement sur la précédente (Columbo :
        #   S1 = 1..9, S2 = 10..17, S3 = 18..24). Une saison bien numérotée mais incomplète
        #   (épisodes 3..10 parce que 1 et 2 manquent) reste telle quelle — vérifié par le user
        #   sur Code Quantum, numérotée proprement par saison.
        fin_orig = {s: max(it[0] for it in l) for s, l in eps.items() if l}   # bornes AVANT renumérotation
        for s in sorted(eps, key=lambda x: int(x)):
            liste = eps[s]
            fin_prec = fin_orig.get(str(int(s) - 1))
            if fin_prec is None or not liste:
                continue
            mn = min(it[0] for it in liste)
            if mn > 1 and mn == fin_prec + 1:
                eps[s] = [[it[0] - mn + 1, it[1], it[2], it[3], it[0]] for it in liste]
        img = ""
        for v in videos:
            if v.get("thumb"):
                img = v["thumb"]
                break
        serie = {"id": sid, "slug": slug, "t": t, "y": annee, "k": norm(t), "img": img, "n": n_eps, "s": eps}
        films = []  # les vidéos hors motif d'une vraie série = bonus/inclassables, on ne les garde pas
    if not serie and not films and videos:
        ex = " | ".join(f"{html.unescape(v.get('title') or '')[:45]} [{v.get('duration')}]" for v in videos[:3])
        print(f"  (rien reconnu dans « {nom[:40] }» : {ex})", flush=True)
    return serie, films, len(videos)


def main():
    t0 = time.time()
    print("Tokyvideo — liste des playlists…", flush=True)
    playlists = lister_playlists()
    if LIMIT:
        playlists = playlists[:LIMIT]
    print(f"{len(playlists)} playlists", flush=True)
    series, films, total_videos = [], [], 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(construire, slug, titre): (slug, titre) for slug, titre in playlists}
        for i, f in enumerate(as_completed(futs), 1):
            slug, titre = futs[f]
            try:
                res = f.result()
            except Exception as e:
                print(f"  !! {slug}: {e}", flush=True)
                continue
            if not res:
                continue
            serie, fl, nv = res
            total_videos += nv
            if serie:
                series.append(serie)
                print(f"  [{i}/{len(playlists)}] SERIE {serie['t']} ({serie['y']}) : {serie['n']} ép., {len(serie['s'])} saison(s)", flush=True)
            else:
                films.extend(fl)
                print(f"  [{i}/{len(playlists)}] films  {titre[:50]} : {len(fl)}", flush=True)
    # dédoublonnage films par (titre normalisé, année) — on garde le premier
    vus, films_u = set(), []
    for fm in films:
        cle = (norm(fm["t"]), fm["y"])
        if cle in vus:
            continue
        vus.add(cle)
        fm["k"] = cle[0]
        films_u.append(fm)
    # Fusion des playlists homonymes (« Mercredi saison 01 » + « Mercredi saison 2 », ou deux
    #   uploadeurs pour la même série) : même clé + même année (ou année inconnue d'un côté)
    #   → une seule série, saisons réunies, épisodes déjà présents conservés.
    fusion = {}
    for sr in series:
        cible = None
        for cle, ex in fusion.items():
            if cle[0] == sr["k"] and (cle[1] == sr["y"] or not cle[1] or not sr["y"]):
                cible = ex
                break
        if cible is None:
            fusion[(sr["k"], sr["y"])] = sr
            continue
        for sn, liste in sr["s"].items():
            deja = {e[0] for e in cible["s"].get(sn, [])}
            cible["s"].setdefault(sn, []).extend(e for e in liste if e[0] not in deja)
            cible["s"][sn].sort(key=lambda e: e[0])
        cible["n"] = sum(len(l) for l in cible["s"].values())
        if not cible["y"] and sr["y"]:
            cible["y"] = sr["y"]
        if not cible["img"] and sr["img"]:
            cible["img"] = sr["img"]
    series = list(fusion.values())
    series.sort(key=lambda s: s["t"].lower())
    films_u.sort(key=lambda f: f["t"].lower())
    payload = {"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "series": series, "films": films_u}
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    n_eps = sum(s["n"] for s in series)
    print(f"OK : {len(series)} séries ({n_eps} épisodes), {len(films_u)} films, {total_videos} vidéos lues, "
          f"{os.path.getsize(OUT)//1024} Ko, {int(time.time()-t0)} s", flush=True)
    if len(series) < 20:
        print("!! trop peu de séries, index non fiable", flush=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
