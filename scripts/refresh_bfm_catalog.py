#!/usr/bin/env python3
"""refresh_bfm_catalog.py — COMPLETE data-replay-bfm.m3u via le catalogue OFFICIEL BFF.

Tourne APRES refresh_bfm.py. Le scraper principal ne lit que les carrousels
gaia-core (limites + rotatifs) -> il rate beaucoup de programmes (ex: "Alaska,
la ruee vers l'or"). Ici on lit le CATALOGUE COMPLET de chaque chaine via
l'API BFF du site rmcbfmplay.com (section "Tous les programmes" = mosaique
paginee, ~285 programmes / chaine), puis on resout l'id JOUABLE (gaia
`NEUF_...`) via la fiche programme (FIP), et on AJOUTE les manquants a
data-replay-bfm.m3u (dedup par l'id numerique deja present). Ne retire rien.

Efficace : on ne resout via FIP que les programmes ABSENTS du fichier (l'id
numerique du catalogue est deja contenu dans les ids gaia existants).

GARDE-FOUS : si l'enumeration ramene moins de MIN_ENUM programmes -> fichier
INTACT. Append avec saut de ligne en tete.
"""
import re, sys, os, time, json, urllib.request

HERE = os.path.dirname(__file__)
M3U = os.path.join(HERE, "..", "data-replay-bfm.m3u")
BFF = "https://www.rmcbfmplay.com/api/bff/v1"
UA  = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
MIN_ENUM = 300
MAX_RESOLVE = 4000  # plafond de securite d'appels FIP par run

CHANNELS = [
    ("bfmtv",         "BFM TV"),
    ("rmc-story",     "RMC Story"),
    ("rmc-decouverte","RMC Découverte"),
    ("bfm-business",  "BFM Business"),
    ("rmc-life",      "RMC Life"),
    ("tech-co",       "Tech & Co"),
    ("rmc-radio",     "RMC Radio"),
]


def get_text(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if i == tries - 1:
                return ""
            time.sleep(1.0)
    return ""


def get_json(url, tries=3):
    t = get_text(url, tries)
    if not t:
        return None
    try:
        return json.loads(t)
    except Exception:
        return None


def img_url(v):
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        u = v.get("url")
        if isinstance(u, str):
            return u
        if isinstance(u, dict):
            return u.get("portrait") or u.get("square") or next(iter(u.values()), "")
    return ""


def mosaique_endpoint(chan_pageid):
    page = get_json(f"{BFF}/page?model=web&page_type=default&page_id={chan_pageid}")
    if not page:
        return None
    for s in page.get("sections", []):
        if "Tous les programmes" in (s.get("titre") or ""):
            ep = s.get("endpoint") or ""
            if ep:
                return ep.replace("type=rail", "type=mosaique")
    return None


def enum_channel(chan_pageid):
    ep = mosaique_endpoint(chan_pageid)
    if not ep:
        return []
    out = []
    for pn in range(1, 60):
        d = get_json(f"{BFF}{ep}&model=web&page_size=50&page_number={pn}")
        if not d:
            break
        items = d.get("items") or (d.get("sections", [{}])[0].get("items") if d.get("sections") else []) or []
        if not items:
            break
        for it in items:
            nid = str(it.get("id") or "")
            if not nid.isdigit():
                continue
            title = (it.get("background_image") or {}).get("alt") or (it.get("logo") or {}).get("alt") or ""
            image = img_url(it.get("background_image")) or img_url(it.get("logo"))
            cta = it.get("call_to_actions") or []
            cta_ep = cta[0].get("endpoint") if cta else None
            out.append({"nid": nid, "title": title.strip(), "image": image, "cta": cta_ep})
        if len(items) < 50:
            break
        time.sleep(0.1)
    return out


def resolve_gaia(nid, cta_ep):
    for url in (f"{BFF}/page?model=web&page_type=fip&page_id={nid}",
                (f"{BFF}{cta_ep}&model=web" if cta_ep and "model=" not in cta_ep else None)):
        if not url:
            continue
        t = get_text(url, tries=2)
        m = re.search(r"NEUF_[A-Z0-9_]+", t)
        if m:
            return m.group(0)
    return None


def main():
    if not os.path.exists(M3U):
        print(f"[STOP] {M3U} absent."); return
    content = open(M3U, encoding="utf-8").read()
    existing_gaia = set(re.findall(r'^bfmplay://(\S+)$', content, re.M))
    # ids numeriques deja presents (contenus dans les ids gaia)
    existing_nums = set(re.findall(r'(\d{9,})', "\n".join(existing_gaia)))
    print(f"Existant : {len(existing_gaia)} gaia | {len(existing_nums)} ids numeriques")

    catalog = []          # programmes du catalogue complet
    for pageid, label in CHANNELS:
        rows = enum_channel(pageid)
        for r in rows:
            r["chan"] = label
        catalog += rows
        print(f"  {label}: {len(rows)} programmes (catalogue)")
        time.sleep(0.2)
    total = len({r["nid"] for r in catalog})
    print(f"Catalogue total : {total} programmes uniques")
    if total < MIN_ENUM:
        print(f"[STOP] catalogue faible ({total} < {MIN_ENUM}) -> fichier INTACT."); return

    # ne resoudre que les manquants
    seen_nid = set()
    added = []
    resolves = 0
    for r in catalog:
        nid = r["nid"]
        if nid in seen_nid or nid in existing_nums:
            continue
        seen_nid.add(nid)
        if resolves >= MAX_RESOLVE:
            break
        resolves += 1
        gaia = resolve_gaia(nid, r["cta"])
        if not gaia or gaia in existing_gaia:
            continue
        existing_gaia.add(gaia)
        title = (r["title"] or "").replace('"', "'")[:140]
        if not title:
            continue
        tvg = "series" if "_S" in gaia else "movie"
        group = f"Catalogue {r['chan']}"
        added.append(f'#EXTINF:-1 tvg-id="bfmplay-{gaia}" tvg-logo="{r["image"]}" '
                     f'tvg-country="FR" tvg-type="{tvg}" group-title="{group}",{title}')
        added.append(f'bfmplay://{gaia}')
        time.sleep(0.05)

    print(f"Manquants resolus via FIP : {resolves} | ajoutes : {len(added)//2}")
    if not added:
        print("Rien a ajouter."); return
    with open(M3U, "a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(added) + "\n")
    print(f"Programmes AJOUTES : {len(added)//2}")


if __name__ == "__main__":
    main()
