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
class LectureIncomplete(RuntimeError):
    """Une page ou un dossier n'a pas pu etre lu : l'index serait troue."""


def api(chemin, params, cle, essais=8):
    # 2026-10-26 : AVANT, un 429 repete rendait None en silence -> le dossier
    #   s'arretait a la page 1 (100 fichiers) et le reste tombait "sans dossier".
    #   Desormais on attend plus longtemps et, en cas d'echec final, on LEVE.
    q = dict(params); q["api_key"] = cle
    url = f"{API}{chemin}?" + urllib.parse.urlencode(q)
    attente = 3.0
    for n in range(essais):
        _cadence()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            print(f"    http {e.code} sur {chemin} {params}, reessai dans {attente:.0f}s", file=sys.stderr)
        except Exception as e:
            print(f"    {type(e).__name__} sur {chemin} {params}, reessai dans {attente:.0f}s", file=sys.stderr)
        if n < essais - 1:
            time.sleep(attente); attente = min(attente * 2, 60.0)
    raise LectureIncomplete(f"API {chemin} {params} illisible apres {essais} essais")


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
        # 2026-10-26 : AVANT, une page en erreur rendait [] en silence -> des
        #   dossiers entiers (Guerre, Linkin Park...) disparaissaient de l'index.
        chemin = f"/files?folder_id={parent_id}" if parent_id else "/files"
        attente, st, html = 3.0, 0, ""
        for n in range(6):
            _cadence()
            try:
                st, html = self._req(f"{SITE}{chemin}")
            except Exception as e:
                st, html = 0, str(e)
            if st == 200:
                break
            print(f"    site {chemin} -> {st}, reessai dans {attente:.0f}s", file=sys.stderr)
            time.sleep(attente); attente = min(attente * 2, 60.0)
        if st != 200:
            raise LectureIncomplete(f"page {chemin} illisible (http {st})")
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
        if not isinstance(d, dict) or not isinstance(d.get("result"), dict):
            raise LectureIncomplete(f"video/list {params} : reponse inattendue {str(d)[:120]}")
        res = d["result"]
        arr = res.get("videos") or []
        if not arr:
            break
        deja = {x["code"] for x in out}
        if all((f.get("filecode") or f.get("file_code") or f.get("code") or "").strip() in deja for f in arr):
            break   # l'API renvoie la meme page : on a tout
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
                # 2026-10-26 : date de depot + numero interne (croissant) -> rangee
                #   « Derniers ajouts » d'ONYX Films, triee du plus recent au plus ancien.
                "ajout": (f.get("uploaded") or "").strip(),
                "vid": int(f.get("vid_id") or 0),
            })
        # Page pleine = il y en a peut-etre d'autres, meme si total_pages manque.
        if len(arr) < PAR_PAGE and page >= int(res.get("total_pages") or 1):
            break
        page += 1
    return out


def index_precedent():
    try:
        with open(SORTIE, encoding="utf-8") as fh:
            return json.load(fh).get("items") or []
    except Exception:
        return []


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
    try:
        noms = {}
        for page in range(1, 21):   # la liste peut etre paginee : on lit tant qu'il en vient
            d = api("/folder/list", {"page": page, "per_page": PAR_PAGE}, cle)
            plats = ((d or {}).get("result") or {}).get("folders") or []
            avant = len(noms)
            for f in plats:
                try:
                    noms[int(f.get("fld_id") or f.get("code"))] = (f.get("name") or "").strip()
                except (TypeError, ValueError):
                    continue
            if len(noms) == avant or len(plats) < PAR_PAGE:
                break
        if not noms:
            raise LectureIncomplete("aucun nom de dossier")

        # 2. arborescence via le site
        site = Site()
        site.connexion(identifiant.strip(), mdp)
        arbre = arbre_du_site(site, noms)
        print(f"  {len(arbre)} dossier(s) : " + ", ".join(sorted(arbre.values())[:8])
              + (" ..." if len(arbre) > 8 else ""))

        # 3. fichiers : dossiers d'abord, racine (reliquat) ensuite, dedup par code
        lot = []
        for fid, nom in sorted(arbre.items(), key=lambda kv: kv[1].lower()):
            lot += fichiers_du_dossier(cle, fid, nom)
        lot += fichiers_du_dossier(cle, None, "")   # sans fld_id = tout le compte -> reliquat
    except LectureIncomplete as e:
        # Mieux vaut l'index d'hier, complet, qu'un index troue.
        print(f"lecture incomplete ({e}) — index NON reecrit.", file=sys.stderr)
        return 1

    items, vus = [], set()
    for it in lot:
        if it["code"] in vus:
            continue
        vus.add(it["code"]); items.append(it)
    print(f"  {len(lot)} fichiers lus, {len(items)} retenus")

    if not items:
        print("aucun fichier — index NON reecrit.", file=sys.stderr)
        return 1

    # 4. filet de securite : un fichier « sans dossier » qui etait range hier
    #    garde son dossier d'hier (un dossier rate ne doit pas vider le rangement).
    ancien = {x.get("code"): x.get("dossier") for x in index_precedent() if x.get("dossier")}
    recases = 0
    for it in items:
        if not it["dossier"] and ancien.get(it["code"]):
            it["dossier"] = ancien[it["code"]]; recases += 1
    sans = sum(1 for it in items if not it["dossier"])
    print(f"  {recases} fichier(s) remis dans leur dossier d'hier, {sans} sans dossier")

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
