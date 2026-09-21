#!/usr/bin/env python3
"""
refresh_vidara_amis.py — « Partage de la communaute » Vidara -> data/vidara_amis.json

Meme principe que refresh_voe.py / refresh_vidara.py : on publie UNIQUEMENT une
table titre -> filecode (+ nom du dossier), jamais une cle. La lecture se fait
sur https://vidara.to/e/{filecode}, lien public. Les cles des amis vivent dans le
secret GitHub VIDARA_AMIS et ne quittent jamais le runner.

Secret attendu : VIDARA_AMIS = "prenom:CLE1,autre:CLE2"   (libelle:cle, virgules)
  - le libelle devient le champ "lib" = le nom du dossier du contributeur dans
    l'application (« Partage de la communaute » -> un groupe par ami) ;
  - plusieurs comptes peuvent cohabiter, chacun avec ses dossiers.

Difference avec refresh_vidara.py : pas de connexion au site (on n'a pas les
identifiants des amis), donc les dossiers sont ceux de l'API, A PLAT : le nom
du dossier direct, sans arborescence. C'est suffisant pour un regroupement.
"""
import json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

API = "https://api.vidara.so/v1"
SORTIE = os.path.join(os.path.dirname(__file__), "..", "data", "vidara_amis.json")
PAR_PAGE = 100
UA = "Mozilla/5.0 (nx-data/refresh_vidara_amis)"

ESPACEMENT_S = 0.35
_dernier_appel = 0.0


def _cadence():
    global _dernier_appel
    ecart = time.monotonic() - _dernier_appel
    if ecart < ESPACEMENT_S:
        time.sleep(ESPACEMENT_S - ecart)
    _dernier_appel = time.monotonic()


def api(chemin, params, cle, essais=5):
    q = dict(params); q["api_key"] = cle
    url = f"{API}{chemin}?" + urllib.parse.urlencode(q)
    attente = 2.0
    for n in range(essais):
        _cadence()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 403):
                print(f"    {e.code} sur {chemin}, reessai dans {attente:.0f}s", file=sys.stderr)
            elif n == essais - 1:
                raise
        except Exception:
            if n == essais - 1:
                raise
        if n < essais - 1:
            time.sleep(attente); attente *= 2
    return None


def dossiers_plats(cle):
    d = api("/folder/list", {}, cle)
    out = {}
    for f in ((d or {}).get("result") or {}).get("folders") or []:
        try:
            out[int(f.get("fld_id") or f.get("code"))] = (f.get("name") or "").strip()
        except (TypeError, ValueError):
            continue
    return out


def fichiers_du_dossier(cle, fld_id, nom_dossier, lib):
    out, page = [], 1
    while page <= 200:
        params = {"page": page, "per_page": PAR_PAGE, "statut": "actif"}
        if fld_id is not None:
            params["fld_id"] = fld_id
        d = api("/video/list", params, cle)
        res = (d or {}).get("result") or {}
        arr = res.get("videos") or []
        if not arr:
            break
        for f in arr:
            code = (f.get("filecode") or f.get("file_code") or f.get("code") or "").strip()
            if not code:
                continue
            if str(f.get("status") or "active").lower() not in ("active", "in progress", ""):
                continue
            nom = (f.get("title") or f.get("name") or "").strip()
            out.append({
                "code": code,
                "titre": nom,
                "nom": nom,
                "q": 0,
                "taille": 0,
                "duree": int(f.get("length") or 0) * 60,
                "lib": lib,
                "dossier": nom_dossier,
            })
        if page >= int(res.get("total_pages") or 1):
            break
        page += 1
    return out


def main():
    brut = os.environ.get("VIDARA_AMIS", "").strip()
    if not brut:
        print("VIDARA_AMIS absent — rien a faire.", file=sys.stderr)
        return 1
    comptes = []
    for morceau in brut.replace(";", ",").split(","):
        lib, _, cle = morceau.strip().partition(":")
        lib, cle = lib.strip(), cle.strip()
        if not cle:
            continue
        comptes.append((lib or f"ami{len(comptes) + 1}", cle))
    if not comptes:
        print("VIDARA_AMIS ne contient aucune entree 'libelle:cle'.", file=sys.stderr)
        return 1

    items, vus = [], set()
    comptes_ok = 0
    for lib, cle in comptes:
        print(f"== {lib}")
        try:
            dossiers = dossiers_plats(cle)
        except Exception as e:
            print(f"  folder/list KO ({e}) — compte ignore", file=sys.stderr)
            continue
        comptes_ok += 1
        lot = []
        for fid, nom in sorted(dossiers.items(), key=lambda kv: kv[1].lower()):
            lot += fichiers_du_dossier(cle, fid, nom, lib)
        lot += fichiers_du_dossier(cle, None, "", lib)     # reliquat = racine
        n_avant = len(items)
        for it in lot:
            k = (lib, it["code"])
            if k in vus:
                continue
            vus.add(k); items.append(it)
        print(f"  {len(dossiers)} dossier(s), {len(items) - n_avant} fichier(s)")

    if not comptes_ok:
        print("aucun compte joignable — index NON reecrit.", file=sys.stderr)
        return 1
    if not items:
        print("comptes joignables mais aucun fichier : index vide publie (normal pour un compte tout neuf).")

    items.sort(key=lambda x: (x["lib"].lower(), x["dossier"].lower(), x["titre"].lower()))
    doc = {
        "genere": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": len(items),
        "contributeurs": sorted({it["lib"] for it in items}),
        "items": items,
    }
    os.makedirs(os.path.dirname(SORTIE), exist_ok=True)
    with open(SORTIE, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    print(f"data/vidara_amis.json : {len(items)} entrees, {len(doc['contributeurs'])} contributeur(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
