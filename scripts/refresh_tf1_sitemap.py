#!/usr/bin/env python3
"""refresh_tf1_sitemap.py — COMPLETE data-replay-tf1.m3u via les SITEMAPS TF1+.

Tourne APRES refresh_tf1.py (qui produit data-replay-tf1.m3u avec de belles
jaquettes JSON-LD). Ce script lit ce fichier, puis via les sitemaps video
officiels de TF1 (maj quotidienne) AJOUTE uniquement les programmes que
l'ancien a rates -> complement SANS DOUBLON (dedup par `tf1plus://<chan>/<slug>`).

2026-09-14 (user : « ton dossier chaine melange un peu tout film serie, il fait
un peu n'importe quoi » puis « le but c'est de le rendre lisible et faire la
part des choses ») — REFONTE de la sortie :

  AVANT : group-title="Replay TF1+ Complement - <CHAINE>", titre reconstruit
          depuis le slug ("Alibi Com", "Lenfant Que Lon Ma Vole"), et
          tvg-type="series" EN DUR pour tout — y compris les films, que l'app
          se mettait donc a traiter comme des series (saisons/episodes).
          Resultat cote app : une liste a plat de 1475 entrees par chaine,
          films + series + emissions melanges, illisible.

  APRES : on exploite ce que le sitemap fournit deja et que l'ancien jetait :
          <video:title>, <video:category> (= nom du programme),
          <video:duration>, <video:expiration_date>. On classe chaque
          programme en Films / Series / Emissions et on sort directement sous
          les axes que l'app connait deja :
              group-title="Replay TF1+ <Axe> - Catalogue"
          Aucun changement cote app : son motif accepte deja
          "Replay TF1+ (Films|Series|Emissions) - .*".

  Mesure sur les 1541 programmes du complement : Films 1258, Series 169,
  Emissions 114, 0 perime.

Methode fine : ~4 requetes XML (sitemaps France), aucune requete par video.

SECURITE : si les sitemaps ne renvoient rien / trop peu, on n'ajoute rien
(le fichier de l'ancien scraper reste intact).
"""
import re, sys, os, time, html, gzip, datetime, urllib.request

SITEMAP_INDEX = "https://www.tf1.fr/sitemaps/sitemap-v.xml"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36")
KEEP_CHANNELS = {"tf1", "tmc", "tfx", "tf1-series-films", "lci"}
OUT = os.path.join(os.path.dirname(__file__), "..", "data-replay-tf1.m3u")
MIN_PROGRAMS = 500  # garde-fou : sous ce seuil on considere le sitemap KO
NOW = datetime.datetime.now(datetime.timezone.utc)


def http_get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Referer": "https://www.tf1.fr/"})
            raw = urllib.request.urlopen(req, timeout=120).read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", "replace")
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def slug_to_title(slug):
    s = re.sub(r"-\d{3,}$", "", slug)
    return " ".join(w.capitalize() for w in s.split("-")) if s else slug


def _tag(name):
    return re.compile(r"<%s[^>]*>([^<]*)</%s>" % (name, name))


URL_BLOCK = re.compile(r"<url>(.*?)</url>", re.S)
LOC_RX    = _tag("loc")
THUMB_RX  = _tag("video:thumbnail_loc")
TITLE_RX  = _tag("video:title")
CAT_RX    = _tag("video:category")
DUR_RX    = _tag("video:duration")
EXP_RX    = _tag("video:expiration_date")
PATH_RX   = re.compile(r"tf1\.fr/([a-z0-9-]+)/([a-z0-9-]+)/videos?/")

# Mots-cles qui font d'un programme une EMISSION quel que soit sa duree
# (sinon "Podcasts TF1" (399 videos jusqu'a 85 min) tombait en Series, et
#  "Les reportages de Martin Weill" aussi).
EMISSION_RX = re.compile(
    r"podcast|reportage|\bjt\b|journal|m[ée]t[ée]o|magazine|"
    r"[ée]mission|t[ée]l[ée]-?r[ée]alit[ée]|\blive\b|c[ée]r[ée]monie|"
    r"concert|debat|d[ée]bat|talk|interview|bonus|coulisses", re.I)

# Titres d'episode : "Episode 12", "Saison 3", "S01E04", "Ep. 7"
EPISODE_RX = re.compile(
    r"episode|saison|\bs\d{1,2}\s*e\d{1,2}\b|\bep\.?\s*\d+", re.I)


def discover_france_sitemaps():
    idx = http_get(SITEMAP_INDEX)
    locs = LOC_RX.findall(idx)
    fr = [u for u in locs if not re.search(r"-fr-[a-z]{2}\d+\.xml$", u)]
    if not fr:
        fr = [f"https://www.tf1.fr/sitemaps/sitemap-v{n}.xml" for n in (1, 2, 3, 4)]
    return fr


