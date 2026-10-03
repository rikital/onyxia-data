"""Règles de nom de chaîne de l'application (OlaTvProvider.kt / IptvLangFilter.kt), portées
en Python pour que le registre OLA arrive DÉJÀ nettoyé et regroupé : sur une TV modeste,
passer 9 000 noms dans ces expressions prenait ~30 s (mesuré sur TCL, 2026-10-03).

À garder IDENTIQUE au code Kotlin : norm(), baseDisplayName(), extractVariantLabel(),
IptvLangFilter.isFrCompatible().
"""
import re

_I = re.I

RX_E = re.compile(r"[éèêë]")
RX_A = re.compile(r"[àâä]")
RX_U = re.compile(r"[ùûü]")
RX_II = re.compile(r"[îï]")
RX_O = re.compile(r"[ôö]")
RX_CROCHETS = re.compile(r"\[.*?]")
RX_PARENTHESES = re.compile(r"\(.*?\)")
RX_TAG_FR = re.compile(r"^[^A-Za-z0-9]*(FR|FRA)[\s|:┃│\]\)\-]+", _I)
RX_PREFIXE_FR = re.compile(r"^\s*(FR|France)\s*[:|\-]\s*", _I)
RX_NORM_MID = re.compile(
    r"\b(hd|sd|fhd|uhd|4k|raw|hevc|h\.?265|ppv|ott|test|backup|fhdr|sdr)\b"
    r"|\blive\b(?!\s*\d)"
    r"|(?:\+\s?1)|(?:1080p|720p|480p|360p)"
)
RX_NORM_FIN = re.compile(r"\s+(fr|french|francais|belgique|be|suisse|ch|lux)\s*$")
RX_POINT_FR = re.compile(r"\.fr\b", _I)
RX_ESPACES = re.compile(r"\s+")
RX_NON_ALNUM = re.compile(r"[^a-z0-9]")
RX_VARIANTE_QUALITE = re.compile(
    r"\b(HD|SD|FHD|UHD|4K|RAW|HEVC|H\.?265|PPV|OTT|LIVE|FHDR|SDR)\b"
    r"|(?:\+\s?1)|(?:1080p|720p|480p|360p)", _I)
RX_VARIANTE_PAYS = re.compile(r"\b(FRANCE|FR|FRENCH|FRANCAIS|BELGIQUE|BE|SUISSE|CH|LUX)\b", _I)
RX_AFFICHE_MID = re.compile(
    r"\b(HD|SD|FHD|UHD|4K|RAW|HEVC|H\.?265|PPV|OTT|LIVE|TEST|BACKUP|FHDR|SDR)\b"
    r"|(?:\+\s?1)|(?:1080p|720p|480p|360p)", _I)
RX_AFFICHE_FIN = re.compile(r"\s+(FR|French|Francais|Belgique|BE|Suisse|CH|LUX)\s*$", _I)
RX_PUCES = re.compile(r"^[\-•●○▪►•‣⮞›»•]+\s*")

# IptvLangFilter
RX_FR_MARQUEUR = re.compile(r"(^|[\s|\[\(])(fr|fra|french|francais|français|france)([\s|:\-\]\)]|$)")
RX_PREFIXE_NON_FR = re.compile(
    r"^[|\[\(]?(de|en|es|pt|it|ar|tr|nl|pl|ro|us|uk|ru|gr|hu|cz|jp|cn|hr|sr|sk|bg|fi|se|no|dk)[|:\-\]\)\s]")
RX_MOT_NON_FR = re.compile(
    r"\b(deutsch|english|spanish|portuguese|italiano|italian|arabic|turkish|"
    r"dutch|polish|romanian|russian|greek|hungarian|czech|japanese|chinese|"
    r"hindi|croatian|serbian|slovak|bulgarian|finnish|swedish|norwegian|danish)\b")


def _sans_accents(s):
    s = RX_E.sub("e", s)
    s = RX_A.sub("a", s)
    s = RX_U.sub("u", s)
    s = RX_II.sub("i", s)
    s = RX_O.sub("o", s)
    return s.replace("ç", "c")


def fr_compatible(nom):
    if not nom.strip():
        return False
    n = nom.lower()
    if RX_FR_MARQUEUR.search(n):
        return True
    if RX_PREFIXE_NON_FR.search(n) or RX_MOT_NON_FR.search(n):
        return False
    return True


def cle(nom):
    s = _sans_accents(nom.lower())
    s = RX_CROCHETS.sub(" ", s)
    s = RX_PARENTHESES.sub(" ", s)
    s = RX_TAG_FR.sub("", s, count=1)
    s = RX_PREFIXE_FR.sub("", s, count=1)
    while True:
        n = RX_NORM_MID.sub(" ", s)
        n = RX_NORM_FIN.sub("", n)
        n = RX_POINT_FR.sub("", n)
        n = RX_ESPACES.sub(" ", n).strip()
        if n == s:
            break
        s = n
    s = s.replace("+", "plus").replace("&", "and")
    return RX_NON_ALNUM.sub("", s).replace("sports", "sport")


def nom_affiche(nom):
    s = RX_CROCHETS.sub(" ", nom)
    s = RX_PARENTHESES.sub(" ", s)
    s = RX_TAG_FR.sub("", s, count=1)
    s = RX_PREFIXE_FR.sub("", s, count=1)
    s = RX_PUCES.sub("", s, count=1)
    while True:
        n = RX_AFFICHE_MID.sub(" ", s)
        n = RX_AFFICHE_FIN.sub("", n)
        n = RX_POINT_FR.sub("", n)
        n = RX_ESPACES.sub(" ", n).strip()
        if n == s:
            break
        s = n
    return s or nom


def variante(nom):
    parts = [m.group(0).upper() for m in RX_VARIANTE_QUALITE.finditer(nom)]
    parts += [m.group(0).upper() for m in RX_VARIANTE_PAYS.finditer(nom)]
    vus, out = set(), []
    for p in parts:
        if p not in vus:
            vus.add(p)
            out.append(p)
    return " ".join(out)


def regrouper(chaines_brutes, max_par_cle=100):
    """chaines_brutes : {nom: [[i, cmd], ...]} (i = rang du portail, 0 = meilleur).
    Renvoie {cle: {"d": nom affiché, "s": [[i, cmd, variante], ...]}} — sources triées par rang
    de portail, dédoublonnées, au plus max_par_cle par chaîne."""
    par_cle = {}
    for nom, sources in chaines_brutes.items():
        if not fr_compatible(nom):
            continue
        k = cle(nom)
        if not k:
            continue
        v = variante(nom)
        g = par_cle.get(k)
        if g is None:
            g = par_cle[k] = {"d": nom_affiche(nom), "s": []}
        g["s"].extend([i, cmd, v] for i, cmd in sources)
    for g in par_cle.values():
        vus, garde = set(), []
        for src in sorted(g["s"], key=lambda x: x[0]):
            cle_src = (src[0], src[1])
            if cle_src in vus:
                continue
            vus.add(cle_src)
            garde.append(src)
            if len(garde) >= max_par_cle:
                break
        g["s"] = garde
    return par_cle


if __name__ == "__main__":
    for t in ["TF1 FHD", "┃FR┃ TF1 HD", "FRANCE 2 HD", "France 2", "Canal+ Sport 360 4K",
              "TF1 +1", "M6 HEVC", "Canal+ Live 1", "DE| Arte", "BFM TV", "TF1 Séries Films"]:
        print(f"{t!r:28} cle={cle(t)!r:20} affiche={nom_affiche(t)!r:20} var={variante(t)!r} fr={fr_compatible(t)}")
