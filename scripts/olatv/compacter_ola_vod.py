#!/usr/bin/env python3
"""
2026-09-27 — Version COMPACTE de l'index VOD OLA (films), pour que l'appli le lise ~3x plus vite.

Même contenu que ola-vod-fr.json, sans les répétitions :
  - films en TABLEAUX [g, cmd, t, y, tmdb, img, c] au lieu d'objets (noms de champs écrits une fois) ;
  - cmd : « ~<stream_id> » quand c'est la commande Stalker standard d'un film mkv, « ~<id>~<ext> »
    pour les autres conteneurs, sinon la commande d'origine telle quelle (URL directe, etc.) ;
  - img : préfixe TMDB commun retiré (« imgp ») ; adresse complète préfixée « ! » ;
  - c   : numéro dans la liste « cats ».
L'appli reconstruit EXACTEMENT les mêmes valeurs (base64 identique octet pour octet). Ce script
vérifie l'aller-retour sur TOUS les films : au moindre écart, il n'écrit rien (l'appli garde
alors l'ancien fichier).

Entrée  : OLA_VOD_IN      (défaut data/olatv/ola-vod-fr.json)
Sortie  : OLA_VOD_COMPACT (défaut data/olatv/ola-vod-fr-compact.json)
"""
import base64
import json
import os
import sys

IN_PATH = os.environ.get("OLA_VOD_IN", "data/olatv/ola-vod-fr.json")
OUT_PATH = os.environ.get("OLA_VOD_COMPACT", "data/olatv/ola-vod-fr-compact.json")
IMGP = "https://image.tmdb.org/t/p/w600_and_h900_bestv2/"
TPL = '{"type":"movie","stream_id":"%s","stream_source":null,"target_container":"[\\"%s\\"]"}'


def cmd_standard(sid, ext):
    return base64.b64encode((TPL % (sid, ext)).encode("utf-8")).decode("ascii")


def compacter_cmd(c):
    try:
        j = json.loads(base64.b64decode(c + "=" * (-len(c) % 4)).decode("utf-8"))
    except Exception:
        return c
    if not isinstance(j, dict):
        return c
    sid, tc = j.get("stream_id"), j.get("target_container")
    if not isinstance(sid, str) or not sid or "~" in sid:
        return c
    if not (isinstance(tc, str) and tc.startswith('["') and tc.endswith('"]')):
        return c
    ext = tc[2:-2]
    if not ext or "~" in ext or '"' in ext:
        return c
    if cmd_standard(sid, ext) != c:
        return c
    return "~" + sid if ext == "mkv" else "~" + sid + "~" + ext


def decompacter_cmd(c):
    """Même logique que OlaVod.kt (sert à vérifier l'aller-retour)."""
    if not c.startswith("~"):
        return c
    parts = c[1:].split("~")
    return cmd_standard(parts[0], parts[1] if len(parts) > 1 else "mkv")


def main():
    d = json.load(open(IN_PATH, encoding="utf-8"))
    films = d.get("films", [])
    cats = []
    idx = {}
    lignes = []
    for f in films:
        c = f.get("c", "")
        if c not in idx:
            idx[c] = len(cats)
            cats.append(c)
        img = f.get("img") or ""
        if img.startswith(IMGP):
            img = img[len(IMGP):]
        elif img:
            img = "!" + img  # adresse complète gardée telle quelle
        lignes.append([f.get("g", -1), compacter_cmd(f.get("cmd", "")), f.get("t", ""),
                       f.get("y", 0), f.get("tmdb", 0), img, idx[c]])

    # Vérification de l'aller-retour, film par film.
    for f, l in zip(films, lignes):
        img = l[5]
        img = "" if img == "" else (img[1:] if img.startswith("!") else IMGP + img)
        refait = {"g": l[0], "cmd": decompacter_cmd(l[1]), "t": l[2], "y": l[3], "tmdb": l[4],
                  "img": img, "c": cats[l[6]]}
        orig = {"g": f.get("g", -1), "cmd": f.get("cmd", ""), "t": f.get("t", ""), "y": f.get("y", 0),
                "tmdb": f.get("tmdb", 0), "img": f.get("img") or "", "c": f.get("c", "")}
        if refait != orig:
            print("ÉCART, compact NON écrit :", orig, "!=", refait)
            sys.exit(1)

    out = {"savedAt": d.get("savedAt"), "generatedBy": "compacter_ola_vod.py", "v": 1,
           "groupes": d.get("groupes", {}), "imgp": IMGP, "cats": cats, "films": lignes}
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT_PATH)
    std = sum(1 for l in lignes if l[1].startswith("~"))
    print("compact écrit : %d films (%d cmd standard), %d catégories, %d -> %d octets"
          % (len(lignes), std, len(cats), os.path.getsize(IN_PATH), os.path.getsize(OUT_PATH)))


if __name__ == "__main__":
    main()
