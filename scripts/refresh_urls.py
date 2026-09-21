#!/usr/bin/env python3
"""
Auto-refresh data.m3u v4 (2026-06-16) :
- A. SMART CHECK des HLS .m3u8 : ouvre le contenu, valide #EXTM3U, descend dans
     le 1er variant des master playlists pour détecter les CDN géo-bloqués.
- B. AUTO-ADD nouvelles chaînes ParaTV → catégorie "Nouveautés ParaTV".
- C. NEW Phase D : multi-sources FR (schumijo, iptv-org, bugsfreeweb, iptv-ch,
     kilirushi). Pour chaque chaîne FR canonique qu'on a déjà : ajoute les URLs
     alternatives dans une catégorie "France TV backup" (= si la principale
     meurt un jour, on a des fallbacks). Pour chaque chaîne FR qu'on n'a pas :
     ajoute dans sa catégorie d'origine normalisée (Info/Cinéma/Musique/Sport/
     Jeunesse/Séries/Documentaire/Généralistes/Radio).

Catégories protégées (URLs JAMAIS modifiées) :
  - Premium FR  → host 185.160.192.14    (= redirect off20 prime-tv)
  - Live Canal  → host live.aab1.top     (= Xtream Codes statique)
  - prime-tv    → host off20.lynxcontents.click
  - PocketBase  → host 47.237.205.89
  - iptv-org M6 → host jmp2.uk           (= redirect Pluto TV M6 FAST)
  - iptv-org W9 → host filegear-sg.me    (= proxy W9 FAST)
  - Pluto TV    → host pluto.tv          (= chaîne FAST destination jmp2)
"""
import os, re, sys, asyncio, aiohttp
from urllib.parse import urlparse, urljoin

PARATV_MAIN = "https://raw.githubusercontent.com/Paradise-91/ParaTV/main/playlists/paratv/main/paratv.m3u"
LOCAL_M3U = os.environ.get("LOCAL_M3U", "data.m3u")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
TIMEOUT = 8
CONCURRENCY = 8
SKIP_HOSTS = ("185.160.192.14", "off20.lynxcontents.click",
              "47.237.205.89", "jmp2.uk", "filegear-sg.me", "pluto.tv",
              # 2026-08-22 : Multi Live — ce sont des PAGES de lecteur,
              #   pas des flux. Sans cette protection, le check les declare
              #   mortes et remplace leur URL.
              "cartelive.club")
# 2026-06-16 : live.aab1.top RETIRE de SKIP_HOSTS car ~90% de ses URLs sont
# mortes (testees 6/60 vivantes). Mais on veut garder M6 (1059) + W9 (1083)
# pour TNT France. Donc on les protege individuellement via SKIP_URLS.
SKIP_URLS = (
    "http://live.aab1.top/live/odai/123321/1059.ts",  # M6 TNT France
    "http://live.aab1.top/live/odai/123321/1083.ts",  # W9 TNT France
)

# === Phase D NEW : sources externes FR ===
# Source FR : (label, url, source_is_fr) — si True, toutes les chaines sont
# considerees FR par defaut sauf si un marqueur etranger (USA/Italy/etc) figure
# dans le group-title.
EXTERNAL_FR_SOURCES = [
    ("schumijo",     "https://raw.githubusercontent.com/schumijo/iptv/main/fr.m3u8", True),
    ("iptv-org-fr",  "https://iptv-org.github.io/iptv/countries/fr.m3u", True),
    ("iptv-org-fra", "https://iptv-org.github.io/iptv/languages/fra.m3u", True),
    ("bugsfreeweb",  "https://raw.githubusercontent.com/bugsfreeweb/LiveTVCollector/main/LiveTV/France/LiveTV.m3u", True),
    ("kilirushi",    "https://raw.githubusercontent.com/kilirushi/iptv/master/fr.m3u", True),
]

# Chaînes FR canoniques (= si présentes dans une source externe, on les considère FR)
FR_CANONICAL = [
    "TF1", "TMC", "TFX", "TF1 SÉRIES FILMS", "TF1 SERIES FILMS", "LCI",
    "FRANCE 2", "FRANCE 3", "FRANCE 4", "FRANCE 5", "FRANCEINFO", "FRANCE INFO", "FRANCE 24",
    "M6", "W9", "6TER", "GULLI", "PARIS PREMIERE", "PARIS PREMIÈRE",
    "CANAL+", "CANAL +", "C8", "CSTAR", "CNEWS", "BFM TV", "BFMTV", "BFM BUSINESS",
    "RMC DECOUVERTE", "RMC DÉCOUVERTE", "RMC STORY",
    "ARTE", "TV5 MONDE", "TV5MONDE",
    "L'EQUIPE", "L'ÉQUIPE", "LEQUIPE", "EUROSPORT 1", "EUROSPORT 2",
    "PUBLIC SENAT", "PUBLIC SÉNAT", "LCP",
    "NRJ 12", "CHERIE 25", "CHÉRIE 25",
]