def collect_sitemap_programs():
    """(chan,prog) -> {poster, nom, n, durees, titres, vivant}"""
    progs = {}
    for sm in discover_france_sitemaps():
        try:
            xml = http_get(sm)
        except Exception as e:
            print(f"  [!] {sm}: {e}", file=sys.stderr)
            continue
        n = 0
        for block in URL_BLOCK.findall(xml):
            loc = LOC_RX.search(block)
            if not loc:
                continue
            m = PATH_RX.search(loc.group(1))
            if not m:
                continue
            chan, prog = m.group(1), m.group(2)
            if chan not in KEEP_CHANNELS:
                continue
            d = progs.get((chan, prog))
            if d is None:
                d = progs[(chan, prog)] = {
                    "poster": "", "nom": "", "n": 0,
                    "durees": [], "titres": [], "vivant": 0}
            d["n"] += 1
            if not d["poster"]:
                t = THUMB_RX.search(block)
                if t:
                    d["poster"] = t.group(1)
            if not d["nom"]:
                c = CAT_RX.search(block)
                if c and c.group(1).strip():
                    d["nom"] = html.unescape(c.group(1)).strip()
            if len(d["titres"]) < 8:
                t = TITLE_RX.search(block)
                if t:
                    d["titres"].append(html.unescape(t.group(1)))
            du = DUR_RX.search(block)
            if du:
                try:
                    d["durees"].append(int(du.group(1)))
                except ValueError:
                    pass
            ex = EXP_RX.search(block)
            if ex:
                try:
                    if datetime.datetime.fromisoformat(ex.group(1)) > NOW:
                        d["vivant"] += 1
                except Exception:
                    d["vivant"] += 1
            else:
                d["vivant"] += 1
            n += 1
        print(f"  {sm.split('/')[-1]}: {n} videos (chaines TF1)")
        del xml
    return progs


def classer(d):
    """-> ('Films'|'Series'|'Emissions')"""
    nom = d["nom"] or ""
    if EMISSION_RX.search(nom):
        return "Emissions"
    durees = sorted(d["durees"]) or [0]
    mediane = durees[len(durees) // 2]
    maxi = durees[-1]
    # Un film : long, et le programme ne compte qu'une poignee de videos
    # (une fiche film = le film + parfois sa bande-annonce).
    if maxi >= 70 * 60 and d["n"] <= 3:
        return "Films"
    episodes = sum(1 for t in d["titres"] if EPISODE_RX.search(t))
    if episodes or (d["n"] >= 8 and 15 * 60 <= mediane <= 60 * 60):
        return "Series"
    return "Emissions"


def read_existing_urls(path):
    urls = set()
    if not os.path.exists(path):
        return urls
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("tf1plus://"):
                urls.add(line)
    return urls


def main():
    t0 = time.time()
    print("=== TF1+ complement via sitemaps ===")
    programs = collect_sitemap_programs()
    print("=" * 52)
    print(f"Programmes sitemap uniques : {len(programs)}")
    if len(programs) < MIN_PROGRAMS:
        print(f"[STOP] sitemap KO ({len(programs)} < {MIN_PROGRAMS}) "
              f"-> aucun ajout, fichier intact.")
        return
    existing = read_existing_urls(OUT)
    print(f"Programmes deja presents   : {len(existing)}")

    added = []
    compte = {"Films": 0, "Series": 0, "Emissions": 0}
    perimes = 0
    for (chan, prog), d in sorted(programs.items()):
        url = f"tf1plus://{chan}/{prog}"
        if url in existing:
            continue
        if d["vivant"] == 0:          # toutes les videos ont expire
            perimes += 1
            continue
        axe = classer(d)
        compte[axe] += 1
        titre = d["nom"] or slug_to_title(prog)
        libelle = {"Films": "Films", "Series": "Séries",
                   "Emissions": "Émissions"}[axe]
        group = f"Replay TF1+ {libelle} - Catalogue"
        tvg_type = "movie" if axe == "Films" else "series"
        added.append(
            f'#EXTINF:-1 tvg-id="tf1plus-{chan}-{prog}-smx" '
            f'tvg-logo="{d["poster"]}" tvg-country="FR" '
            f'tvg-type="{tvg_type}" '
            f'group-title="{group}",{titre}')
        added.append(url)

    if not added:
        print("Rien a ajouter (tout deja present).")
    else:
        header = "" if os.path.exists(OUT) else "#EXTM3U\n"
        with open(OUT, "a", encoding="utf-8") as f:
            if header:
                f.write(header)
            f.write("\n" + "\n".join(added) + "\n")

    print(f"Programmes AJOUTES (complement) : {len(added)//2}")
    print(f"  Films     : {compte['Films']}")
    print(f"  Series    : {compte['Series']}")
    print(f"  Emissions : {compte['Emissions']}")
    print(f"  Ecartes (tout perime) : {perimes}")
    print(f"Duree : {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
