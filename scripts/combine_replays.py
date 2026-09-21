#!/usr/bin/env python3
"""combine_replays.py — concatène les 5 m3u par site en 1 seul data-replay.m3u.

Stratégie tolérante : si un m3u manque (= workflow d'un site pas encore tourné),
on garde le contenu actuel du data-replay.m3u pour CE site (= aucune
régression). Permet à l'app de continuer à recevoir le contenu des 4 autres
sites même si l'un est en panne.
"""
import os, re, sys
from pathlib import Path

SITES = [
    ("data-replay-bfm.m3u",      "BFM/RMC",         "bfmplay|bfmlive"),
    ("data-replay-tf1.m3u",      "TF1+",            "tf1plus|tf1live"),
    ("data-replay-m6.m3u",       "M6+",             "m6play"),
    ("data-replay-francetv.m3u", "France.tv",       "francetv"),
    ("data-replay-arte.m3u",     "Arte",            "arte"),
]
OUTPUT = os.environ.get("OUTPUT", "data-replay.m3u")


def parse_m3u_blocks(content):
    """Découpe un m3u en blocs (#EXTINF + URL). Retourne list[str]."""
    if not content:
        return []
    blocks = []
    lines = content.split("\n")
    i = 0
    if lines and lines[0].startswith("#EXTM3U"):
        i = 1
    while i < len(lines):
        if lines[i].startswith("#EXTINF"):
            block = [lines[i]]
            j = i + 1
            while j < len(lines) and lines[j].startswith("#") and not lines[j].startswith("#EXTINF"):
                block.append(lines[j])
                j += 1
            if j < len(lines) and lines[j].strip():
                block.append(lines[j])
                blocks.append("\n".join(block))
                i = j + 1
            else:
                i += 1
        else:
            i += 1
    return blocks


def extract_site_blocks(content, url_pattern):
    """Filtre les blocs dont l'URL matche un site donné."""
    pattern = re.compile(f"^({url_pattern})://", re.MULTILINE)
    result = []
    for b in parse_m3u_blocks(content):
        last_line = b.split("\n")[-1].strip()
        if pattern.match(last_line):
            result.append(b)
    return result


def main():
    # Lit l'ancien data-replay.m3u pour fallback si un site manque
    old_content = ""
    if Path(OUTPUT).exists():
        old_content = Path(OUTPUT).read_text(encoding="utf-8")

    combined = ["#EXTM3U"]
    stats = []

    for filename, label, url_pattern in SITES:
        path = Path(filename)
        if path.exists():
            content = path.read_text(encoding="utf-8")
            blocks = parse_m3u_blocks(content)
            stats.append(f"  {label:12} : {len(blocks):5} blocs (depuis {filename})")
            combined.extend(blocks)
        else:
            # Fallback : récupère les blocs du site depuis l'ancien data-replay.m3u
            blocks = extract_site_blocks(old_content, url_pattern)
            stats.append(f"  {label:12} : {len(blocks):5} blocs (FALLBACK ancien m3u, {filename} manquant)")
            combined.extend(blocks)

    out = "\n".join(combined) + "\n"
    Path(OUTPUT).write_text(out, encoding="utf-8")

    print("=== combine_replays.py ===")
    for s in stats:
        print(s)
    print(f"\n[OK] Total : {len(combined) - 1} blocs → {OUTPUT} ({len(out)} bytes)")


if __name__ == "__main__":
    main()
