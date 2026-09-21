#!/usr/bin/env python3
"""
refresh_replays.py — génère data-replay.m3u (catalogue replay FR du jour)

Plateformes supportées :
  - France.tv (France 2/3/4/5/Info)   → URLs francetv://<si_id>
  - Arte+7 (Cinéma/Séries/Docus/…)    → URLs arte://<programId>
  - TF1+ (TF1/TMC/TFX/TF1SF/LCI)      → URLs tf1plus://<chan>/<program-slug>
  - M6+ (M6/W9/6ter/Gulli/Téva/PP)    → URLs m6play://<service>/<programId>
  - BFM/RMC Play (BFMTV/RMC Story/…)  → URLs bfmplay://<productId> + bfmlive://<chan>

Les URLs spéciales sont résolues à la lecture par l'app ONYX :
  - FrancetvResolver (k7.ftven.fr + hdfauth.ftven.fr)
  - ArteResolver (api.arte.tv/api/player/v2/config)
  - TF1Resolver (mediainfo.tf1.fr/mediainfocombo) — login compte TF1 requis
  - M6Resolver (drm.6cloud.fr upfront-token) — login Gigya OAuth requis
  - BfmResolver (ws-backendtv.rmcbfmplay.com replay/play) — login OIDC requis

⚠️ Auth/JWT signés sur IP cliente FR : résolution OBLIGATOIRE côté app FR.
⚠️ TF1+ : le token de session est obtenu via WebView de login dans ONYX
   (= LoginWebViewActivity → cookies Gigya capturés → TF1Auth.saveToken).
⚠️ BFM : le token SSO est obtenu via WebView OIDC (sso.rmcbfmplay.com)
   → fragment #access_token=BFM_xxx capturé dans LoginWebViewActivity.
"""
import os, sys, json, time, re, urllib.request, gzip
from pathlib import Path

