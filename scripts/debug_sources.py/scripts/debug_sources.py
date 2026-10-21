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
    print("--- tentative de franchissement par cookies ---")
    for nom in ("age_verified", "age_ok", "ageok", "ep_age", "ep_age_verified",
                "age", "adult", "nv_age", "ep_age_ok", "ageverif"):
        POT.set_cookie(http.cookiejar.Cookie(
            version=0, name=nom, value="1", port=None, port_specified=False,
            domain=".eporner.com", domain_specified=True, domain_initial_dot=True,
            path="/", path_specified=True, secure=True, expires=None,
            discard=False, comment=None, comment_url=None, rest={}, rfc2109=False))
    statut, finale, texte = lire("https://www.eporner.com/")
    bloque = "Age verification" in texte[:2000]
    print(f"apres cookies : statut {statut} | {len(texte)} octets | "
          f"portail {'ENCORE LA' if bloque else 'FRANCHI'}")
    print("cookies poses :", [c.name for c in POT])
    if bloque:
        print("--- forme du portail ---")
        print("  " + extrait(texte, r"<form").replace("\n", "\n  ")[:1800])
        print("--- entetes de la reponse ---")
        try:
            req = urllib.request.Request("https://www.eporner.com/", headers=ENTETES)
            with OUV.open(req, timeout=25) as r:
                for k, v in r.headers.items():
                    print(f"  {k}: {v}")
        except Exception as e:
            print("  (entetes illisibles)", e)

sondage("liste", "https://www.
