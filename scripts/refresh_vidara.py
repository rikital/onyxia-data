#!/usr/bin/env python3
"""
refresh_vidara.py — index de la bibliotheque Vidara -> data/vidara.json

Meme role et meme format que refresh_voe.py, mais pour Vidara. Publie UNIQUEMENT
la table titre -> filecode + chemin de dossier ; la lecture se fait sur
https://vidara.so/e/{filecode} (lien public). Ni la cle API ni le mot de passe du
site ne quittent les secrets GitHub.

⚠ Vidara DIFFERE de VOE : l'API publique (cle) rend les dossiers A PLAT (aucun
  parent). L'arborescence imbriquee n'existe que sur le SITE vidara.so. On se
  connecte donc au site (login JSON + cookie) et on lit les sous-dossiers page
  par page (GET /files?folder_id=X), puis on liste les FICHIERS de chaque dossier
  via l'API (video/list?fld_id). Meme decoupage que refresh_voe : dossiers d'abord,
  racine (reliquat) ensuite, dedup par filecode.

Secrets attendus :
  VIDARA_SITE    = "identifiant:motdepasse"   (login vidara.so, pour l'arbo)
  VIDARA_API_KEY = "<cle api>"                (pour lister les fichiers)
"""
import http.cookiejar, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

API = "https://api.vidara.so/v1"
SITE = "https://vidara.so"
SORTIE = os.path.join(os.path.dirname(__file__), "..", "data", "vidara.json")
PAR_PAGE = 100
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

ESPACEMENT_S = 0.35
_dernier_appel = 0.0


def _cadence():
    global _dernier_appel
    ecart = time.monotonic() - _dernier_appel
    if ecart < ESPACEMENT_S:
        time.sleep(ESPACEMENT_S - ecart)
    _dernier_appel = time.monotonic()


# ───────────────────────────────────────────────────────── API Vidara (cle)
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
            if e.code == 429:
                print(f"    429 sur {chemin}, reessai dans {attente:.0f}s", file=sys.stderr)
            elif n == essais - 1:
                raise
        except Exception:
            if n == essais - 1:
                raise
        if n < essais - 1:
            time.sleep(attente); attente *= 2
    return None


# ───────────────────────────────────────────────────────── SITE (arborescence)
class Site:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
        self.token = None

    def _req(self, url, data=None, hdr=None, method=None):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("User-Agent", UA)
        for k, v in (hdr or {}).items():
            req.add_header(k, v)
        try:
            with self.op.open(req, timeout=30) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def _cookie(self, nom):
        for c in self.cj:
            if c.name == nom:
                return c.value
        return None

    def connexion(self, identifiant, mdp):
        self._req(f"{SITE}/login")
        self.token = self._cookie("csrf_token")
        if not self.token:
            raise RuntimeError("jeton csrf introuvable")
        corps = json.dumps({
            "username": identifiant, "password": mdp,
            "fingerprint": {"browser": {"name": "Chrome", "version": "126"}},
        }).encode()
        st, rep = self._req(f"{SITE}/login", corps, {
            "Content-Type": "application/json", "Accept": "application/json",
            "X-CSRF-Token": self.token, "Referer": f"{SITE}/login",
            "Origin": SITE, "X-Requested-With": "XMLHttpRequest",
        }, method="POST")
        ok = False
        try:
            ok = bool(json.loads(rep).get("success"))
        except Exception:
            pass
        if not ok or not self._cookie("session_token"):
            raise RuntimeError(f"connexion refusee : {rep[:160]}")
        return True

    def enfants_ids(self, parent_id, ancetres):
        _cadence()
        chemin = f"/files?folder_id={parent_id}" if parent_id else "/files"
        st, html = self._req(f"{SITE}{chemin}")
        if st != 200:
            return []
        vus, out = set(), []
        for m in re.finditer(r"folder_id=(\d+)", html):
            i = int(m.group(1))
            if i and i != parent_id and i not in ancetres and i not in vus:
                vus.add(i); out.append(i)
        return out


def arbre_du_site(site, noms):
    """{fld_id(str): "Chemin/Sous-chemin"} — imbrication lue niveau par niveau
       sur le site, noms pris dans la carte de l'API."""
    out = {}
    niveau = [(0, "", frozenset({0}))]
    prof = 0
    while niveau and prof <= 6:
        suivant = []
        for (fid, chem, anc) in niveau:
            for cid in site.enfants_ids(fid, anc):
                nom = noms.get(cid, "").strip()
                if not nom:
                    continue
                complet = f"{chem}/{nom}" if chem else nom
                out[str(cid)] = complet
                suivant.append((cid, complet, anc | {cid, fid}))
        niveau = suivant
        prof += 1
    return out


def fichiers_du_dossier(cle, fld_id, nom_dossier):
    out, page = [], 1
    while page <= 200:
        params = {"page": page, "per_page": PAR_PAGE}
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
            nom = (f.get("title") or f.get("name") or "").strip()
            out.append({
                "code": code,
                "titre": nom,
                "nom": nom,
                "q": 0,
                "taille": 0,
                "duree": int(f.get("length") or 0) * 60,
                "lib": "perso",
                "dossier": nom_dossier,
            })
        if page >= int(res.get("total_pages") or 1):
            break
        page += 1
    return out


def main():
    site_creds = os.environ.get("VIDARA_SITE", "").strip()
    cle = os.environ.get("VIDARA_API_KEY", "").strip()
    if not site_creds or not cle:
        print("VIDARA_SITE et/ou VIDARA_API_KEY absents — rien a faire.", file=sys.stderr)
        return 1
    identifiant, _, mdp = site_creds.partition(":")
    if not mdp:
        print("VIDARA_SITE doit etre 'identifiant:motdepasse'.", file=sys.stderr)
        return 1

    # 1. noms des dossiers (API, a plat)
    d = api("/folder/list", {}, cle)
    plats = ((d or {}).get("result") or {}).get("folders") or []
    noms = {}
    for f in plats:
        try:
            noms[int(f.get("fld_id") or f.get("code"))] = (f.get("name") or "").strip()
        except (TypeError, ValueError):
            continue

    # 2. arborescence via le site
    site = Site()
    site.connexion(identifiant.strip(), mdp)
    arbre = arbre_du_site(site, noms)
    print(f"  {len(arbre)} dossier(s) : " + ", ".join(sorted(arbre.values())[:8])
          + (" ..." if len(arbre) > 8 else ""))

    # 3. fichiers : dossiers d'abord, racine (reliquat) ensuite, dedup par code
    items, vus = [], set()
    lot = []
    for fid, nom in sorted(arbre.items(), key=lambda kv: kv[1].lower()):
        lot += fichiers_du_dossier(cle, fid, nom)
    lot += fichiers_du_dossier(cle, None, "")   # sans fld_id = tout le compte -> reliquat
    for it in lot:
        if it["code"] in vus:
            continue
        vus.add(it["code"]); items.append(it)
    print(f"  {len(lot)} fichiers lus, {len(items)} retenus")

    if not items:
        print("aucun fichier — index NON reecrit.", file=sys.stderr)
        return 1

    items.sort(key=lambda x: (x["lib"], x["titre"].lower()))
    doc = {
        "genere": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": len(items),
        "items": items,
    }
    os.makedirs(os.path.dirname(SORTIE), exist_ok=True)
    with open(SORTIE, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    print(f"data/vidara.json : {len(items)} entrees")
    return 0


if __name__ == "__main__":
    sys.exit(main())