UA = ("Mozilla/5.0 (Linux; Android 14; AndroidTV) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 15
MAX_ITEMS_PER_CHAN = 999      # TF1+ ItemList = 96, France.tv jusqu'à ~80
MAX_ITEMS_PER_ARTE_CAT = 500  # Arte page catégorie typiquement 40-60

# ───── Catalogues ─────

FRANCETV_CHANNELS = [
    ("france-2",   "France 2",        "https://i.imgur.com/sJZBuY4.png"),
    ("france-3",   "France 3",        "https://i.imgur.com/PWbIICf.png"),
    ("france-4",   "France 4",        "https://i.imgur.com/wEsxQLP.png"),
    ("france-5",   "France 5",        "https://i.imgur.com/X4Y5jKR.png"),
    ("franceinfo", "Franceinfo",      "https://i.imgur.com/eITXz6A.png"),
    ("slash",      "Slash",           "https://i.imgur.com/sJZBuY4.png"),
    ("sport",      "FranceTV Sport",  "https://i.imgur.com/sJZBuY4.png"),
    ("france-24",  "France 24",       "https://i.imgur.com/yAiTedt.png"),
    ("france-o",   "France Ô",        "https://i.imgur.com/sJZBuY4.png"),
]

# 2026-06-22 (user "tout prendre comme BFM") : 10 catégories TRANSVERSES France.tv
#   (= menu "Catégories" du site france.tv). Endpoint identique aux chaînes :
#     /apps/categories/{slug}?platform=apps → même structure JSON.
#   FRANCE.TV est 100% gratuit public → pas de marker payant à filtrer.
FRANCETV_CATEGORIES = [
    ("series-et-fictions",      "Séries et fictions",  "https://i.imgur.com/sJZBuY4.png"),
    ("documentaires",           "Documentaires",       "https://i.imgur.com/sJZBuY4.png"),
    ("films",                   "Cinéma",              "https://i.imgur.com/sJZBuY4.png"),
    ("societe",                 "Société",             "https://i.imgur.com/sJZBuY4.png"),
    ("info",                    "Info",                "https://i.imgur.com/eITXz6A.png"),
    ("spectacles-et-culture",   "Arts et spectacles",  "https://i.imgur.com/sJZBuY4.png"),
    ("sport",                   "Sport",               "https://i.imgur.com/sJZBuY4.png"),
    ("jeux-et-divertissements", "Divertissement",      "https://i.imgur.com/sJZBuY4.png"),
    ("enfants",                 "Enfants",             "https://i.imgur.com/wEsxQLP.png"),
    ("podcasts",                "Podcasts",            "https://i.imgur.com/sJZBuY4.png"),
]

ARTE_CATEGORIES = [
    # 2026-06-22 (user "tout prendre comme BFM") : audit menu arte.tv complet.
    #   Corrections : "documentaires" → "documentaires-et-reportages" (URL réelle),
    #   "societe" → "info-et-societe", "arts" supprimé (n'existe plus dans le menu).
    #   Ajouts : "voyages-et-decouvertes" et "emissions" (émissions TV régulières).
    ("cinema",                       "Cinéma"),
    ("series-et-fictions",           "Séries et fictions"),
    ("documentaires-et-reportages",  "Documentaires et reportages"),
    ("sciences",                     "Sciences"),
    ("culture-et-pop",               "Culture et pop"),
    ("histoire",                     "Histoire"),
    ("info-et-societe",              "Info et société"),
    ("voyages-et-decouvertes",       "Voyages et découvertes"),
    ("emissions",                    "Émissions"),
]
ARTE_LOGO = ("https://i.imgur.com/ecXMjNl.png")

# 2026-06-22 (user "y a du boulot sur Arte, fouille partout") : sous-genres
#   musique d'ARTE Concert. Endpoint /fr/p/<slug>/ (PAS /fr/videos/<slug>/).
#   10 sous-genres × ~20 items = ~200 items supplémentaires.
ARTE_CONCERT_GENRES = [
    ("pop-rock",                "Pop & Rock"),
    ("classique",               "Classique"),
    ("musiques-electroniques",  "Électro"),
    ("jazz",                    "Jazz"),
    ("arts-de-la-scene",        "Arts de la scène"),
    ("hip-hop",                 "Hip-hop"),
    ("metal",                   "Metal"),
    ("opera",                   "Opéra"),
    ("world",                   "World"),
    ("musique-baroque",         "Baroque"),
]
# Pages thématiques bonus curées par Arte sur la home /fr/p/<slug>/
ARTE_THEMED_PAGES = [
    ("a-voir-en-famille",       "À voir en famille"),
    # 'a-venir' = "Bientôt en ligne" — exclu car les vidéos ne sont pas encore
    #   disponibles (= clics retournent 404 jusqu'à la diffusion).
]

# TF1+ : 5 chaînes du groupe. Logins compte TF1 requis pour résoudre les
#   streams. Côté script on liste les programmes (= page replay scrapée via
#   JSON-LD ItemList Schema.org).
TF1_CHANNELS = [
    ("tf1",              "TF1",              "https://i.imgur.com/qkOSt0o.png"),
    ("tmc",              "TMC",              "https://i.imgur.com/RY3iEMb.png"),
    ("tfx",              "TFX",              "https://i.imgur.com/JJVZJqL.png"),
    ("tf1-series-films", "TF1 Séries Films", "https://i.imgur.com/3OZdMb9.png"),
    ("lci",              "LCI",              "https://i.imgur.com/jVxzNHL.png"),
]

# v2 (gain marginal) : catégories /programmes-tv/* pour récupérer programmes
#   qui ne sont pas dans /<chan>/replay (ex: sport, reportages, jeunesse)
TF1_CATEGORIES = [
    # 2026-06-22 (user "tout prendre, pas que les saisons") : ajout "series"
    #   (= 295 programmes uniques sur /programmes-tv/series, la plus grosse
    #   catégorie de TF1+ et MANQUAIT du script).
    ("series",               "Séries"),
    ("sport",                "Sport"),
    ("reportages",           "Reportages"),
    ("telefilms",            "Téléfilms"),
    ("people-43944072",      "People"),
    ("podcasts-70045207",    "Podcasts"),
    ("impact",               "Impact"),
    ("jeunesse",             "Jeunesse"),
    ("divertissement",       "Divertissement"),
    ("films",                "Films"),
    ("info",                 "Info"),
]

TF1_REPLAY_URL = "https://www.tf1.fr/{slug}/replay"

# Live TF1 (36 chaînes directes TF1+). Résolution côté ONYX via `tf1live://<slug>`
#   → mediainfo.tf1.fr/mediainfocombo/L_<CHAN_ID>. Login compte TF1 requis.
#   Toutes les chaînes sont BASIC = gratuites (pas de Premium requis).

# Traditionnelles (slug → LIVE_CHANNEL_IDS map dans TF1Resolver.kt)
TF1_LIVE_CHANNELS = [
    ("tf1",              "TF1 Direct",              "https://i.imgur.com/qkOSt0o.png"),
    ("tmc",              "TMC Direct",              "https://i.imgur.com/RY3iEMb.png"),
    ("tfx",              "TFX Direct",              "https://i.imgur.com/JJVZJqL.png"),
    ("tf1-series-films", "TF1 Séries Films Direct", "https://i.imgur.com/3OZdMb9.png"),
    ("lci",              "LCI Direct",              "https://i.imgur.com/jVxzNHL.png"),
]

# Chaînes externes gratuites sur TF1+ Direct (slug → map)
TF1_LIVE_EXTERNAL = [
    ("arte",             "ARTE",               "https://i.imgur.com/qkOSt0o.png"),
    ("l-equipe",         "L'Equipe",            "https://i.imgur.com/qkOSt0o.png"),
    ("lcp-public-senat", "LCP / Public Sénat",  "https://i.imgur.com/qkOSt0o.png"),
    ("le-figaro",        "Le Figaro TV",        "https://i.imgur.com/qkOSt0o.png"),
    ("novo19",           "Paris Première",      "https://i.imgur.com/qkOSt0o.png"),
    ("redbulltv",        "Red Bull TV",         "https://i.imgur.com/qkOSt0o.png"),
]

# Chaînes FAST TF1+ (replay 24/7). ID direct L_FAST_* passthrough dans TF1Resolver
# Format: (channel_id, display_name, group_suffix)
TF1_LIVE_FAST = [
    # Fictions
    ("L_FAST_v2l-ad-demain-nous-appartient-38296145", "Demain nous appartient 24/7",  "Fictions FAST"),
    ("L_FAST_v2l-ad-ici-tout-commence-53671915",      "Ici tout commence 24/7",       "Fictions FAST"),
    ("L_FAST_v2l-ad-plus-belle-la-vie-86242005",      "Plus belle la vie 24/7",       "Fictions FAST"),
    ("L_FAST_v2l-ad-comedie-fiction-25247701",         "Comédie & Fiction 24/7",       "Fictions FAST"),
    ("L_FAST_v2l-ad-pas-de-ca-entre-nous-33100936",   "Pas de ça entre nous 24/7",    "Fictions FAST"),
    ("L_FAST_v2l-ad-chante-69061019",                  "Chanté ! 24/7",                "Fictions FAST"),
    ("L_FAST_v2l-ad-sous-le-soleil-18693784",          "Sous le soleil 24/7",          "Fictions FAST"),
    ("L_FAST_v2l-ad-foudre-27131861",                  "Foudre 24/7",                  "Fictions FAST"),
    ("L_FAST_v2l-ad-camping-paradis-42908515",         "Camping Paradis 24/7",         "Fictions FAST"),
    ("L_FAST_v2l-ad-josephine-ange-gardien-04343471",  "Joséphine ange gardien 24/7",  "Fictions FAST"),
    ("L_FAST_v2l-ad-les-mysteres-de-lamour-99639599",  "Les Mystères de l'amour 24/7", "Fictions FAST"),
    ("L_FAST_v2l-ad-les-bracelets-rouges-18062915",    "Les Bracelets rouges 24/7",    "Fictions FAST"),
    ("L_FAST_v2l-ad-je-te-promets-34143660",           "Je te promets 24/7",           "Fictions FAST"),
    ("L_FAST_v2l-ad-alice-nevers-78424271",             "Alice Nevers 24/7",             "Fictions FAST"),
    ("L_FAST_v2l-ad-le-destin-de-lisa-90714215",        "Le Destin de Lisa 24/7",        "Fictions FAST"),
    # Divertissement
    ("L_FAST_v2l-ad-mamans-and-celebres-08240458",     "Mamans & Célèbres 24/7",       "Divertissement FAST"),
    ("L_FAST_v2l-ad-star-academy-70671668",             "Star Academy 24/7",             "Divertissement FAST"),
    ("L_FAST_v2l-ad-danse-avec-les-stars-00457635",     "Danse avec les stars 24/7",     "Divertissement FAST"),
    ("L_FAST_v2l-ad-lolywood-16739451",                 "Lolywood 24/7",                 "Divertissement FAST"),
    ("L_FAST_v2l-ad-revivez-lintegral-mask-singer-91828794", "Mask Singer 24/7",         "Divertissement FAST"),
    ("L_FAST_v2l-ad-super-nanny-14977255",              "Super Nanny 24/7",              "Divertissement FAST"),
    ("L_FAST_v2l-ad-baby-boom-88288927",                "Baby Boom 24/7",                "Divertissement FAST"),
    ("L_FAST_v2l-ad-les-enfoires-35654015",             "Les Enfoirés 24/7",             "Divertissement FAST"),
    ("L_FAST_v2l-ad-les-restos-du-coeur-59894021",     "Les Restos du cœur 24/7",      "Divertissement FAST"),
    # Jeunesse
    ("L_FAST_v2l-ad-mighty-express-44092248",            "Mighty Express 24/7",           "Jeunesse FAST"),
]


# ───── VPN / Proxy (Cloudflare WARP) ─────
# tf1.fr bloque les IPs datacenter (GitHub Actions → 403). Sur CI, le script
# installe Cloudflare WARP (VPN gratuit) et l'utilise comme proxy SOCKS5.
# En local (IP résidentielle), pas de VPN = accès direct, ça marche tel quel.
import subprocess, shutil

WARP_PROXY = ""  # set dynamiquement par setup_warp() si besoin

def setup_warp():
    """Installe + démarre Cloudflare WARP en mode proxy SOCKS5 (port 40000).
    Appelé uniquement quand tf1.fr retourne 403 (= IP datacenter détectée)."""
    global WARP_PROXY
    if WARP_PROXY:
        return True  # déjà setup
    if shutil.which("warp-cli"):
        # WARP déjà installé (ex: pré-installé dans le workflow)
        pass
    elif os.path.exists("/etc/os-release"):
        # Linux (GitHub Actions = ubuntu-latest)
        print("[VPN] Installation de Cloudflare WARP...", file=sys.stderr)
        cmds = [
            "curl -fsSL https://pkg.cloudflarewarp.com/pubkey.gpg | sudo gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg",
            'echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflarewarp.com/ $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflare-client.list',
            "sudo apt-get update -qq && sudo apt-get install -y -qq cloudflare-warp",
        ]
        for c in cmds:
            r = subprocess.run(c, shell=True, capture_output=True)
            if r.returncode != 0:
                print(f"[VPN] Install échouée: {r.stderr.decode()[:200]}", file=sys.stderr)
                return False
    else:
        print("[VPN] OS non supporté pour WARP auto-install", file=sys.stderr)
        return False
    # Enregistrer + mode proxy + connecter
    for cmd in [
        ["warp-cli", "--accept-tos", "registration", "new"],
        ["warp-cli", "--accept-tos", "mode", "proxy"],
        ["warp-cli", "--accept-tos", "connect"],
    ]:
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:
            # registration new peut échouer si déjà enregistré, on continue
            pass
    import time; time.sleep(5)
    # Vérifier que le proxy SOCKS5 est actif
    test = subprocess.run(
        ["curl", "-s", "-x", "socks5h://127.0.0.1:40000",
         "--max-time", "10", "https://ipinfo.io/ip"],
        capture_output=True)
    if test.returncode == 0 and test.stdout.strip():
        WARP_PROXY = "socks5h://127.0.0.1:40000"
        print(f"[VPN] WARP OK — IP proxy: {test.stdout.decode().strip()}", file=sys.stderr)
        return True
    print("[VPN] WARP proxy non fonctionnel", file=sys.stderr)
    return False


def http_get_via_proxy(url, headers=None):
    """Fetch URL via curl + SOCKS5 proxy (Cloudflare WARP)."""
    cmd = ["curl", "-sL", "-x", WARP_PROXY,
           "-A", UA, "--max-time", str(TIMEOUT)]
    if headers:
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise Exception(f"curl proxy error (rc={result.returncode}): {result.stderr.decode()}")
    raw = result.stdout
    return raw.decode("utf-8", errors="replace")


# ───── HTTP helper ─────

def http_get(url, headers=None):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/json",
                 "Accept-Encoding": "gzip", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", errors="replace")


def http_get_tf1(url, headers=None):
    """Accès tf1.fr : direct d'abord, VPN auto si 403."""
    try:
        return http_get(url, headers=headers)
    except Exception as e:
        if "403" in str(e) or "Forbidden" in str(e):
            # IP datacenter bloquée → installer WARP et retenter
            if setup_warp() and WARP_PROXY:
                print(f"  [VPN] 403 direct → retry via WARP...", file=sys.stderr)
                return http_get_via_proxy(url, headers=headers)
        raise


# ───── France.tv ─────

def francetv_channel_programs(channel_path):
    url = (f"https://api-mobile.yatta.francetv.fr/apps/channels/{channel_path}"
           f"?platform=apps")
    try:
        raw = http_get(url)
    except Exception as e:
        print(f"[!] Fetch error {channel_path}: {e}", file=sys.stderr)
        return []
    data = json.loads(raw)
    seen = set()
    out = []
    for coll in data.get("collections", []):
        if coll.get("type") in ("live", "link"):
            continue
        for item in coll.get("items", []):
            si = item.get("si_id")
            if not si or si in seen:
                continue
            seen.add(si)
            title = (item.get("title") or "").strip()
            program = item.get("program") or {}
            program_title = program.get("label") or program.get("title") or ""
            if not title:
                title = program_title
            elif program_title and program_title.lower() not in title.lower():
                title = f"{program_title} — {title}"
            if not title:
                continue
            logo = ""
            for k in ("image_url", "background_url"):
                v = item.get(k)
                if v and isinstance(v, str):
                    logo = v
                    break
            if not logo:
                imgs = item.get("media_image") or {}
                if isinstance(imgs, dict):
                    logo = imgs.get("url", "")
            out.append({
                "si_id": si,
                "title": title[:140],
                "logo": logo,
            })
            if len(out) >= MAX_ITEMS_PER_CHAN:
                break
        if len(out) >= MAX_ITEMS_PER_CHAN:
            break
    return out


# ───── Arte+7 ─────

ARTE_HREF_RE = re.compile(r"href=\"/fr/videos/([^/\"]+)/([^\"#]+?)/\"")
# v3 : filtre PID Arte valide pour exclure les slugs catégorie (culture-et-pop, etc.)
ARTE_PID_VALID = re.compile(r"^(\d{6}-\d{3}-[A-Z]|RC-[A-Za-z0-9]+)$")

def slug_to_title(slug):
    SHORT = {"et", "le", "la", "les", "de", "du", "des", "un", "une", "à"}
    words = []
    for i, w in enumerate(slug.replace('-', ' ').split()):
        if i == 0 or w not in SHORT:
            words.append(w.capitalize())
        else:
            words.append(w)
    return ' '.join(words)

# v4 (2026-06-19) : check de disponibilité du stream avant d'ajouter un PID
#   au M3U. L'API Arte renvoie ERROR_NO_RIGHTS sur les contenus retirés du
#   catalogue (= droits expirés, ex: Spartacus). On les filtre ici pour
#   éviter qu'ils traînent dans la liste sans pouvoir être joués.
ARTE_API_CFG = "https://api.arte.tv/api/player/v2/config/fr/{}"

def francetv_category_programs(category_slug):
    """2026-06-22 — variante de francetv_channel_programs() pour les catégories
    transverses (Séries, Cinéma, Documentaires, etc.). Endpoint similaire mais
    /apps/categories/{slug} au lieu de /apps/channels/{path}. Même format JSON
    (collections + items)."""
    url = (f"https://api-mobile.yatta.francetv.fr/apps/categories/{category_slug}"
           f"?platform=apps")
    try:
        raw = http_get(url)
    except Exception as e:
        print(f"[!] Fetch error category {category_slug}: {e}", file=sys.stderr)
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    seen = set()
    out = []
    for coll in data.get("collections", []):
        if coll.get("type") in ("live", "link"):
            continue
        for item in coll.get("items", []):
            si = item.get("si_id")
            if not si or si in seen:
                continue
            seen.add(si)
            title = (item.get("title") or "").strip()
            program = item.get("program") or {}
            program_title = program.get("label") or program.get("title") or ""
            if not title:
                title = program_title
            elif program_title and program_title.lower() not in title.lower():
                title = f"{program_title} — {title}"
            if not title:
                continue
            logo = ""
            for k in ("image_url", "background_url"):
                v = item.get(k)
                if v and isinstance(v, str):
                    logo = v
                    break
            if not logo:
                imgs = item.get("media_image") or {}
                if isinstance(imgs, dict):
                    logo = imgs.get("url", "")
            out.append({
                "si_id": si,
                "title": title[:140],
                "logo": logo,
            })
            if len(out) >= MAX_ITEMS_PER_CHAN:
                break
        if len(out) >= MAX_ITEMS_PER_CHAN:
            break
    return out



def arte_check_available(pid):
    """Retourne True si le stream est jouable (= streams non-vide, pas
    d'erreur ERROR_NO_RIGHTS). Timeout 6s pour ne pas plomber le scrape."""
    try:
        raw = http_get(ARTE_API_CFG.format(pid),
                       headers={"Accept": "application/json"})
        j = json.loads(raw)
        data = j.get("data") or {}
        attrs = data.get("attributes") or {}
        streams = attrs.get("streams") or []
        if not streams:
            return False
        # double check : si error block présent → indispo
        err = attrs.get("error") or data.get("error")
        if err and isinstance(err, dict) and err.get("code"):
            return False
        return True
    except Exception:
        # Sur timeout/erreur réseau on garde par défaut (= pas pénaliser)
        return True


def arte_category_programs(category_slug, max_items=MAX_ITEMS_PER_ARTE_CAT):
    url = f"https://www.arte.tv/fr/videos/{category_slug}/"
    try:
        raw = http_get(url, headers={"Accept": "text/html"})
    except Exception as e:
        print(f"[!] Arte fetch error {category_slug}: {e}", file=sys.stderr)
        return []
    seen = set()
    candidates = []
    for m in ARTE_HREF_RE.finditer(raw):
        pid = m.group(1)
        slug = m.group(2)
        # v3 : exclut les slugs catégorie (= ne sont pas des PIDs valides)
        if not ARTE_PID_VALID.match(pid):
            continue
        if pid in seen:
            continue
        seen.add(pid)
        title = slug_to_title(slug)[:140]
        if not title:
            continue
        candidates.append({"program_id": pid, "title": title})
        # Surdimensionne (×2) pour absorber les futurs filtrés indispo
        if len(candidates) >= max_items * 2:
            break
    # v4 : check parallèle de disponibilité (ThreadPool=8) — on garde les
    #   programmes dont le stream Arte est encore actif.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        availability = list(ex.map(lambda c: arte_check_available(c["program_id"]), candidates))
    out = [c for c, ok in zip(candidates, availability) if ok][:max_items]
    dropped = sum(1 for ok in availability if not ok)
    if dropped:
        print(f"[arte] {category_slug}: {dropped} programmes filtrés (ERROR_NO_RIGHTS)", file=sys.stderr)
    return out


# ───── TF1+ ─────

def arte_p_page_programs(slug, max_items=MAX_ITEMS_PER_ARTE_CAT):
    """2026-06-22 — variante pour pages thématiques /fr/p/<slug>/ d'Arte
    (= ARTE Concert sous-genres, "À voir en famille", etc.). Même format HTML
    scraping que arte_category_programs, juste URL différente."""
    url = f"https://www.arte.tv/fr/p/{slug}/"
    try:
        raw = http_get(url, headers={"Accept": "text/html"})
    except Exception as e:
        print(f"[!] Arte /p/ fetch error {slug}: {e}", file=sys.stderr)
        return []
    seen = set()
    candidates = []
    for m in ARTE_HREF_RE.finditer(raw):
        pid = m.group(1)
        sub_slug = m.group(2)
        if not ARTE_PID_VALID.match(pid):
            continue
        if pid in seen:
            continue
        seen.add(pid)
        title = slug_to_title(sub_slug)[:140]
        if not title:
            continue
        candidates.append({"program_id": pid, "title": title})
        if len(candidates) >= max_items * 2:
            break
    # Check disponibilité (= filtre ERROR_NO_RIGHTS)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        availability = list(ex.map(lambda c: arte_check_available(c["program_id"]), candidates))
    out = [c for c, ok in zip(candidates, availability) if ok][:max_items]
    return out



def tf1plus_channel_programs(channel_slug, max_items=MAX_ITEMS_PER_CHAN):
    """Scrape la page replay TF1+. Extrait les programmes via JSON-LD."""
    url = TF1_REPLAY_URL.format(slug=channel_slug)
    try:
        raw = http_get_tf1(url, headers={"Accept": "text/html"})
    except Exception as e:
        print(f"[!] TF1+ fetch error {channel_slug}: {e}", file=sys.stderr)
        return []
    blocks = re.findall(
        r'<script type="application/ld\+json"[^>]*>([^<]+)</script>',
        raw, re.DOTALL)
    out = []
    seen = set()
    for b in blocks:
        try:
            data = json.loads(b)
        except Exception:
            continue
        if data.get("@type") != "ItemList":
            continue
        for elem in data.get("itemListElement", []):
            if len(out) >= max_items:
                return out
            if not isinstance(elem, dict):
                continue
            item = elem.get("item")
            if not isinstance(item, dict):
                continue
            prog_url = item.get("url") or ""
            name = item.get("name") or ""
            image = item.get("image") or ""
            if not prog_url or not name:
                continue
            path = prog_url.replace("https://www.tf1.fr/", "").strip("/")
            if "/" not in path or len(path.split("/")) > 2:
                continue
            if path in seen:
                continue
            seen.add(path)
            # 2026-06-19 : capture @type JSON-LD (TVSeries/Movie/TVEpisode/etc.)
            item_type = item.get("@type", "")
            out.append({
                "tf1_type": item_type,
                "si_id": path,
                "title": name.strip()[:140],
                "logo": image,
            })
    return out



TF1_CAT_HREF_RE = re.compile(r'href="/(tf1|tmc|tfx|tf1-series-films|lci)/([a-z0-9-]+)"')

def tf1_category_programs(category_slug, max_items=MAX_ITEMS_PER_CHAN):
    """Scrape /programmes-tv/<cat>. Format : hrefs `/(chan)/(slug)`.
    Retourne liste programmes avec si_id `<chan>/<slug>`. Pas de logo/title précis,
    on prendra ceux qu'on trouvera via fetch ultérieur ou on laissera vide."""
    url = f"https://www.tf1.fr/programmes-tv/{category_slug}"
    try:
        raw = http_get_tf1(url, headers={"Accept": "text/html"})
    except Exception as e:
        print(f"[!] TF1 cat fetch error {category_slug}: {e}", file=sys.stderr)
        return []
    out = []
    seen = set()
    # Liens exclus = pages réservées (replay, direct, news, videos, programmes-tv)
    excluded = {"replay", "direct", "news", "videos", "programmes-tv"}
    for m in TF1_CAT_HREF_RE.finditer(raw):
        chan = m.group(1)
        slug = m.group(2)
        if slug in excluded:
            continue
        si_id = f"{chan}/{slug}"
        if si_id in seen:
            continue
        seen.add(si_id)
        title = slug_to_title(slug)[:140]
        out.append({"si_id": si_id, "title": title, "logo": ""})
        if len(out) >= max_items:
            break
    return out


# ───── M6+ Replay (2026-06-19) ─────
# API publique pc.middleware.6play.fr (= equiv web du middleware Android qui ne
#   marche plus). Pas besoin d'auth Gigya pour le CATALOGUE — seul le PLAYBACK
#   exige Widevine + Gigya. Chaque chaîne M6+ a son propre service_id :
#     M6=m6replay, W9=w9replay, 6ter=6terreplay, Gulli=gulli,
#     Téva=tevareplay, Paris Première=parispremierereplay.
M6_BASE = "https://pc.middleware.6play.fr/6play/v2/platforms/m6group_web/services"
# (service_id, chan_label, logo_url)
M6_CHANNELS = [
    ("m6replay",              "M6",              "https://i.imgur.com/4lhxLPB.png"),
    ("w9replay",              "W9",              "https://i.imgur.com/oFGn1On.png"),
    ("6terreplay",            "6ter",            "https://i.imgur.com/M7vGd6Y.png"),
    ("gulli",                 "Gulli",           "https://i.imgur.com/tFNzQQM.png"),
    ("tevareplay",            "Téva",            "https://i.imgur.com/HuLNVjC.png"),
    ("parispremierereplay",   "Paris Première",  "https://i.imgur.com/oCBzd0e.png"),
]
M6_PAGE_SIZE = 100  # API caps à 100 par requête, pagination obligatoire

# 2026-06-22 (user "tout prendre comme BFM") : 9 catégories TRANSVERSES M6+
#   (= menu "Catégories" du site m6.fr). Endpoint :
#     /services/m6replay/folders/{folder_id}/programs?limit=100
#   IMPORTANT : les folders sont CROSS-CHAÎNE — un même appel récupère TOUS
#   les programmes d'une catégorie peu importe le service. 1 seul appel
#   suffit par folder. Filtre M6+MAX premium si marker (subscriptions).
M6_FOLDERS = [
    (10,   "Divertissement"),
    (232,  "Séries réalité"),
    (8,    "Séries"),
    (58,   "Sport"),
    (12,   "Infos & Société"),
    (907,  "Cinéma"),
    (70,   "Téléfilms"),
    (52,   "Jeunesse"),
    (2996, "Podcasts"),
]
M6_LOGO_GENERIC = "https://i.imgur.com/4lhxLPB.png"

# ───── BFM / RMC Play (2026-06-21) ─────
# API CDN publique Gaia-core. Chaque chaîne a une page menu avec un spot
#   "Tous les replays" paginé (tiles = programmes avec productId).
# Login BFM requis pour PLAYBACK (Widevine DRM). Catalogue = public.
BFM_CDN = "https://ws-cdn.tv.sfr.net/gaia-core/rest/api/web/v1"
BFM_LOGO_BASE = "https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/france"
# (channel_key, chan_label, logo_url)
# Live channels (= keys in BfmResolver.LIVE_CHANNELS dans ONYX)
BFM_LIVE_CHANNELS = [
    ("bfmtv",         "BFM TV",          f"{BFM_LOGO_BASE}/bfm-tv-fr.png"),
    ("rmcstory",      "RMC Story",       f"{BFM_LOGO_BASE}/rmc-story-fr.png"),
    ("rmcdecouverte", "RMC Découverte",  f"{BFM_LOGO_BASE}/rmc-decouverte-fr.png"),
    ("bfmbusiness",   "BFM Business",    f"{BFM_LOGO_BASE}/bfm-business-fr.png"),
    ("rmclife",       "RMC Life",        f"{BFM_LOGO_BASE}/bfm-tv-fr.png"),
    ("techco",        "Tech & Co",       f"{BFM_LOGO_BASE}/bfm-tv-fr.png"),
]
# Replay channels (menu_id, slug, label, logo) — menu_id = RefMenuItem key in Gaia
BFM_REPLAY_CHANNELS = [
    ("rmcgo_home_bfmtv",         "bfmtv",         "BFM TV",          f"{BFM_LOGO_BASE}/bfm-tv-fr.png"),
    ("rmcgo_home_rmcstory",      "rmcstory",      "RMC Story",       f"{BFM_LOGO_BASE}/rmc-story-fr.png"),
    ("rmcgo_home_rmcdecouverte", "rmcdecouverte", "RMC Découverte",  f"{BFM_LOGO_BASE}/rmc-decouverte-fr.png"),
    ("rmcgo_home_bfmbusiness",   "bfmbusiness",   "BFM Business",    f"{BFM_LOGO_BASE}/bfm-business-fr.png"),
    ("rmcgo_home_rmclife",       "rmclife",       "RMC Life",        f"{BFM_LOGO_BASE}/bfm-tv-fr.png"),
    ("rmcgo_home_01TV",          "techco",        "Tech & Co",       f"{BFM_LOGO_BASE}/bfm-tv-fr.png"),
    ("rmcgo_home_radios",        "rmcradio",      "RMC Radio",       f"{BFM_LOGO_BASE}/bfm-tv-fr.png"),
    ("rmcgo_home_bfmavod",       "bfmexclus",     "Exclus BFM Play", f"{BFM_LOGO_BASE}/bfm-tv-fr.png"),
    ("rmcgo_home_rmccrime",      "rmccrime",      "100% Crime",      f"{BFM_LOGO_BASE}/rmc-story-fr.png"),
    ("fb303324-2100-4e72-9840-967d4e899c99", "7alamaison", "7 à la maison", f"{BFM_LOGO_BASE}/bfm-tv-fr.png"),
    ("c67c4f5e-73ae-40fe-b562-35391a9f5931", "topmecanic", "Top Mecanic",   f"{BFM_LOGO_BASE}/rmc-decouverte-fr.png"),
    ("2d0d7898-fad8-47db-a87a-eb1b62c11ef9", "100docs",    "100% DOCS",     f"{BFM_LOGO_BASE}/rmc-decouverte-fr.png"),
]
BFM_CDN_V2 = "https://ws-cdn.tv.sfr.net/gaia-core/rest/api/web/v2"
BFM_PARAMS = "app=bfmrmc&device=browser&operators=NEXTTV"
# Content ID prefixes that return HTTP 500 (third-party channels, not hosted on BFM CDN)
BFM_BROKEN_PREFIXES = [
    "NEUF_CINE_PLUS_OCS", "NEUF_01NET", "NEUF_LEQUIPETV",
    "NEUF_VIRGIN17", "NEUF_UNIVERSAL", "NEUF_KITCHEN_MANIA",
    "NEUF_USHUAIA", "NEUF_FILMDAFRIQUE",
]

# Thématiques transverses BFM/RMC (= menu "Thématiques" du site rmcbfmplay.com).
#   14 thématiques découvertes via probe DOM 2026-06-22. Chaque thématique = un
#   themeId RefTile qui retourne des Saisons cross-chaîne. Endpoint :
#     /tile/RefTile::<themeId>/content?app=bfmrmc&...&page=0&size=200
# Le param size=200 débloque le cap par défaut (= 10). Certaines thématiques
# retournent jusqu'à 150 items (= probablement cap interne API). Tester
# régulièrement si BFM en ajoute.
BFM_THEMES = [
    ("crime-investigation",          "02179209-fc21-4001-8593-d2d8b7696788", "Crime & Investigation"),
    ("cinema-fiction",               "f2e897a0-76d8-40c9-89f4-148411aca185", "Cinéma & Fiction"),
    ("moteur-mecanique",             "8055d4b0-47b1-42b8-8686-a6861cd8ea9b", "Moteur & Mécanique"),
    ("aventure-survie",              "09cbd302-808a-4724-a591-18a17d17455f", "Aventure & Survie"),
    ("divertissement",               "1fba40d2-820d-470e-ad70-5e1be1cb2f4c", "Divertissement"),
    ("documentaire",                 "5fc555aa-4f58-4372-ba6e-2a1a3ab2707c", "Documentaire"),
    ("mystere-etrange",              "4d5db435-cfce-4024-9580-b0b21331a5d0", "Mystère & Étrange"),
    ("histoire-civilisation",        "91e978e9-bc32-4f56-9bc3-1028c333fd20", "Histoire & Civilisation"),
    ("science-technologie",          "a296a74f-7bd0-45f9-aceb-bbb7609d5dba", "Science & Technologie"),
    ("societe-immersion",            "d4fd74f7-2587-4eba-a26e-3f00e4ae992f", "Société & Immersion"),
    ("docu-realite",                 "2d39f387-9593-414c-9089-01e3b6ef7b1e", "Docu-Réalité"),
    ("sport-combat",                 "5af91e75-a280-454b-beef-6fdba4f81598", "Sport & Combat"),
    ("info-talk",                    "bf31206d-3bdb-40d6-b5f2-475032d7797b", "Info & Talk"),
    ("grand-reportage-ligne-rouge",  "d952ba56-c92c-4114-981b-2a68c53cf5b6", "Grand Reportage"),
]


def m6_channel_programs(service_id, max_items=2000):
    """Liste les programmes (= émissions/séries) disponibles pour une chaîne M6+
    via /services/{svc}/programs?limit=100&offset=N&csa=6 avec PAGINATION.
    L'API M6 refuse limit>100 (retourne [] ou cap silencieux). On pagine
    par tranches de 100 jusqu'à épuisement. Retourne une liste de dicts
    {program_id, title, image, service, tvg_type}."""
    out = []
    offset = 0
    while offset < max_items:
        url = f"{M6_BASE}/{service_id}/programs?limit={M6_PAGE_SIZE}&offset={offset}&csa=6"
        try:
            raw = http_get(url, headers={"Accept": "application/json"})
        except Exception as e:
            print(f"[!] M6 fetch error {service_id} offset={offset}: {e}", file=sys.stderr)
            break
        try:
            arr = json.loads(raw)
        except Exception as e:
            print(f"[!] M6 JSON parse error {service_id} offset={offset}: {e}", file=sys.stderr)
            break
        if not isinstance(arr, list) or len(arr) == 0:
            break
        for p in arr:
            pid = p.get("id")
            if not pid:
                continue
            code = (p.get("code") or "").strip()
            name = (p.get("name") or "").strip()
            title = name if name else slug_to_title(code)
            if not title:
                continue
            # image preview (img[0].external_key) si dispo
            img = ""
            imgs = p.get("images") or []
            if imgs and isinstance(imgs, list):
                img = imgs[0].get("external_key", "") if isinstance(imgs[0], dict) else ""
            # 2026-06-19 : classification série vs film/unitaire via program_type_wording.code
            ptype = (p.get("program_type_wording") or {}).get("code", "")
            is_series = ptype in {"episode", "emission", "magazine", "journal", "dessin-anime"}
            tvg_type = "series" if is_series else "movie"
            out.append({"program_id": pid, "title": title[:140], "image": img, "service": service_id, "tvg_type": tvg_type})
        if len(arr) < M6_PAGE_SIZE:
            break  # dernière page
        offset += M6_PAGE_SIZE
    return out




def m6_folder_programs(folder_id, folder_label, max_items=2000):
    """2026-06-22 — scrape une catégorie transverse M6+ (= folder cross-chaîne).
    Endpoint /services/m6replay/folders/{fid}/programs avec pagination.
    Le service est inchangé (= m6replay) car les folders sont transverses :
    on récupère TOUS les programmes de la catégorie peu importe la chaîne.
    Filtre les contenus M6+MAX premium (marqueur 'is_subscription' ou
    'subscriptions' non vide). Retourne list[{program_id,title,image,
    tvg_type,folder_label}]."""
    out = []
    offset = 0
    while offset < max_items:
        url = (f"{M6_BASE}/m6replay/folders/{folder_id}/programs"
               f"?limit={M6_PAGE_SIZE}&offset={offset}&csa=6")
        try:
            raw = http_get(url, headers={"Accept": "application/json"})
        except Exception as e:
            print(f"[!] M6 folder {folder_id} offset={offset}: {e}", file=sys.stderr)
            break
        try:
            arr = json.loads(raw)
        except Exception:
            break
        if not isinstance(arr, list) or len(arr) == 0:
            break
        for p in arr:
            pid = p.get("id")
            if not pid:
                continue
            # Filtre M6+MAX premium
            if p.get("subscriptions") or p.get("is_subscription"):
                continue
            code = (p.get("code") or "").strip()
            name = (p.get("name") or "").strip()
            title = name if name else slug_to_title(code)
            if not title:
                continue
            img = ""
            imgs = p.get("images") or []
            if imgs and isinstance(imgs, list):
                img = imgs[0].get("external_key", "") if isinstance(imgs[0], dict) else ""
            ptype = (p.get("program_type_wording") or {}).get("code", "")
            is_series = ptype in {"episode", "emission", "magazine", "journal", "dessin-anime"}
            tvg_type = "series" if is_series else "movie"
            out.append({
                "program_id": pid,
                "title": title[:140],
                "image": img,
                "service": "m6replay",
                "tvg_type": tvg_type,
                "folder_label": folder_label,
            })
        if len(arr) < M6_PAGE_SIZE:
            break
        offset += M6_PAGE_SIZE
    return out


def bfm_channel_programs(menu_id, max_items=500):
    """Scrape BFM/RMC channel via Gaia-core CDN API (public, no auth).
    Fetches the menu structure, then ALL spot contents via v2 endpoint.
    Each spot returns up to ~20 tiles. Dedup by productId.
    Returns [{product_id, title, image, tvg_type, category}]."""

    # Step 1: menu structure (v1, with params for max spots)
    menu_url = f"{BFM_CDN}/menu/RefMenuItem::{menu_id}/structure?{BFM_PARAMS}"
    try:
        raw = http_get(menu_url, headers={"Accept": "application/json"})
    except Exception as e:
        print(f"[!] BFM menu error {menu_id}: {e}", file=sys.stderr)
        return []
    try:
        menu = json.loads(raw)
    except Exception:
        print(f"[!] BFM menu JSON error {menu_id}", file=sys.stderr)
        return []

    out = []
    seen = set()

    # Step 2: fetch ALL spots content (v2 endpoint, works for all spot types)
    for spot in menu.get("spots", []):
        spot_id = spot.get("id", "")
        spot_title = (spot.get("title") or "").strip()
        if not spot_id:
            continue

        spot_url = f"{BFM_CDN_V2}/spot/{spot_id}/content?{BFM_PARAMS}"
        try:
            sraw = http_get(spot_url, headers={"Accept": "application/json"})
            sdata = json.loads(sraw)
        except Exception:
            continue

        spot_count = 0
        for tile in sdata.get("tiles", []):
            # Extract productId (direct field or via action.actionIds.contentId)
            pid_raw = tile.get("productId") or ""
            if not pid_raw:
                aids = (tile.get("action") or {}).get("actionIds") or {}
                pid_raw = aids.get("contentId", "")
            pid = pid_raw.replace("Product::", "")
            if not pid or "NEUF_" not in pid or pid in seen:
                continue
            # Filter broken third-party content
            if any(pid.startswith(pfx) for pfx in BFM_BROKEN_PREFIXES):
                continue
            seen.add(pid)

            title = (tile.get("title") or "").strip()
            if not title:
                continue

            # Image: prefer 2/3 or 16/9 format without title overlay
            image = ""
            for img in (tile.get("images") or []):
                fmt = img.get("format", "")
                url = img.get("url", "")
                wt = img.get("withTitle", False)
                if fmt in ("2/3", "16/9") and not wt and url:
                    image = url
                    break
                if not image and url and not wt:
                    image = url

            ct = tile.get("contentType", "")
            tvg_type = "series" if ct in ("Season", "Series", "Episode") else "movie"

            out.append({
                "product_id": pid,
                "title": title[:140],
                "image": image,
                "tvg_type": tvg_type,
                "category": spot_title,
            })
            spot_count += 1

        if spot_count:
            print(f"    [{menu_id}] {spot_title}: {spot_count}", file=sys.stderr)

        if len(out) >= max_items:
            break
        time.sleep(0.15)

    return out[:max_items]



def bfm_fetch_episodes(content_id):
    """2026-06-22 — pour une Saison BFM (= 1 émission/série), récupère tous
    ses épisodes individuels via l'endpoint /content/{id}/episodes du Gaia CDN.
    Sans token requis (= endpoint public).
    Retourne liste de dicts {product_id, title, image, tvg_type, paid}.
    Filtre auto les épisodes avec svodId non vide (= payant SVOD)."""
    url = (f"{BFM_CDN}/content/Product::{content_id}/episodes"
           f"?universe=PROVIDER&accountTypes=NEXTTV&operators=NEXTTV"
           f"&noTracking=false&page=0&size=1000")
    try:
        raw = http_get(url, headers={"Accept": "application/json"})
        data = json.loads(raw)
    except Exception:
        return []
    items = data.get("content", [])
    out = []
    for ep in items:
        # contentId de l'épisode (action.actionIds.contentId, fallback id direct)
        ep_pid_raw = (ep.get("action") or {}).get("actionIds", {}).get("contentId", "")
        if not ep_pid_raw:
            ep_pid_raw = ep.get("id", "") or ep.get("productId", "")
        ep_pid = ep_pid_raw.replace("Product::", "")
        if not ep_pid:
            continue
        # Filtre payant (svodId rempli = abonnement requis)
        if ep.get("svodId"):
            continue
        if any(ep_pid.startswith(pfx) for pfx in BFM_BROKEN_PREFIXES):
            continue
        title = (ep.get("title") or "").strip()
        if not title:
            continue
        # Image : prefer 16/9 ou 2/3 sans titre overlay
        image = ""
        for img in (ep.get("images") or []):
            fmt = img.get("format", "")
            u = img.get("url", "")
            wt = img.get("withTitle", False)
            if fmt in ("16/9", "2/3") and not wt and u:
                image = u
                break
            if not image and u and not wt:
                image = u
        ct = ep.get("contentType", "")
        tvg_type = "movie" if ct == "Movie" else "series"
        out.append({
            "product_id": ep_pid,
            "title": title[:140],
            "image": image,
            "tvg_type": tvg_type,
        })
    return out


def bfm_theme_programs(theme_id, theme_label, max_items=500):
    """2026-06-22 — scrape une thématique transverse BFM/RMC (= page
    /thematiques/<slug>?themeId=RefTile::xxx du site). Le param size=200
    débloque le cap par défaut (= 10 sinon).
    Retourne liste de dicts {product_id, title, image, tvg_type, theme_label}.
    Filtre svodId payant."""
    url = (f"{BFM_CDN}/tile/RefTile::{theme_id}/content"
           f"?{BFM_PARAMS}&page=0&size=200")
    try:
        raw = http_get(url, headers={"Accept": "application/json"})
        data = json.loads(raw)
    except Exception as e:
        print(f"[!] BFM theme error {theme_label}: {e}", file=sys.stderr)
        return []
    items = data.get("items", [])
    out = []
    seen = set()
    for tile in items:
        if tile.get("svodId"):
            continue
        pid_raw = tile.get("productId") or ""
        if not pid_raw:
            aids = (tile.get("action") or {}).get("actionIds") or {}
            pid_raw = aids.get("contentId", "")
        pid = pid_raw.replace("Product::", "")
        if not pid or "NEUF_" not in pid or pid in seen:
            continue
        if any(pid.startswith(pfx) for pfx in BFM_BROKEN_PREFIXES):
            continue
        seen.add(pid)
        title = (tile.get("title") or "").strip()
        if not title:
            continue
        image = ""
        for img in (tile.get("images") or []):
            fmt = img.get("format", "")
            u = img.get("url", "")
            wt = img.get("withTitle", False)
            if fmt in ("2/3", "16/9") and not wt and u:
                image = u
                break
            if not image and u and not wt:
                image = u
        ct = tile.get("contentType", "")
        tvg_type = "series" if ct in ("Season", "Series", "Episode") else "movie"
        out.append({
            "product_id": pid,
            "title": title[:140],
            "image": image,
            "tvg_type": tvg_type,
            "theme_label": theme_label,
        })
        if len(out) >= max_items:
            break
    return out[:max_items]



# ───── Generation ─────

def generate_m3u(output_path):
    lines = ["#EXTM3U"]
    total = 0

    # France.tv
    print("\n=== France.tv ===")
    for channel_path, channel_label, channel_logo in FRANCETV_CHANNELS:
        progs = francetv_channel_programs(channel_path)
        print(f"  {channel_label}: {len(progs)} programmes")
        for p in progs:
            logo = p["logo"] or channel_logo
            # 2026-06-19 : classification basique pour France TV. p peut avoir
            #   un champ "program_type" si parsé. Sinon on garde "series" par
            #   défaut (= les Replay France TV sont surtout des émissions
            #   régulières / journaux / magazines).
            tvg_type = p.get("tvg_type", "series")
            extinf = (
                f'#EXTINF:-1 tvg-id="francetv-{p["si_id"]}" '
                f'tvg-logo="{logo}" '
                f'tvg-country="FR" '
                f'tvg-type="{tvg_type}" '
                f'group-title="Replay {channel_label}",{p["title"]}'
            )
            lines.append(extinf)
            lines.append(f'francetv://{p["si_id"]}')
            total += 1
        time.sleep(0.4)

    # 2026-06-22 — NOUVEAU : France.tv Thématiques transverses (10 catégories
    #   cross-chaîne : Séries, Docs, Cinéma, Société, Info, Arts, Sport,
    #   Divertissement, Enfants, Podcasts). Dedup vs les chaînes.
    print("\n=== France.tv Thématiques transverses ===")
    ftv_themes_seen = set()
    for line in lines:
        if line.startswith('francetv://'):
            try:
                ftv_themes_seen.add(line.replace('francetv://', '').strip())
            except Exception:
                pass
    for cat_slug, cat_label, cat_logo in FRANCETV_CATEGORIES:
        progs = francetv_category_programs(cat_slug)
        added = 0
        group = f"Thématique France TV - {cat_label}"
        for p in progs:
            si = p["si_id"]
            if si in ftv_themes_seen:
                continue
            ftv_themes_seen.add(si)
            logo = p["logo"] or cat_logo
            extinf = (
                f'#EXTINF:-1 tvg-id="francetv-{si}" '
                f'tvg-logo="{logo}" '
                f'tvg-country="FR" '
                f'tvg-type="series" '
                f'group-title="{group}",{p["title"]}'
            )
            lines.append(extinf)
            lines.append(f'francetv://{si}')
            total += 1
            added += 1
        print(f"  Théma {cat_label}: {len(progs)} progs → {added} nouveaux (dedup)")
        time.sleep(0.3)

    # Arte+7
    print("\n=== Arte+7 ===")
    # 2026-06-19 : classification par catégorie Arte. Films/Documentaires sont
    #   des unitaires ; Séries et fictions sont des séries ; les autres
    #   catégories (Arts, Culture, Histoire, Sciences) sont surtout des
    #   programmes one-shot → movie par défaut.
    ARTE_SERIES_CATS = {"series-et-fictions"}  # par slug
    for cat_slug, cat_label in ARTE_CATEGORIES:
        progs = arte_category_programs(cat_slug)
        print(f"  {cat_label}: {len(progs)} programmes")
        tvg_type = "series" if cat_slug in ARTE_SERIES_CATS else "movie"
        for p in progs:
            extinf = (
                f'#EXTINF:-1 tvg-id="arte-{p["program_id"]}" '
                f'tvg-logo="{ARTE_LOGO}" '
                f'tvg-country="FR" '
                f'tvg-type="{tvg_type}" '
                f'group-title="Arte {cat_label}",{p["title"]}'
            )
            lines.append(extinf)
            lines.append(f'arte://{p["program_id"]}')
            total += 1
        time.sleep(0.5)

    # 2026-06-22 — NOUVEAU : Arte Concert (10 sous-genres musique) + pages
    #   thématiques curées par Arte. Endpoint /fr/p/<slug>/.
    print("\n=== Arte Concert (sous-genres musique) ===")
    arte_concert_seen = set()
    for line in lines:
        if line.startswith('arte://'):
            arte_concert_seen.add(line.replace('arte://', '').strip())
    for genre_slug, genre_label in ARTE_CONCERT_GENRES:
        progs = arte_p_page_programs(genre_slug)
        added = 0
        group = f"Arte Concert - {genre_label}"
        for p in progs:
            pid = p["program_id"]
            if pid in arte_concert_seen:
                continue
            arte_concert_seen.add(pid)
            extinf = (
                f'#EXTINF:-1 tvg-id="arte-{pid}" '
                f'tvg-logo="{ARTE_LOGO}" '
                f'tvg-country="FR" '
                f'tvg-type="movie" '
                f'group-title="{group}",{p["title"]}'
            )
            lines.append(extinf)
            lines.append(f'arte://{pid}')
            total += 1
            added += 1
        print(f"  {genre_label}: {len(progs)} progs → {added} nouveaux")
        time.sleep(0.3)

    print("\n=== Arte Pages Thématiques ===")
    for page_slug, page_label in ARTE_THEMED_PAGES:
        progs = arte_p_page_programs(page_slug)
        added = 0
        group = f"Arte Thématique - {page_label}"
        for p in progs:
            pid = p["program_id"]
            if pid in arte_concert_seen:
                continue
            arte_concert_seen.add(pid)
            extinf = (
                f'#EXTINF:-1 tvg-id="arte-{pid}" '
                f'tvg-logo="{ARTE_LOGO}" '
                f'tvg-country="FR" '
                f'tvg-type="movie" '
                f'group-title="{group}",{p["title"]}'
            )
            lines.append(extinf)
            lines.append(f'arte://{pid}')
            total += 1
            added += 1
        print(f"  {page_label}: {len(progs)} progs → {added} nouveaux")
        time.sleep(0.3)

    # TF1 Live (chaînes directes, 1 entrée par chaîne)
    print("\n=== TF1 Live ===")
    for chan_slug, chan_label, chan_logo in TF1_LIVE_CHANNELS:
        extinf = (
            f'#EXTINF:-1 tvg-id="tf1live-{chan_slug}" '
            f'tvg-logo="{chan_logo}" '
            f'tvg-country="FR" '
            f'group-title="Live TF1+",{chan_label}'
        )
        lines.append(extinf)
        lines.append(f'tf1live://{chan_slug}')
        total += 1

    # TF1+ Live — chaînes externes (ARTE, L'Equipe, etc.)
    print("\n=== TF1+ Live Chaînes externes ===")
    for chan_slug, chan_label, chan_logo in TF1_LIVE_EXTERNAL:
        extinf = (
            f'#EXTINF:-1 tvg-id="tf1live-{chan_slug}" '
            f'tvg-logo="{chan_logo}" '
            f'tvg-country="FR" '
            f'group-title="Live TF1+ Chaînes",{chan_label}'
        )
        lines.append(extinf)
        lines.append(f'tf1live://{chan_slug}')
        total += 1

    # TF1+ Live — chaînes FAST (replay 24/7)
    print("\n=== TF1+ Live FAST ===")
    for chan_id, chan_label, group_suffix in TF1_LIVE_FAST:
        extinf = (
            f'#EXTINF:-1 tvg-id="tf1live-fast-{chan_id}" '
            f'tvg-logo="https://i.imgur.com/qkOSt0o.png" '
            f'tvg-country="FR" '
            f'group-title="Live TF1+ {group_suffix}",{chan_label}'
        )
        lines.append(extinf)
        # ID direct L_FAST_* : passthrough dans TF1Resolver
        lines.append(f'tf1live://{chan_id}')
        total += 1

    # Pré-scan /programmes-tv/telefilms pour savoir quels si_id sont des
    # téléfilms (= unitaires TV, ni films ni séries). On construit un set
    # AVANT la boucle per-channel pour tagger correctement dès le 1er pass.
    print("\n=== TF1+ pré-scan /telefilms ===")
    tf1_telefilm_ids = set()
    for p in tf1_category_programs("telefilms"):
        tf1_telefilm_ids.add(p["si_id"])
    print(f"  {len(tf1_telefilm_ids)} téléfilms identifiés")

    # TF1+ Replay (login compte TF1 requis pour la résolution côté app)
    print("\n=== TF1+ Replay ===")
    for chan_slug, chan_label, chan_logo in TF1_CHANNELS:
        progs = tf1plus_channel_programs(chan_slug)
        print(f"  {chan_label}: {len(progs)} programmes")
        for p in progs:
            ilogo = p.get("logo") or chan_logo
            # 2026-06-19 v2 : classification JSON-LD @type d'abord, heuristique en fallback.
            #   TVSeries/TVSeason → series ; Movie/CreativeWork/Episode → movie selon context.
            si_path = (p.get("si_id") or "").lower()
            tf1_type = (p.get("tf1_type") or "").lower()
            tvg_type = (
                "telefilm" if p.get("si_id", "") in tf1_telefilm_ids else
                "series" if tf1_type in ("tvseries", "tvseason") else
                "movie" if tf1_type == "movie" else
                ("movie" if (chan_slug == "tf1seriesfilms" or "film" in si_path or "/cinema/" in si_path) else "series")
            )
            extinf = (
                f'#EXTINF:-1 tvg-id="tf1plus-{p["si_id"]}" '
                f'tvg-logo="{ilogo}" '
                f'tvg-country="FR" '
                f'tvg-type="{tvg_type}" '
                f'group-title="Replay {chan_label}",{p["title"]}'
            )
            lines.append(extinf)
            lines.append(f'tf1plus://{p["si_id"]}')
            total += 1
        time.sleep(0.5)

    # v2 (gain marginal) : catégories /programmes-tv/* (sport, reportages, etc.)
    #   Route chaque prog vers SA chaîne (= "Replay TF1", "Replay TMC", etc.)
    #   pour bénéficier de la card connexion TF1+ existante.
    print("\n=== TF1+ /programmes-tv/* (gain marginal) ===")
    tf1_cat_seen = set()
    for chan_slug, _, _ in TF1_CHANNELS:
        for p in tf1plus_channel_programs(chan_slug):
            tf1_cat_seen.add(p["si_id"])
    # Map chan_slug → (label, logo)
    chan_meta = {c[0]: (c[1], c[2]) for c in TF1_CHANNELS}
    for cat_slug, cat_label in TF1_CATEGORIES:
        progs = tf1_category_programs(cat_slug)
        added = 0
        for p in progs:
            if p["si_id"] in tf1_cat_seen:
                continue
            tf1_cat_seen.add(p["si_id"])
            # Extract chan depuis si_id "chan/slug"
            chan = p["si_id"].split("/")[0]
            meta = chan_meta.get(chan)
            if not meta:
                continue
            chan_label, chan_logo = meta
            si_path = (p.get("si_id") or "").lower()
            if cat_slug == "telefilms":
                tvg_type = "telefilm"
            else:
                is_film = "film" in si_path or "/cinema/" in si_path
                tvg_type = "movie" if is_film else "series"
            extinf = (
                f'#EXTINF:-1 tvg-id="tf1plus-{p["si_id"].replace(chr(47), chr(45))}" '
                f'tvg-logo="{chan_logo}" '
                f'tvg-country="FR" '
                f'tvg-type="{tvg_type}" '
                f'group-title="Replay {chan_label}",{p["title"]}'
            )
            lines.append(extinf)
            lines.append(f'tf1plus://{p["si_id"]}')
            added += 1
            total += 1
        print(f"  {cat_label}: +{added} nouveaux")
        time.sleep(0.3)

    # M6+ Replay (2026-06-19) — login compte M6 requis pour PLAYBACK (Widevine
    #   DRM). Le catalogue est public. Une catégorie M3U par chaîne (= 6 cards
    #   "🔓 Connexion 6play" côté LiveTvHubProvider quand non loggé).
    print("\n=== M6+ Replay ===")
    for service_id, chan_label, chan_logo in M6_CHANNELS:
        progs = m6_channel_programs(service_id)
        print(f"  {chan_label}: {len(progs)} programmes")
        for p in progs:
            ilogo = p.get("image") or chan_logo
            # Si image = external_key d'images.6play.fr, construire URL CDN
            if ilogo and not ilogo.startswith("http"):
                ilogo = f"https://images.6play.fr/v1/images/{ilogo}/raw"
            extinf = (
                f'#EXTINF:-1 tvg-id="m6plus-{p["program_id"]}" '
                f'tvg-logo="{ilogo}" '
                f'tvg-country="FR" '
                f'tvg-type="{p.get("tvg_type", "series")}" '
                f'group-title="Replay {chan_label}",{p["title"]}'
            )
            lines.append(extinf)
            # v2 : URL inclut le service (= chaîne API) pour que M6Resolver
            #   sache quel endpoint /services/{service}/... appeler côté Kotlin
            #   sans devoir essayer les 6 services à chaque clic.
            lines.append(f'm6play://{service_id}/{p["program_id"]}')
            total += 1
        time.sleep(0.4)

    # 2026-06-22 — NOUVEAU : M6+ Thématiques transverses (9 catégories
    #   cross-chaîne : Divertissement, Séries réalité, Séries, Sport,
    #   Infos & Société, Cinéma, Téléfilms, Jeunesse, Podcasts).
    #   Dossier dédié "Thématiques M6+" côté app (FolderDef regex
    #   ^Thématique M6\+ - .* dans LiveTvHubProvider).
    #   Dedup global : un programme déjà dans une chaîne M6+ ne sera pas
    #   re-ajouté dans la thématique (= évite doublons UI).
    print("\n=== M6+ Thématiques transverses ===")
    m6_themes_seen = set()
    # Récupère IDs déjà vus dans les chaînes M6+ pour dedup
    for line in lines:
        if line.startswith('m6play://'):
            try:
                pid_already = line.split('/')[-1]
                m6_themes_seen.add(pid_already)
            except Exception:
                pass
    for folder_id, folder_label in M6_FOLDERS:
        progs = m6_folder_programs(folder_id, folder_label)
        added = 0
        group = f"Thématique M6+ - {folder_label}"
        for p in progs:
            pid = p["program_id"]
            if pid in m6_themes_seen:
                continue
            m6_themes_seen.add(pid)
            ilogo = p.get("image") or M6_LOGO_GENERIC
            if ilogo and not ilogo.startswith("http"):
                ilogo = f"https://images.6play.fr/v1/images/{ilogo}/raw"
            extinf = (
                f'#EXTINF:-1 tvg-id="m6plus-{pid}" '
                f'tvg-logo="{ilogo}" '
                f'tvg-country="FR" '
                f'tvg-type="{p.get("tvg_type", "series")}" '
                f'group-title="{group}",{p["title"]}'
            )
            lines.append(extinf)
            # service par défaut m6replay (= les folders sont cross-chaîne,
            #   M6Resolver côté Kotlin sait gérer en essayant les services).
            lines.append(f'm6play://m6replay/{pid}')
            total += 1
            added += 1
        print(f"  Théma {folder_label}: {len(progs)} progs → {added} nouveaux (dedup)")
        time.sleep(0.3)

    # BFM / RMC Play Live (2026-06-21)
    print("\n=== BFM / RMC Play Live ===")
    for chan_key, chan_label, chan_logo in BFM_LIVE_CHANNELS:
        extinf = (
            f'#EXTINF:-1 tvg-id="bfmlive-{chan_key}" '
            f'tvg-logo="{chan_logo}" '
            f'tvg-country="FR" '
            f'group-title="Live BFM Play",{chan_label} Direct'
        )
        lines.append(extinf)
        lines.append(f'bfmlive://{chan_key}')
        total += 1
    print(f"  {len(BFM_LIVE_CHANNELS)} chaînes live")

    # BFM / RMC Play Replay (2026-06-21) — login compte BFM requis pour PLAYBACK
    #   (Widevine DRM). Le catalogue est public. Une catégorie M3U par chaîne
    #   (= cards "🔓 Connexion RMC BFM Play" côté LiveTvHubProvider quand non
    #   loggé, comme pour M6+ et TF1+).
    #   Dedup GLOBAL cross-channel : un même productId ne doit apparaître qu'1×
    #   dans le M3U (première chaîne qui le contient gagne).
    print("\n=== BFM / RMC Play Replay ===")
    # 2026-06-22 (user "il faut tout prendre, pas juste les saisons") :
    #   Pour chaque Saison récupérée, on appelle /content/{id}/episodes
    #   pour récupérer les épisodes individuels datés. Si la saison renvoie
    #   ≥1 épisode, on PUSH les épisodes ET PAS la saison (= évite doublon).
    #   Si /episodes retourne 0 (= ce n'est pas une vraie série), on garde
    #   le bloc saison comme avant.
    bfm_global_seen = set()
    for menu_id, chan_slug, chan_label, chan_logo in BFM_REPLAY_CHANNELS:
        progs = bfm_channel_programs(menu_id)
        added_seasons = 0
        added_episodes = 0
        for p in progs:
            season_pid = p["product_id"]
            if season_pid in bfm_global_seen:
                continue
            bfm_global_seen.add(season_pid)
            ilogo_season = p.get("image") or chan_logo
            category = p.get("category", "")
            group = f"Replay {chan_label} - {category}" if category else f"Replay {chan_label}"
            # Tente fetch épisodes pour cette saison
            episodes = bfm_fetch_episodes(season_pid)
            if episodes:
                # Pousser les épisodes datés
                for ep in episodes:
                    ep_pid = ep["product_id"]
                    if ep_pid in bfm_global_seen:
                        continue
                    bfm_global_seen.add(ep_pid)
                    ep_logo = ep.get("image") or ilogo_season
                    ep_title = f'{p["title"]} - {ep["title"]}' if ep["title"] != p["title"] else p["title"]
                    lines.append(
                        f'#EXTINF:-1 tvg-id="bfmplay-{ep_pid}" '
                        f'tvg-logo="{ep_logo}" '
                        f'tvg-country="FR" '
                        f'tvg-type="{ep.get("tvg_type", "series")}" '
                        f'group-title="{group}",{ep_title[:200]}'
                    )
                    lines.append(f'bfmplay://{ep_pid}')
                    total += 1
                    added_episodes += 1
                time.sleep(0.05)
            else:
                # Pas d'épisodes → push la saison telle quelle (fallback)
                lines.append(
                    f'#EXTINF:-1 tvg-id="bfmplay-{season_pid}" '
                    f'tvg-logo="{ilogo_season}" '
                    f'tvg-country="FR" '
                    f'tvg-type="{p.get("tvg_type", "series")}" '
                    f'group-title="{group}",{p["title"]}'
                )
                lines.append(f'bfmplay://{season_pid}')
                total += 1
                added_seasons += 1
        print(f"  {chan_label}: {len(progs)} saisons, {added_episodes} épisodes + {added_seasons} saisons fallback ajoutés")
        time.sleep(0.4)

    # 2026-06-22 — NOUVEAU : Thématiques transverses BFM/RMC (14 thématiques
    #   cross-chaîne : Crime, Cinéma, Moteur, Aventure, Divertissement, Docu,
    #   Mystère, Histoire, Science, Société, Docu-Réalité, Sport, Info & Talk,
    #   Grand Reportage). Endpoint /tile/RefTile::xxx/content?size=200.
    #   Dossier dédié "Thématiques BFM Play" côté app (FolderDef regex
    #   ^Thématique BFM Play - .* dans LiveTvHubProvider).
    print("\n=== BFM/RMC Thématiques transverses ===")
    bfm_themes_global_seen = set()
    for slug, theme_id, theme_label in BFM_THEMES:
        progs = bfm_theme_programs(theme_id, theme_label)
        added_episodes = 0
        added_seasons = 0
        group = f"Thématique BFM Play - {theme_label}"
        for p in progs:
            season_pid = p["product_id"]
            # Dedup global cross-théma ET cross-chaîne (= un programme dans
            #   "Documentaire" thématique pourrait être déjà dans Replay BFM TV).
            if season_pid in bfm_global_seen or season_pid in bfm_themes_global_seen:
                continue
            bfm_themes_global_seen.add(season_pid)
            ilogo_season = p.get("image") or f"{BFM_LOGO_BASE}/bfm-tv-fr.png"
            episodes = bfm_fetch_episodes(season_pid)
            if episodes:
                for ep in episodes:
                    ep_pid = ep["product_id"]
                    if ep_pid in bfm_global_seen or ep_pid in bfm_themes_global_seen:
                        continue
                    bfm_themes_global_seen.add(ep_pid)
                    ep_logo = ep.get("image") or ilogo_season
                    ep_title = f'{p["title"]} - {ep["title"]}' if ep["title"] != p["title"] else p["title"]
                    lines.append(
                        f'#EXTINF:-1 tvg-id="bfmplay-{ep_pid}" '
                        f'tvg-logo="{ep_logo}" '
                        f'tvg-country="FR" '
                        f'tvg-type="{ep.get("tvg_type", "series")}" '
                        f'group-title="{group}",{ep_title[:200]}'
                    )
                    lines.append(f'bfmplay://{ep_pid}')
                    total += 1
                    added_episodes += 1
                time.sleep(0.05)
            else:
                lines.append(
                    f'#EXTINF:-1 tvg-id="bfmplay-{season_pid}" '
                    f'tvg-logo="{ilogo_season}" '
                    f'tvg-country="FR" '
                    f'tvg-type="{p.get("tvg_type", "series")}" '
                    f'group-title="{group}",{p["title"]}'
                )
                lines.append(f'bfmplay://{season_pid}')
                total += 1
                added_seasons += 1
        print(f"  Thématique {theme_label}: {len(progs)} saisons → {added_episodes} épisodes + {added_seasons} saisons ajoutés")
        time.sleep(0.3)

    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[OK] Wrote {total} replay programs to {output_path}")
    return total


if __name__ == "__main__":
    out = os.environ.get("OUTPUT", "data-replay.m3u")
    n = generate_m3u(out)
    # Cleanup WARP si on l'a démarré
    if WARP_PROXY:
        subprocess.run(["warp-cli", "disconnect"], capture_output=True)
        print("[VPN] WARP déconnecté", file=sys.stderr)
    if n == 0:
        print("[!] No replays found, exiting with error", file=sys.stderr)
        sys.exit(1)
