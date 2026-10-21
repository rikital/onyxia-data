ageverify/", "/age-verify/", "/?age=1", "/?ageok=1"):
        _lire("https://www.eporner.com" + chemin, "chemin portail")
        h = retenter(f"chemin {chemin}")
        if h:
            return h

    print("      AUCUN contournement n'a fonctionne", flush=True)
    return None


def _trouver_videos(html):
    morceaux = re.split(r'data-id="(\d+)"', html)
    trouvees = []
    for i in range(1, len(morceaux) - 1, 2):
        numid, bloc = morceaux[i], morceaux[i + 1]
        m_lien = re.search(r'href="[^"]*?/video-([A-Za-z0-9]+)/([^/"]+)/"', bloc)
        if not m_lien:
            continue                       # carte de photo, pas de video
        m_alt = re.search(r'alt="([^"]*)"', bloc)
        m_vig = re.search(r'(?:data-src|src)="(https://[^"]*?\.jpg)"', bloc)
        m_dur = re.search(r'class="mbtim"[^>]*>(\d{1,3}):(\d{2})', bloc)
        titre = html_mod.unescape((m_alt.group(1) if m_alt else "").strip())
        if not titre:
            titre = m_lien.group(2).replace("-", " ")
        duree = int(m_dur.group(1)) * 60 + int(m_dur.group(2)) if m_dur else 0
        trouvees.append((m_lien.group(1), numid, titre, duree,
                         m_vig.group(1) if m_vig else ""))
    return trouvees


def lien_vivant(url):
    for methode in ("HEAD", "GET"):
        for essai in range(3):
            try:
                req = urllib.request.Request(url, method=methode, headers=ENTETES)
                if methode == "GET":
                    req.add_header("Range", "bytes=0-2047")
                with OUVREUR.open(req, timeout=DELAI) as r:
                    return 200 <= r.status < 300
            except urllib.error.HTTPError as e:
                if e.code in (405, 501):
                    break
                if e.code in REESSAYABLES and essai < 2:
                    time.sleep(1.0 * (essai + 1))
                    continue
                return False
            except Exception:
                if essai < 2:
                    time.sleep(0.8 * (essai + 1))
                    continue
                break
    return False


def resoudre(hash_video, numid):
    for q in QUALITES:
        url = f"https://www.eporner.com/dload/{hash_video}/{q}/{numid}-{q}p.mp4"
        if not VERIFIER or lien_vivant(url):
            return url
    return None


def _pages_eporner(source):
    base = source.rstrip("/")
    urls = [source]
    for n in range(2, PAGES_EPORNER + 1):
        urls.append(f"{base}/{n}/")
    return urls


def _lire_page(url):
    h = _lire(url, url)
    if h and _portail(h):
        if _franchir_portail(h):
            h = _lire(url, url)
    return h


def bloc_eporner():
    print("Eporner :", flush=True)
    accueil = _lire("https://www.eporner.com/", "accueil")
    if not accueil:
        print("  [!] accueil injoignable", flush=True)
        return []
    if _portail(accueil) and not _franchir_portail(accueil):
        print("  [!] portail d'age non franchi", flush=True)
        return []
    print(f"  cookies en session : {len(POT_A_COOKIES)}", flush=True)

    urls = []
    for source in SOURCES_EPORNER:
        urls.extend(_pages_eporner(source))
    print(f"  {len(urls)} pages a lire", flush=True)

    vues = {}
    for debut in range(0, len(urls), THREADS_EPO):
        lot = urls[debut:debut + THREADS_EPO]
        with ThreadPoolExecutor(max_workers=THREADS_EPO) as pool:
            for i, page in enumerate(pool.map(_lire_page, lot), start=debut + 1):
                if not page:
                    print(f"  page {i:2d} : vide", flush=True)
                    continue
                trouvees = _trouver_videos(page)
                print(f"  page {i:2d} : {len(trouvees):3d} videos", flush=True)
                for hash_video, numid, titre, duree, vignette in trouvees:
                    if numid not in vues:
                        vues[numid] = (hash_video, titre, duree, vignette)
        time.sleep(PAUSE_EPO)

    print(f"  {len(vues)} videos distinctes", flush=True)
    lignes = []
    for numid, (hash_video, titre, duree, vignette) in vues.items():
        url = resoudre(hash_video, numid)
        if url:
            lignes.extend(_entree(titre, duree, vignette, url))
    print(f"  {len(lignes) // 2} entrees gardees", flush=True)
    return lignes
