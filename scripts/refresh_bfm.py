#!/usr/bin/env python3
"""refresh_bfm.py — génère data-replay-bfm.m3u (BFM/RMC Play replay catalog).

Endpoints publics ws-cdn.tv.sfr.net/gaia-core (sans token nécessaire pour le
catalogue ; le token BFM SSO est utilisé UNIQUEMENT côté app Onyx pour le
playback Widevine). Filtre svodId pour exclure le contenu premium SVOD.
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from utils import http_get

BFM_CDN = "https://ws-cdn.tv.sfr.net/gaia-core/rest/api/web/v1"
BFM_CDN_V2 = "https://ws-cdn.tv.sfr.net/gaia-core/rest/api/web/v2"
BFM_PARAMS = "app=bfmrmc&device=browser&operators=NEXTTV"
BFM_LOGO_BASE = "https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/france"

BFM_LIVE_CHANNELS = [
    ("bfmtv",         "BFM TV",          f"{BFM_LOGO_BASE}/bfm-tv-fr.png"),
    ("rmcstory",      "RMC Story",       f"{BFM_LOGO_BASE}/rmc-story-fr.png"),
    ("rmcdecouverte", "RMC Découverte",  f"{BFM_LOGO_BASE}/rmc-decouverte-fr.png"),
    ("bfmbusiness",   "BFM Business",    f"{BFM_LOGO_BASE}/bfm-business-fr.png"),
    ("rmclife",       "RMC Life",        f"{BFM_LOGO_BASE}/bfm-tv-fr.png"),
    ("techco",        "Tech & Co",       f"{BFM_LOGO_BASE}/bfm-tv-fr.png"),
    # 2026-09-05 : RMC BFM Play est devenu RMC+ (www.rmcplus.fr). Les clés ci-dessus sont
    # traduites par l'app (BfmResolver.CHAINES) ; celles ci-dessous sont DIRECTEMENT les ids
    # RMC+ (page_id de /api/bff/v1/page?page_type=direct), relevés sur l'accueil du site.
    ("rmc",                     "RMC (Intégrale Sport)",     f"{BFM_LOGO_BASE}/rmc-story-fr.png"),
    ("bfm_2",                   "BFM2",                      f"{BFM_LOGO_BASE}/bfm-tv-fr.png"),
    ("brut",                    "Brut",                      f"{BFM_LOGO_BASE}/bfm-tv-fr.png"),
    ("after_foot_tv",           "L'After Foot TV",           f"{BFM_LOGO_BASE}/rmc-story-fr.png"),
    ("bfm_grands_reportages",   "BFM Grands Reportages",     f"{BFM_LOGO_BASE}/bfm-tv-fr.png"),
    ("j_irai_dormir_chez_vous", "J'irai dormir chez vous",   f"{BFM_LOGO_BASE}/rmc-decouverte-fr.png"),
    ("rmc_mystere",             "RMC Mystère",               f"{BFM_LOGO_BASE}/rmc-story-fr.png"),
    ("rmc_mecanic",             "RMC Mécanic",               f"{BFM_LOGO_BASE}/rmc-decouverte-fr.png"),
    ("rmc_wow",                 "RMC Wow",                   f"{BFM_LOGO_BASE}/rmc-decouverte-fr.png"),
    ("rmc_alerte_secours",      "RMC Alerte Secours",        f"{BFM_LOGO_BASE}/rmc-decouverte-fr.png"),
]
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
BFM_BROKEN_PREFIXES = [
    "NEUF_CINE_PLUS_OCS", "NEUF_01NET", "NEUF_LEQUIPETV",
    "NEUF_VIRGIN17", "NEUF_UNIVERSAL", "NEUF_KITCHEN_MANIA",
    "NEUF_USHUAIA", "NEUF_FILMDAFRIQUE",
]
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


def bfm_channel_programs(menu_id, max_items=500):
    """Scrape un menu BFM (= chaîne ou sous-collection) : structure + tous spots."""
    menu_url = f"{BFM_CDN}/menu/RefMenuItem::{menu_id}/structure?{BFM_PARAMS}"
    try:
        menu = json.loads(http_get(menu_url, headers={"Accept": "application/json"}))
    except Exception as e:
        print(f"[!] BFM menu error {menu_id}: {e}", file=sys.stderr)
        return []
    out = []
    seen = set()
    for spot in menu.get("spots", []):
        spot_id = spot.get("id", "")
        spot_title = (spot.get("title") or "").strip()
        if not spot_id:
            continue
        spot_url = f"{BFM_CDN_V2}/spot/{spot_id}/content?{BFM_PARAMS}"
        try:
            sdata = json.loads(http_get(spot_url, headers={"Accept": "application/json"}))
        except Exception:
            continue
        spot_count = 0
        for tile in sdata.get("tiles", []):
            pid_raw = tile.get("productId") or ""
            if not pid_raw:
                pid_raw = (tile.get("action") or {}).get("actionIds", {}).get("contentId", "")
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
                url = img.get("url", "")
                wt = img.get("withTitle", False)
                if fmt in ("2/3", "16/9") and not wt and url:
                    image = url
                    break
                if not image and url and not wt:
                    image = url
            ct = tile.get("contentType", "")
            tvg_type = "series" if ct in ("Season", "Series", "Episode") else "movie"
            out.append({"product_id": pid, "title": title[:140], "image": image,
                        "tvg_type": tvg_type, "category": spot_title})
            spot_count += 1
        if spot_count:
            print(f"    [{menu_id}] {spot_title}: {spot_count}", file=sys.stderr)
        if len(out) >= max_items:
            break
        time.sleep(0.15)
    return out[:max_items]


def bfm_fetch_episodes(content_id):
    """Récupère les épisodes individuels d'une saison BFM. Filtre svodId."""
    url = (f"{BFM_CDN}/content/Product::{content_id}/episodes"
           f"?universe=PROVIDER&accountTypes=NEXTTV&operators=NEXTTV"
           f"&noTracking=false&page=0&size=1000")
    try:
        data = json.loads(http_get(url, headers={"Accept": "application/json"}))
    except Exception:
        return []
    out = []
    for ep in data.get("content", []):
        if ep.get("svodId"):  # filtre premium SVOD
            continue
        ep_pid_raw = (ep.get("action") or {}).get("actionIds", {}).get("contentId", "")
        if not ep_pid_raw:
            ep_pid_raw = ep.get("id", "") or ep.get("productId", "")
        ep_pid = ep_pid_raw.replace("Product::", "")
        if not ep_pid or any(ep_pid.startswith(pfx) for pfx in BFM_BROKEN_PREFIXES):
            continue
        title = (ep.get("title") or "").strip()
        if not title:
            continue
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
        out.append({"product_id": ep_pid, "title": title[:140], "image": image, "tvg_type": tvg_type})
    return out


