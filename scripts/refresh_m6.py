#!/usr/bin/env python3
"""refresh_m6.py — génère data-replay-m6.m3u (M6+ replay catalog).

Endpoint pc.middleware.6play.fr (= équiv web du middleware). Pas besoin d'auth
Gigya pour le catalogue. L'auth M6 (Widevine DRM) est utilisée par l'app Onyx
au moment du PLAYBACK. Filtre subscriptions/is_subscription (M6+MAX premium).
"""
import json, os, sys, time, urllib.request, re
sys.path.insert(0, os.path.dirname(__file__))
from utils import http_get, slug_to_title

M6_BASE = "https://pc.middleware.6play.fr/6play/v2/platforms/m6group_web/services"
M6_PAGE_SIZE = 100

M6_CHANNELS = [
    ("m6replay",              "M6",              "https://i.imgur.com/4lhxLPB.png"),
    ("w9replay",              "W9",              "https://i.imgur.com/oFGn1On.png"),
    ("6terreplay",            "6ter",            "https://i.imgur.com/M7vGd6Y.png"),
    ("gulli",                 "Gulli",           "https://i.imgur.com/tFNzQQM.png"),
    ("tevareplay",            "Téva",            "https://i.imgur.com/HuLNVjC.png"),
    ("parispremierereplay",   "Paris Première",  "https://i.imgur.com/oCBzd0e.png"),
]
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

# 2026-06-23 : scrape M6+ SECTIONS THÉMATIQUES via __dehydratedState
# (= store Redux sérialisé dans le HTML de m6.fr/m6plus/<slug>-m6-f_<fid>).
# Permet d'avoir "Drame", "Comédie", "Romance", "Action", "Nouveautés", etc.
# pour Cinéma (907), Séries (8), Téléfilms (70). No token nécessaire.
M6_PLUS_HOME = "https://www.m6.fr/m6plus"
M6_PLUS_UA = "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/148 Safari/537.36"


def m6plus_fetch_state(fid, slug):
    url = f"{M6_PLUS_HOME}/{slug}-m6-f_{fid}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": M6_PLUS_UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[!] M6+ fetch error {fid}/{slug}: {e}", file=sys.stderr)
        return None
    m = re.search(r'root\.__dehydratedState\s*=\s*"((?:\\.|[^"\\])*)"', html)
    if not m:
        return None
    try:
        return json.loads(json.loads('"' + m.group(1) + '"'))
    except Exception:
        return None


def m6plus_extract_sections(fid, slug):
    """Retourne [(section_title, [{title, poster, seo, ucid}])]."""
    state = m6plus_fetch_state(fid, slug)
    if not state:
        return []
    folder = state.get("layout", {}).get("layouts", {}).get("main", {}).get("folder", {}).get(str(fid), {})
    blocks = folder.get("blocks", [])
    sections = []
    sub_folders = []
    for block in blocks:
        if block.get("type") == "parallax":
            continue
        block_title = (block.get("analytics", {}) or {}).get("tealium", {}).get("block_title") or ""
        items_raw = (block.get("content", {}) or {}).get("items", [])
        programs = []
        for item in items_raw:
            ic = item.get("itemContent") or {}
            tgt = (ic.get("action") or {}).get("target") or {}
            val = tgt.get("value_layout") or {}
            if not isinstance(val, dict):
                continue
            if val.get("type") == "program":
                title = ic.get("title") or (val.get("seo") or "").replace("-", " ").title()
                poster = (ic.get("image") or {}).get("src", "")
                seo = val.get("seo") or ""
                ucid = item.get("ucid") or ""
                programs.append({"title": title[:140], "poster": poster, "seo": seo, "ucid": ucid})
            elif val.get("type") == "folder":
                sub_fid = val.get("id")
                sub_seo = val.get("seo") or ""
                if sub_fid and not re.match(r"^top-\d+-", sub_seo):
                    sub_folders.append((sub_fid, sub_seo))
        if programs and block_title:
            sections.append((block_title, programs))
    seen = set()
    for sub_fid, sub_seo in sub_folders:
        if sub_fid in seen:
            continue
        seen.add(sub_fid)
        time.sleep(0.3)
        sub_state = m6plus_fetch_state(sub_fid, sub_seo)
        if not sub_state:
            continue
        sub_folder = sub_state.get("layout", {}).get("layouts", {}).get("main", {}).get("folder", {}).get(str(sub_fid), {})
        sub_blocks = sub_folder.get("blocks", [])
        sub_name = (sub_seo.replace("-cinema-m6", "").replace("-series-m6", "")
                          .replace("-telefilms-m6", "").replace("-", " ").title())
        all_progs = []
        for sb in sub_blocks:
            if sb.get("type") == "parallax":
                continue
            for it in (sb.get("content") or {}).get("items", []):
                ic2 = it.get("itemContent") or {}
                tgt2 = (ic2.get("action") or {}).get("target") or {}
                val2 = tgt2.get("value_layout") or {}
                if isinstance(val2, dict) and val2.get("type") == "program":
                    title2 = ic2.get("title") or (val2.get("seo") or "").replace("-", " ").title()
                    poster2 = (ic2.get("image") or {}).get("src", "")
                    seo2 = val2.get("seo") or ""
                    ucid2 = it.get("ucid") or ""
                    all_progs.append({"title": title2[:140], "poster": poster2, "seo": seo2, "ucid": ucid2})
        if all_progs:
            sections.append((sub_name, all_progs))
    return sections