# Mapping catégorie source → catégorie cible (fusion + normalisation)
CATEGORY_NORMALIZATIONS = [
    # (matcher_lower, target) — ordre = priorite
    ("informations", "Info"), ("information", "Info"), ("news", "Info"), ("actualité", "Info"), ("actualite", "Info"),
    ("films", "Cinéma"), ("movies", "Cinéma"), ("movie", "Cinéma"), ("cinema", "Cinéma"), ("cinéma", "Cinéma"), ("vod", "VOD"), ("replay", "Replay"),
    ("music", "Musique"), ("música", "Musique"), ("musica", "Musique"), ("musique", "Musique"),
    ("kids", "Jeunesse"), ("children", "Jeunesse"), ("jeunesse", "Jeunesse"), ("enfants", "Jeunesse"),
    ("sports", "Sport"), ("sport", "Sport"),
    ("series", "Séries"), ("serie", "Séries"), ("séries", "Séries"), ("série", "Séries"),
    ("documentary", "Documentaire"), ("docus", "Documentaire"), ("documentaire", "Documentaire"),
    ("découverte", "Documentaire"), ("decouverte", "Documentaire"),
    ("generaliste", "Généralistes"), ("généraliste", "Généralistes"), ("generalist", "Généralistes"), ("general", "Généralistes"),
    ("tnt", "TNT France"),
    ("style de vie", "Lifestyle"), ("lifestyle", "Lifestyle"), ("pratique", "Lifestyle"),
    ("cooking", "Lifestyle"),
    ("divertissement", "Divertissement"), ("entertainment", "Divertissement"), ("comedy", "Divertissement"),
    ("radio", "Radio FR"),
    ("religious", "Religion"), ("religion", "Religion"),
    ("culture", "Culture"),
    ("politique", "Politique"),
    ("régionale", "Locale"), ("regionale", "Locale"), ("locale", "Locale"), ("local", "Locale"),
    ("etrangère", "Internationale"), ("etrangere", "Internationale"), ("étrangère", "Internationale"),
    ("international", "Internationale"),
    ("undefined", "Nouveautés FR"),
]


def extract_channel_name(extinf_block: str) -> str:
    """Extrait le nom de chaine d'un bloc EXTINF, en gerant correctement les
    attributs quoted contenant des virgules (ex: tvg-logo='foo,bar.png')."""
    first_line = extinf_block.split('\n', 1)[0]
    # Trouve la derniere virgule HORS guillemets
    in_quote = False
    last_comma = -1
    for i, c in enumerate(first_line):
        if c == '"':
            in_quote = not in_quote
        elif c == ',' and not in_quote:
            last_comma = i
    if last_comma >= 0:
        return first_line[last_comma+1:].strip()
    return ""


def normalize_name(s: str) -> str:
    """Strip numero prefix, brackets, parens (qualite), accents, suffix qualite."""
    import unicodedata
    s = re.sub(r'^\d+\.\s*', '', s)
    s = re.sub(r'\[.*?\]', '', s)
    s = re.sub(r'\(.*?\)', '', s)  # strip "(1080p)" "(720p)" etc.
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')  # ascii fold
    s = s.upper().strip()
    s = re.sub(r'\s+(HD|FHD|UHD|4K|SD|1080P|720P|480P)\s*$', '', s)
    # Strip apostrophe variants
    s = s.replace("'", "").replace('`', '').replace('-', ' ')
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def normalize_category(g: str) -> str:
    g_clean = (g or '').strip()
    if not g_clean:
        return "Nouveautés FR"
    g_lower = g_clean.lower()
    for src, dst in CATEGORY_NORMALIZATIONS:
        if src in g_lower:
            return dst
    return g_clean


def is_french_content(name: str, group: str, tvg_id: str, source_is_fr: bool = False) -> bool:
    """Détecte si une chaîne est française (TV, radio, films/séries FR).
    source_is_fr=True : la source est étiquetée FR (= toutes ses chaînes sont FR
    sauf marqueur explicite étranger)."""
    n = (name or '').upper()
    g = (group or '').upper()
    t = (tvg_id or '').lower()
    # Marqueurs étranger forts (= override : on rejette même si source FR)
    foreign_groups = ['ITALY', 'SPAIN', 'GERMANY', 'PORTUGAL', 'RUSSIA', 'JAPAN',
                      'CHINA', 'KOREA', 'USA', 'UK', 'HAITI', 'GREECE', 'ARAB',
                      'TURKEY', 'BRAZIL']
    if any(fg in g for fg in foreign_groups):
        return False
    # Si la source est étiquetée FR, on accepte par défaut
    if source_is_fr:
        return True
    # Marqueurs FR forts
    if 'FRANCE' in g or 'FRENCH' in g or 'FRANÇAIS' in g or 'FRANCAIS' in g:
        return True
    if t.endswith('.fr') or '.fr@' in t or 'fr@' in t:
        return True
    if 'FR' in n.split() or '[FR]' in n or 'FRANCE' in n or 'FRENCH' in n:
        return True
    # Catégories en français = très bon signal FR
    fr_categories_markers = ['CINÉMA', 'SÉRIES', 'DOCUMENTAIRE', 'JEUNESSE',
                              'GÉNÉRALISTE', 'DIVERTISSEMENT', 'MUSIQUE',
                              'INFORMATION', 'ACTUALITÉ', 'RELIGION', 'CULTURE',
                              'POLITIQUE', 'STYLE DE VIE', 'RÉGIONALE', 'LOCALE',
                              'NOUVEAUTÉS', 'DÉCOUVERTE']
    if any(fc in g for fc in fr_categories_markers):
        return True
    # Chaîne FR canonique
    for c in FR_CANONICAL:
        if c in n:
            return True
    # Radios FR connues
    fr_radios = ['NRJ', 'RTL', 'EUROPE 1', 'EUROPE 2', 'FRANCE INTER',
                 'FRANCE CULTURE', 'SKYROCK', 'FUN RADIO', 'RADIO FRANCE',
                 'RIRE ET CHANSONS', 'CHERIE FM', 'CHÉRIE FM', 'MFM', 'OUI FM',
                 'NOSTALGIE', 'VIRGIN RADIO']
    for r in fr_radios:
        if r in n:
            return True
    return False