def bfm_theme_programs(theme_id, theme_label, max_items=500):
    """Scrape une thématique transverse via /tile/RefTile::xxx/content?size=200."""
    url = f"{BFM_CDN}/tile/RefTile::{theme_id}/content?{BFM_PARAMS}&page=0&size=200"
    try:
        data = json.loads(http_get(url, headers={"Accept": "application/json"}))
    except Exception as e:
        print(f"[!] BFM theme error {theme_label}: {e}", file=sys.stderr)
        return []
    out = []
    seen = set()
    for tile in data.get("items", []):
        if tile.get("svodId"):
            continue
        pid_raw = tile.get("productId") or ""
        if not pid_raw:
            pid_raw = (tile.get("action") or {}).get("actionIds", {}).get("contentId", "")
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
        out.append({"product_id": pid, "title": title[:140], "image": image,
                    "tvg_type": tvg_type, "theme_label": theme_label})
        if len(out) >= max_items:
            break
    return out[:max_items]


def generate(output_path):
    lines = ["#EXTM3U"]
    total = 0
    bfm_global_seen = set()

    print("\n=== BFM / RMC Play Live ===")
    for chan_key, chan_label, chan_logo in BFM_LIVE_CHANNELS:
        lines.append(
            f'#EXTINF:-1 tvg-id="bfmlive-{chan_key}" '
            f'tvg-logo="{chan_logo}" tvg-country="FR" '
            f'group-title="Live BFM Play",{chan_label} Direct'
        )
        lines.append(f'bfmlive://{chan_key}')
        total += 1
    print(f"  {len(BFM_LIVE_CHANNELS)} chaînes live")

    print("\n=== BFM / RMC Play Replay (chaînes + épisodes) ===")
    for menu_id, _, chan_label, chan_logo in BFM_REPLAY_CHANNELS:
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
            episodes = bfm_fetch_episodes(season_pid)
            if episodes:
                for ep in episodes:
                    ep_pid = ep["product_id"]
                    if ep_pid in bfm_global_seen:
                        continue
                    bfm_global_seen.add(ep_pid)
                    ep_logo = ep.get("image") or ilogo_season
                    ep_title = f'{p["title"]} - {ep["title"]}' if ep["title"] != p["title"] else p["title"]
                    lines.append(
                        f'#EXTINF:-1 tvg-id="bfmplay-{ep_pid}" '
                        f'tvg-logo="{ep_logo}" tvg-country="FR" '
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
                    f'tvg-logo="{ilogo_season}" tvg-country="FR" '
                    f'tvg-type="{p.get("tvg_type", "series")}" '
                    f'group-title="{group}",{p["title"]}'
                )
                lines.append(f'bfmplay://{season_pid}')
                total += 1
                added_seasons += 1
        print(f"  {chan_label}: {len(progs)} saisons, {added_episodes} épisodes + {added_seasons} saisons fallback")
        time.sleep(0.4)

    print("\n=== BFM/RMC Thématiques transverses (14 thématiques) ===")
    bfm_themes_seen = set()
    for slug, theme_id, theme_label in BFM_THEMES:
        progs = bfm_theme_programs(theme_id, theme_label)
        added_episodes = 0
        added_seasons = 0
        group = f"Thématique BFM Play - {theme_label}"
        for p in progs:
            season_pid = p["product_id"]
            if season_pid in bfm_global_seen or season_pid in bfm_themes_seen:
                continue
            bfm_themes_seen.add(season_pid)
            ilogo_season = p.get("image") or f"{BFM_LOGO_BASE}/bfm-tv-fr.png"
            episodes = bfm_fetch_episodes(season_pid)
            if episodes:
                for ep in episodes:
                    ep_pid = ep["product_id"]
                    if ep_pid in bfm_global_seen or ep_pid in bfm_themes_seen:
                        continue
                    bfm_themes_seen.add(ep_pid)
                    ep_logo = ep.get("image") or ilogo_season
                    ep_title = f'{p["title"]} - {ep["title"]}' if ep["title"] != p["title"] else p["title"]
                    lines.append(
                        f'#EXTINF:-1 tvg-id="bfmplay-{ep_pid}" '
                        f'tvg-logo="{ep_logo}" tvg-country="FR" '
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
                    f'tvg-logo="{ilogo_season}" tvg-country="FR" '
                    f'tvg-type="{p.get("tvg_type", "series")}" '
                    f'group-title="{group}",{p["title"]}'
                )
                lines.append(f'bfmplay://{season_pid}')
                total += 1
                added_seasons += 1
        print(f"  Théma {theme_label}: {len(progs)} → {added_episodes} épisodes + {added_seasons} saisons")
        time.sleep(0.3)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    print(f"\n[OK] Wrote {total} BFM/RMC programs to {output_path}")
    return total


if __name__ == "__main__":
    out = os.environ.get("OUTPUT", "data-replay-bfm.m3u")
    n = generate(out)
    if n == 0:
        sys.exit(1)
