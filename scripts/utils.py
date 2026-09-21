#!/usr/bin/env python3
"""Module partagé pour tous les refresh_*.py — HTTP helpers, slug formatting."""
import gzip, json, re, sys, urllib.request

UA = ("Mozilla/5.0 (Linux; Android 14; AndroidTV) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
TIMEOUT = 15


def http_get(url, headers=None):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/json",
                 "Accept-Encoding": "gzip", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", errors="replace")


def http_get_tf1(url, headers=None):
    """TF1+ exige un UA Desktop sinon le HTML JSON-LD est vidé."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA_DESKTOP, "Accept": "text/html",
                 "Accept-Encoding": "gzip", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", errors="replace")


def slug_to_title(slug):
    SHORT = {"et", "le", "la", "les", "de", "du", "des", "un", "une", "à"}
    words = []
    for i, w in enumerate(slug.replace('-', ' ').split()):
        if i == 0 or w not in SHORT:
            words.append(w.capitalize())
        else:
            words.append(w)
    return ' '.join(words)