async def fetch_paratv_index(session) -> dict:
    async with session.get(PARATV_MAIN, headers={"User-Agent": UA},
                           timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
        text = await r.text()
    index = {}
    blocks = re.split(r'(?=#EXTINF)', text)
    for b in blocks:
        name_raw = extract_channel_name(b)
        url_m = re.search(r'\n(https?://\S+)', b)
        if name_raw and url_m:
            name = normalize_name(name_raw)
            url = url_m.group(1).strip()
            index.setdefault(name, url)
    return index


async def fetch_paratv_full(session) -> list:
    async with session.get(PARATV_MAIN, headers={"User-Agent": UA},
                           timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
        text = await r.text()
    blocks = re.split(r'(?=#EXTINF)', text)
    result = []
    for b in blocks:
        if not b.startswith('#EXTINF'):
            continue
        name_raw = extract_channel_name(b)
        url_m = re.search(r'\n(https?://\S+)', b)
        group_m = re.search(r'group-title="([^"]*)"', b)
        if name_raw and url_m:
            result.append((
                name_raw,
                url_m.group(1).strip(),
                group_m.group(1) if group_m else "",
                b
            ))
    return result


async def fetch_external_source(session, label: str, url: str) -> list:
    """Fetch une source externe et retourne list[(name, url, group, tvg_id, full_block)]."""
    try:
        async with session.get(url, headers={"User-Agent": UA},
                               timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
            text = await r.text()
    except Exception as e:
        print(f"  [{label}] fetch error: {e}")
        return []
    if not text.lstrip().startswith('#EXTM3U'):
        print(f"  [{label}] not a valid m3u")
        return []
    result = []
    blocks = re.split(r'(?=#EXTINF)', text)
    for b in blocks:
        if not b.startswith('#EXTINF'):
            continue
        name_raw = extract_channel_name(b)
        url_m = re.search(r'\n(https?://\S+)', b)
        group_m = re.search(r'group-title="([^"]*)"', b)
        tvgid_m = re.search(r'tvg-id="([^"]*)"', b)
        if name_raw and url_m:
            result.append((
                name_raw,
                url_m.group(1).strip(),
                group_m.group(1) if group_m else "",
                tvgid_m.group(1) if tvgid_m else "",
                b.rstrip()
            ))
    print(f"  [{label}] {len(result)} chaines parsées")
    return result


# === DOSSIERS MIX FR : liens M3U colles par l'utilisateur (fichier mixfr-dossiers.txt) ===
# 2026-09-05 (user « je veux ajouter une playlist M3U dans Mix FR, en un dossier ») :
#   chaque ligne « Nom du dossier | https://lien.m3u » du fichier mixfr-dossiers.txt
#   (racine du depot) est importee EN ENTIER dans data.m3u sous un seul
#   group-title = le nom du dossier -> un dossier dans « Autres Replays > Mix FR ».
#   La section est reconstruite a chaque refresh (entre les deux marqueurs), donc
#   toujours a jour ; si un lien ne repond pas, son ancien contenu est conserve.
DOSSIERS_FICHIER = os.path.join(os.path.dirname(LOCAL_M3U) or ".", "mixfr-dossiers.txt")
DOSSIERS_DEBUT = "# ===== DOSSIERS MIX FR (import automatique de mixfr-dossiers.txt, ne pas editer ici) ====="
DOSSIERS_FIN = "# ===== FIN DOSSIERS MIX FR ====="


def lire_dossiers_m3u() -> list:
    """Retourne [(nom_dossier, url)] depuis mixfr-dossiers.txt (lignes « Nom | URL »)."""
    out = []
    if not os.path.exists(DOSSIERS_FICHIER):
        return out
    with open(DOSSIERS_FICHIER, encoding='utf-8') as f:
        for ligne in f:
            l = ligne.strip()
            if not l or l.startswith('#'):
                continue
            if '|' in l:
                nom, _, url = l.partition('|')
            else:
                nom, url = "", l
            nom, url = nom.strip(), url.strip()
            if not url.startswith(('http://', 'https://')):
                continue
            out.append((nom or "Playlist", url))
    return out


def _section_dossiers(content: str) -> str:
    """Contenu actuel de la section (entre marqueurs), '' si absente."""
    i = content.find(DOSSIERS_DEBUT)
    j = content.find(DOSSIERS_FIN)
    if i < 0 or j < 0 or j < i:
        return ""
    return content[i:j + len(DOSSIERS_FIN)]


def _blocs_dossier(section: str, nom: str) -> str:
    """Sous-partie de la section pour un dossier donne (ancien contenu de secours)."""
    marque = f"# --- dossier: {nom} ---\n"
    i = section.find(marque)
    if i < 0:
        return ""
    j = section.find("# --- dossier: ", i + len(marque))
    if j < 0:
        j = section.find(DOSSIERS_FIN, i)
    return section[i:j] if j > i else ""


async def _telecharger_dossier(session, nom: str, url: str) -> str:
    """Telecharge une playlist et rend ses blocs, tous sous group-title=nom."""
    try:
        async with session.get(url, headers={"User-Agent": UA},
                               timeout=aiohttp.ClientTimeout(total=max(TIMEOUT, 20))) as r:
            text = await r.text()
    except Exception as e:
        print(f"  [dossier {nom}] fetch error: {e}")
        return ""
    if '#EXTINF' not in text:
        print(f"  [dossier {nom}] pas une playlist M3U")
        return ""
    # Fins de ligne normalisees + lignes vides retirees : le parseur de l'appli
    #   (parseMixFrM3u) oublie l'EXTINF en cours des qu'il rencontre une ligne vide.
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    # L'appli (LiveHubFolderDialog.displayAggregatedCategories) range dans le
    #   fourre-tout « Programmes » tout groupe SANS « - » dans son nom, et
    #   affiche la partie APRES « - » comme nom de dossier. Pour obtenir un
    #   dossier « Zone 18@ » a part entiere, le group-title devient
    #   « Zone 18@ - Zone 18@ ».
    gt = nom if ' - ' in nom else f"{nom} - {nom}"
    sortie, n = [], 0
    for b in re.split(r'(?=#EXTINF)', text):
        if not b.startswith('#EXTINF'):
            continue
        b = '\n'.join(l.strip() for l in b.split('\n') if l.strip())
        if not re.search(r'\n(https?://\S+)', b):
            continue
        premiere, _, reste = b.partition('\n')
        if 'group-title=' in premiere:
            premiere = re.sub(r'group-title="[^"]*"', f'group-title="{gt}"', premiere)
        else:
            premiere = re.sub(r'^#EXTINF:(-?\d+)', rf'#EXTINF:\1 group-title="{gt}"', premiere, count=1)
        sortie.append(premiere + '\n' + reste + '\n')
        n += 1
    print(f"  [dossier {nom}] {n} chaines importees")
    return ''.join(sortie)


async def importer_dossiers_m3u(content: str, ancienne: str = None) -> str:
    """Reconstruit la section DOSSIERS MIX FR de data.m3u depuis mixfr-dossiers.txt.
    `ancienne` = section precedente (secours si un lien ne repond pas)."""
    dossiers = lire_dossiers_m3u()
    if ancienne is None:
        ancienne = _section_dossiers(content)
    elif ancienne and ancienne not in content:
        content = content.rstrip() + "\n\n" + ancienne + "\n"
    sans = content.replace(ancienne, "").rstrip() + "\n" if ancienne else content
    if not dossiers:
        return sans if ancienne else content
    print(f"\n=== Phase 5 : dossiers Mix FR ({len(dossiers)} lien(s) dans mixfr-dossiers.txt) ===")
    parties = [DOSSIERS_DEBUT + "\n"]
    conn = aiohttp.TCPConnector(limit=4, ssl=False)
    async with aiohttp.ClientSession(connector=conn) as session:
        for nom, url in dossiers:
            blocs = await _telecharger_dossier(session, nom, url)
            if not blocs:
                secours = _blocs_dossier(ancienne, nom)
                if secours:
                    print(f"  [dossier {nom}] lien injoignable -> ancien contenu conserve")
                    parties.append(secours)
                continue
            parties.append(f"# --- dossier: {nom} ---\n" + blocs)
    parties.append(DOSSIERS_FIN + "\n")
    return sans.rstrip() + "\n\n" + ''.join(parties)


# === DOSSIERS D'AFFICHAGE MIX FR (Phase 6) ===
# 2026-09-05 (user « Programmes a 1468 chaines, les autres dossiers sont a moitie
#   vides, trie tout ca ; ce que tu ne sais pas trier va dans Autre ») :
#   l'appli (LiveHubFolderDialog.displayAggregatedCategories) construit les
#   sous-dossiers de Mix FR a partir de la partie APRES « - » du group-title,
#   et jette dans « Programmes » tout groupe SANS « - ». Ici on donne a chaque
#   groupe une forme « <origine> - <Dossier> » avec un nom de dossier canonique
#   (Sport/Sports/Sports & Auto -> Sport, Cinema/Cinéma/Films -> Films...).
#   Groupe sans « - » inconnu -> « Autres ». Les groupes speciaux (Multi Live,
#   Live MIX) et la section DOSSIERS MIX FR ne sont pas touches.
DOSSIERS_AFFICHAGE = {
    'sport': 'Sport', 'sports': 'Sport', 'sports & auto': 'Sport',
    'info': 'Info', 'news': 'Info', 'actualites': 'Info', 'actualités': 'Info', 'bfm / rmc': 'Info',
    'business': 'Info', 'legislative': 'Info', 'politique': 'Info',
    'jeunesse': 'Jeunesse', 'kids': 'Jeunesse', 'animation': 'Jeunesse', 'anime': 'Jeunesse',
    'cinema': 'Films', 'cinéma': 'Films', 'films': 'Films', 'vod': 'Films',
    'action & thriller': 'Action & Thriller', 'comedie': 'Comédie', 'comédie': 'Comédie',
    'crime': 'Crime & Mystère', 'crime & mystere': 'Crime & Mystère', 'drame & romance': 'Drame & Romance',
    'séries': 'Séries', 'series': 'Séries', 'series tv': 'Séries', 'séries fr': 'Séries FR',
    'series policieres': 'Séries policières', 'a binge-watch': 'A Binge-Watch',
    'documentaire': 'Documentaires', 'documentaires': 'Documentaires', 'culture': 'Documentaires',
    'nature & animaux': 'Nature & Animaux', 'voyages et gastronomie': 'Voyages et Gastronomie',
    'lifestyle': 'Lifestyle',
    'tele realite': 'Télé-réalité', 'tv realite': 'Télé-réalité',
    'divertissement': 'Divertissement', 'emissions cultes': 'Émissions cultes',
    'musique': 'Musique', 'radio fr': 'Radio',
    '100% francais': '100% Français', 'généralistes': '100% Français', 'generalistes': '100% Français',
    'tnt france': '100% Français', 'live tf1+': '100% Français', 'live canal': '100% Français',
    'canal+': '100% Français', 'france': '100% Français', 'locale': '100% Français',
    'nouveautes paratv': 'Nouveautés', 'nouveautés paratv': 'Nouveautés', 'nouveautés fr': 'Nouveautés',
    'nouveau sur pluto tv': 'Nouveautés',
    'pluto tv': 'Pluto TV', 'rakuten tv': 'Rakuten TV', 'sony one': 'Sony One',
    'autres': 'Autres', 'religion': 'Autres', 'netplus': 'Autres', 'suisse': 'Autres', 'belgique': 'Autres',
    'education': 'Autres', 'france tv backup': 'Autres', 'internationale': 'Autres', 'replay': 'Autres',
}
GROUPES_PROTEGES = ('multi live', 'live mix')


def dossier_affichage(group: str) -> str:
    """Rend le group-title a ecrire pour un groupe donne (voir DOSSIERS_AFFICHAGE)."""
    g = (group or '').strip()
    gl = g.lower()
    if not g or gl.startswith(GROUPES_PROTEGES):
        return g
    if ' - ' in g:
        origine, sous = g.split(' - ', 1)
        sous = sous.strip()
        # Deja de la forme « Nom - Nom » (dossiers colles par l'utilisateur) : inchange.
        if sous == origine.strip():
            return g
        cible = DOSSIERS_AFFICHAGE.get(sous.lower(), sous)
        return f"{origine.strip()} - {cible}"
    cible = DOSSIERS_AFFICHAGE.get(gl, 'Autres')
    return f"{g} - {cible}"


def appliquer_dossiers_affichage(content: str) -> str:
    """Phase 6 : reecrit tous les group-title (hors section DOSSIERS MIX FR)."""
    stats = {}
    def remplacer(m):
        avant = m.group(1)
        apres = dossier_affichage(avant)
        if apres != avant:
            stats[apres.split(' - ', 1)[1] if ' - ' in apres else apres] = \
                stats.get(apres.split(' - ', 1)[1] if ' - ' in apres else apres, 0) + 1
        return f'group-title="{apres}"'
    out = re.sub(r'group-title="([^"]*)"', remplacer, content)
    if stats:
        print("\n=== Phase 6 : dossiers d'affichage Mix FR ===")
        for k, v in sorted(stats.items(), key=lambda kv: -kv[1]):
            print(f"  -> {k:24s} {v} chaine(s) reclassee(s)")
    return out


def skip_url(url: str) -> bool:
    if url in SKIP_URLS:
        return True
    host = (urlparse(url).hostname or "").lower()
    return any(s in host for s in SKIP_HOSTS)


async def is_alive_smart(session, sem, url: str) -> bool:
    async with sem:
        try:
            url_no_q = url.split('?')[0].lower()
            is_m3u8 = url_no_q.endswith('.m3u8')
            if is_m3u8:
                async with session.get(url, headers={"User-Agent": UA},
                                       timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                                       allow_redirects=True) as r:
                    if r.status >= 400:
                        return False
                    chunk = await r.content.read(8192)
                    try:
                        text = chunk.decode('utf-8', errors='replace')
                    except Exception:
                        return False
                if not text.lstrip().startswith('#EXTM3U'):
                    return False
                if '#EXT-X-STREAM-INF' in text:
                    lines = text.splitlines()
                    variant_url = None
                    for j, line in enumerate(lines):
                        if line.startswith('#EXT-X-STREAM-INF'):
                            if j+1 < len(lines):
                                cand = lines[j+1].strip()
                                if cand and not cand.startswith('#'):
                                    variant_url = cand
                                    break
                    if variant_url:
                        if not variant_url.startswith('http'):
                            variant_url = urljoin(url, variant_url)
                        # 2026-06-16 (user "TF1 reste 404 alors que paratv a la
                        #   nouvelle URL") : LE HEAD du variant échouait depuis
                        #   le runner GitHub US sur les CDN SFR/TF1/M6 (UA
                        #   strict + geo-blocking → 403). Du coup une URL master
                        #   PLAYLIST PARFAITEMENT VALIDE était marquée dead +
                        #   pas remplacée. Si le master playlist répond bien
                        #   #EXTM3U, on FAIT CONFIANCE pour les sources de
                        #   référence ParaTV — leurs master playlists sont
                        #   directement servis depuis raw.githubusercontent.com
                        #   donc pas de souci geo/UA. Pour les autres CDN
                        #   (france.tv, etc.) on garde le check.
                        host = (urlparse(url).hostname or '').lower()
                        trusted_master_hosts = ('raw.githubusercontent.com', 'github.com',
                                                'static-cdn.tv.sfr.net', 'photos.tf1.fr',
                                                'i.imgur.com')
                        if any(h in host for h in trusted_master_hosts):
                            # Master playlist host trusted → master OK = alive.
                            return True
                        try:
                            async with session.head(variant_url, headers={"User-Agent": UA},
                                                    timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                                                    allow_redirects=True) as vr:
                                if vr.status == 405:
                                    async with session.get(variant_url, headers={"User-Agent": UA},
                                                           timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                                                           allow_redirects=True) as vg:
                                        chunk2 = await vg.content.read(2048)
                                        body2 = chunk2.decode('utf-8', errors='replace').lower()
                                        if 'denied' in body2 or 'forbidden' in body2 or vg.status >= 400:
                                            return False
                                        return True
                                # 2026-06-16 : si HEAD = 403/401 (typique CDN
                                #   anti-bot), on tente GET ; si GET marche
                                #   (status<400) on accepte. Sinon dead.
                                if vr.status in (401, 403):
                                    async with session.get(variant_url, headers={"User-Agent": UA},
                                                           timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                                                           allow_redirects=True) as vg2:
                                        return vg2.status < 400
                                if vr.status >= 400:
                                    return False
                        except Exception:
                            # 2026-06-16 : exception réseau sur variant ne veut
                            #   pas dire dead — master playlist OK = ALIVE.
                            return True
                return True
            else:
                async with session.head(url, headers={"User-Agent": UA},
                                        timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                                        allow_redirects=True) as r:
                    if r.status == 405:
                        async with session.get(url, headers={"User-Agent": UA},
                                               timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                                               allow_redirects=True) as g:
                            return g.status < 400
                    return r.status < 400
        except Exception:
            return False


def build_m3u_block(name: str, url: str, group: str, tvg_id: str = "", logo: str = "") -> str:
    """Construit un bloc EXTINF + URL standard pour ajouter au M3U."""
    attrs = []
    if tvg_id:
        attrs.append(f'tvg-id="{tvg_id}"')
    if logo:
        attrs.append(f'tvg-logo="{logo}"')
    attrs.append(f'group-title="{group}"')
    attr_str = " ".join(attrs)
    return f'#EXTINF:-1 {attr_str},{name}\n{url}'


async def main_async():
    print(f"=== Auto-refresh {LOCAL_M3U} v4 (multi-sources FR, concurrency={CONCURRENCY}) ===")
    conn = aiohttp.TCPConnector(limit=CONCURRENCY * 2, ssl=False)
    async with aiohttp.ClientSession(connector=conn) as session:
        # === Sources : ParaTV (principal) + externes FR ===
        print(f"\n--- Fetch sources ---")
        paratv_simple = await fetch_paratv_index(session)
        paratv_full = await fetch_paratv_full(session)
        print(f"  ParaTV: {len(paratv_full)} chaines")

        external_data = {}  # label -> list of (name, url, group, tvg_id, block)
        for label, src_url, src_is_fr in EXTERNAL_FR_SOURCES:
            chans = await fetch_external_source(session, label, src_url)
            external_data[label] = (chans, src_is_fr)

        with open(LOCAL_M3U, encoding='utf-8') as f:
            content = f.read()

        # Dossiers Mix FR (liens colles par l'utilisateur) : SORTIS du traitement
        #   ci-dessous (pas de test « mort », pas de remplacement ParaTV, pas de
        #   suppression) ; la section est reconstruite telle quelle en Phase 5.
        section_dossiers = _section_dossiers(content)
        contenu_travail = content.replace(section_dossiers, "").rstrip() + "\n" if section_dossiers else content

        blocks = re.split(r'(?=#EXTINF)', contenu_travail)
        entries = []
        for i, b in enumerate(blocks[1:], 1):
            if not b.startswith('#EXTINF'):
                continue
            name_raw = extract_channel_name(b)
            url_m = re.search(r'\n(https?://\S+)', b)
            if not name_raw or not url_m:
                continue
            name = name_raw
            url = url_m.group(1).strip()
            if skip_url(url):
                continue
            entries.append((i, name, url))

        print(f"\n  -> {len(entries)} URLs to check (skip-list filtered)")
        sem = asyncio.Semaphore(CONCURRENCY)

        # === Phase 1 : check alive ===
        alive_results = await asyncio.gather(
            *(is_alive_smart(session, sem, e[2]) for e in entries),
            return_exceptions=True
        )
        dead_entries = [(i, name, url) for (i, name, url), alive in zip(entries, alive_results)
                        if alive is False or isinstance(alive, Exception)]
        n_alive = len(alive_results) - len(dead_entries)
        print(f"  Alive: {n_alive}  Dead: {len(dead_entries)}")

        # === Phase 2 : remplacer morts par ParaTV (puis fallback sources externes) ===
        candidates = []
        for i, name, url in dead_entries:
            new_url = paratv_simple.get(normalize_name(name))
            if new_url and new_url != url:
                candidates.append((i, name, url, new_url, "ParaTV"))
                continue
            # Fallback : cherche dans sources externes
            for label, (chans, _) in external_data.items():
                for nm, u, gp, tid, blk in chans:
                    if normalize_name(nm) == normalize_name(name) and u != url:
                        candidates.append((i, name, url, u, label))
                        break
                else:
                    continue
                break

        candidate_alives = await asyncio.gather(
            *(is_alive_smart(session, sem, c[3]) for c in candidates),
            return_exceptions=True
        )
        n_replaced = 0
        replaced_indices = set()
        for (i, name, url, new_url, src), alive in zip(candidates, candidate_alives):
            if alive is True and i not in replaced_indices:
                blocks[i] = blocks[i].replace(url, new_url)
                n_replaced += 1
                replaced_indices.add(i)
                print(f"  OK [{src}] {name[:30]:30} -> {new_url[-45:]}")

        # === Suppression des URLs mortes sans replacement (= Live Canal aab1 morts) ===
        # On supprime UNIQUEMENT les chaines du groupe "Live Canal" mortes (= aab1.top
        # qui sont en train de mourir massivement). Les autres groupes : on garde le
        # bloc en cas de revenir-vivant futur.
        to_remove_indices = set()
        for i, name, url in dead_entries:
            if i in replaced_indices:
                continue
            block = blocks[i]
            # Test si c'est dans "Live Canal" et URL aab1.top morte
            if 'group-title="Live Canal' in block:   # « Live Canal » ou « Live Canal - ... » (Phase 6)
                # Toute URL morte du groupe Live Canal est supprimée (pas juste aab1)
                # car ce groupe doit rester PROPRE : que des chaines qui marchent
                to_remove_indices.add(i)
                print(f"  RM {name[:30]:30} (Live Canal mort, suppr)")
            else:
                print(f"  KO {name[:30]:30} dead, no replacement found")
        if to_remove_indices:
            # Construit new_content en sautant les blocs supprimés
            kept_blocks = [b for i, b in enumerate(blocks) if i not in to_remove_indices]
            new_content = ''.join(kept_blocks)
            print(f"\n=== {len(to_remove_indices)} blocs mortes supprimes (Live Canal aab1) ===")
        else:
            new_content = ''.join(blocks)

        # === Phase 3 : AUTO-ADD nouvelles chaînes ParaTV ===
        our_names_norm = set()
        our_urls = set()
        for b in re.split(r'(?=#EXTINF)', new_content):
            if not b.startswith('#EXTINF'):
                continue
            name_raw = extract_channel_name(b)
            url_m = re.search(r'\n(https?://\S+)', b)
            if name_raw:
                our_names_norm.add(normalize_name(name_raw))
            if url_m:
                our_urls.add(url_m.group(1).strip())

        new_paratv = [(n,u,g,b) for (n,u,g,b) in paratv_full
                      if normalize_name(n) not in our_names_norm]

        n_added = 0
        added_blocks = []
        if new_paratv:
            print(f"\n=== {len(new_paratv)} chaines ParaTV pas chez nous — check vivantes ===")
            new_alives = await asyncio.gather(
                *(is_alive_smart(session, sem, c[1]) for c in new_paratv),
                return_exceptions=True
            )
            alive_new = [c for c, a in zip(new_paratv, new_alives) if a is True]
            print(f"  -> {len(alive_new)}/{len(new_paratv)} vivantes")
            if alive_new:
                added_blocks.append("\n# === Nouveautes ParaTV (auto-ajoute) ===\n")
                for (name, url, group, full_block) in alive_new:
                    modified = full_block.rstrip()
                    if 'group-title=' in modified:
                        modified = re.sub(r'group-title="[^"]*"',
                                          'group-title="Nouveautes ParaTV"', modified)
                    else:
                        modified = re.sub(r'#EXTINF:([-\d.]+)',
                                          r'#EXTINF:\1 group-title="Nouveautes ParaTV"', modified, count=1)
                    added_blocks.append(modified + "\n")
                    print(f"  + {name[:50]}")
                    n_added += 1
                # Recompute our_urls/names with new ParaTV adds
                for (name, url, group, full_block) in alive_new:
                    our_names_norm.add(normalize_name(name))
                    our_urls.add(url)

        # === Phase 4 NEW : multi-sources FR ===
        print(f"\n=== Phase 4 : multi-sources FR (backups + ajouts manquants) ===")

        # 4a. "France TV backup" : URLs alternatives pour chaînes FR canoniques qu'on a déjà
        backup_entries = []  # (name, url, source_label)
        n_backup_added = 0
        for label, (chans, src_is_fr) in external_data.items():
            for nm, u, gp, tid, blk in chans:
                if not is_french_content(nm, gp, tid, source_is_fr=src_is_fr):
                    continue
                if u in our_urls:
                    continue
                # Si on a cette chaîne déjà, on l'ajoute en backup
                if normalize_name(nm) in our_names_norm:
                    backup_entries.append((nm, u, label, tid))

        if backup_entries:
            # Check vivants
            print(f"  4a. {len(backup_entries)} URLs backup candidates — check vivantes")
            bk_alives = await asyncio.gather(
                *(is_alive_smart(session, sem, e[1]) for e in backup_entries),
                return_exceptions=True
            )
            alive_bk = [e for e, a in zip(backup_entries, bk_alives) if a is True]
            print(f"      -> {len(alive_bk)}/{len(backup_entries)} vivantes")
            if alive_bk:
                added_blocks.append("\n# === France TV backup (URLs alternatives — auto-multi-sources) ===\n")
                seen_pair = set()
                for nm, u, label, tid in alive_bk:
                    # Dedup par (nom normalisé, URL)
                    key = (normalize_name(nm), u)
                    if key in seen_pair:
                        continue
                    seen_pair.add(key)
                    pretty = f"{nm} [{label}]"
                    block = build_m3u_block(pretty, u, "France TV backup", tid)
                    added_blocks.append(block + "\n")
                    our_urls.add(u)
                    n_backup_added += 1
                print(f"      + {n_backup_added} ajoutées dans France TV backup")

        # 4b. Ajouts chaînes FR manquantes — par catégorie normalisée
        missing_by_cat = {}  # categorie_normalisée -> list (name, url, label, tvg_id)
        for label, (chans, src_is_fr) in external_data.items():
            for nm, u, gp, tid, blk in chans:
                if not is_french_content(nm, gp, tid, source_is_fr=src_is_fr):
                    continue
                if u in our_urls:
                    continue
                if normalize_name(nm) in our_names_norm:
                    continue  # déjà traité en 4a
                cat = normalize_category(gp)
                missing_by_cat.setdefault(cat, []).append((nm, u, label, tid))

        total_missing = sum(len(v) for v in missing_by_cat.values())
        print(f"  4b. {total_missing} chaines FR manquantes dans {len(missing_by_cat)} categories")

        n_new_added = 0
        if missing_by_cat:
            # Dedup par nom normalisé + URL (= certaines chaînes peuvent être dans plusieurs sources)
            global_seen = set()
            for cat in sorted(missing_by_cat.keys()):
                chans = missing_by_cat[cat]
                # Check alive en batch
                cat_alives = await asyncio.gather(
                    *(is_alive_smart(session, sem, c[1]) for c in chans),
                    return_exceptions=True
                )
                alive_in_cat = [c for c, a in zip(chans, cat_alives) if a is True]
                if not alive_in_cat:
                    continue
                added_blocks.append(f"\n# === Ajouts FR multi-sources : {cat} ===\n")
                cat_count = 0
                for nm, u, label, tid in alive_in_cat:
                    key = (normalize_name(nm), u)
                    if key in global_seen:
                        continue
                    global_seen.add(key)
                    block = build_m3u_block(nm, u, cat, tid)
                    added_blocks.append(block + "\n")
                    our_urls.add(u)
                    cat_count += 1
                    n_new_added += 1
                print(f"      + {cat:25s}: {cat_count} chaines")

        # Append all additions
        if added_blocks:
            new_content = new_content.rstrip() + "\n" + "".join(added_blocks)

    # === Stats finales ===
    print(f"\n=== Stats ===")
    print(f"Checked: {len(entries)}  Alive: {n_alive}  Dead: {len(dead_entries)}  Replaced: {n_replaced}")
    print(f"ParaTV ajoutes: {n_added}  | FR backup: {n_backup_added}  | FR nouveaux: {n_new_added}")

    # === Phase 6 : dossiers d'affichage Mix FR (noms canoniques, plus de « Programmes ») ===
    new_content = appliquer_dossiers_affichage(new_content)

    # === Phase 5 : dossiers Mix FR (mixfr-dossiers.txt), re-importes tels quels ===
    try:
        new_content = await importer_dossiers_m3u(new_content, section_dossiers)
    except Exception as e:
        print(f"  Phase 5 KO ({e}) -> ancienne section conservee")
        if section_dossiers:
            new_content = new_content.rstrip() + "\n\n" + section_dossiers + "\n"

    if new_content == content:
        print("\n-> No change, exiting.")
        return

    with open(LOCAL_M3U, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f"\n-> {LOCAL_M3U} updated")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
