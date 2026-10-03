#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
registre_olatv.py — Ola TV : REGISTRE COMPLET des chaînes FR (version 2), façon Vegeta.

Pourquoi (2026-10-03, mesuré sur une TV) : l'app ne scannait qu'une poignée de portails
à chaque lancement (7 actifs, 40-50 s de scan, ~41 sources pour TF1) alors que des
centaines d'autres ont les chaînes. Ici le runner fait le travail lourd une seule fois.

Ce qu'on a appris en explorant l'API :
  · un cid n'est PAS un portail : chaque appel getToken128910 rend un COMPTE différent
    (10 tirages = 10 MAC), parfois sur un autre serveur, avec un abonnement différent
    (de 2 371 à 16 663 chaînes ; certains ont 668 chaînes FR, d'autres aucune). On tire
    donc plusieurs comptes par cid et on range tout PAR SERVEUR (les `cmd` d'un serveur
    sont les mêmes pour tous ses comptes).
  · beaucoup de portails écrivent `┃FR┃ TÉLÉVISION DE BASE+` / `┃FR┃ TF1 FHD` : l'ancienne
    règle (`fr|`, `fr `, « france ») ne les voyait pas → portails entiers ignorés.

Le statut sert au CLASSEMENT, jamais à la suppression (le runner GitHub, en IP de
datacenter, ne voit pas exactement ce que voit l'appareil) :
  ok          : le flux envoie de la vidéo
  refus       : HTTP 456/401/403/429/451 (compte saturé ou bloqué, souvent passager)
  remplissage : redirection vers un fichier fixe (mesuré : .../video/black.ts, noir+muet)
  mort        : pas de réponse, create_link KO
Aucun identifiant de compte (MAC) n'est publié : l'app tire elle-même un compte du cid
auprès de l'API OLA, puis prend les chaînes du serveur sur lequel elle tombe.

Sortie (version 2) — data/olatv/registre-fr.json.gz :
  {"version": 2, "generated_at": …,
   "portails": [{"cid","hote","statut","nb","comptes","comptes_fr","test","ms"}, …]  (classés)
   "chaines":  {"TF1": [[i, "cmd"], …], …}}   i = index dans "portails"
Env : OLA_REG_CIDS ("api" = tous les cids OLA, sinon chemin/URL de live-cids.json),
      OLA_REG_TIRAGES (4), OLA_REG_WORKERS (12), OLA_REG_MAX (0 = tous), OLA_REG_TMO (8),
      OLA_REG_PAGES (30), OLA_REG_OUT, OLA_REG_SUIVI (cids à détailler, séparés par des virgules)
"""
import gzip, json, os, re, sys, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from noms_app import regrouper  # règles de nom de l'app, portées en Python (registre v3)
from refresh_olatv import get_servers, get_mac, MAG_UA  # mêmes appels OLA que le classement

SOURCE_CIDS = os.environ.get("OLA_REG_CIDS", "api")
OUT = os.environ.get("OLA_REG_OUT", "data/olatv/registre-fr.json.gz")
TIRAGES = int(os.environ.get("OLA_REG_TIRAGES", "4"))
WORKERS = int(os.environ.get("OLA_REG_WORKERS", "12"))
MAX = int(os.environ.get("OLA_REG_MAX", "0"))
TMO = int(os.environ.get("OLA_REG_TMO", "8"))
MAX_PAGES = int(os.environ.get("OLA_REG_PAGES", "30"))
SUIVI = [c.strip() for c in os.environ.get("OLA_REG_SUIVI", "").split(",") if c.strip()]
# Plafond de sources par nom de chaîne (les mieux classées d'abord) : garde le registre
#   léger pour les appareils modestes (Fire Stick, Chromecast) qui le lisent.
MAX_SOURCES = int(os.environ.get("OLA_REG_MAX_SOURCES", "15"))  # par nom exact (les meilleurs portails d'abord) : ~1 Mo gz, supportable par une TV
PLAYER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36")
ORDRE_STATUT = {"ok": 0, "refus": 1, "mort": 2, "remplissage": 3}
SANS_MAC = re.compile(r"([?&]mac=)[^&\s]*", re.I)
# v3 : plafond de sources par chaîne regroupée (clé de l'app), meilleurs portails d'abord.
MAX_PAR_CLE = int(os.environ.get("OLA_REG_MAX_PAR_CLE", "100"))
# Clés (règles de l'app) des chaînes qui n'émettent plus — retirées du registre publié.
CHAINES_ARRETEES = ("nrj12", "nrj12lq", "c8", "c8lq")  # NRJ 12 et C8 : arrêtées le 28/02/2025

# Catégorie FR : « FR »/« FRA » en mot isolé, quelle que soit la décoration (┃FR┃, [FR],
# FR|, FR:…), ou france/french/français. « AFR » (Afrique) n'est PAS du FR.
FR_GENRE = re.compile(r"(^|[^a-z])(fr|fra)([^a-z]|$)|france|french|fran[cç]ais", re.I)
ADULTE = re.compile(r"xxx|adult|\+ ?18|18 ?\+|porn|érot|erot", re.I)
# Nettoyage des noms : celui de l'app (OlaTvProvider) + l'étiquette décorée « ┃FR┃ ».
TAG_FR = re.compile(r"^[\W_]*(FR|FRA)[\s|:┃│\]\)\-]+", re.I)
PREFIXE = re.compile(r"^(FR|ES|PT|EN|DE|IT|AR|TR|NL|PL|RO|US|UK|BE|CH)[|:\s]+", re.I)
PUCE = re.compile(r"^[\-•●○▪►‣⮞›»•]+\s*")
# Chaînes « témoin » pour le test : un serveur est jugé sur une grande chaîne s'il l'a
TEMOINS = [re.compile(p, re.I) for p in (r"^tf1\b", r"^france ?2\b", r"^m6\b", r"^france ?3\b")]


def genre_fr(titre):
    return bool(FR_GENRE.search(titre)) and not ADULTE.search(titre)


def nettoyer(nom):
    n = TAG_FR.sub("", nom.strip())
    n = PREFIXE.sub("", n)
    n = PUCE.sub("", n)
    return re.sub(r"\s+", " ", n).strip()


def meilleur(a, b):
    if a is None:
        return b
    return a if ORDRE_STATUT.get(a, 9) <= ORDRE_STATUT.get(b, 9) else b


def est_fichier_remplissage(url):
    """Redirection vers un fichier fixe au lieu d'un direct (ex. /video/black.ts)."""
    u = (url or "").lower().split("?")[0]
    return "black" in u or bool(re.search(r"/video/[^/]+\.(ts|mp4|m4v)$", u))


def tester_flux(url):
    """Lit au plus 64 Ko du flux puis coupe : on occupe la connexion le moins possible."""
    h = {"User-Agent": PLAYER_UA}
    try:
        r = requests.get(url, headers=h, timeout=TMO, stream=True, allow_redirects=False)
        sauts = 0
        while r.is_redirect and sauts < 3:
            loc = r.headers.get("Location", "")
            r.close()
            if est_fichier_remplissage(loc):
                return "remplissage"
            url = urllib.parse.urljoin(url, loc)
            r = requests.get(url, headers=h, timeout=TMO, stream=True, allow_redirects=False)
            sauts += 1
        if r.status_code in (401, 403, 429, 451, 456):
            r.close(); return "refus"
        if r.status_code >= 400:
            r.close(); return "mort"
        # Un direct n'a pas de taille fixe ; un fichier statique (taille + Range) si.
        if r.headers.get("Content-Length", "").isdigit() and \
                r.headers.get("Accept-Ranges", "").lower() == "bytes":
            r.close(); return "remplissage"
        lu, t0 = 0, time.time()
        for bloc in r.iter_content(8192):
            lu += len(bloc)
            if lu >= 65536 or time.time() - t0 > TMO:
                break
        r.close()
        return "ok" if lu >= 16384 else "mort"
    except Exception:
        return "mort"


class Portail:
    """Session Stalker (même protocole que l'app : handshake → profil → genres → pages)."""

    def __init__(self, base, mac):
        self.portal = base.rstrip("/") + "/portal.php"
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": MAG_UA,
            "Cookie": f"mac={urllib.parse.quote(mac)}; stb_lang=en; timezone=Europe%2FLondon",
        })

    def appel(self, **params):
        q = "&".join(f"{k}={v}" for k, v in params.items())
        r = self.s.get(f"{self.portal}?{q}&JsHttpRequest=1-xml", timeout=TMO)
        return json.loads(r.text).get("js")

    def connecter(self):
        js = self.appel(type="stb", action="handshake", token="")
        token = js.get("token", "") if isinstance(js, dict) else ""
        if not token:
            return False
        self.s.headers["Authorization"] = "Bearer " + str(token)
        try:
            self.appel(type="stb", action="get_profile", auth_token=token)
        except Exception:
            pass
        return True

    def genres_fr(self):
        genres = self.appel(type="itv", action="get_genres") or []
        return [str(g.get("id", "")) for g in genres
                if isinstance(g, dict) and genre_fr(str(g.get("title", "")))]

    def chaines(self, genre_ids):
        vues, out = set(), []
        for gid in genre_ids:
            page, nb_pages = 1, 1
            while page <= nb_pages and page <= MAX_PAGES:
                try:
                    js = self.appel(type="itv", action="get_ordered_list", genre=gid,
                                    force_ch_link_check="", fav="0", sortby="name", p=page)
                except Exception:
                    break
                js = js if isinstance(js, dict) else {}
                data = js.get("data") or []
                if page == 1:
                    total = int(js.get("total_items", 0) or 0)
                    par_page = int(js.get("max_page_items", 14) or 14)
                    nb_pages = max(1, -(-total // max(1, par_page)))
                for ch in data:
                    if not isinstance(ch, dict):
                        continue
                    nom, cmd = str(ch.get("name", "")).strip(), str(ch.get("cmd", "")).strip()
                    if not nom or nom.startswith("#") or not cmd:
                        continue
                    n = nettoyer(nom)
                    if n and n not in vues:
                        vues.add(n)
                        out.append((n, cmd))
                if not data:
                    break
                page += 1
        return out

    def tester(self, cmd):
        brut = cmd.replace("ffrt ", "").replace("ffmpeg ", "").strip()
        if brut.startswith("http") and "localhost" not in brut and "127.0.0.1" not in brut:
            url = brut
        else:
            js = self.appel(type="itv", action="create_link", cmd=urllib.parse.quote(brut, safe=""),
                            series="", forced_storage="undefined", fav="0")
            url = str(js.get("cmd", "")) if isinstance(js, dict) else ""
            url = url.replace("ffrt ", "").replace("ffmpeg ", "").strip()
        return tester_flux(url) if url.startswith("http") else "mort"


def choisir_temoin(chaines):
    for motif in TEMOINS:
        for nom, cmd in chaines:
            if motif.search(nom):
                return nom, cmd
    return chaines[0]


def traiter_cid(cid):
    """Tire TIRAGES comptes du cid ; pour chaque serveur rencontré, liste les chaînes FR
    avec le 1er compte qui en a, puis teste la chaîne témoin (2 comptes FR max)."""
    t0 = time.time()
    vus, par_hote = set(), {}
    for _ in range(TIRAGES):
        creds = get_mac(cid)
        if not creds or creds in vus:
            continue
        vus.add(creds)
        base, mac = creds
        hote = urllib.parse.urlparse(base).netloc or base
        h = par_hote.setdefault(hote, {"comptes": 0, "comptes_fr": 0, "chaines": [],
                                       "statut": None, "test": "", "essais": 0})
        h["comptes"] += 1
        p = Portail(base, mac)
        try:
            if not p.connecter():
                continue
            ids = p.genres_fr()
            if not ids:
                continue
            h["comptes_fr"] += 1
            if not h["chaines"]:
                h["chaines"] = p.chaines(ids)
            if h["chaines"] and h["statut"] != "ok" and h["essais"] < 2:
                nom, cmd = choisir_temoin(h["chaines"])
                h["test"] = nom
                h["essais"] += 1
                h["statut"] = meilleur(h["statut"], p.tester(cmd))
        except Exception:
            pass
        finally:
            p.s.close()
    return cid, par_hote, int((time.time() - t0) * 1000)


def lire_cids():
    if SOURCE_CIDS == "api":
        cids = get_servers()
    elif SOURCE_CIDS.startswith("http"):
        cids = [str(c) for c in requests.get(SOURCE_CIDS, timeout=30).json().get("cids", [])]
    else:
        with open(SOURCE_CIDS, encoding="utf-8") as f:
            cids = [str(c) for c in json.load(f).get("cids", [])]
    for c in SUIVI:  # les cids suivis sont toujours traités, même avec OLA_REG_MAX
        if c in cids:
            cids.remove(c)
        cids.insert(0, c)
    return cids[:MAX] if MAX > 0 else cids


def main():
    t0 = time.time()
    cids = lire_cids()
    print(f"[registre v2] {len(cids)} cids, {TIRAGES} comptes tirés par cid, "
          f"{WORKERS} en parallèle", flush=True)
    portails, fait = [], 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(traiter_cid, c) for c in cids]
        for fut in as_completed(futs):
            fait += 1
            cid, par_hote, ms = fut.result()
            for hote, h in par_hote.items():
                if h["chaines"]:
                    portails.append({"cid": cid, "hote": hote, "statut": h["statut"] or "mort",
                                     "nb": len(h["chaines"]), "comptes": h["comptes"],
                                     "comptes_fr": h["comptes_fr"], "test": h["test"], "ms": ms,
                                     "_chaines": h["chaines"]})
            if cid in SUIVI:
                print(f"  [suivi {cid}] " + "; ".join(
                    f"{hh}: {v['comptes']} comptes, {v['comptes_fr']} FR, {len(v['chaines'])} chaînes, "
                    f"test {v['test'] or '-'} → {v['statut']}" for hh, v in par_hote.items()) or "rien", flush=True)
            if fait % 50 == 0 or fait == len(cids):
                ok = sum(1 for x in portails if x["statut"] == "ok")
                print(f"  {fait}/{len(cids)} cids — {len(portails)} serveurs FR, {ok} ok "
                      f"({int(time.time() - t0)} s)", flush=True)

    if len(portails) < 10:
        print(f"ATTENTION : {len(portails)} serveurs FR seulement -> on n'écrase rien.", flush=True)
        if os.path.exists(OUT):
            return
        sys.exit(1)

    # Classement : statut du test, puis part de comptes ayant le FR, puis richesse.
    portails.sort(key=lambda p: (ORDRE_STATUT.get(p["statut"], 9),
                                 -(p["comptes_fr"] / max(1, p["comptes"])), -p["nb"]))
    chaines = {}
    for i, p in enumerate(portails):
        for nom, cmd in p.pop("_chaines"):
            lst = chaines.setdefault(nom, [])
            if len(lst) < MAX_SOURCES:
                # Aucun compte dans le fichier public : « mac=… » vidé, l'app y met le sien.
                lst.append([i, SANS_MAC.sub(r"\1", cmd)])
    # v3 (2026-10-03) : noms déjà nettoyés et regroupés avec les règles de l'app (noms_app.py) —
    #   l'app n'a plus à passer 9 000 noms dans ses expressions (~30 s sur une TV modeste).
    regroupees = regrouper(chaines, MAX_PAR_CLE)
    # Chaînes qui n'émettent plus : les portails les listent encore mais n'envoient rien
    #   (chaque source bloque 6 s dans le lecteur). NRJ 12 : arrêtée fin février 2025.
    for arretee in CHAINES_ARRETEES:
        regroupees.pop(arretee, None)
    payload = {"version": 3, "generated_at": int(time.time()), "portails": portails,
               "chaines": regroupees}
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    brut = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(OUT, "wb", compresslevel=9) as f:
        f.write(brut)

    par_statut = {}
    for p in portails:
        par_statut[p["statut"]] = par_statut.get(p["statut"], 0) + 1
    sources = sum(len(g["s"]) for g in regroupees.values())
    print(f"[done] {len(portails)} serveurs FR ({par_statut}) sur {len({p['cid'] for p in portails})} cids, "
          f"{len(chaines)} noms -> {len(regroupees)} chaînes, {sources} sources, {len(brut) // 1024} Ko brut -> "
          f"{os.path.getsize(OUT) // 1024} Ko gz, en {int(time.time() - t0)} s -> {OUT}", flush=True)
    for temoin in ("tf1", "france2", "france3", "m6", "canalplus"):
        lst = regroupees.get(temoin, {}).get("s", [])
        nb_ok = sum(1 for s in lst if portails[s[0]]["statut"] == "ok")
        print(f"  sources {temoin} : {len(lst)} (dont {nb_ok} sur un serveur testé ok)", flush=True)


if __name__ == "__main__":
    main()
