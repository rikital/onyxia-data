#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_olatv.py — Ola TV (nx-data) : CLASSEMENT des serveurs FR.

Publie data/olatv/live-cids.json : les cids qui ont REELLEMENT des chaines FR,
CLASSES par richesse (nb de chaines FR, du plus riche au moins riche). L'app pioche
les meilleurs en premier au lieu de tester au hasard 1269 serveurs -> rapide.

NOTE (2026-07-06) : le catalogue pre-valide par chaine (checker M3U) a ete abandonne :
depuis une IP datacenter GitHub, le re-scraping se fait rate-limiter et les CDN de
streaming bloquent le fetch des liens -> resultats non representatifs. La validite des
liens + le switch de source se verifient sur l'APPAREIL (l'app le fait deja).

Protocole (OlaTvProvider.kt) : POST api.php data=base64(JSON clair){salt,sign,method_name}.
  newolatvcategory0326 -> cids ; getToken128910 -> token1/token2 (portal/MAC).
  Portail Stalker : handshake -> get_genres FR -> get_ordered_list p=1 (total_items).
Env: OLA_WORKERS(16), OLA_PROBE_TIMEOUT(8), OLA_API_TIMEOUT(25), OLA_OUT.
"""
import base64, hashlib, json, os, random, re, sys, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

API_URL   = "http://iptvdroid.monster/IP11/api.php"
AES_KEY   = b"3234567890123453"
SECRET    = "MRZEREZIS"
OUT_PATH    = os.environ.get("OLA_OUT", "data/olatv/live-cids.json")
MAX_WORKERS = int(os.environ.get("OLA_WORKERS", "16"))
PROBE_TMO   = int(os.environ.get("OLA_PROBE_TIMEOUT", "8"))
API_TMO     = int(os.environ.get("OLA_API_TIMEOUT", "25"))
MAG_UA = ("Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 "
          "(KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3")

def build_payload(method_name, extras=None):
    salt = str(random.randint(0, 899))
    sign = hashlib.md5((SECRET + salt).encode()).hexdigest()
    obj = {"salt": salt, "sign": sign, "method_name": method_name}
    if extras: obj.update(extras)
    return base64.b64encode(json.dumps(obj).encode("utf-8")).decode()

def api_json(method_name, extras=None, tries=4):
    for attempt in range(1, tries + 1):
        try:
            r = requests.post(API_URL, data={"data": build_payload(method_name, extras)},
                              headers={"User-Agent": "okhttp/3.12.1"}, timeout=API_TMO)
            if r.status_code != 200:
                time.sleep(1.5 * attempt); continue
            body = r.text.strip()
            txt = body if (body.startswith("[") or body.startswith("{")) else \
                unpad(AES.new(AES_KEY, AES.MODE_CBC, AES_KEY).decrypt(base64.b64decode(body)), 16).decode("utf-8", "replace")
            return json.loads(txt)
        except Exception:
            time.sleep(1.5 * attempt)
    return None

def get_servers():
    resp = api_json("newolatvcategory0326")
    items = resp if isinstance(resp, list) else (resp.get("LIVETV") if isinstance(resp, dict) else None) or []
    out, seen = [], set()
    for s in items:
        if isinstance(s, dict):
            cid = str(s.get("cid", "")).strip()
            if cid and cid not in seen:
                seen.add(cid); out.append(cid)
    print(f"[servers] {len(out)} cids", flush=True)
    return out

def _dec1(t1):
    try:
        t = base64.b64decode(t1).decode("iso-8859-1"); i = t.find("http")
        return t[i:].strip() if i >= 0 else None
    except Exception: return None

def _dec2(t2):
    try:
        t = base64.b64decode(t2).decode("iso-8859-1")
        m = re.search(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", t)
        return m.group(1) if m else None
    except Exception: return None

def get_mac(cid):
    resp = api_json("getToken128910", {"cid": cid})
    if not resp: return None
    tgt = None
    if isinstance(resp, dict):
        lv = resp.get("LIVETV"); tgt = (lv[0] if isinstance(lv, list) and lv else resp)
    elif isinstance(resp, list) and resp: tgt = resp[0]
    if not isinstance(tgt, dict): return None
    t1, t2 = tgt.get("token1", ""), tgt.get("token2", "")
    if not t1 or not t2: return None
    base, mac = _dec1(t1), _dec2(t2)
    return (base, mac) if base and mac else None

def _is_fr(title):
    t = title.lower()
    return t.startswith("fr|") or t.startswith("fr ") or "france" in t or "french" in t or "français" in t or "francais" in t

def fr_count(base, mac):
    portal = base.rstrip("/") + "/portal.php"
    cookie = f"mac={urllib.parse.quote(mac)}; stb_lang=en; timezone=Europe%2FLondon"
    sess = requests.Session()
    try:
        r = sess.get(portal + "?type=stb&action=handshake&token=&JsHttpRequest=1-xml",
                     headers={"User-Agent": MAG_UA, "Cookie": cookie}, timeout=PROBE_TMO)
        tok = json.loads(r.text).get("js", {}).get("token", "")
        if not tok: return 0
        Hb = {"User-Agent": MAG_UA, "Cookie": cookie, "Authorization": "Bearer " + str(tok)}
        r = sess.get(portal + "?type=itv&action=get_genres&JsHttpRequest=1-xml", headers=Hb, timeout=PROBE_TMO)
        genres = json.loads(r.text).get("js", []) or []
        fr_ids = [str(g.get("id", "")) for g in genres if isinstance(g, dict) and _is_fr(g.get("title", ""))]
        if not fr_ids: return 0
        total = 0
        for gid in fr_ids[:12]:
            try:
                u = portal + f"?type=itv&action=get_ordered_list&genre={gid}&force_ch_link_check=&fav=0&sortby=name&p=1&JsHttpRequest=1-xml"
                r = sess.get(u, headers=Hb, timeout=PROBE_TMO)
                total += int(json.loads(r.text).get("js", {}).get("total_items", 0) or 0)
            except Exception:
                continue
        return total
    except Exception:
        return 0
    finally:
        sess.close()

def probe(cid):
    creds = get_mac(cid)
    if not creds: return (cid, 0)
    base, mac = creds
    return (cid, fr_count(base, mac))

def main():
    t0 = time.time()
    cids = get_servers()
    if not cids:
        print("ERREUR : aucun cid.", flush=True); sys.exit(1)
    counts, done = {}, 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(probe, c): c for c in cids}
        for fut in as_completed(futs):
            done += 1
            try: cid, n = fut.result()
            except Exception: cid, n = futs[fut], 0
            if n > 0: counts[cid] = n
            if done % 100 == 0 or done == len(cids):
                print(f"  probe {done}/{len(cids)} — FR cids={len(counts)}", flush=True)
    if not counts:
        print("ATTENTION : 0 cid FR -> on n'ecrase rien.", flush=True)
        if os.path.exists(OUT_PATH): return
        sys.exit(1)
    ranked = sorted(counts.keys(), key=lambda c: (-counts[c], len(c), c))
    payload = {"generated_at": int(time.time()), "category": "fr-ranked",
               "total": len(cids), "alive": len(ranked),
               "cids": ranked, "fr_counts": {c: counts[c] for c in ranked}}
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    top = [(c, counts[c]) for c in ranked[:8]]
    print(f"[done] {len(ranked)} cids FR classes en {int(time.time()-t0)}s. Top: {top} -> {OUT_PATH}", flush=True)

if __name__ == "__main__":
    main()
