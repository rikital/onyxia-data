#!/usr/bin/env python3
"""
refresh_fast.py — Fetch FAST channel playlists (Samsung TV+, Pluto TV, Plex TV,
LG Channels, Rakuten TV, Sony One) and write data-fast.m3u (STANDALONE).

NE TOUCHE PAS data.m3u. Fichier autonome comme refresh_arte.py, refresh_bfm.py, etc.
Uses stdlib only (no pip install needed). Triggered via workflow_dispatch by cron-job.org.
"""

import json, re, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen

OUTPUT = "data-fast.m3u"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"

# -- Source URLs --
SAMSUNG_URL = "https://textup.fr/902510Ao?filetype=txt"
PLUTO_URL   = "https://textup.fr/902266T0?filetype=txt"
PLUTO_FALLBACK_URL = "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/plutotv_fr.m3u"
PLEX_URL    = "https://textup.fr/902513mk?filetype=txt"
LG_URL      = "https://www.apsattv.com/frlg.m3u"
RAKUTEN_URL = "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/fr_rakuten.m3u"

# Sony One FR — 6 chaines CloudFront stables
SONY_ONE = [
    ("Sony One Hits Action",     "https://d1tvu4639eiqii.cloudfront.net/playlist.m3u8",  ""),
    ("Sony One Hits Comedie",    "https://d3nvxupr08boay.cloudfront.net/playlist.m3u8",  ""),
    ("Sony One Series Comedie",  "https://d1d95b9dbrm3cl.cloudfront.net/playlist.m3u8",  ""),
    ("Sony One Favoris",         "https://d21fkiqbljd83t.cloudfront.net/playlist.m3u8",  ""),
    ("Sony One Series Thriller", "https://d1573iiyr4aa7c.cloudfront.net/playlist.m3u8",  ""),
    ("Sony One Blacklist",       "https://d1x87j1jmcypab.cloudfront.net/playlist.m3u8",  ""),
]

# Mapping source key -> group-title prefix in data-fast.m3u
_SOURCE_PREFIXES = {
    "samsung": "Samsung TV+",
    "pluto":   "Pluto TV",
    "plex":    "Plex TV",
    "lg":      "LG Channels",
    "rakuten": "Rakuten TV",
    "sony":    "Sony One",
}


def fetch(url, timeout=20):
    try:
        req = Request(url, headers={"User-Agent": UA})
        with urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  WARN: fetch {url}: {e}", file=sys.stderr)
        return None


def _attr(line, name):
    m = re.search(rf'{name}="([^"]*?)"', line)
    return m.group(1) if m else ""


def read_existing_m3u(path):
    """Read existing M3U file and return list of (name, url, group, logo) entries."""
    entries = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return entries
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        if ln.startswith("#EXTINF:") and i + 1 < len(lines):
            ul = lines[i + 1].strip()
            if ul and ul.startswith("http") and not ul.startswith("#"):
                logo = _attr(ln, "tvg-logo")
                gt = _attr(ln, "group-title")
                nm = ln[ln.rfind(",") + 1:].strip() if "," in ln else ""
                if nm:
                    entries.append((nm, ul, gt, logo))
            i += 2
            continue
        i += 1
    return entries


# ---------- Samsung TV+ (JSON historique OU M3U depuis 2026-09) ----------
def parse_samsung(body):
    # 2026-09-03 : la source textup.fr est passee de JSON a M3U (#EXTM3U,
    #   group-title "Cinema"/"Divertissement"/... et URLs html.bet/jmp2.uk).
    #   json.loads() plantait alors -> 0 chaine -> dossier Samsung vide dans
    #   l'app. On delegue donc au parseur M3U generique avec le service
    #   "Samsung TV+" (=> groupes "Samsung TV+ - Cinema", format attendu par
    #   l'app). Chemin JSON conserve en repli si la source repasse a l'ancien.
    if body.lstrip()[:7] == "#EXTM3U" or "#EXTINF" in body[:4000]:
        return parse_m3u(body, "Samsung TV+")
    entries = []
    try:
        data = json.loads(body)
        for grp in data.get("groups", []):
            gn = grp.get("name", "Divers")
            for s in grp.get("stations", []):
                n, u, l = s.get("name", "").strip(), s.get("url", "").strip(), s.get("image", "")
                if n and u and "c9v3.s.gy" not in u:
                    entries.append((n, u, f"Samsung TV+ - {gn}", l))
        for s in data.get("stations", []):
            n, u, l = s.get("name", "").strip(), s.get("url", "").strip(), s.get("image", "")
            fold = s.get("infold", "Samsung TV+")
            if n and u and "c9v3.s.gy" not in u:
                entries.append((n, u, fold or "Samsung TV+", l))
    except Exception as e:
        print(f"  WARN: parse_samsung: {e}", file=sys.stderr)
    return entries


