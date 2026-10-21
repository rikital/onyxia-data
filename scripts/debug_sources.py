#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Diagnostic : affiche ce que Eporner et XVideos renvoient reellement,
# pour recaler les extractions quand un site change sa page.

import http.cookiejar
import re
import urllib.error
import urllib.request

ENTETES = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
}

POT = http.cookiejar.CookieJar()
OUV = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(POT))


def lire(url):
    try:
        req = urllib.request.Request(url, headers=ENTETES)
        with OUV.open(req, timeout=25) as r:
            return r.status, r.geturl(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, url, ""
    except Exception as e:
        return "err", url, f"{type(e).__name__}: {e}"


def extrait(texte, motif, avant=250, apres=700):
    m = re.search(motif, texte)
    if not m:
        return "(motif absent)"
    d = max(0, m.start() - avant)
    return texte[d:m.start() + apres]


def sondage(nom, url, marqueurs, motif=None):
    statut, finale, texte = lire(url)
    print("=" * 70)
    print(f"{nom}  ->  {url}")
    print(f"statut {statut} | url finale {finale} | {len(texte)} octets")
    for m in marqueurs:
        print(f"  {m!r} : {'OUI' if m in texte else 'non'}")
    if motif:
        print("  --- extrait ---")
        print("  " + extrait(texte, motif).replace("\n", "\n  ")[:1400])
    return texte


print("### XVIDEOS")
liste = sondage("liste", "https://www.xvideos.com/new/1/",
                ["thumb-block", "/video.", "data-id", "setVideoUrlHigh",
                 "videoUrlHigh", "mp4_sd"], motif=r"/video\.")

eids = re.findall(r'href="(?:https?://[^"]*?)?(/video\.([a-z0-9]+)/[^"]*)"', liste)
print(f"eids trouves avec le motif actuel : {len(eids)}")
if not eids:
    eids2 = re.findall(r"/video\.([a-z0-9]+)", liste)
    print(f"eids trouves en cherchant juste /video. : {len(eids2)}")
    if eids2:
        print("premiers :", eids2[:5])

if eids:
    sondage("page video", "https://www.xvideos.com" + eids[0][0],
            ["setVideoUrlHigh", "setVideoUrlLow", "setVideoHLS", "mp4_sd"],
            motif=r"setVideoUrlHigh|mp4_sd")

print()
print("### EPORNER")
accueil = sondage("accueil", "https://www.eporner.com/",
                  ["Age verification", "data-id", "/video-", "mbtim"],
                  motif=r"<form")

if "Age verification" in accueil[:2000]:
    print("Eporner impose sa verification d'age : pas de contournement, Eporner reste ignore.")
