#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_vegeta_vod.py — index VOD (films + séries FR) des serveurs Vegeta.

Pourquoi ici et pas dans l'app : depuis un réseau français, `player_api.php` et
`get.php` des panels Xtream sont coupés (connexion fermée) alors que les FLUX
(/movie/…, /series/…) passent. Les proxies CORS de l'app renvoient 522. Le
runner GitHub, lui, atteint les panels → on construit tout ici, l'app ne
télécharge que du JSON.

Sorties :
  data/vegetatv/vegeta-vod-fr.json       films + séries (1 entrée par œuvre,
                                          jusqu'à 3 serveurs par entrée)
  data/vegetatv/vod-ep/<00..3f>.json      épisodes des séries, 64 shards,
                                          clé "<serveur>:<series_id>"
Format (compact) :
  servers : { "<pos>": {"b": base, "u": user, "p": pass} }
  films   : [ {"t": titre, "y": année, "tmdb": id|0, "img": affiche, "c": catégorie,
               "l": "VF"|"VOSTFR"|"", "s": [[pos, stream_id, ext], …]} ]
  series  : [ {"t", "y", "tmdb", "img", "c", "k": clé normalisée, "s": [[pos, series_id], …]} ]
  shard   : { "<pos>:<series_id>": { "<saison>": [[num, id, ext, titre], …] } }
"""
import os, re, sys, json, time, hashlib, unicodedata, threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

SERVERS_URL = "http://vegetatv.duckdns.org/data/server_status.json"
OUT_INDEX   = os.environ.get("VEGETA_VOD_OUT", "data/vegetatv/vegeta-vod-fr.json")
OUT_EP_DIR  = os.environ.get("VEGETA_VOD_EP_DIR", "data/vegetatv/vod-ep")
MAX_SERVERS = int(os.environ.get("VEGETA_VOD_MAX_SERVERS", "6"))
# 2026-09-07 : panels qui refusent les flux DEPUIS LA FRANCE (HTTP 458) alors qu'ils
#   répondent au runner GitHub (hors France) — le test de flux ci-dessous ne peut pas
#   les voir. Mesuré sur PC + Oppo (FR) : 000006708.xyz → 458 systématique, matin et soir.
#   Hôte seul, sans port. À compléter si un autre panel se comporte pareil.
PANELS_EXCLUS_FR = {
    "000006708.xyz",            # refuse les flux depuis la France (repond au runner, pas a nous)
    "iptvpro.vision-new.org",   # 2026-09-20 : compte a UNE SEULE connexion. Valide jusqu'en
                                #   decembre, mais sature en permanence avec ~40 000 utilisateurs :
                                #   il gagnait sa place de "serveur 1" et echouait pour presque
                                #   tout le monde. Cout du retrait : 1719 films dont il etait la
                                #   seule source disparaissent -- ils etaient de toute facon
                                #   illisibles. POUR LE REACTIVER : mettre un # devant sa ligne.
}
MAX_SRC     = int(os.environ.get("VEGETA_VOD_MAX_SRC", "5"))     # serveurs par film (tous gardés : si un panel tombe, les autres restent)
MAX_SRC_SER = int(os.environ.get("VEGETA_VOD_MAX_SRC_SER", "2")) # serveurs par série (1 fiche épisodes chacun)
EP_WORKERS  = int(os.environ.get("VEGETA_VOD_EP_WORKERS", "32"))
API_TIMEOUT = int(os.environ.get("VEGETA_VOD_API_TIMEOUT", "240"))
NB_SHARDS   = 64
UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")
H = {"User-Agent": UA}

def log(*a):
    print(*a, file=sys.stderr, flush=True)

# ─────────────────────────────────────────────── filtres FR / catégories
RE_CAT_FR = re.compile(r"(?i)(^|[^a-z])(FR|FRA|FRANCE|FRENCH|FRANCAIS|FRANÇAIS|VF|VOSTFR|QUEBEC|QC)([^a-z]|$)")
RE_CAT_ADULT = re.compile(r"(?i)adult|xxx|\+18|18\+|porn|erotic|hot\b")
# Titres : préfixe langue et décorations
RE_PREFIX = re.compile(r"^\s*(?:FR|VF|VFF|FRENCH|FRA|QC|AF)\s*[\-|:–]+\s*", re.I)
RE_YEAR   = re.compile(r"[\(\[]?\b((?:19|20)\d{2})\b[\)\]]?")
RE_LANG   = re.compile(r"(?i)[\(\[]\s*(VOSTFR|VOST|VO|MULTI|FRENCH|VFF|VF|TRUEFRENCH|SUBFRENCH|FRENCH MULTI SUB)[^\)\]]*[\)\]]")
RE_QUAL   = re.compile(r"(?i)\b(4K|UHD|FHD|HDR|HEVC|H265|x265|1080p|720p|CAM|TS|R5|WEBRIP|BLURAY|DOLBY VISION)\b|\((?:FR|VF|VFF|FRENCH)\)")
RE_SPACES = re.compile(r"\s+")
RE_SUFFIX_LANG = re.compile(r"(?i)[\s_\-]+(fr|vf|vff|vost|vostfr|vo|french|multi)\s*$")

def norm(s):
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]", "", s)

def clean_title(raw):
    """« FR - Les Anges (2011) (FR) » → (« Les Anges », 2011, lang)."""
    s = raw or ""
    s = RE_PREFIX.sub("", s)
    lang = ""
    m = RE_LANG.search(s)
    if m:
        tag = m.group(1).upper()
        lang = "VOSTFR" if tag in ("VOSTFR", "VOST", "VO", "SUBFRENCH") else "VF"
        s = RE_LANG.sub(" ", s)
    year = 0
    ym = None
    for ym_ in RE_YEAR.finditer(s):
        ym = ym_
    if ym:
        year = int(ym.group(1))
        s = s[:ym.start()] + " " + s[ym.end():]
    s = RE_QUAL.sub(" ", s)
    s = re.sub(r"[\(\[]\s*[\)\]]", " ", s)
    # Suffixes de langue collés au titre : « Desi Bling FR », « Age of Attraction _fr »,
    #   « Funny AF vost », « Parenthood FR », « ...-fr » (cf. capture Oppo 2026-09-06).
    for _ in range(2):
        m2 = RE_SUFFIX_LANG.search(s)
        if not m2:
            break
        if m2.group(1).lower() in ("vost", "vostfr", "vo"):
            lang = "VOSTFR"
        s = s[:m2.start()]
    s = re.sub(r"\s*[\-–|:]\s*$", "", s)
    s = RE_SPACES.sub(" ", s).strip(" -–|:_")
    return s, year, lang

FILM_CATS = [
    (re.compile(r"(?i)2026|nouveaut|latest|dernier|recent|nouveau"), "Nouveautés"),
    (re.compile(r"(?i)\b2025\b"), "Films 2025"),
    (re.compile(r"(?i)\b2024\b"), "Films 2024"),
    (re.compile(r"(?i)\b2023\b"), "Films 2023"),
    (re.compile(r"(?i)netflix"), "Netflix"),
    (re.compile(r"(?i)disney|marvel|\bdc\b|pixar"), "Disney+ / Marvel"),
    (re.compile(r"(?i)prime|amazon"), "Prime Video"),
    (re.compile(r"(?i)apple"), "Apple TV+"),
    (re.compile(r"(?i)canal|ocs|paramount|mytf1|tf1\+"), "Canal+ / MyTF1"),
    (re.compile(r"(?i)anim|jeunesse|kids|enfant|famil|cartoon|manga|anime"), "Animation & Jeunesse"),
    (re.compile(r"(?i)horreur|horror|thriller|crime|mafia|gangster|policier"), "Thriller & Horreur"),
    (re.compile(r"(?i)action|guerre|war|arts?.?martiaux|western|aventure|adventure"), "Action & Aventure"),
    (re.compile(r"(?i)com[eé]die|comedy|humour"), "Comédie"),
    (re.compile(r"(?i)drame|drama|romance"), "Drame & Romance"),
    (re.compile(r"(?i)sci|fantas|fantasy|sf\b"), "Science-fiction & Fantastique"),
    (re.compile(r"(?i)docu"), "Documentaires"),
    (re.compile(r"(?i)spectacle|concert|stand|humour|theatre|théâtre|show"), "Spectacles & Concerts"),
    (re.compile(r"(?i)noel|noël|christmas"), "Noël"),
    (re.compile(r"(?i)classique|1980|1990|ancien|old|vintage|retro"), "Classiques"),
    (re.compile(r"(?i)afrique|african|bollywood|asiat|asia|cor[eé]e|kor"), "Monde"),
    (re.compile(r"(?i)4k|uhd|dolby|light"), "Films 4K"),
]
SERIE_CATS = [
    (re.compile(r"(?i)my ?tf1|tf1"), "MyTF1"),
    (re.compile(r"(?i)t[eé]l[eé].?r[eé]alit|reality|realite"), "Télé-réalité"),
    (re.compile(r"(?i)2026|nouveaut|latest|dernier|recent|nouveau"), "Nouveautés"),
    (re.compile(r"(?i)\b2025\b"), "Séries 2025"),
    (re.compile(r"(?i)\b2024\b"), "Séries 2024"),
    (re.compile(r"(?i)netflix"), "Netflix"),
    (re.compile(r"(?i)disney|marvel|pixar"), "Disney+ / Marvel"),
    (re.compile(r"(?i)prime|amazon"), "Prime Video"),
    (re.compile(r"(?i)apple"), "Apple TV+"),
    (re.compile(r"(?i)canal|ocs|paramount|hbo|max\b|starz|showtime"), "Canal+ / HBO"),
    (re.compile(r"(?i)anim|jeunesse|kids|enfant|famil|cartoon"), "Animation & Jeunesse"),
    (re.compile(r"(?i)manga|anime"), "Anime & Manga"),
    (re.compile(r"(?i)docu"), "Documentaires"),
    (re.compile(r"(?i)asiat|asia|cor[eé]e|kor|turc|turk|novela|telenovela"), "Monde"),
    (re.compile(r"(?i)com[eé]die|comedy"), "Comédie"),
    (re.compile(r"(?i)drame|drama|romance"), "Drame & Romance"),
    (re.compile(r"(?i)action|thriller|crime|policier|sci|fantas"), "Action & Thriller"),
    (re.compile(r"(?i)ancien|old|classique|vintage"), "Classiques"),
    (re.compile(r"(?i)vost|vo\b"), "VOSTFR"),
]

RE_TMDB_IMG = re.compile(r"^https?://image\.tmdb\.org/t/p/[^/]+(/[A-Za-z0-9_\-]+\.(?:jpg|png|webp))$")

def img(url):
    """Affiche : on ne garde que le chemin TMDB (« /abc.jpg ») quand c'en est un —
    l'app reconstruit https://image.tmdb.org/t/p/w342<chemin>. Sinon l'URL entière."""
    u = str(url or "").strip()
    if not u:
        return ""
    m = RE_TMDB_IMG.match(u)
    return m.group(1) if m else u[:200]

def map_cat(name, table, default):
    for rx, lbl in table:
        if rx.search(name or ""):
            return lbl
    return default

# ─────────────────────────────────────────────── serveurs
def creds(url):
    from urllib.parse import urlparse, parse_qs
    u = urlparse(url); q = parse_qs(u.query)
    return "%s://%s" % (u.scheme, u.netloc), q.get("username", [""])[0], q.get("password", [""])[0]

def api(srv, action, retries=2, timeout=None, **kw):
    """Appel player_api avec 2 nouvelles tentatives : les gros catalogues (150 000 films)
    tombent parfois en 5xx/timeout au premier essai."""
    url = "%s/player_api.php?username=%s&password=%s&action=%s" % (srv["b"], srv["u"], srv["p"], action)
    for k, v in kw.items():
        url += "&%s=%s" % (k, v)
    last = None
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=H, timeout=(15, timeout or API_TIMEOUT))
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            if i < retries:
                time.sleep(3 + 5 * i)
    raise last

def fetch_servers():
    r = requests.get(SERVERS_URL, headers=H, timeout=30)
    r.raise_for_status()
    lst = (r.json() or {}).get("list") or []
    out = []
    for i, o in enumerate(lst):
        if not (o.get("up") or o.get("status") == "up"):
            continue
        url = o.get("url", "") or ""
        if not url.startswith("http"):
            continue
        base, user, pw = creds(url)
        if not user or not pw:
            continue
        hote = re.sub(r"^https?://", "", base).split("/")[0].split(":")[0].lower()
        if hote in PANELS_EXCLUS_FR:
            log("[%2d] %s : exclu (refuse les flux depuis la France)" % (i + 1, base))
            continue
        flag = o.get("flag", "") or ""
        out.append({"pos": i + 1, "b": base, "u": user, "p": pw,
                    "fr": "\U0001F1EB\U0001F1F7" in flag,
                    "ping": int(o.get("response_time_ms", 9999) or 9999)})
    return out

REFUS_FLUX = (401, 403, 456, 458, 512)

def flux_refuse(srv):
    """Lit les 100 premiers octets d'UN film du panel. Renvoie le code HTTP si le panel
    refuse franchement (deux essais à 20 s d'écart), sinon None. Sans film testable ou
    sur erreur réseau → None (on ne condamne pas sur un doute)."""
    # 2026-09-25 (user : « il y a beaucoup moins de contenu en film ») : sansat.cc
    #   (~37 000 films) a été écarté le 21/09 sur un seul 512, alors que ses films se
    #   lisent (vérifié depuis la France le 25/09). 512 = « trop de connexions » sur ces
    #   panels : c'est PASSAGER. Un refus franc (401/403/456/458) sur un film suffit
    #   toujours à écarter le panel ; un 512, lui, ne l'écarte que s'il se répète sur
    #   TROIS films différents (deux essais chacun, à 20 s d'écart).
    try:
        cat = next(iter(srv["vod_cats"]), None)
        vod = api(srv, "get_vod_streams", category_id=cat) if cat else api(srv, "get_vod_streams")
        films = [v for v in vod if v.get("stream_id")][:3]
        if not films:
            return None
    except Exception:
        return None
    hdr = dict(H)
    hdr["Range"] = "bytes=0-100"
    dernier = None
    for st in films:
        url = "%s/movie/%s/%s/%s.%s" % (srv["b"], srv["u"], srv["p"], st["stream_id"],
                                        st.get("container_extension") or "mp4")
        for essai in range(2):
            try:
                r = requests.get(url, headers=hdr, timeout=(15, 20), stream=True, allow_redirects=True)
                code = r.status_code
                r.close()
            except Exception:
                return None
            if code not in REFUS_FLUX:
                return None
            dernier = code
            if essai == 0:
                time.sleep(20)
        if dernier != 512:
            return dernier          # refus franc : un film suffit
    return dernier                  # 512 sur les trois films → panel réellement saturé

def probe_server(srv):
    """Compte les catégories FR ; renvoie None si le panel ne répond pas."""
    try:
        info = api(srv, "get_vod_categories")
        vod_fr = [c for c in info if RE_CAT_FR.search(str(c.get("category_name", "")))]
        sinfo = api(srv, "get_series_categories")
        ser_fr = [c for c in sinfo if RE_CAT_FR.search(str(c.get("category_name", "")))]
        srv["vod_cats"] = {str(c["category_id"]): c["category_name"] for c in vod_fr
                           if not RE_CAT_ADULT.search(str(c.get("category_name", "")))}
        srv["ser_cats"] = {str(c["category_id"]): c["category_name"] for c in ser_fr
                           if not RE_CAT_ADULT.search(str(c.get("category_name", "")))}
        srv["score"] = len(srv["vod_cats"]) + len(srv["ser_cats"])
        log("[%2d] %s : %d cat films FR, %d cat séries FR" % (srv["pos"], srv["b"], len(vod_fr), len(ser_fr)))
        # 2026-09-07 (user : « une série avec un seul serveur qui ne marche pas, et ça arrive
        #   sur plein de séries ») : un panel peut répondre à l'API et REFUSER tous ses flux
        #   (mesuré : 000006708.xyz → 458 depuis la France, 456 depuis le runner ; nasadigitaltv
        #   et vismawe → 401 partout). Ses films/séries étaient publiés quand même = entrées
        #   mortes dans Ciné Films. On lit UN flux réel avant de retenir le panel : refus franc
        #   (401/403/456/458) deux fois de suite → panel écarté. Une page « FAILED TO CONNECT »
        #   en 200 ou un 404 sur ce film précis ne condamne pas le panel (c'est propre au film).
        refus = flux_refuse(srv)
        if refus:
            log("[%2d] %s : flux refusés (HTTP %s) → panel écarté" % (srv["pos"], srv["b"], refus))
            return None
        return srv
    except Exception as e:
        log("[%2d] %s : KO %s" % (srv["pos"], srv["b"], str(e)[:80]))
        return None

# ─────────────────────────────────────────────── catalogue
def ingest(srv, films, series, lock):
    nf = ns = 0
    try:
        vod = api(srv, "get_vod_streams")
    except Exception as e:
        log("[%2d] films KO %s" % (srv["pos"], str(e)[:80])); vod = []
    sig_vod = len(vod)
    for v in vod:
        cid = str(v.get("category_id", ""))
        cname = srv["vod_cats"].get(cid)
        if cname is None:
            continue
        if str(v.get("is_adult", "0")) in ("1", "true"):
            continue
        title, year, lang = clean_title(str(v.get("name", "")))
        if len(title) < 2:
            continue
        tmdb = str(v.get("tmdb", "") or "").strip()
        tmdb = int(tmdb) if tmdb.isdigit() else 0
        key = ("m%d" % tmdb) if tmdb else "m_%s_%d" % (norm(title)[:40], year)
        ext = str(v.get("container_extension", "") or "mp4")
        sid = v.get("stream_id")
        if sid is None:
            continue
        with lock:
            e = films.get(key)
            if e is None:
                e = films[key] = {"id": key, "t": title, "y": year, "tmdb": tmdb,
                                  "img": img(v.get("stream_icon")),
                                  "c": map_cat(cname, FILM_CATS, "Films"),
                                  "l": lang, "s": []}
                nf += 1
            if not e["img"] and v.get("stream_icon"):
                e["img"] = img(v["stream_icon"])
            if len(e["s"]) < MAX_SRC and all(x[0] != srv["pos"] for x in e["s"]):
                e["s"].append([srv["pos"], int(sid), ext])
    try:
        ser = api(srv, "get_series")
    except Exception as e:
        log("[%2d] séries KO %s" % (srv["pos"], str(e)[:80])); ser = []
    for s in ser:
        cid = str(s.get("category_id", ""))
        cname = srv["ser_cats"].get(cid)
        if cname is None:
            continue
        title, year, lang = clean_title(str(s.get("name", "")))
        if len(title) < 2:
            continue
        tmdb = str(s.get("tmdb", "") or "").strip()
        tmdb = int(tmdb) if tmdb.isdigit() else 0
        k = norm(title)
        key = ("t%d" % tmdb) if tmdb else "t_" + k[:40]
        sid = s.get("series_id")
        if sid is None:
            continue
        with lock:
            e = series.get(key)
            if e is None:
                e = series[key] = {"id": key, "t": title, "y": year, "tmdb": tmdb,
                                   "img": img(s.get("cover")),
                                   "c": map_cat(cname, SERIE_CATS, "Séries"),
                                   "k": k, "s": []}
                ns += 1
            if not e["img"] and s.get("cover"):
                e["img"] = img(s["cover"])
            if len(e["s"]) < MAX_SRC_SER and all(x[0] != srv["pos"] for x in e["s"]):
                e["s"].append([srv["pos"], int(sid)])
    log("[%2d] +%d films, +%d séries (brut %d films)" % (srv["pos"], nf, ns, sig_vod))
    return sig_vod, nf + ns

# ─────────────────────────────────────────────── épisodes
RE_EP_TITLE = re.compile(r"(?i)^.*?s\d{1,3}\s*e\d{1,4}\s*[\-–:]?\s*")

def short_ep_title(title, serie_title):
    t = str(title or "")
    t2 = RE_EP_TITLE.sub("", t).strip(" -–:")
    if not t2:
        return ""
    if serie_title and norm(t2) == norm(serie_title):
        return ""
    if re.match(r"(?i)^(episode|épisode)\s*\d+$", t2):
        return ""
    return t2[:60]

def fetch_episodes(srv_by_pos, pos, series_id, serie_title):
    srv = srv_by_pos[pos]
    try:
        # Fiche = petite réponse : 45 s max, sinon un serveur lent bloque la fin du run
        #   (1er run : les 1 500 dernières fiches à 0,75/s avec le timeout de 240 s).
        info = api(srv, "get_series_info", retries=1, timeout=45, series_id=series_id)
    except Exception as e:
        return None
    eps = info.get("episodes") or {}
    if isinstance(eps, list):   # certains panels rendent une liste de listes
        eps = {str(i + 1): v for i, v in enumerate(eps)}
    out = {}
    for season, lst in eps.items():
        if not isinstance(lst, list):
            continue
        arr = []
        for e in lst:
            try:
                eid = int(e.get("id"))
            except Exception:
                continue
            num = e.get("episode_num", 0)
            try:
                num = int(num)
            except Exception:
                num = 0
            ext = str(e.get("container_extension", "") or "mp4")
            arr.append([num, eid, ext, short_ep_title(e.get("title", ""), serie_title)])
        if arr:
            arr.sort(key=lambda x: x[0])
            out[str(season)] = arr
    return out

def shard_of(key):
    return int(hashlib.md5(key.encode("utf-8")).hexdigest()[:2], 16) % NB_SHARDS

# ─────────────────────────────────────────────── main
def main():
    t0 = time.time()
    servers = fetch_servers()
    log("%d serveurs up" % len(servers))
    with ThreadPoolExecutor(max_workers=8) as ex:
        probed = [s for s in ex.map(probe_server, servers) if s]
    # ne garder que ceux qui ont un vrai catalogue FR
    probed = [s for s in probed if len(s["vod_cats"]) >= 5 or len(s["ser_cats"]) >= 3]
    # FR d'abord, puis catalogue le plus riche, puis ping
    probed.sort(key=lambda s: (0 if s["fr"] else 1, -s["score"], s["ping"]))
    log("candidats : %s" % [(s["pos"], s["score"]) for s in probed])

    films, series, lock = OrderedDict(), OrderedDict(), threading.Lock()
    kept = []
    seen_sig = set()
    # Ingestion séquentielle par serveur pour pouvoir écarter les miroirs
    #   (même catalogue = même nombre brut de films) sans les compter 2 fois.
    for s in probed:
        if len(kept) >= MAX_SERVERS:
            break
        sig, ajoutes = ingest(s, films, series, lock)
        if ajoutes == 0:
            continue
        if sig and sig in seen_sig:
            log("[%2d] miroir (signature %d) → conservé comme source alternative seulement" % (s["pos"], sig))
        if sig:
            seen_sig.add(sig)
        kept.append(s)
    log("%d serveurs retenus, %d films, %d séries uniques (%.0fs)" %
        (len(kept), len(films), len(series), time.time() - t0))
    # ── 2026-09-06 (user : « des doublons dans plusieurs catégories ») — FUSION titre+année.
    #   Le serveur 25 ne donne pas d'identifiant TMDB alors que 53/54 (même catalogue) le
    #   donnent → le même film sortait 2 fois (clé « m<tmdb> » et clé « m_<titre>_<année> »),
    #   parfois dans 2 catégories. On fusionne : on GARDE l'entrée avec TMDB et on lui AJOUTE
    #   tous les serveurs de l'autre — aucun serveur n'est perdu (jusqu'à MAX_SRC=5), au
    #   contraire chaque film gagne les serveurs de ses doublons. Catégorie : la plus précise
    #   (tout sauf « Films » / « Séries » / « Nouveautés » quand une autre existe).
    def fusionner(table, cle_de, cap):
        par = {}
        for key, e in list(table.items()):
            k2 = cle_de(e)
            if k2 not in par:
                par[k2] = key; continue
            k1 = par[k2]; a = table[k1]; b = e
            garde, autre = (a, b) if (a["tmdb"] or not b["tmdb"]) else (b, a)
            gk = k1 if garde is a else key
            for src in autre["s"]:
                if len(garde["s"]) < cap and all(x[0] != src[0] for x in garde["s"]):
                    garde["s"].append(src)
            if not garde["img"] and autre["img"]: garde["img"] = autre["img"]
            if not garde["y"] and autre["y"]: garde["y"] = autre["y"]
            generiques = ("Films", "Séries", "Nouveautés")
            if garde["c"] in generiques and autre["c"] not in generiques: garde["c"] = autre["c"]
            if garde.get("l") == "" and autre.get("l"): garde["l"] = autre["l"]
            del table[key if garde is a else k1]
            par[k2] = gk
    nf0, ns0 = len(films), len(series)
    fusionner(films, lambda e: (norm(e["t"]), e["y"]), MAX_SRC)
    fusionner(series, lambda e: (norm(e["t"]), e["y"]), MAX_SRC_SER)
    log("fusion titre+année : films %d → %d, séries %d → %d" % (nf0, len(films), ns0, len(series)))

    # Épisodes : pour chaque (serveur, série) retenu. ⚠ Tout serveur cité par une source doit
    #   être connu ici (1er run : un serveur dont la liste de films avait échoué mais qui avait
    #   fourni des séries manquait → KeyError sur 25 000 jobs).
    srv_by_pos = {s["pos"]: s for s in probed}
    # 2 passes (1er run sur le runner : 3,7 fiches/s → 2 h pour 25 800 fiches) :
    #   passe 1 = source PRINCIPALE de chaque série ; passe 2 = source de secours
    #   UNIQUEMENT pour les séries dont la principale a échoué. ~moitié moins d'appels.
    shards = [dict() for _ in range(NB_SHARDS)]
    # Limite par serveur pour ne pas se faire bannir : 6 requêtes simultanées / panel.
    sem = {pos: threading.Semaphore(6) for pos in srv_by_pos}
    # 2026-09-07 : DISJONCTEUR par panel. Le run du soir a été annulé à 120 min : un panel
    #   qui ne répond plus fait attendre 45 s par fiche, 6 fiches à la fois, sur des
    #   milliers de fiches (2 451 KO au run précédent) — c'est lui qui mange le budget.
    #   Après SEUIL_MORT échecs consécutifs sur un panel, ses fiches restantes sont
    #   marquées KO sans requête ; une réussite remet le compteur à zéro.
    SEUIL_MORT = 40
    echecs_suite = {pos: 0 for pos in srv_by_pos}
    morts = set()
    verrou_morts = threading.Lock()
    def run(job):
        key, pos, sid, title = job
        if pos in morts:
            return job, None
        try:
            with sem[pos]:
                eps = fetch_episodes(srv_by_pos, pos, sid, title)
        except Exception as e:      # jamais laisser une fiche tuer le run entier
            log("  fiche %s:%s KO %s" % (pos, sid, str(e)[:60]))
            eps = None
        with verrou_morts:
            if eps:
                echecs_suite[pos] = 0
            else:
                echecs_suite[pos] += 1
                if echecs_suite[pos] >= SEUIL_MORT and pos not in morts:
                    morts.add(pos)
                    log("  panel %d : %d fiches KO d'affilée → on n'insiste plus" % (pos, SEUIL_MORT))
        return job, eps
    done = fail = 0
    def passe(jobs, libelle):
        nonlocal done, fail
        rates = set()
        log("%s : %d fiches séries à récupérer" % (libelle, len(jobs)))
        with ThreadPoolExecutor(max_workers=EP_WORKERS) as ex:
            for fut in as_completed([ex.submit(run, j) for j in jobs]):
                (key, pos, sid, title), eps = fut.result()
                if eps:
                    sk = "%d:%d" % (pos, sid)
                    shards[shard_of(sk)][sk] = eps
                    done += 1
                else:
                    fail += 1
                    rates.add(key)
                if (done + fail) % 250 == 0:
                    log("  épisodes : %d ok, %d KO (%.0fs)" % (done, fail, time.time() - t0))
        return rates
    principales = [(key, e["s"][0][0], e["s"][0][1], e["t"]) for key, e in series.items()]
    rates = passe(principales, "passe 1 (source principale)")
    # 2026-09-06 (user : « si un serveur est coupé, on perd le film ») : la passe 2 récupère
    #   la fiche de la 2e source pour TOUTES les séries qui en ont une (pas seulement les échecs),
    #   sinon le nettoyage ci-dessous la supprimait et chaque série n'avait plus qu'un serveur.
    #   Coût mesuré : ~14 000 fiches en 21 min → ~40 min pour les deux passes, sous la limite.
    secours = [(key, e["s"][1][0], e["s"][1][1], e["t"])
               for key, e in series.items() if len(e["s"]) > 1]
    if secours:
        passe(secours, "passe 2 (2e source de chaque série)")
    log("épisodes : %d fiches ok, %d KO" % (done, fail))
    # Une série sans AUCUNE fiche d'épisodes ne sert à rien : on l'enlève.
    ok_keys = set()
    for sh in shards:
        ok_keys.update(sh.keys())
    for key in list(series.keys()):
        e = series[key]
        e["s"] = [x for x in e["s"] if ("%d:%d" % (x[0], x[1])) in ok_keys]
        if not e["s"]:
            del series[key]

    payload = {
        "savedAt": int(time.time() * 1000),
        "generatedBy": "nx-data cron (refresh_vegeta_vod.py)",
        "servers": {str(p): {"b": srv_by_pos[p]["b"], "u": srv_by_pos[p]["u"], "p": srv_by_pos[p]["p"]}
                    for p in sorted(set([x[0] for e in films.values() for x in e["s"]] +
                                        [x[0] for e in series.values() for x in e["s"]]))
                    if p in srv_by_pos},
        "films": list(films.values()),
        "series": list(series.values()),
    }
    os.makedirs(os.path.dirname(OUT_INDEX), exist_ok=True)
    with open(OUT_INDEX, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    os.makedirs(OUT_EP_DIR, exist_ok=True)
    for i, sh in enumerate(shards):
        with open(os.path.join(OUT_EP_DIR, "%02x.json" % i), "w", encoding="utf-8") as fh:
            json.dump(sh, fh, ensure_ascii=False, separators=(",", ":"))
    log("OK : %d films, %d séries, %d fiches épisodes → %s (%.0f Ko) en %.0fs" %
        (len(films), len(series), done, OUT_INDEX, os.path.getsize(OUT_INDEX) / 1024, time.time() - t0))

if __name__ == "__main__":
    main()
