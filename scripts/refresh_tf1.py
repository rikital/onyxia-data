#!/usr/bin/env python3
"""refresh_tf1.py — génère data-replay-tf1.m3u (TF1+ live + replay catalog).

Scraping JSON-LD ItemList sur tf1.fr/{chan}/replay + programmes-tv/{cat}.
SKIP /a-la-carte (= 100% payant : Sans pub 0,69€, Avant-premières, VOD Cinéma).
"""
import json, os, re, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from utils import http_get_tf1, slug_to_title

MAX_ITEMS_PER_CHAN = 999

TF1_CHANNELS = [
    ("tf1",              "TF1",              "https://i.imgur.com/qkOSt0o.png"),
    ("tmc",              "TMC",              "https://i.imgur.com/RY3iEMb.png"),
    ("tfx",              "TFX",              "https://i.imgur.com/JJVZJqL.png"),
    ("tf1-series-films", "TF1 Séries Films", "https://i.imgur.com/3OZdMb9.png"),
    ("lci",              "LCI",              "https://i.imgur.com/jVxzNHL.png"),
]
TF1_CATEGORIES = [
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

# 2026-09-14 (user « tu peux les mettre dans films » pour les téléfilms) :
#   toutes les catégories NON-fiction passent par le scrape par rails et
#   atterrissent sous "Replay TF1+ Émissions" (3e axe du dossier Replay TF1+).
#   Avant, elles étaient versées en vrac dans "Replay <chaîne>" et leurs
#   catégories étaient donc invisibles côté app.
TF1_EMISSIONS = (
    "divertissement", "jeunesse", "reportages", "sport",
    "info", "people-43944072", "podcasts-70045207", "impact",
)

TF1_REPLAY_URL = "https://www.tf1.fr/{slug}/replay"
TF1_CAT_HREF_RE = re.compile(r'href="/(tf1|tmc|tfx|tf1-series-films|lci)/([a-z0-9-]+)"')

# 2026-06-23 : scrape SEULEMENT les FAST channels (= L_FAST_v2l-ad-*-NNNNNNNN).
# Les chaînes FIXES (TF1/TMC/TFX/TF1 Séries Films/LCI + ARTE/L'Equipe/LCP/
# Le Figaro/Paris Première/Red Bull TV) sont gérées en hardcode dans
# LiveTvHubProvider.kt côté app (= stables, jamais retirées, pas besoin de
# scrape). Seules les FAST (replay 24/7) changent → on les scrape ici.
TF1_LIVE_DIRECT_URL = "https://www.tf1.fr/chaines-tv/direct"
TF1_LOGO = "https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/france/tf1-fr.png"


def _fast_to_human(fid):
    """L_FAST_v2l-ad-demain-nous-appartient-38296145 → 'Demain Nous Appartient'."""
    slug = fid.replace("L_FAST_v2l-ad-", "")
    slug = re.sub(r'-\d+$', '', slug)
    return slug.replace("-", " ").replace("_", " ").title().replace("And", "&").strip()


def scrape_tf1_fast_channels():
    """Scrape tf1.fr/chaines-tv/direct → liste des IDs L_FAST_*."""
    try:
        html = http_get_tf1(TF1_LIVE_DIRECT_URL)
    except Exception as e:
        print(f"[!] TF1 FAST scrape error: {e}", file=sys.stderr)
        return []
    fast_ids = sorted(set(re.findall(r'L_FAST_v2l-ad-[a-z0-9\-]+-\d+', html)))
    return [(fid, _fast_to_human(fid)) for fid in fast_ids]


def tf1plus_channel_programs(channel_slug, max_items=MAX_ITEMS_PER_CHAN):
    """Scrape JSON-LD ItemList sur la page replay TF1+."""
    try:
        raw = http_get_tf1(TF1_REPLAY_URL.format(slug=channel_slug))
    except Exception as e:
        print(f"[!] TF1+ fetch error {channel_slug}: {e}", file=sys.stderr)
        return []
    blocks = re.findall(r'<script type="application/ld\+json"[^>]*>([^<]+)</script>', raw, re.DOTALL)
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
            item_type = item.get("@type", "")
            # 2026-06-23 : Movies/TvSeries/TvSeason → ces items viennent maintenant
            # via le scrape par SECTIONS thématiques (phase 2 tf1_category_sections
            # sur /programmes-tv/films + /series). On les skip ici pour éviter
            # les doublons "Replay TF1 - Films" + "Replay TF1+ Films - Top 10".
            if item_type in ("Movie", "TVSeries", "TVSeason", "TvSeries", "TvSeason"):
                continue
            out.append({"tf1_type": item_type, "si_id": path,
                        "title": name.strip()[:140], "logo": image})
    return out


def tf1_category_sections(category_slug, max_items=MAX_ITEMS_PER_CHAN):
    """Scrape /programmes-tv/<cat> en PRÉSERVANT les sections thématiques
    (= les rails "Top 10", "Les films populaires en ce moment", "Action",
    "Comédies", etc. tels qu'affichés sur le site).
    Retourne [(section_name, [{si_id, title}]), ...] dans l'ordre du site.
    """
    try:
        raw = http_get_tf1(f"https://www.tf1.fr/programmes-tv/{category_slug}")
    except Exception as e:
        print(f"[!] TF1 cat sections fetch error {category_slug}: {e}", file=sys.stderr)
        return []
    sec_re = re.compile(r'<h2 class="headline-5[^"]*"[^>]*>([^<]+)</h2>')
    sections_pos = [(m.start(), m.group(1)) for m in sec_re.finditer(raw)]
    def decode_sec(s):
        return (s.replace('&#x27;', "'").replace('&apos;', "'")
                 .replace('&amp;', '&').replace('&#x2F;', '/').strip())
    sections_pos = [(p, decode_sec(n)) for p, n in sections_pos]
    SKIP_SECTIONS = {"Tout l'univers", "Tous les films avec", "Sagas à prix doux"}

    # 2026-09-11 (user : « fais en sorte qu'on n'ait pas de doublons ») —
    #   TF1+ ne classe pas ses films par genre mais par rayons éditoriaux qui se
    #   recoupent : mesuré sur la page live, 340 liens pour 159 films distincts,
    #   Wonder Woman présente 10 fois, Spider-Man Homecoming 10 fois. On dédoublonne
    #   donc d'une section à l'autre : chaque film n'apparaît qu'une seule fois.
    #   On ne JETTE aucun rayon : les rayons de genre passent simplement EN
    #   PREMIER, donc un film y est rangé en priorité, et les rayons marketing
    #   (Top 10, Les films populaires en ce moment, Vos séances ciné à prix mini,
    #   Derniers jours pour voir, Cinéma XXL prix XXS, Entre potes, Entre filles,
    #   Ados, Casting de stars…) ne gardent que ce qu'aucun genre n'a pris. Un
    #   film qui ne vit QUE dans un rayon marketing reste donc au catalogue.
    GENRES_FILMS = {
        "Action", "Comédies", "Aventure", "Thrillers", "Drames", "Romance",
        "Science-fiction", "Policiers", "Cinéma français", "Les courts-métrages",
        "Des films pour toute la famille",
    }
    prioritaires = GENRES_FILMS if category_slug == "films" else set()
    href_re = re.compile(r'href="/(tf1|tmc|tfx|tf1-series-films|lci)/([a-z0-9-]+)"')
    # 2026-06-23 : extract poster from <picture><source srcSet="..."> near each
    # <article> card → utilisé comme jaquette dans l'app au lieu du logo chaîne.
    poster_re = re.compile(r'srcSet="(https://photos\.tf1\.fr/[^"]+?\.(?:avif|webp|jpg))')
    excluded = {"replay", "direct", "news", "videos", "programmes-tv", "a-la-carte"}

    # Bornes de chaque rayon, puis passage des rayons de genre en tête. L'ordre
    #   d'affichage dans l'app reste celui du site (cf. tri côté Kotlin), seul
    #   l'ordre de REMPLISSAGE change.
    bornes = []
    for i, (start, name) in enumerate(sections_pos):
        if name in SKIP_SECTIONS:
            continue
        end = sections_pos[i+1][0] if i + 1 < len(sections_pos) else len(raw)
        bornes.append((name, start, end))
    bornes.sort(key=lambda b: 0 if b[0] in prioritaires else 1)

    out = []
    vus_global = set()   # un film ne sort qu'une fois, toutes sections confondues
    for name, start, end in bornes:
        chunk = raw[start:end]
        seen = vus_global
        items = []
        # Split par <article : chaque card a 1 poster + 1 href
        for part in chunk.split('<article')[1:]:
            href_m = href_re.search(part)
            if not href_m:
                continue
            chan, slug = href_m.group(1), href_m.group(2)
            if slug in excluded:
                continue
            si_id = f"{chan}/{slug}"
            if si_id in seen:
                continue
            seen.add(si_id)
            poster_m = poster_re.search(part)
            poster = poster_m.group(1) if poster_m else ""
            title = slug_to_title(slug)[:140]
            items.append({"si_id": si_id, "title": title, "logo": poster})
            if len(items) >= max_items:
                break
        if items:
            out.append((name, items))
    return out


def tf1_category_programs(category_slug, max_items=MAX_ITEMS_PER_CHAN):
    """Scrape /programmes-tv/<cat>. Format : hrefs /(chan)/(slug)."""
    try:
        raw = http_get_tf1(f"https://www.tf1.fr/programmes-tv/{category_slug}")
    except Exception as e:
        print(f"[!] TF1 cat fetch error {category_slug}: {e}", file=sys.stderr)
        return []
    out = []
    seen = set()
    excluded = {"replay", "direct", "news", "videos", "programmes-tv", "a-la-carte"}
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


def generate(output_path):
    lines = ["#EXTM3U"]
    total = 0

    print("\n=== Live TF1+ FAST (scrapé tf1.fr/chaines-tv/direct) ===")
    fast_chans = scrape_tf1_fast_channels()
    print(f"  {len(fast_chans)} chaînes FAST trouvées")
    for fid, label in fast_chans:
        lines.append(
            f'#EXTINF:-1 tvg-id="tf1live-{fid}" '
            f'tvg-logo="{TF1_LOGO}" tvg-country="FR" '
            f'group-title="Live TF1+",{label}'
        )
        lines.append(f'tf1live://{fid}')
        total += 1

    print("\n=== TF1+ Replay (5 chaînes JSON-LD) ===")
    for chan_slug, chan_label, chan_logo in TF1_CHANNELS:
        progs = tf1plus_channel_programs(chan_slug)
        print(f"  {chan_label}: {len(progs)} programmes")
        for p in progs:
            ilogo = p.get("logo") or chan_logo
            si_path = (p.get("si_id") or "").lower()
            tf1_type = (p.get("tf1_type") or "").lower()
            tvg_type = (
                "series" if tf1_type in ("tvseries", "tvseason") else
                "movie" if tf1_type == "movie" else
                ("movie" if (chan_slug == "tf1seriesfilms" or "film" in si_path or "/cinema/" in si_path) else "series")
            )
            lines.append(
                f'#EXTINF:-1 tvg-id="tf1plus-{p["si_id"]}" '
                f'tvg-logo="{ilogo}" tvg-country="FR" '
                f'tvg-type="{tvg_type}" '
                f'group-title="Replay {chan_label}",{p["title"]}'
            )
            lines.append(f'tf1plus://{p["si_id"]}')
            total += 1
        time.sleep(0.5)

    print("\n=== TF1+ /programmes-tv/<cat> (11 catégories) ===")
    tf1_cat_seen = set()
    for chan_slug, _, _ in TF1_CHANNELS:
        for p in tf1plus_channel_programs(chan_slug):
            tf1_cat_seen.add(p["si_id"])
    chan_meta = {c[0]: (c[1], c[2]) for c in TF1_CHANNELS}
    for cat_slug, cat_label in TF1_CATEGORIES:
        # 2026-06-23 : Films et Séries → scrape par SECTIONS thématiques
        # 2026-09-14 (user « ramener les émissions dans le bon dossier, en faisant
        #   un 3e dossier avec les catégories, pour TF1 et M6 ») : divertissement passe
        #   lui aussi par le scrape par rails, sous le préfixe "Replay TF1+ Émissions"
        #   → 3e axe à côté de Films/Séries dans le dossier Replay TF1+ de l'app
        #   (cf. LiveHubFolderDialog.themedPrefix + LiveTvHubProvider).
        # telefilms rejoint l'axe Films (choix user 2026-09-14).
        if cat_slug in ("films", "series", "telefilms") or cat_slug in TF1_EMISSIONS:
            sections = tf1_category_sections(cat_slug)
            group_prefix = (
                "Replay TF1+ Films" if cat_slug in ("films", "telefilms")
                else "Replay TF1+ Séries" if cat_slug == "series"
                else "Replay TF1+ Émissions"
            )
            section_added = 0
            for sec_name, items in sections:
                for p in items:
                    chan = p["si_id"].split("/")[0]
                    meta = chan_meta.get(chan)
                    if not meta:
                        continue
                    chan_label_sec, chan_logo_sec = meta
                    # 2026-06-23 : utilise le poster du film si extracté (jaquettes), sinon logo chaîne
                    poster = p.get("logo") or chan_logo_sec
                    si_path = (p.get("si_id") or "").lower()
                    # 2026-09-14 : une émission dont le slug contient « film » ne doit
                    #   pas être typée movie → on exclut divertissement du test.
                    is_film = cat_slug in ("films", "telefilms") or (
                        cat_slug not in TF1_EMISSIONS
                        and ("film" in si_path or "/cinema/" in si_path)
                    )
                    tvg_type = "movie" if is_film else "series"
                    lines.append(
                        f'#EXTINF:-1 tvg-id="tf1plus-{p["si_id"].replace(chr(47), chr(45))}-{sec_name.replace(chr(32), chr(45)).lower()}" '
                        f'tvg-logo="{poster}" tvg-country="FR" '
                        f'tvg-type="{tvg_type}" '
                        f'group-title="{group_prefix} - {sec_name}",{p["title"]}'
                    )
                    lines.append(f'tf1plus://{p["si_id"]}')
                    section_added += 1
                    total += 1
            print(f"  {cat_label}: +{section_added} entries via {len(sections)} sections")
            time.sleep(0.3)
            continue
        progs = tf1_category_programs(cat_slug)
        added = 0
        for p in progs:
            if p["si_id"] in tf1_cat_seen:
                continue
            tf1_cat_seen.add(p["si_id"])
            chan = p["si_id"].split("/")[0]
            meta = chan_meta.get(chan)
            if not meta:
                continue
            chan_label, chan_logo = meta
            si_path = (p.get("si_id") or "").lower()
            is_film = "film" in si_path or "/cinema/" in si_path
            tvg_type = "movie" if is_film else "series"
            lines.append(
                f'#EXTINF:-1 tvg-id="tf1plus-{p["si_id"].replace(chr(47), chr(45))}" '
                f'tvg-logo="{chan_logo}" tvg-country="FR" '
                f'tvg-type="{tvg_type}" '
                f'group-title="Replay {chan_label}",{p["title"]}'
            )
            lines.append(f'tf1plus://{p["si_id"]}')
            added += 1
            total += 1
        print(f"  {cat_label}: +{added} nouveaux")
        time.sleep(0.3)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    print(f"\n[OK] Wrote {total} TF1+ programs to {output_path}")
    return total


if __name__ == "__main__":
    out = os.environ.get("OUTPUT", "data-replay-tf1.m3u")
    n = generate(out)
    if n == 0:
        sys.exit(1)
