#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_ola_vod.py — index VOD (films FR) des portails OLA TV.

2026-09-26 (user : « si tu es sûr qu'on peut choper du VOD avec Ola TV, on crée le même
script que Vegeta, en décalé, et on s'en sert comme serveur supplémentaire »).

Les ~1 270 serveurs OLA TV sont des portails Stalker (MAG) : en plus du direct, la plupart
exposent un catalogue VOD (type=vod). Mesuré le 26/09 : 38 portails distincts parmi les
cids vivants, 30 avec des catégories FR — souvent le MÊME catalogue servi par plusieurs
adresses (un gros fournisseur français). On regroupe donc les portails par catalogue
(signature = titres des catégories FR) et on n'aspire chaque catalogue qu'UNE fois.

Lecture (côté app) : handshake Stalker (MAC) → type=vod&action=create_link&cmd=<cmd> →
URL /play/movie.php?mac=…&stream=… (jeton de session, ne se construit pas à la main).
L'index garde donc, par film, le `cmd` Stalker et le groupe de portails qui le sert.

Sortie : data/olatv/ola-vod-fr.json
  { "savedAt": ms,
    "groupes": { "<g>": [ {"b": base, "m": mac}, … ] },
    "films":   [ {"g": g, "cmd": cmd, "t": titre, "y": année, "tmdb": id|0,
                  "img": affiche, "c": catégorie}, … ] }
    "series":  [ {"g": g, "sid": id série Stalker, "t", "y", "tmdb", "img", "c"}, … ]
Séries : seules les FICHES sont indexées ; saisons et épisodes sont demandés au portail par
l'app à l'ouverture (type=series&action=get_ordered_list&movie_id=<sid>), puis lus par
create_link(cmd saison, series=<n° épisode>).

2026-09-26 (user : « tu mets un maximum sur les serveurs ») : tous les cids OLA (pas seulement
ceux vivants pour le direct) et jusqu'à 8 MAC par portail.
"""
import base64, json, os, re, sys, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import requests

# 2026-09-26 : résolution DNS de secours (DoH Google) quand le DNS système ne connaît pas
#   un portail (blocage FAI en test local ; sans effet sur GitHub). L'app fait déjà du DoH.
import socket
_gai_systeme = socket.getaddrinfo
_doh_cache = {}
def _gai_doh(host, *a, **k):
    try:
        return _gai_systeme(host, *a, **k)
    except socket.gaierror:
        ip = _doh_cache.get(host)
        if ip is None:
            try:
                j = requests.get("https://dns.google/resolve", params={"name": host, "type": "A"}, timeout=6).json()
                ip = next((x["data"] for x in j.get("Answer", []) if x.get("type") == 1), "")
            except Exception:
                ip = ""
            _doh_cache[host] = ip
        if not ip:
            raise
        return _gai_systeme(ip, *a, **k)
socket.getaddrinfo = _gai_doh

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import refresh_olatv as ola   # protocole API OLA (get_mac) — pas de main() à l'import

CIDS_PATH = os.environ.get("OLA_CIDS", "data/olatv/live-cids.json")
OUT_PATH = os.environ.get("OLA_VOD_OUT", "data/olatv/ola-vod-fr.json")
MAX_CIDS = int(os.environ.get("OLA_VOD_MAX_CIDS", "5000"))
MACS_PAR_HOTE = 8          # MAC valides gardées par portail (secours à la lecture)
MACS_ESSAIS = 60           # MAC essayées au plus par portail
MAX_PAGES = 600
UA = ola.MAG_UA
RE_FR = re.compile(r"(?i)(^|[^A-Z])(FR|FRANCE|FRENCH|VF|VOSTFR|FRAN[CÇ]AIS)([^A-Z]|$)")
RE_EN = re.compile(r"(?i)\|\s*EN\s*\||ENG SUB|ENGLISH")
RE_ADULTE = re.compile(r"(?i)adult|xxx|\+18|18\+|porn|erotic")
RE_PREFIXE = re.compile(r"^\s*(\|?\s*(FR|VF|VOSTFR|MULTI)\s*\|?\s*[-:|]?\s*)+", re.I)
RE_ANNEE = re.compile(r"\((19|20)\d{2}\)\s*$")

def log(*a):
    print(*a, file=sys.stderr, flush=True)

class Portail:
    def __init__(self, base, mac):
        self.base, self.mac = base.rstrip("/"), mac
        self.url = self.base + "/portal.php"
        self.s = requests.Session()
        self.h = {"User-Agent": UA,
                  "Cookie": "mac=%s; stb_lang=en; timezone=Europe%%2FLondon" % urllib.parse.quote(mac)}

    def js(self, qs, timeout=12):
        r = self.s.get(self.url + "?" + qs + "&JsHttpRequest=1-xml", headers=self.h, timeout=timeout)
        return json.loads(r.text).get("js")

    def connecter(self):
        tok = (self.js("type=stb&action=handshake&token=", 8) or {}).get("token")
        if not tok:
            return False
        self.h["Authorization"] = "Bearer " + tok
        self.js("type=stb&action=get_profile", 8)
        return True

    def lien(self, cmd):
        r = self.js("type=vod&action=create_link&cmd=%s&series=&forced_storage=&disable_ad=0&download=0"
                    % urllib.parse.quote(cmd)) or {}
        return (r.get("cmd") or "").replace("ffmpeg ", "").strip()

def sonder(base, mac):
    """Connexion + catégories FR + UN film réellement lisible. None si inutilisable."""
    p = Portail(base, mac)
    try:
        if not p.connecter():
            return None
        cats = p.js("type=vod&action=get_categories") or []
        fr = {str(c["id"]): c.get("title", "") for c in cats if isinstance(c, dict)
              and RE_FR.search(c.get("title", "")) and not RE_EN.search(c.get("title", ""))
              and not RE_ADULTE.search(c.get("title", ""))}
        if not fr:
            return None
        cid = next(iter(fr))
        lst = p.js("type=vod&action=get_ordered_list&category=%s&p=1&sortby=added" % cid) or {}
        it = next((x for x in (lst.get("data") or []) if x.get("cmd")), None)
        if not it:
            return None
        url = p.lien(it["cmd"])
        if not url.startswith("http"):
            return None
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-2000"},
                         timeout=(10, 15), stream=True, allow_redirects=True)
        code = r.status_code
        r.close()
        if code not in (200, 206):
            log("  %s : film refusé (HTTP %s)" % (p.base, code))
            return None
        sig = "|".join(sorted(fr.values()))
        scats = p.js("type=series&action=get_categories") or []
        sfr = {str(c["id"]): c.get("title", "") for c in scats if isinstance(c, dict)
               and RE_FR.search(c.get("title", "")) and not RE_EN.search(c.get("title", ""))
               and not RE_ADULTE.search(c.get("title", ""))}
        return {"b": p.base, "m": mac, "cats": fr, "scats": sfr, "sig": sig, "p": p}
    except (requests.ConnectionError, requests.Timeout) as e:
        log("  %s : injoignable %s" % (base, str(e)[:60]))
        return "RESEAU"
    except Exception as e:
        log("  %s : KO %s" % (base, str(e)[:70]))
        return None

def titre_propre(nom):
    t = RE_PREFIXE.sub("", nom or "").strip()
    m = RE_ANNEE.search(t)
    annee = int(m.group(0)[1:5]) if m else 0
    t = RE_ANNEE.sub("", t).strip(" -")
    return t, annee

def aspirer(p, cats):
    films, vus = [], set()
    for cid, cnom in cats.items():
        page, recus = 1, 0
        while page <= MAX_PAGES:
            try:
                lst = p.js("type=vod&action=get_ordered_list&category=%s&p=%d&sortby=added" % (cid, page)) or {}
            except Exception:
                time.sleep(2)
                try:
                    lst = p.js("type=vod&action=get_ordered_list&category=%s&p=%d&sortby=added" % (cid, page)) or {}
                except Exception:
                    break
            data = lst.get("data") or []
            if not data:
                break
            for it in data:
                cmd = it.get("cmd") or ""
                if not cmd or cmd in vus or str(it.get("censored", "0")) == "1":
                    continue
                vus.add(cmd)
                t, y = titre_propre(it.get("name", ""))
                if len(t) < 2:
                    continue
                if not y:
                    yy = str(it.get("year", "") or "")[:4]
                    y = int(yy) if yy.isdigit() else 0
                tm = str(it.get("tmdb_id", "") or "").strip()
                films.append({"cmd": cmd, "t": t, "y": y, "tmdb": int(tm) if tm.isdigit() else 0,
                              "img": it.get("screenshot_uri") or "", "c": RE_PREFIXE.sub("", cnom).strip()})
            recus += len(data)
            total = int(lst.get("total_items") or 0)
            if total and recus >= total:
                break
            page += 1
        log("    %-40s %d films" % (cnom[:40], recus))
    return films

def aspirer_series(p, cats):
    series, vus = [], set()
    for cid, cnom in cats.items():
        page, recus = 1, 0
        while page <= MAX_PAGES:
            try:
                lst = p.js("type=series&action=get_ordered_list&category=%s&p=%d&sortby=added" % (cid, page)) or {}
            except Exception:
                break
            data = lst.get("data") or []
            if not data:
                break
            for it in data:
                sid = str(it.get("id") or "")
                if not sid or sid in vus or str(it.get("censored", "0")) == "1":
                    continue
                vus.add(sid)
                t, y = titre_propre(it.get("name", ""))
                if len(t) < 2:
                    continue
                if not y:
                    yy = str(it.get("year", "") or "")[:4]
                    y = int(yy) if yy.isdigit() else 0
                tm = str(it.get("tmdb_id", "") or "").strip()
                series.append({"sid": sid, "t": t, "y": y, "tmdb": int(tm) if tm.isdigit() else 0,
                               "img": it.get("screenshot_uri") or "", "c": RE_PREFIXE.sub("", cnom).strip()})
            recus += len(data)
            total = int(lst.get("total_items") or 0)
            if total and recus >= total:
                break
            page += 1
        log("    [séries] %-32s %d" % (cnom[:32], recus))
    return series

def main():
    try:
        cids = ola.get_servers()[:MAX_CIDS]
    except Exception:
        cids = []
    if not cids:
        cids = json.load(open(CIDS_PATH, encoding="utf-8")).get("cids", [])[:MAX_CIDS]
    log("%d cids" % len(cids))
    with ThreadPoolExecutor(16) as ex:
        paires = [x for x in ex.map(lambda c: (lambda r: r)(ola.get_mac(c)), cids) if x]
    # 2026-09-26 (user : « changer d'adresse MAC, si tu peux en avoir plusieurs différentes, ça
    #   peut débloquer ») : on garde TOUTES les MAC connues de chaque portail (jusqu'à 106 pour
    #   certains) et on les essaie une par une jusqu'à en avoir MACS_PAR_HOTE qui marchent,
    #   au lieu des 8 premières seulement. Chemin /c/portal.php essayé si /portal.php échoue.
    par_hote = {}
    for base, mac in paires:
        hote = re.sub(r"^https?://", "", base).split("/")[0].lower()
        ent = par_hote.setdefault(hote, [base.rstrip("/"), []])
        if mac not in ent[1]:
            ent[1].append(mac)
    log("%d portails distincts, %d MAC" % (len(par_hote), sum(len(v[1]) for v in par_hote.values())))
    def sonder_hote(ent):
        base, macs = ent
        ok = []
        for chemin in ("", "/c"):
            for mac in macs[:MACS_ESSAIS]:
                s = sonder(base + chemin, mac)
                if s == "RESEAU":
                    break
                if s:
                    ok.append(s)
                    if len(ok) >= MACS_PAR_HOTE:
                        return ok
            if ok:
                return ok
        return ok
    with ThreadPoolExecutor(24) as ex:
        sondes = [s for lst in ex.map(sonder_hote, par_hote.values()) for s in lst]
    log("%d couples portail/MAC lisibles" % len(sondes))
    groupes = {}
    for s in sondes:
        groupes.setdefault(s["sig"], []).append(s)
    out_g, out_f, out_s = {}, [], []
    for gi, (sig, membres) in enumerate(sorted(groupes.items(), key=lambda kv: -len(kv[1]))):
        log("groupe %d : %d portails, %d catégories FR" % (gi, len(membres), len(membres[0]["cats"])))
        films = aspirer(membres[0]["p"], membres[0]["cats"])
        series = []   # séries : script à part, refresh_ola_series.py (user : « indépendant »)
        if not films and not series:
            continue
        out_g[str(gi)] = [{"b": m["b"], "m": m["m"]} for m in membres]
        for f in films:
            f["g"] = gi
        for x in series:
            x["g"] = gi
        out_f += films
        out_s += series
        log("  → %d films, %d séries" % (len(films), len(series)))
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump({"savedAt": int(time.time() * 1000), "generatedBy": "refresh_ola_vod.py",
                   "groupes": out_g, "films": out_f}, fh, ensure_ascii=False, separators=(",", ":"))
    log("écrit %s : %d films, %d séries, %d groupes" % (OUT_PATH, len(out_f), len(out_s), len(out_g)))

if __name__ == "__main__":
    main()