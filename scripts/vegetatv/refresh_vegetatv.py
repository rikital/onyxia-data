#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_vegetatv.py — genere data/vegetatv/vegeta-fr.json (registre pret a charger)

Le GROS boulot (fetch server_status.json + telechargement/parse des m3u Xtream geants +
filtre FR) est fait ICI, cote GitHub runner. L'app Android n'a plus qu'a fetch ce petit
JSON -> registre instantane, zero parse m3u.

Format de sortie = celui de VegetaTvProvider.saveRegistryCache() (loadRegistryCache le lit).
"""
import os, re, sys, json, time, unicodedata, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

SERVERS_URL   = "http://vegetatv.duckdns.org/data/server_status.json"
OUT_PATH      = os.environ.get("VEGETA_OUT", "data/vegetatv/vegeta-fr.json")
WORKERS       = int(os.environ.get("VEGETA_WORKERS", "8"))
M3U_TIMEOUT   = int(os.environ.get("VEGETA_M3U_TIMEOUT", "40"))
API_TIMEOUT   = int(os.environ.get("VEGETA_API_TIMEOUT", "25"))
MAX_STREAMS   = int(os.environ.get("VEGETA_MAX_STREAMS", "4"))   # par chaine
# 2026-09-13 quater : serveurs SUPPLEMENTAIRES hors liste Vegeta (comptes Xtream trouves sur
#   Telegram etc.). Un fichier texte a cote du script, une ligne par serveur. Voir
#   load_extra_servers(). Rien a changer dans l'app : ils arrivent dans le JSON comme les autres.
EXTRA_PATH    = os.environ.get("VEGETA_EXTRA",
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "servers_extra.txt"))
EXTRA_BASE_IDX = 100   # serverIdx 100, 101, ... : jamais en collision avec les "pos" Vegeta (1-56)
# 2026-09-13 quinquies (user « il ne devrait pas y avoir de limite, le reste suit apres ») :
#   second registre SANS plafond. Il n'est PAS ecrit dans le depot (l'historique git garderait
#   chaque version : le depot pesait deja 315 Mo pour 44 Mo de fichiers) mais publie comme
#   fichier de release, remplace a chaque passage. L'app le charge en arriere-plan, apres le
#   registre leger, quand l'utilisateur entre dans Vegeta TV.
OUT_FULL      = os.environ.get("VEGETA_OUT_FULL", "")
UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")

# Filtre FR de MARQUE (indicatif de VegetaTvProvider.frNameRegex). On RESTREINT a ces marques
#   MEME sur les serveurs FR : sinon on garde les 25000 chaines de chaque serveur -> JSON 192 MB
#   (> limite GitHub 100 MB, push rejete). Le regex ramene les ~centaines de chaines qui comptent.
FR_NAME_RE = re.compile(
    r"\b(TF1|TMC|TFX|LCI|France\s*[1-9]|FR[1-9]|M6|W9|6ter|Gulli|BFM|CNews|LCP|"
    r"FranceInfo|France\s*Info|Arte|RTL9?|NRJ\s*12|NT1|Canal\+|OCS|Cin[ée]\+|"
    r"Paramount\+|TV5|TV\s*5|L'?[ÉEée]quipe|beIN|RMC|Eurosport|13[èe]me\s*Rue|"
    r"Syfy|Discovery|National\s*Geo|Histoire|MCM|Trace|Nostalgie|Disney|Boomerang|"
    r"Cartoon|Tiji|Piwi|Nickelodeon|T[ée]l[ée]toon|TFOU|Mangas|AB1|AB3|RTBF|"
    r"TV5MONDE|France\s*24|FR:|FRENCH|Fran[çc]ais)\b", re.IGNORECASE)

NON_FR_TAG = re.compile(r"(?i)(^|[|\[\(\s])(AR|TR|DE|ES|IT|PT|NL|PL|RO|EN|UK|US|RU|"
                        r"AL|GR|IN|PK|BR|MX|SE|NO|FI|DK|CZ|HU|BG|HR|SRB?|MK)([|\]\)\s]|$)")

# 2026-09-13 : PORT FIDELE de VegetaTvProvider.norm() (Kotlin). L'ancienne version
#   supprimait le "+" au lieu de le convertir en "plus" : "Canal+ Cinema" donnait la cle
#   "canalcinema" alors que l'app cherche "canalpluscinema" -> TOUT Canal+/Cine+ etait
#   bien dans le JSON mais introuvable pour l'app. Toute divergence entre les deux norm()
#   = chaine invisible : ne modifier celle-ci qu'en meme temps que le Kotlin.
_NORM_MID = re.compile(
    r"\b(hd|sd|fhd|uhd|4k|raw|hevc|h\.?265|ppv|ott|test|backup|fhdr|sdr|multi|vip|full ?hd)\b"
    r"|\blive\b(?!\s*\d)|(?:\+\s?1)|(?:1080p|720p|480p|360p)")
_NORM_END = re.compile(r"\s+(fr|french|francais|belgique|be|suisse|ch|lux)\s*$")

_PREFIXE_PAYS = re.compile(
    r"^[\s|\-\u2022\u2502\u2503\u258C\u258E\u258F\u2590\u2588]*"
    r"(BE-FR|FRA|FR|ES|PT|EN|DE|IT|AR|TR|NL|PL|RO|US|UK|BE|CH)"
    r"[|:\s\-\u2502\u2503\u258C\u258E\u258F\u2590\u2588]+", re.IGNORECASE)

# 2026-09-13 bis : tags qualite en EXPOSANT ("ᴴᴰ", "ᵁᴴᴰ", "ᶠᴴᴰ", "⁴ᴷ") — bloc Unicode des
#   petites capitales/exposants (U+1D2C-U+1D6A) + chiffres en exposant (U+2070-U+209F).
#   Aucun vrai nom de chaine n'en contient : on les efface avant normalisation.
_EXPOSANTS = re.compile(r"[\u1D2C-\u1D6A\u2070-\u209F]+")

# 2026-09-13 bis : ALIAS EXPLICITES (pas de regle generale, trop risquee : "CANAL J" n'est pas
#   "CANAL+ J", "CANAL ALPHA" est une chaine suisse). Sur le serveur 52, les chaines Canal+/Cine+
#   existent en double, avec et sans "+" ("CANAL FAMILY" = "CANAL+ FAMILY") ; on les rapproche
#   de la cle curatee de l'app. Polar+ apparait comme "CINE POLAR" / "CINE+ POLAR+".
_ALIAS = {
    "canalcinema": "canalpluscinema", "canalseries": "canalplusseries",
    "canalfamily": "canalplusfamily", "canaldocs": "canalplusdocs",
    "canaldecale": "canalplusdecale", "canalsport": "canalplussport",
    "canalfoot": "canalplusfoot", "canalkids": "canalpluskids",
    "canalgrandecran": "canalplusgrandecran", "canalboxoffice": "canalplusboxoffice",
    "cinepremier": "cinepluspremier", "cinefrisson": "cineplusfrisson",
    "cineemotion": "cineplusemotion", "cinefamiz": "cineplusfamiz",
    "cineclub": "cineplusclub", "cineclassic": "cineplusclassic",
    "cinepolar": "polarplus", "cinepluspolar": "polarplus", "cinepluspolarplus": "polarplus",
}

def norm(name):
    s = _EXPOSANTS.sub(" ", name).lower()
    s = re.sub(r"[\u00e9\u00e8\u00ea\u00eb]", "e", s)
    s = re.sub(r"[\u00e0\u00e2\u00e4]", "a", s)
    s = re.sub(r"[\u00f9\u00fb\u00fc]", "u", s)
    s = re.sub(r"[\u00ee\u00ef]", "i", s)
    s = re.sub(r"[\u00f4\u00f6]", "o", s)
    s = s.replace("\u00e7", "c")
    s = re.sub(r"\[.*?\]", " ", s)
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"^\s*(fr|france)\s*[:|\-]\s*", "", s)
    while True:
        n = _NORM_MID.sub(" ", s)
        n = _NORM_END.sub("", n)
        n = re.sub(r"\.fr\b", "", n)
        n = re.sub(r"\s+", " ", n).strip()
        if n == s:
            break
        s = n
    s = s.replace("+", "plus").replace("&", "and")
    s = re.sub(r"[^a-z0-9]", "", s).replace("sports", "sport")
    return _ALIAS.get(s, s)

def base_display(name):
    return re.sub(r"\s+", " ", re.sub(r"(?i)\b(fhd|uhd|4k|hd|sd|hevc|h265|vip)\b", "", name)).strip() or name

def variant_label(name):
    m = re.search(r"(?i)\b(4K|UHD|FHD|1080|720|HD|SD)\b", name)
    return m.group(1).upper() if m else ""

def is_fr_compatible(raw):
    return not NON_FR_TAG.search(raw)

def fetch_servers():
    r = requests.get(SERVERS_URL, headers={"User-Agent": UA}, timeout=API_TIMEOUT)
    r.raise_for_status()
    lst = (r.json() or {}).get("list") or []
    out = []
    for i, o in enumerate(lst):
        if not (o.get("up") or o.get("status") == "up"):
            continue
        flag = o.get("flag", "") or ""
        pos = int(o.get("pos", i + 1))
        is_fr = ("\U0001F1EB\U0001F1F7" in flag)  # 2026-09-16 : fenetre 32-37 retiree (le JSON n a pas de champ pos, c etait l ordre d apparition, plus les FR)
        is_global = "\U0001F310" in flag
        # 2026-09-13 : on ne jette PLUS les serveurs a drapeau etranger (MX/ES/TR/IT...).
        #   Avant : `if not is_fr and not is_global: continue` ecartait 32 des 46 serveurs
        #   UP. Or ces panels sont des revendeurs multi-pays qui diffusent aussi du FR :
        #   resultat, tf1/m6 etaient dans le JSON mais TOUT Canal+/Cine+ manquait.
        #   FR_NAME_RE en aval ne retient de toute facon que les marques FR, quel que
        #   soit le serveur. rank : FR=0, global=1, etranger=2 (cf. tri final dans main).
        rank = 0 if is_fr else (1 if is_global else 2)
        url = o.get("url", "") or ""
        if not url.startswith("http"):
            continue
        out.append({"pos": pos, "url": url, "isFr": is_fr, "rank": rank,
                    "ping": int(o.get("response_time_ms", 9999))})
    out.sort(key=lambda s: (s["rank"], s["ping"]))
    return out

def ingest_server(srv, registry, lock):
    added = 0
    try:
        r = requests.get(srv["url"], headers={"User-Agent": UA}, timeout=M3U_TIMEOUT)
        m3u = r.text
    except Exception as e:
        print("[Server %s] m3u KO: %s" % (srv["pos"], e), file=sys.stderr)
        m3u = ""
    if "#EXTINF" not in m3u and srv.get("xt"):
        # 2026-09-13 quater : get.php absent/404 -> on passe par player_api (serveurs extra).
        try:
            m3u, fr_only = m3u_from_player_api(srv["xt"])
            if fr_only:
                srv["trustedFr"] = True   # categories FR du panel -> pas de filtre de marque
            print("[Server %s] get.php KO -> player_api OK (categories FR: %s)" % (srv["pos"], fr_only), file=sys.stderr)
        except Exception as e:
            print("[Server %s] player_api KO: %s" % (srv["pos"], e), file=sys.stderr)
            return 0
    if "#EXTINF" not in m3u:
        return 0
    pending = None
    for raw in m3u.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            pending = line
            continue
        if line.startswith("#"):
            if pending:
                pending += " " + line
            continue
        if line.startswith("http"):
            if not pending:
                continue
            ext = pending; pending = None
            raw_name = ext.rsplit(",", 1)[-1].strip()
            if not raw_name:
                continue
            # RESTREINT aux chaines FR de MARQUE (regex) partout (cf. entete).
            if not is_fr_compatible(raw_name):
                continue
            if not srv.get("trustedFr") and not FR_NAME_RE.search(raw_name):
                continue
            # 2026-09-13 bis : le code pays peut etre ENCADRE ("|FR| CANAL+DOCS", "▎FR▎ TF1")
            #   et s'ecrire FRA: ou BE-FR: (serveurs 13 et 52). L'ancien motif exigeait le
            #   code en tout debut et ne connaissait que FR -> cles polluees "frcanalplusdocs",
            #   "fracanalplusboxoffice" jamais retrouvees par l'app. Le code doit RESTER suivi
            #   d'un separateur : c'est ce qui protege "BEIN", "CHERIE 25", "FRANCE 2".
            cleaned = _PREFIXE_PAYS.sub("", raw_name)
            cleaned = re.sub(r"^[\-•●○▪►‣›»]+\s*", "", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if not cleaned:
                continue
            key = norm(cleaned)
            if not key:
                continue
            with lock:
                info = registry.setdefault(key, {
                    "displayName": base_display(cleaned), "category": "", "logo": "", "streams": []
                })
                # 2026-09-13 : le plafond MAX_STREAMS n'est PLUS applique ici (ordre
                #   d'arrivee des threads = un serveur etranger rapide pouvait remplir les
                #   4 places avant les serveurs FR). On collecte tout avec le rang du
                #   serveur ("_rank", champ TEMPORAIRE retire avant ecriture) ; le tri +
                #   plafond se font dans main(), FR d'abord.
                if all(s["url"] != line for s in info["streams"]):
                    info["streams"].append({
                        "serverIdx": srv["pos"],
                        "label": variant_label(cleaned) or ("Server %s" % srv["pos"]),
                        "url": line,
                        "_rank": srv["rank"],
                    })
                    added += 1
    print("[Server %s] +%d flux (isFr=%s)" % (srv["pos"], added, srv["isFr"]), file=sys.stderr)
    return added

def load_extra_servers():
    """Serveurs extra (servers_extra.txt). Une ligne par serveur :
         - soit l'URL complete du get.php du panel
         - soit "host user pass" (separes par espaces ou |) -> l'URL get.php est construite.
       Lignes vides et lignes commencant par # ignorees.
       rank 3 = DERNIERS dans le round-robin de main() : ils ne prennent une place qu'apres
       tous les serveurs Vegeta, mais SAUVENT les chaines qui n'ont aucun serveur (Canal+/Cine+
       quand le 52 tombe). Un compte mort ne renvoie aucun flux -> il disparait tout seul du JSON."""
    if not os.path.exists(EXTRA_PATH):
        return []
    out = []
    with open(EXTRA_PATH, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            xt = None   # (host, user, pass) pour le repli player_api
            if ln.startswith("http"):
                url = ln
                m = re.match(r"(https?://[^/]+)/get\.php\?.*?username=([^&\s]+).*?password=([^&\s]+)", ln)
                if m:
                    xt = (m.group(1), m.group(2), m.group(3))
            else:
                parts = [p for p in re.split(r"[\s|]+", ln) if p]
                if len(parts) < 3:
                    print("[extra] ligne ignoree (attendu: URL ou 'host user pass'): %r" % ln, file=sys.stderr)
                    continue
                host = parts[0] if parts[0].startswith("http") else "http://" + parts[0]
                xt = (host.rstrip("/"), parts[1], parts[2])
                url = "%s/get.php?username=%s&password=%s&type=m3u_plus&output=m3u8" % xt
            out.append({"pos": EXTRA_BASE_IDX + len(out), "url": url, "xt": xt,
                        "isFr": True, "rank": 3, "ping": 0})
    return out

def m3u_from_player_api(xt):
    """Certains panels renvoient 404 sur get.php mais servent player_api.php.
       On reconstruit alors un M3U minimal depuis action=get_live_streams :
       URL de lecture = host/live/user/pass/<stream_id>.m3u8 (format Xtream standard)."""
    host, user, pw = xt
    api = "%s/player_api.php?username=%s&password=%s&action=" % (host, user, pw)
    # Categories FR du panel ("EU | FR | CINEMA", "FR: SPORT", "FRANCE HD"...). Si on en trouve,
    # on ne garde QUE leurs chaines, et on saute le filtre de marque FR_NAME_RE (cf. ingest_server) :
    # ces categories sont deja 100 % FR et contiennent des chaines sans marque dans le regex
    # (Polar+, Serie Club, TCM, Warner TV, Action, Comedy Central...).
    fr_cats = set()
    try:
        r = requests.get(api + "get_live_categories", headers={"User-Agent": UA}, timeout=API_TIMEOUT)
        for c in r.json() or []:
            cname = (c.get("category_name") or "")
            if re.search(r"(?i)(^|[|\s\[(])(FR|FRA|FRANCE|FRENCH|FRANCAIS|FRAN\u00c7AIS)($|[|:\s\])])", cname):
                fr_cats.add(str(c.get("category_id")))
    except Exception as e:
        print("[extra] get_live_categories KO (%s) -> pas de filtre categorie" % e, file=sys.stderr)
    r = requests.get(api + "get_live_streams", headers={"User-Agent": UA}, timeout=M3U_TIMEOUT)
    r.raise_for_status()
    lines = ["#EXTM3U"]
    for o in r.json() or []:
        name = (o.get("name") or "").strip()
        sid = o.get("stream_id")
        if not name or sid is None or name.startswith("#"):   # "##### FRANCE CINEMA #####" = separateur
            continue
        if fr_cats and str(o.get("category_id")) not in fr_cats:
            continue
        lines.append("#EXTINF:-1,%s" % name)
        lines.append("%s/live/%s/%s/%s.m3u8" % (host, user, pw, sid))
    return "\n".join(lines), bool(fr_cats)

def repartir(streams, plafond):
    """Round-robin par serveur : 1er flux de chaque serveur (FR d'abord, puis global, puis
       etranger, puis extra), puis 2e flux de chacun, etc., jusqu'a `plafond`.
       Ne modifie pas `streams` (les listes internes sont des copies)."""
    tries = sorted(streams, key=lambda s: s.get("_rank", 2))   # stable : rang puis ordre d'arrivee
    par_srv = {}
    for st in tries:
        par_srv.setdefault(st["serverIdx"], []).append(st)     # insertion = ordre de rang
    out = []
    while len(out) < plafond and any(par_srv.values()):
        for lst in par_srv.values():
            if lst:
                out.append(lst.pop(0))
                if len(out) >= plafond:
                    break
    return out

def sans_rang(st):
    return {k: v for k, v in st.items() if k != "_rank"}

def ecrire(chemin, registry, plafond, etiquette):
    """Serialise le registre avec un plafond de flux par chaine. Rend (nb chaines, nb flux, octets)."""
    canaux, total = {}, 0
    for cle, info in registry.items():
        flux = [sans_rang(st) for st in repartir(info["streams"], plafond)]
        if not flux:
            continue
        canaux[cle] = {"displayName": info["displayName"], "category": info["category"],
                       "logo": info["logo"], "streams": flux}
        total += len(flux)
    payload = {
        "savedAt": int(time.time() * 1000),
        "generatedBy": "nx-data cron (refresh_vegetatv.py)%s" % etiquette,
        "channels": canaux,
    }
    dossier = os.path.dirname(chemin)
    if dossier:
        os.makedirs(dossier, exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    return len(canaux), total, os.path.getsize(chemin)

def main():
    try:
        servers = fetch_servers()
    except Exception as e:
        # 2026-09-13 quater : si vegetatv.duckdns.org est HS, on continue avec les extra
        #   plutot que de laisser le JSON precedent pourrir sans rien.
        print("fetch_servers KO (%s) -> on continue avec les serveurs extra seuls" % e, file=sys.stderr)
        servers = []
    extra = load_extra_servers()
    print("%d serveur(s) extra (servers_extra.txt)" % len(extra), file=sys.stderr)
    servers = servers + extra
    print("%d serveurs a scanner (FR=%d, global=%d, etranger=%d, extra=%d)" % (
        len(servers),
        sum(1 for s in servers if s["rank"] == 0),
        sum(1 for s in servers if s["rank"] == 1),
        sum(1 for s in servers if s["rank"] == 2),
        sum(1 for s in servers if s["rank"] == 3)), file=sys.stderr)
    registry, lock, total = {}, threading.Lock(), 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(ingest_server, s, registry, lock) for s in servers]
        for f in as_completed(futs):
            total += f.result() or 0
    # 2026-09-13 : garde-fou. Si (presque) rien n'a ete ingere (Vegeta HS + extra HS),
    #   on NE reecrit PAS le JSON : mieux vaut garder le registre precedent (l'app accepte
    #   un JSON de 24h) que publier un fichier vide. Exit 1 -> le job echoue, rien n'est commite.
    if len(registry) < 30:
        print("ABANDON : seulement %d chaines ingerees (< 30) -> JSON precedent conserve" % len(registry), file=sys.stderr)
        sys.exit(1)

    # Registre LEGER (plafond MAX_STREAMS) : celui que l'app charge au demarrage du provider.
    nb, total, octets = ecrire(OUT_PATH, registry, MAX_STREAMS, "")
    print("OK: %d chaines, %d flux, %.1f Mo -> %s" % (nb, total, octets / 1048576.0, OUT_PATH), file=sys.stderr)

    # Registre COMPLET (aucun plafond) : publie en release par le workflow, jamais commite.
    if OUT_FULL:
        nbf, totalf, octetsf = ecrire(OUT_FULL, registry, 10 ** 9, " - complet, sans plafond")
        print("COMPLET: %d chaines, %d flux, %.1f Mo -> %s" % (
            nbf, totalf, octetsf / 1048576.0, OUT_FULL), file=sys.stderr)

if __name__ == "__main__":
    main()