# ---------- Generic M3U parser ----------
def parse_m3u(body, service):
    entries, lines, i = [], body.splitlines(), 0
    while i < len(lines):
        ln = lines[i].strip()
        if ln.startswith("#EXTINF:") and i + 1 < len(lines):
            ul = lines[i + 1].strip()
            if ul and ul.startswith("http") and not ul.startswith("#"):
                logo = _attr(ln, "tvg-logo")
                gt = _attr(ln, "group-title")
                nm = ln[ln.rfind(",") + 1:].strip() if "," in ln else ""
                if nm:
                    g = f"{service} - {gt}" if gt else service
                    entries.append((nm, ul, g, logo))
            i += 2
            continue
        i += 1
    return entries


# ---------- LG Channels (categorisation) ----------
_LG = [
    (["pluto tv"], "Pluto TV"), (["rakuten"], "Rakuten TV"), (["sony one"], "Sony One"),
    (["sport", "football", "nba", "top gear", "motorvision", "wwe", "eurosport"], "Sport"),
    (["action", "western", "thriller", "horror", "sci-fi", "polar", "asylum"], "Action & Thriller"),
    (["comedie", "comedy", "rire"], "Comedie"),
    (["drame", "drama", "romance", "romantique", "novela"], "Drame & Romance"),
    (["film", "cinema", "movie", "lionsgate", "nanar", "grjngo", "moviesphere",
      "allocine"], "Cinema"),
    (["serie", "series", "binge"], "Series"),
    (["kid", "enfant", "toon", "cartoon", "pokemon", "sonic", "miraculous", "teen"], "Jeunesse"),
    (["crime", "ncis", "blacklist", "jag", "walker"], "Crime"),
    (["musique", "music", "mtv", "trace", "vevo", "live nation"], "Musique"),
    (["nature", "animaux", "animal", "planet", "pets"], "Nature & Animaux"),
    (["docu", "histoire", "history", "science", "discovery", "insight", "real stories"], "Documentaire"),
    (["cuisine", "food", "maison", "jardin", "lifestyle", "fashion", "beauty"], "Lifestyle"),
    (["info", "news", "euronews", "france 24", "afrique"], "Info"),
    (["rmc", "bfm"], "BFM / RMC"),
]


def _clf(name):
    lo = name.lower()
    for keys, cat in _LG:
        if any(k in lo for k in keys):
            return cat
    return "Autres"


def parse_lg(body):
    entries, lines, i = [], body.splitlines(), 0
    while i < len(lines):
        ln = lines[i].strip()
        if ln.startswith("#EXTINF:") and i + 1 < len(lines):
            ul = lines[i + 1].strip()
            if ul and ul.startswith("http") and not ul.startswith("#"):
                logo = _attr(ln, "tvg-logo")
                raw = ln[ln.rfind(",") + 1:].strip() if "," in ln else ""
                if raw:
                    nm = re.sub(r"^\d+\s+", "", raw)
                    entries.append((nm or raw, ul, f"LG Channels - {_clf(nm)}", logo))
            i += 2
            continue
        i += 1
    return entries


# ---------- Rakuten TV (M3U from iptv-org) ----------
def parse_rakuten(body):
    return parse_m3u(body, "Rakuten TV")


def fetch_plex_lineup():
    """2026-06-26 : Plex free live TV via API officielle (token anonyme + lineup).
    Emet plex://<channelId> = ref stable resolue a la lecture par l'app (PlexTvResolver).
    Remplace l'ancien m3u jmp2.uk mort (session Plex perimee -> 404)."""
    import uuid as _u, json as _j
    cid = str(_u.uuid4())
    try:
        req = Request("https://plex.tv/api/v2/users/anonymous", data=b"",
                      headers={"Accept": "application/json",
                               "X-Plex-Product": "Plex Mediaverse",
                               "X-Plex-Client-Identifier": cid})
        tok = _j.loads(urlopen(req, timeout=20).read().decode("utf-8")).get("authToken", "")
        if not tok:
            return []
        lu = ("https://epg.provider.plex.tv/lineups/plex/channels"
              "?X-Plex-Token=" + tok + "&X-Plex-Client-Identifier=" + cid)
        data = _j.loads(urlopen(Request(lu, headers={"Accept": "application/json"}), timeout=20).read().decode("utf-8"))
        chans = (data.get("MediaContainer", data).get("Channel") or [])
        out = []
        for ch in chans:
            cid2 = ch.get("id", "")
            title = (ch.get("title") or "").strip()
            if not cid2 or not title:
                continue
            logo = ch.get("coverPoster") or ch.get("thumb") or ch.get("art") or ""
            out.append((title, "plex://" + cid2, "Plex TV - France", logo))
        return out
    except Exception as e:
        print("  WARN plex lineup: " + str(e), file=sys.stderr)
        return []


