#!/usr/bin/env python3
"""refresh_francetv.py — génère data-replay-francetv.m3u.

API publique api-mobile.yatta.francetv.fr/apps/. Pas de payant côté France.tv
(= 100% gratuit).

Deux types d'entrées :
  - VIDÉO directe : francetv://<si_id>            (= film / doc / épisode unique)
  - PROGRAMME     : francetv://program/<path>     (= série → dépliée en épisodes
                    côté app via /apps/program/<path>, collection playlist_video)
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from utils import http_get

MAX_ITEMS_PER_CHAN = 999

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


def _extract_logo(item):
    logo = ""
    imgs = item.get("images") or []
    if isinstance(imgs, list):
        portrait_img = next((i for i in imgs if i.get("type", "").startswith("vignette")), None)
        bg_img = next((i for i in imgs if i.get("type", "").startswith("background")), None)
        chosen = portrait_img or bg_img or (imgs[0] if imgs else None)
        if chosen:
            urls = chosen.get("urls", {}) or {}
            for size_key in ("w:400", "w:300", "w:800", "w:265", "w:1024", "w:2500"):
                if urls.get(size_key):
                    logo = urls[size_key]
                    break
            if not logo and urls:
                logo = next(iter(urls.values()))
    return logo


def _extract_item(item, seen):
    """Retourne {kind,ref,title,logo} ou None.
    kind='video' (si_id direct = jouable) ou 'program' (path = série à déplier)."""
    si = item.get("si_id")
    path = item.get("program_path")
    key = si or (("prog:" + path) if path else None)
    if not key or key in seen:
        return None
    seen.add(key)
    title = (item.get("title") or "").strip()
    program = item.get("program") or {}
    program_title = program.get("label") or program.get("title") or ""
    if not title:
        title = program_title
    elif program_title and program_title.lower() not in title.lower():
        title = f"{program_title} — {title}"
    if not title:
        title = (item.get("label") or "").strip()
    if not title:
        return None
    logo = _extract_logo(item)
    if si:
        return {"kind": "video", "ref": si, "title": title[:140], "logo": logo}
    if path:
        return {"kind": "program", "ref": path, "title": title[:140], "logo": logo}
    return None


def _emit(p):
    """(src_url, tvg_id) selon le type d'entrée."""
    if p["kind"] == "program":
        return f"francetv://program/{p['ref']}", f"francetv-prog-{p['ref']}"
    return f"francetv://{p['ref']}", f"francetv-{p['ref']}"


def _parse_yatta_response(data):
    """Aplati toutes les collections en une seule liste (= usage chaînes)."""
    seen = set()
    out = []
    for coll in data.get("collections", []):
        if coll.get("type") in ("live", "link"):
            continue
        for item in coll.get("items", []):
            it = _extract_item(item, seen)
            if it is None:
                continue
            out.append(it)
            if len(out) >= MAX_ITEMS_PER_CHAN:
                break
        if len(out) >= MAX_ITEMS_PER_CHAN:
            break
    return out


def _parse_yatta_sections(data):
    """PRÉSERVE les rayons (collections). Retourne [(rail_title, [items]), ...].
    Skip les rayons live/link/navigation (playlist_sous_categories)."""
    skip_types = {"live", "link", "playlist_sous_categories"}
    out = []
    for coll in data.get("collections", []):
        if coll.get("type") in skip_types:
            continue
        rail = (coll.get("title") or coll.get("label") or "").strip()
        if not rail:
            continue
        seen = set()
        items = []
        for item in coll.get("items", []):
            it = _extract_item(item, seen)
            if it is not None:
                items.append(it)
        if items:
            out.append((rail, items))
    return out


def francetv_channel_programs(channel_path):
    url = f"https://api-mobile.yatta.francetv.fr/apps/channels/{channel_path}?platform=apps"
    try:
        return _parse_yatta_response(json.loads(http_get(url)))
    except Exception as e:
        print(f"[!] FTV chan {channel_path}: {e}", file=sys.stderr)
        return []


def francetv_category_sections(category_slug):
    url = f"https://api-mobile.yatta.francetv.fr/apps/categories/{category_slug}?platform=apps"
    try:
        return _parse_yatta_sections(json.loads(http_get(url)))
    except Exception as e:
        print(f"[!] FTV cat {category_slug}: {e}", file=sys.stderr)
        return []


def generate(output_path):
    lines = ["#EXTM3U"]
    total = 0

    print("\n=== France.tv (9 chaînes) ===")
    for channel_path, channel_label, channel_logo in FRANCETV_CHANNELS:
        progs = francetv_channel_programs(channel_path)
        print(f"  {channel_label}: {len(progs)} programmes")
        for p in progs:
            logo = p["logo"] or channel_logo
            src, tvgid = _emit(p)
            lines.append(
                f'#EXTINF:-1 tvg-id="{tvgid}" '
                f'tvg-logo="{logo}" tvg-country="FR" tvg-type="series" '
                f'group-title="Replay {channel_label}",{p["title"]}'
            )
            lines.append(src)
            total += 1
        time.sleep(0.4)

    print("\n=== France.tv Catégories + sous-rayons (10 catégories) ===")
    # 2026-06-26 : on PRÉSERVE les rayons (3-5 ans, Séries animées, Comédie...)
    #   et on émet par item soit une vidéo (si_id) soit un PROGRAMME (série, via
    #   francetv://program/<path>). PLUS de dédup contre les chaînes : les 9
    #   chaînes scrapent tout, ce qui vidait les catégories (Enfants/Okoo =
    #   dessins animés). Chaque rayon est complet, comme sur france.tv.
    for cat_slug, cat_label, cat_logo in FRANCETV_CATEGORIES:
        sections = francetv_category_sections(cat_slug)
        added = 0
        for rail_name, items in sections:
            group = f"Thématique France TV - {cat_label} - {rail_name}"
            for p in items:
                logo = p["logo"] or cat_logo
                src, tvgid = _emit(p)
                lines.append(
                    f'#EXTINF:-1 tvg-id="{tvgid}" '
                    f'tvg-logo="{logo}" tvg-country="FR" tvg-type="series" '
                    f'group-title="{group}",{p["title"]}'
                )
                lines.append(src)
                total += 1
                added += 1
        print(f"  {cat_label}: {len(sections)} rayons, {added} entrées")
        time.sleep(0.3)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    print(f"\n[OK] Wrote {total} France.tv programs to {output_path}")
    return total


if __name__ == "__main__":
    out = os.environ.get("OUTPUT", "data-replay-francetv.m3u")
    n = generate(out)
    if n == 0:
        sys.exit(1)