M6_PLUS_SECTION_FOLDERS = [
    (907, "cinema",    "Films",     "movie"),
    (8,   "series",    "Séries",    "series"),
    (70,  "telefilms", "Téléfilms", "movie"),
]


def m6_channel_programs(service_id, max_items=2000):
    """Pagination /services/{svc}/programs?limit=100&offset=N."""
    out = []
    offset = 0
    while offset < max_items:
        url = f"{M6_BASE}/{service_id}/programs?limit={M6_PAGE_SIZE}&offset={offset}&csa=6"
        try:
            arr = json.loads(http_get(url, headers={"Accept": "application/json"}))
        except Exception as e:
            print(f"[!] M6 chan {service_id} offset={offset}: {e}", file=sys.stderr)
            break
        if not isinstance(arr, list) or len(arr) == 0:
            break
        for p in arr:
            pid = p.get("id")
            if not pid:
                continue
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
            out.append({"program_id": pid, "title": title[:140], "image": img,
                        "service": service_id, "tvg_type": tvg_type})
        if len(arr) < M6_PAGE_SIZE:
            break
        offset += M6_PAGE_SIZE
    return out


def m6_folder_programs(folder_id, folder_label, max_items=2000):
    out = []
    offset = 0
    while offset < max_items:
        url = (f"{M6_BASE}/m6replay/folders/{folder_id}/programs"
               f"?limit={M6_PAGE_SIZE}&offset={offset}&csa=6")
        try:
            arr = json.loads(http_get(url, headers={"Accept": "application/json"}))
        except Exception as e:
            print(f"[!] M6 folder {folder_id}: {e}", file=sys.stderr)
            break
        if not isinstance(arr, list) or len(arr) == 0:
            break
        for p in arr:
            pid = p.get("id")
            if not pid:
                continue
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
            out.append({"program_id": pid, "title": title[:140], "image": img,
                        "service": "m6replay", "tvg_type": tvg_type,
                        "folder_label": folder_label})
        if len(arr) < M6_PAGE_SIZE:
            break
        offset += M6_PAGE_SIZE
    return out


def generate(output_path):
    lines = ["#EXTM3U"]
    total = 0

    print("\n=== M6+ Replay (6 chaînes) ===")
    for service_id, chan_label, chan_logo in M6_CHANNELS:
        progs = m6_channel_programs(service_id)
        print(f"  {chan_label}: {len(progs)} programmes")
        for p in progs:
            ilogo = p.get("image") or chan_logo
            if ilogo and not ilogo.startswith("http"):
                ilogo = f"https://images.6play.fr/v1/images/{ilogo}/raw"
            lines.append(
                f'#EXTINF:-1 tvg-id="m6plus-{p["program_id"]}" '
                f'tvg-logo="{ilogo}" tvg-country="FR" '
                f'tvg-type="{p.get("tvg_type", "series")}" '
                f'group-title="Replay {chan_label}",{p["title"]}'
            )
            lines.append(f'm6play://{service_id}/{p["program_id"]}')
            total += 1
        time.sleep(0.4)

    print("\n=== M6+ Sections thématiques via __dehydratedState ===")
    for sec_fid, sec_slug, kind, tvg_type in M6_PLUS_SECTION_FOLDERS:
        secs = m6plus_extract_sections(sec_fid, sec_slug)
        n_films_in_sections = 0
        for sec_name, progs in secs:
            for p in progs:
                logo = p.get("poster") or M6_LOGO_GENERIC
                sec_slug_kebab = re.sub(r"\s+", "-", sec_name.strip()).lower()
                ident = p.get("ucid") or p.get("seo") or ""
                lines.append(
                    f'#EXTINF:-1 tvg-id="m6plus-{ident}-{sec_slug_kebab}" '
                    f'tvg-logo="{logo}" tvg-country="FR" '
                    f'tvg-type="{tvg_type}" '
                    f'group-title="Replay M6+ {kind} - {sec_name}",{p["title"]}'
                )
                lines.append(f'm6play://{p.get("seo") or p.get("ucid")}')
                n_films_in_sections += 1
                total += 1
        print(f"  {kind} ({sec_fid}/{sec_slug}): {len(secs)} sections, {n_films_in_sections} entries")
        time.sleep(0.3)

    print("\n=== M6+ Thématiques transverses (9 catégories) ===")
    m6_themes_seen = set(); m6_service_par_pid = {}  # 2026-09-16 : pid -> service reel
    for line in lines:
        if line.startswith('m6play://'):
            try:
                m6_themes_seen.add(line.split('/')[-1]); m6_service_par_pid[line.split('/')[-1]] = (line[len('m6play://'):].split('/')[0] if line.count('/') >= 3 else 'm6replay')
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
            lines.append(
                f'#EXTINF:-1 tvg-id="m6plus-{pid}" '
                f'tvg-logo="{ilogo}" tvg-country="FR" '
                f'tvg-type="{p.get("tvg_type", "series")}" '
                f'group-title="{group}",{p["title"]}'
            )
            lines.append(f"m6play://{m6_service_par_pid.get(str(pid), 'm6replay')}/{pid}")  # 2026-09-16 : service reel, plus m6replay en dur
            total += 1
            added += 1
        print(f"  Théma {folder_label}: {len(progs)} → {added} nouveaux (dedup)")
        time.sleep(0.3)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    print(f"\n[OK] Wrote {total} M6+ programs to {output_path}")
    return total


if __name__ == "__main__":
    out = os.environ.get("OUTPUT", "data-replay-m6.m3u")
    n = generate(out)
    if n == 0:
        sys.exit(1)
