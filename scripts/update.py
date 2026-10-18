"""Leitura diária do highscore do Tibia para o site PT Dream.

Roda no GitHub Actions. Lê a configuração (chars da PT e servidores do ranking)
do Firestore, busca o highscore na API TibiaData e grava os arquivos em data/.
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.tibiadata.com/v4/highscores/{world}/{cat}/all/{page}"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
PROJECT = os.environ.get("FIREBASE_PROJECT", "").strip()


def get_json(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "pt-dream-updater"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            print(f"falhou {url}: {e}", file=sys.stderr)
            time.sleep(3 * (i + 1))
    return None


def fs_value(v):
    """Converte um valor tipado do Firestore REST em Python."""
    if "stringValue" in v:
        return v["stringValue"]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "booleanValue" in v:
        return v["booleanValue"]
    if "nullValue" in v:
        return None
    if "arrayValue" in v:
        return [fs_value(x) for x in v["arrayValue"].get("values", [])]
    if "mapValue" in v:
        return {k: fs_value(x) for k, x in v["mapValue"].get("fields", {}).items()}
    return None


def load_config():
    if PROJECT:
        url = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents/config/main"
        doc = get_json(url)
        if doc and "fields" in doc:
            print("config lida do Firestore")
            return {k: fs_value(v) for k, v in doc["fields"].items()}
    print("usando config de data/seed.json")
    with open(os.path.join(DATA, "seed.json"), encoding="utf-8") as f:
        return json.load(f)["config"]


def page(world, cat, n):
    d = get_json(API.format(world=world, cat=cat, page=n))
    if not d:
        return None, None
    hs = d.get("highscores") or {}
    return hs.get("highscore_list") or [], (d.get("information") or {}).get("timestamp")


def main():
    today = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")
    cfg = load_config()
    pt_world = cfg.get("world") or "Collabra"
    worlds = [w for w in (cfg.get("worlds") or []) if w]
    if pt_world not in worlds:
        worlds.insert(0, pt_world)
    chars = cfg.get("chars") or []
    stamp = None
    lines_by_world = {}

    # 1) ranking top 500 de cada servidor
    for w in worlds:
        rows = []
        for n in range(1, 11):
            lst, ts = page(w, "experience", n)
            if lst is None:
                rows = None
                break
            stamp = stamp or ts
            rows += lst
            if len(lst) < 50:
                break
        if not rows:
            print(f"{w}: sem dados, pulando")
            continue
        lines_by_world[w] = rows
        folder = os.path.join(DATA, "srv", w)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, today + ".txt"), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(f"{r['rank']}|{r['name']}|{r['vocation']}|{r['level']}|{r['value']}\n")
        print(f"{w}: {len(rows)} linhas")

    idx_path = os.path.join(DATA, "srv", "index.json")
    idx = json.load(open(idx_path)) if os.path.exists(idx_path) else {}
    for w in lines_by_world:
        ds = set(idx.get(w, []))
        ds.add(today)
        idx[w] = sorted(ds)
    # apaga leituras com mais de 40 dias
    limit = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%d")
    for w, ds in idx.items():
        for d in [d for d in ds if d < limit]:
            p = os.path.join(DATA, "srv", w, d + ".txt")
            if os.path.exists(p):
                os.remove(p)
        idx[w] = [d for d in ds if d >= limit]
    json.dump(idx, open(idx_path, "w"), indent=0)

    # 2) XP e skill dos chars da PT
    found = {}
    want = {c["name"].lower(): c["name"] for c in chars if c.get("name")}
    pool = list(lines_by_world.get(pt_world, []))
    n = 11
    while True:
        for r in pool:
            k = r["name"].lower()
            if k in want and want[k] not in found:
                found[want[k]] = {"exp": int(r["value"]), "level": int(r["level"])}
        if len(found) == len(want) or n > 20:
            break
        lst, _ = page(pt_world, "experience", n)
        if not lst:
            break
        pool = lst
        n += 1

    cats = {}
    for c in chars:
        if c.get("skillCat") and c["name"] in found:
            cats.setdefault(c["skillCat"], []).append(c["name"])
    for cat, names in cats.items():
        left = {x.lower(): x for x in names}
        for p in range(1, 4):
            lst, _ = page(pt_world, cat, p)
            if not lst:
                break
            for r in lst:
                k = r["name"].lower()
                if k in left:
                    found[left.pop(k)]["skill"] = int(r["value"])
            if not left:
                break

    if found:
        sp = os.path.join(DATA, "snaps.json")
        snaps = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {}
        snaps[today] = {"source": "tibiadata", "fetchedAt": stamp, "chars": found}
        json.dump(dict(sorted(snaps.items())), open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    json.dump({"updatedAt": stamp or datetime.now(timezone.utc).isoformat(), "date": today},
              open(os.path.join(DATA, "status.json"), "w"))
    print("PT:", ", ".join(f"{k} {v['level']}" for k, v in found.items()) or "nenhum char achado")


if __name__ == "__main__":
    main()
