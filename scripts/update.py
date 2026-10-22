"""Leitura diária do highscore do Tibia para o site PT Dream.

Roda no GitHub Actions, 5 vezes por dia depois do server save (10h de Berlim).
Lê a configuração (chars da PT e servidores do ranking) do Firestore, busca o
highscore na API TibiaData e grava os arquivos em data/.

Regras:
- Cada leitura vale como "XP no server save". O arquivo do dia leva a data do dia
  jogado (data do server save menos 1 dia).
- Uma página só vale se os dados dela forem de depois do server save
  (timestamp - highscore_age >= SS). Página antiga (cache) nunca é gravada.
- A primeira leitura válida do dia fica; as seguintes só completam o que falta.
- O ranking do GuildStats só é gravado se a XP dele bater exatamente com a
  diferença do highscore oficial entre dois server saves.
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
    """Uma página do highscore. Devolve (linhas, dado_em) ou (None, None).

    dado_em = information.timestamp - highscore_age minutos: o instante real dos dados.
    """
    d = get_json(API.format(world=world, cat=cat, page=n))
    if not d:
        return None, None
    hs = d.get("highscores") or {}
    ts = (d.get("information") or {}).get("timestamp")
    at = None
    if ts:
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            at = t - timedelta(minutes=int(hs.get("highscore_age") or 0))
        except ValueError:
            at = None
    return hs.get("highscore_list") or [], at


def ss_at(day):
    """Instante (UTC) do server save: 10:00 em Berlim na data dada."""
    y, m, d = map(int, day.split("-"))
    return datetime(y, m, d, 10, 0, tzinfo=ZoneInfo("Europe/Berlin")).astimezone(timezone.utc)


def last_ss(now):
    day = now.astimezone(ZoneInfo("Europe/Berlin")).date()
    s = ss_at(day.isoformat())
    if now < s:
        s = ss_at((day - timedelta(days=1)).isoformat())
    return s


def iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def line(r):
    return f"{r['rank']}|{r['name']}|{r['vocation']}|{r['level']}|{r['value']}"


GS_URL = "https://guildstats.eu/top-experience?world={world}&time={time}&type=0"
VOCS = {"EK", "ED", "MS", "RP", "EM", "K", "D", "S", "P", "M", "N"}




def guildstats_rank(world):
    """Ranking pronto de XP (ontem, 7 e 30 dias) do GuildStats. Devolve None se o site bloquear."""
    import html as H
    import re
    out = {}
    for key, t in (("d1", 0), ("d7", 7), ("d30", 30)):
        try:
            req = urllib.request.Request(GS_URL.format(world=world, time=t), headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"})
            with urllib.request.urlopen(req, timeout=40) as r:
                page_html = r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            print(f"guildstats {world} {key}: {e}", file=sys.stderr)
            return None
        lines = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, re.S | re.I):
            cells = [H.unescape(re.sub(r"<[^>]+>", " ", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)]
            cells = [re.sub(r"\s+", " ", c) for c in cells]
            if len(cells) < 5 or not cells[0].isdigit():
                continue
            name = re.split(r"\s+Verificar no|\s+Check on|\s+Comparar", cells[1])[0].strip()
            level, gain = re.sub(r"\D", "", cells[2]), re.sub(r"[^\d-]", "", cells[3])
            voc = next((c.split()[0] for c in cells[4:] if c and c.split()[0] in VOCS), "")
            if name and level and gain:
                lines.append(f"{name}|{level}|{gain}|{voc}")
        if len(lines) < 20:
            print(f"guildstats {world} {key}: só {len(lines)} linhas (bloqueado?)", file=sys.stderr)
            return None
        out[key] = "\n".join(lines)
    return out


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj, **kw):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, **kw)


def fs_doc(path):
    """Documento do Firestore (leitura pública) ou None."""
    if not PROJECT:
        return None
    d = get_json(f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents/{path}", tries=2)
    if d and "fields" in d:
        return {k: fs_value(v) for k, v in d["fields"].items()}
    return None


def read_srv(world, day):
    """{nome: exp} do top 500 de um dia. Igual ao site: o Firebase vale antes do arquivo do repositório."""
    out = {}
    p = os.path.join(DATA, "srv", world, day + ".txt")
    rows = []
    fb = fs_doc(f"srv/{world}/days/{day}")
    if fb and fb.get("raw"):
        rows = [x.split("|") for x in str(fb["raw"]).splitlines()]
    elif os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            rows = [x.split("|") for x in f.read().splitlines()]
    else:
        pend = load_json(os.path.join(DATA, "pending", world + ".json"), {})
        if pend.get("day") == day:
            for pg in pend.get("pages", {}).values():
                rows += [x.split("|") for x in pg["rows"]]
    for r in rows:
        if len(r) >= 5 and r[1] != "Name Changed" and r[4].isdigit():
            out[r[1].lower()] = int(r[4])
    return out


def read_world(world, day, ss):
    """Top 500 do servidor com a regra do server save. Devolve linhas válidas (dicts)."""
    folder = os.path.join(DATA, "srv", world)
    final = os.path.join(folder, day + ".txt")
    if os.path.exists(final):
        print(f"{world}: {day} já completo")
        with open(final, encoding="utf-8") as f:
            return [dict(zip(("rank", "name", "vocation", "level", "value"), x.split("|")))
                    for x in f.read().splitlines() if x.count("|") >= 4]
    pp = os.path.join(DATA, "pending", world + ".json")
    pend = load_json(pp, {})
    if pend.get("day") != day:
        pend = {"day": day, "ss": iso(ss), "pages": {}}
    last = None  # última página do servidor (menos de 50 linhas)
    for n in range(1, 11):
        if str(n) in pend["pages"]:
            if len(pend["pages"][str(n)]["rows"]) < 50:
                last = n
                break
            continue
        lst, at = None, None
        for _ in range(2):
            lst, at = page(world, "experience", n)
            if lst is not None and at and at >= ss:
                break
        if lst is None:
            print(f"{world} p{n}: falhou")
            continue
        if not at or at < ss:
            print(f"{world} p{n}: ainda com dados de antes do server save ({iso(at) if at else '?'})")
            continue
        pend["pages"][str(n)] = {"at": iso(at), "rows": [line(r) for r in lst]}
        if len(lst) < 50:
            last = n
            break
    need = range(1, (last or 10) + 1)
    done = all(str(n) in pend["pages"] for n in need)
    rows = []
    for n in sorted(pend["pages"], key=int):
        rows += pend["pages"][n]["rows"]
    print(f"{world}: {len(pend['pages'])} páginas válidas de {len(need)}")
    if done and rows:
        os.makedirs(folder, exist_ok=True)
        with open(final, "w", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")
        if os.path.exists(pp):
            os.remove(pp)
    elif pend["pages"]:
        save_json(pp, pend, indent=0)
    return [dict(zip(("rank", "name", "vocation", "level", "value"), x.split("|"))) for x in rows]


def check_gs(gs_d1, pairs, why=None):
    """Confere a XP do GuildStats com a diferença oficial. Devolve quantos nomes bateram ou None."""
    gs = {}
    for l in gs_d1.splitlines():
        p = l.split("|")
        if len(p) >= 3 and p[0] != "Name Changed":
            try:
                gs[p[0].lower()] = int(p[2])
            except ValueError:
                pass
    ok, bad = 0, []
    for name, delta in pairs.items():
        if name in gs:
            if gs[name] != delta:
                bad.append(f"{name}: GuildStats {gs[name]} x oficial {delta}")
            else:
                ok += 1
    if why is not None:
        why.extend(bad[:6])
        why.append(f"{ok} bateram, {len(bad)} não bateram, {len(gs)} linhas no GuildStats")
    for b in bad[:6]:
        print("  " + b)
    return None if bad else ok


def main():
    now = datetime.now(timezone.utc)
    ss = last_ss(now)
    ss_day = ss.astimezone(ZoneInfo("Europe/Berlin")).date()
    # o arquivo leva a data do dia jogado: a leitura do SS de D traz o XP até o fim de D-1
    today = (ss_day - timedelta(days=1)).isoformat()
    yday = (ss_day - timedelta(days=2)).isoformat()
    print(f"server save {iso(ss)} · dia jogado {today}")
    cfg = load_config()
    pt_world = cfg.get("world") or "Collabra"
    worlds = [w for w in (cfg.get("worlds") or []) if w]
    if pt_world not in worlds:
        worlds.insert(0, pt_world)
    chars = cfg.get("chars") or []

    # 1) ranking top 500 de cada servidor (só páginas de depois do SS)
    lines_by_world = {}
    for w in worlds:
        rows = read_world(w, today, ss)
        if rows:
            lines_by_world[w] = rows

    idx_path = os.path.join(DATA, "srv", "index.json")
    idx = load_json(idx_path, {})
    for w in worlds:
        if os.path.exists(os.path.join(DATA, "srv", w, today + ".txt")):
            idx[w] = sorted(set(idx.get(w, [])) | {today})
    limit = (now - timedelta(days=40)).strftime("%Y-%m-%d")
    for w, ds in idx.items():
        for d in [d for d in ds if d < limit]:
            p = os.path.join(DATA, "srv", w, d + ".txt")
            if os.path.exists(p):
                os.remove(p)
        idx[w] = [d for d in ds if d >= limit]
    save_json(idx_path, idx, indent=0)

    # 2) XP e skill dos chars da PT (chars já gravados hoje ficam como estão)
    sp = os.path.join(DATA, "snaps.json")
    snaps = load_json(sp, {})
    snap = snaps.get(today) or {}
    have = dict(snap.get("chars") or {})
    want = {c["name"].lower(): c["name"] for c in chars if c.get("name") and c["name"] not in have}
    found, at_max = {}, None
    for r in lines_by_world.get(pt_world, []):
        k = r["name"].lower()
        if k in want and want[k] not in found:
            found[want[k]] = {"exp": int(r["value"]), "level": int(r["level"])}
    top_done = os.path.exists(os.path.join(DATA, "srv", pt_world, today + ".txt"))
    if want and len(found) < len(want) and top_done:
        for n in range(11, 21):
            lst, at = page(pt_world, "experience", n)
            if lst is None or not at or at < ss:
                print(f"{pt_world} p{n}: sem dado válido")
                break
            at_max = max(at_max or at, at)
            for r in lst:
                k = r["name"].lower()
                if k in want and want[k] not in found:
                    found[want[k]] = {"exp": int(r["value"]), "level": int(r["level"]), "at": iso(at)}
            if len(found) == len(want) or len(lst) < 50:
                break

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
        have.update(found)
        snaps[today] = {"source": "tibiadata", "ss": iso(ss),
                        "fetchedAt": iso(at_max or now), "chars": have}
        save_json(sp, dict(sorted(snaps.items())), indent=0)
        print("PT:", ", ".join(f"{k} {v['level']}" for k, v in found.items()))
    else:
        print("PT: nada novo" if not want else "PT: chars ainda sem dado válido")

    # 3) GuildStats: só grava se a XP dele bater com a diferença oficial entre dois SS
    gs_status = {}
    for w in worlds:
        gp = os.path.join(DATA, "gsrank", w + ".json")
        old = load_json(gp, {})
        if old.get("date") == today and old.get("checked"):
            gs_status[w] = f"já gravado ({old['checked']} nomes conferidos)"
            continue
        pairs = {}
        if w == pt_world:
            def snap_chars(day):
                c = dict((snaps.get(day) or {}).get("chars") or {})
                c.update((fs_doc(f"snaps/{day}") or {}).get("chars") or {})
                return c
            a, b = snap_chars(yday), snap_chars(today)
            for n in b:
                if n in a and b[n].get("exp") and a[n].get("exp"):
                    pairs[n.lower()] = b[n]["exp"] - a[n]["exp"]
        a, b = read_srv(w, yday), read_srv(w, today)
        for n in list(b)[:30]:
            if n in a:
                pairs.setdefault(n, b[n] - a[n])
        pt_n = sum(1 for c in chars if c.get("name")) if w == pt_world else 0
        need = min(3, pt_n) if 0 < pt_n < 3 else 3
        if len(pairs) < need:
            gs_status[w] = "sem dados de ontem e hoje para conferir"
            print(f"{w}: {gs_status[w]}")
            continue
        gs = guildstats_rank(w)
        if not gs:
            gs_status[w] = "site do GuildStats não respondeu ou bloqueou"
            continue
        why = []
        ok = check_gs(gs["d1"], pairs, why)
        if not ok or ok < need:
            gs_status[w] = "não gravado: " + " | ".join(why)
            print(f"{w}: {gs_status[w]}")
            continue
        gs_status[w] = f"gravado ({ok} nomes conferidos)"
        save_json(gp, {"world": w, "date": today, "source": "guildstats", "checked": ok, **gs})
        print(f"{w}: GuildStats gravado ({ok} nomes conferidos)")

    save_json(os.path.join(DATA, "status.json"),
              {"updatedAt": iso(now), "ss": iso(ss), "date": today, "guildstats": gs_status})


if __name__ == "__main__":
    main()