# ---------- Main ----------
def main():
    print("refresh_fast.py: fetching FAST playlists...")
    print("  Output: data-fast.m3u (STANDALONE, ne touche PAS data.m3u)")

    bodies = {}
    tasks = {
        "samsung": SAMSUNG_URL,
        "pluto": PLUTO_URL,
        "plex": PLEX_URL,
        "lg": LG_URL,
        "rakuten": RAKUTEN_URL,
    }
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(fetch, url): svc for svc, url in tasks.items()}
        for f in as_completed(futs):
            bodies[futs[f]] = f.result()

    all_e = []
    failed = set()

    # --- Samsung TV+ ---
    samsung_body = bodies.get("samsung")
    if samsung_body:
        e = parse_samsung(samsung_body)
        print(f"  Samsung TV+: {len(e)} channels")
        all_e.extend(e)
    else:
        print("  Samsung TV+: FAILED")
        failed.add("samsung")

    # --- Pluto TV (avec fallback jmp2.uk si textup.fr fail) ---
    pluto_body = bodies.get("pluto")
    if not pluto_body:
        print("  Pluto TV: textup.fr failed, trying fallback jmp2.uk...")
        pluto_body = fetch(PLUTO_FALLBACK_URL)
    if pluto_body:
        e = parse_m3u(pluto_body, "Pluto TV")
        print(f"  Pluto TV: {len(e)} channels")
        all_e.extend(e)
    else:
        print("  Pluto TV: FAILED (primary + fallback)")
        failed.add("pluto")

    # --- Plex TV (2026-06-26 : API officielle, token anonyme + lineup -> plex://<id>) ---
    e = fetch_plex_lineup()
    if e:
        print(f"  Plex TV: {len(e)} channels (API officielle)")
        all_e.extend(e)
    else:
        print("  Plex TV: FAILED (lineup API)")
        failed.add("plex")

    # --- LG Channels ---
    lg_body = bodies.get("lg")
    if lg_body:
        e = parse_lg(lg_body)
        print(f"  LG Channels: {len(e)} channels")
        all_e.extend(e)
    else:
        print("  LG Channels: FAILED")
        failed.add("lg")

    # --- Rakuten TV ---
    rakuten_body = bodies.get("rakuten")
    if rakuten_body:
        e = parse_rakuten(rakuten_body)
        print(f"  Rakuten TV: {len(e)} channels")
        all_e.extend(e)
    else:
        print("  Rakuten TV: FAILED")
        failed.add("rakuten")

    # --- Sony One (hardcoded, never fails) ---
    for name, url, logo in SONY_ONE:
        all_e.append((name, url, "Sony One", logo))
    print(f"  Sony One: {len(SONY_ONE)} channels")

    # --- Keep old channels for failed sources ---
    if failed:
        old = read_existing_m3u(OUTPUT)
        kept = 0
        for name, url, group, logo in old:
            for src, prefix in _SOURCE_PREFIXES.items():
                if src in failed and group.startswith(prefix):
                    all_e.append((name, url, group, logo))
                    kept += 1
                    break
        if kept:
            print(f"  Kept {kept} old channels for failed sources: {', '.join(sorted(failed))}")
        else:
            print(f"  No old channels found for failed sources: {', '.join(sorted(failed))}")

    print(f"  Total: {len(all_e)} FAST channels")
    if not all_e:
        print("  ERROR: 0 channels -- aborting")
        sys.exit(1)

    # Write standalone data-fast.m3u
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for name, url, group, logo in all_e:
            l = f' tvg-logo="{logo}"' if logo else ""
            f.write(f'#EXTINF:-1{l} group-title="{group}",{name}\n')
            f.write(f'{url}\n')

    with open(OUTPUT) as f:
        n = sum(1 for _ in f)
    print(f"  Written {OUTPUT}: {n} lines")


if __name__ == "__main__":
    main()
