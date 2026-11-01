#!/usr/bin/env python3
"""Contrôle des fichiers de la base ONYX hébergés sur une adresse IP brute.

Le Worker Cloudflare de la base n'a pas le droit d'appeler une adresse IP (403 systématique) :
ce script, lancé chaque jour par GitHub Actions, s'en charge à sa place.

1. demande au Worker la liste des fichiers à contrôler (signalés d'abord, puis le tour mensuel) ;
2. teste chaque fichier (les 1 024 premiers octets seulement, au plus une requête toutes les 5 s
   par serveur ; un serveur qui répond de façon incompréhensible est laissé tranquille) ;
3. renvoie les résultats au Worker, qui applique ses règles (deux « n'existe plus » à 24 h
   d'intervalle avant de retirer quoi que ce soit ; un refus ou un délai ne retirent jamais rien).

Aucun secret : le workflow s'identifie avec son jeton OIDC GitHub, vérifié par le Worker.
Bibliothèque standard uniquement.
"""
import json, os, sys, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "https://onyx-index-test.logami61250.workers.dev"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
PAUSE_PAR_SERVEUR = 5


def jeton():
    url, cle = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL"), os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not cle:
        sys.exit("Pas de jeton OIDC (le workflow doit avoir « permissions: id-token: write »).")
    req = urllib.request.Request(url + "&audience=onyx-index", headers={"Authorization": "Bearer " + cle})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)["value"]


def worker(chemin, corps=None):
    data = json.dumps(corps).encode() if corps is not None else None
    req = urllib.request.Request(BASE + chemin, data=data, headers={
        "Authorization": "Bearer " + jeton(), "Content-Type": "application/json", "User-Agent": "controle-fichiers"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def verifier(src):
    req = urllib.request.Request(src, headers={"Range": "bytes=0-1023", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            code, ct = r.status, (r.headers.get("Content-Type") or "").lower()
    except urllib.error.HTTPError as e:
        code, ct = e.code, ((e.headers.get("Content-Type") or "").lower() if e.headers else "")
    except Exception as e:  # délai, coupure, DNS… : on ne sait pas → on garde
        return "inconnu", str(e)[:100]
    if code in (404, 410):
        return "mort", f"HTTP {code}"
    if code in (200, 206) and "text/html" in ct:
        return "mort", f"page HTML au lieu du fichier (HTTP {code})"
    if code in (200, 206):
        return "vivant", f"HTTP {code} {ct[:40]}".strip()
    return "inconnu", f"HTTP {code}"


def controler_serveur(lot):
    out = []
    for i, f in enumerate(lot):
        if i:
            time.sleep(PAUSE_PAR_SERVEUR)
        res, detail = verifier(f["src"])
        out.append({"rid": f["rid"], "resultat": res, "detail": detail + " · GitHub"})
        if res == "inconnu":
            break  # ce serveur ne répond pas clairement : on le laisse tranquille pour aujourd'hui
    return out


def main():
    liste = worker("/ip/a-controler")
    print(f"{len(liste)} fichier(s) sur adresse IP à contrôler")
    if not liste:
        return
    par_serveur = {}
    for f in liste:
        par_serveur.setdefault(urllib.parse.urlsplit(f["src"]).netloc, []).append(f)
    with ThreadPoolExecutor(max_workers=16) as ex:
        resultats = [r for lot in ex.map(controler_serveur, par_serveur.values()) for r in lot]
    bilan = {}
    for r in resultats:
        bilan[r["resultat"]] = bilan.get(r["resultat"], 0) + 1
    print("résultats :", bilan, f"({len(par_serveur)} serveurs)")
    for i in range(0, len(resultats), 100):
        print("envoi :", worker("/ip/resultats", {"resultats": resultats[i:i + 100]}))


if __name__ == "__main__":
    main()
