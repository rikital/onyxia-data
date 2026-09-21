#!/usr/bin/env python3
"""refresh_m6_fix_slugs.py — repare les entrees M6 en "slug seul" dans data-replay-m6.m3u.

Tourne APRES refresh_m6.py. Le scraper de sections M6+ (Films/Series/Telefilms)
emet des URL `m6play://<slug>` SANS service ni id numerique. Or l'app resout
via `/services/<service>/programs/<id>/videos` -> une URL slug-seul construit
`/services/<slug>/programs/<slug>/videos` = INVALIDE -> l'entree s'affiche mais
ne joue AUCUN episode. ~200 programmes morts.

FIX (sans reseau par-service) :
  1. slug -> id numerique via le sitemap officiel m6.fr/sitemap-program.xml
  2. id -> service correct via UN appel middleware (champ service_display.code,
     le lookup via m6replay marche comme lookup universel)
  3. reecrit l'URL en `m6play://<service>/<id>` (= exactement ce que l'app sait jouer)
Dedup : si le service/id existe deja ailleurs dans le fichier -> on SUPPRIME
l'entree slug (le programme reste joignable via l'autre entree).
Non-resolvable (absent du sitemap / lookup KO) -> entree SUPPRIMEE (elle etait
de toute facon morte -> aucune regression, revient des qu'elle redevient resolvable).

GARDE-FOUS (anti-casse) :
  - sitemap KO ou < MIN_SITEMAP programmes -> on NE TOUCHE PAS le fichier.
  - si le taux d'echec des lookups depasse MAX_FAIL_RATIO -> on ABANDONNE et on
    laisse le fichier INTACT (protege contre une panne middleware qui viderait tout).
"""
import re, sys, os, time, json, gzip, urllib.request

HERE = os.path.dirname(__file__)
M3U = os.path.join(HERE, "..", "data-replay-m6.m3u")
SITEMAP = "https://www.m6.fr/sitemap-program.xml"
MW = "https://pc.middleware.6play.fr/6play/v2/platforms/m6group_web/services/m6replay/programs"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
MIN_SITEMAP = 400
MAX_FAIL_RATIO = 0.5
VALID_SERVICES = {"m6replay", "w9replay", "6terreplay", "gulli", "tevareplay", "parispremierereplay"}


def http_get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data.decode("utf-8", "replace")
        except Exception as e:
            if i == tries - 1:
                print(f"  [warn] GET {url[:70]} -> {e}", file=sys.stderr)
                return ""
            time.sleep(1.0)
    return ""


def load_sitemap_slug_to_id():
    xml = http_get(SITEMAP)
    m = dict(re.findall(r'm6\.fr/([a-z0-9-]+)-p_(\d+)', xml))
    return m


_service_cache = {}
def service_for_id(pid):
    if pid in _service_cache:
        return _service_cache[pid]
    txt = http_get(f"{MW}/{pid}?with=links", tries=2)
    svc = None
    if txt:
        try:
            svc = ((json.loads(txt).get("service_display") or {}).get("code")) or None
        except Exception:
            svc = None
    if svc not in VALID_SERVICES:
        svc = None
    _service_cache[pid] = svc
    return svc


def main():
    if not os.path.exists(M3U):
        print(f"[STOP] {M3U} absent -> rien a faire.")
        return
    content = open(M3U, encoding="utf-8").read()
    lines = content.split("\n")

    slug2id = load_sitemap_slug_to_id()
    print(f"Sitemap : {len(slug2id)} programmes (slug->id)")
    if len(slug2id) < MIN_SITEMAP:
        print(f"[STOP] sitemap KO ({len(slug2id)} < {MIN_SITEMAP}) -> fichier INTACT.")
        return

    # 1er passage : reperer les slugs a resoudre
    slug_re = re.compile(r'^m6play://([^/\n]+)$')
    todo = []  # (line_index, slug)
    for i, l in enumerate(lines):
        m = slug_re.match(l)
        if m:
            todo.append((i, m.group(1)))
    print(f"Entrees slug-seul a traiter : {len(todo)}")
    if not todo:
        print("Rien a reparer.")
        return

    # resoudre
    fixed = {}   # line_index -> new_url  (ou None = supprimer)
    fail = 0
    for idx, slug in todo:
        pid = slug2id.get(slug)
        if not pid:
            fixed[idx] = None          # absent du sitemap -> drop
            continue
        svc = service_for_id(pid)
        if not svc:
            fail += 1
            fixed[idx] = None          # lookup KO -> drop
            continue
        # on REECRIT (playable) meme si un numerique identique existe ailleurs :
        # l'entree reste dans sa section M6+ (Nouveautes/Drame/...) = multi-rail voulu.
        fixed[idx] = f"m6play://{svc}/{pid}"
        time.sleep(0.05)

    ratio = fail / max(1, len(todo))
    print(f"Lookups en echec : {fail}/{len(todo)} ({ratio:.0%})")
    if ratio > MAX_FAIL_RATIO:
        print(f"[STOP] trop d'echecs (> {MAX_FAIL_RATIO:.0%}) -> fichier INTACT (probable panne middleware).")
        return

    # reconstruire : pour chaque entree slug, soit reecrire l'URL, soit supprimer
    # le couple (EXTINF precedent + ligne URL).
    drop_idx = set()
    n_fix = n_drop = 0
    for idx, new_url in fixed.items():
        if new_url is None:
            drop_idx.add(idx)
            if idx - 1 >= 0 and lines[idx - 1].startswith("#EXTINF"):
                drop_idx.add(idx - 1)
            n_drop += 1
        else:
            lines[idx] = new_url
            n_fix += 1

    out = [l for i, l in enumerate(lines) if i not in drop_idx]
    open(M3U, "w", encoding="utf-8").write("\n".join(out))
    print(f"Reparees (slug->service/id, jouables) : {n_fix}")
    print(f"Supprimees (irrecuperables) : {n_drop}")


if __name__ == "__main__":
    main()
