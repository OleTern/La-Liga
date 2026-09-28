#!/usr/bin/env python3
"""
Обновляет data/laliga.json для страницы index.html.

  * календарь и результаты La Liga 2026/27 — открытый набор openfootball (GitHub);
  * коэффициенты К1 / Н / К2 — The Odds API (нужен ключ в переменной ODDS_API_KEY).

Запуск:
  python scripts/update.py              # результаты + коэффициенты ближайших матчей
  python scripts/update.py --no-odds    # только результаты
  python scripts/update.py --backfill   # + исторические коэффициенты уже сыгранных матчей
                                        #   (нужен платный тариф The Odds API, ~10 кредитов за игровой день)

Только стандартная библиотека Python 3.9+.
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SEASON = "2026-27"
SEASON_START = 2026
OF_URL = "https://raw.githubusercontent.com/openfootball/espana/master/{s}/1-liga.txt"
HIST_SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]

SPORT = "soccer_spain_la_liga"
API = "https://api.the-odds-api.com/v4"
REGIONS = os.environ.get("ODDS_REGIONS", "eu")
BOOKMAKER = os.environ.get("ODDS_BOOKMAKER", "").strip()  # напр. "pinnacle"; пусто = среднее по всем

MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

# ---------- имена команд ----------
CANON_RULES = [
    (r"Alav", "Alaves"), (r"Athletic", "Athletic Bilbao"), (r"Atl[eé]tico", "Atletico Madrid"),
    (r"Espanyol", "Espanyol"), (r"Barcelona", "Barcelona"), (r"Celta", "Celta Vigo"),
    (r"Deportivo La Coru|RC Deportivo|Deportivo A Coru|^Deportivo$", "Deportivo"),
    (r"Elche", "Elche"), (r"Getafe", "Getafe"), (r"Levante", "Levante"), (r"M[aá]laga", "Malaga"),
    (r"Osasuna", "Osasuna"), (r"Racing", "Racing Santander"), (r"Rayo", "Rayo Vallecano"),
    (r"Betis", "Real Betis"), (r"Real Madrid", "Real Madrid"), (r"Real Sociedad", "Real Sociedad"),
    (r"Sevilla", "Sevilla"), (r"Valencia", "Valencia"), (r"Villarreal", "Villarreal"),
    (r"Girona", "Girona"), (r"Mallorca", "Mallorca"), (r"Oviedo", "Oviedo"), (r"C[aá]diz", "Cadiz"),
    (r"Granada", "Granada"), (r"Almer", "Almeria"), (r"Las Palmas", "Las Palmas"),
    (r"Legan", "Leganes"), (r"Valladolid", "Valladolid"),
]


def canon(name):
    name = name.strip()
    for pat, out in CANON_RULES:
        if re.search(pat, name):
            return out
    return name


# ---------- разбор файлов openfootball ----------
def parse_openfootball(txt, start_year):
    txt = txt.replace("\r", "")
    out, rnd, date, order = [], None, None, 0
    for line in txt.split("\n"):
        m = re.match(r"▪\s*(?:Matchday|Regular Season\s*-)\s*(\d+)", line)
        if m:
            rnd = int(m.group(1)); continue
        m = re.match(r"\s*(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+([A-Z][a-z]{2})\s+(\d+)(?:\s+(\d{4}))?\s*$", line)
        if m:
            mon, d = MONTHS[m.group(1)], int(m.group(2))
            y = int(m.group(3)) if m.group(3) else (start_year if mon >= 7 else start_year + 1)
            date = dt.date(y, mon, d); continue
        if not (date and rnd):
            continue
        # формат «Хозяева v Гости 2-1 (1-0)»
        m = re.match(r"\s+(?:(\d\d:\d\d)\s+)?(.+?)\s+v\s+(.+?)\s*(?:(\d+)-(\d+)(?:\s+\(\d+-\d+\))?)?\s*$", line)
        if m:
            t, h, a, gh, ga = m.groups()
        else:
            # формат «Хозяева 2-1 (1-0) Гости»
            m = re.match(r"\s+(?:(\d\d:\d\d)\s+)?(.+?)\s+(\d+)-(\d+)(?:\s+\(\d+-\d+\))?\s{2,}(.+?)\s*$", line)
            if not m:
                continue
            t, h, gh, ga, a = m.groups()
        out.append(dict(r=rnd, d=date.isoformat(), t=t or "", h=canon(h), a=canon(a),
                        hg=int(gh) if gh is not None else None, ag=int(ga) if ga is not None else None, o=order))
        order += 1
    # время у матчей без времени наследуем от предыдущего в тот же день
    last_d, last_t = None, ""
    for m in out:
        if m["d"] != last_d:
            last_d, last_t = m["d"], ""
        if m["t"]:
            last_t = m["t"]
        else:
            m["t"] = last_t
    return sorted(out, key=lambda m: (m["d"], m["t"] or "99:99", m["o"]))


def http_get(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": "laliga-series/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers


def load_fixtures():
    raw, _ = http_get(OF_URL.format(s=SEASON))
    ms = parse_openfootball(raw.decode("utf-8"), SEASON_START)
    if len(ms) != 380:
        raise RuntimeError(f"Ожидалось 380 матчей, разобрано {len(ms)} — формат файла мог измениться")
    for m in ms:
        m["id"] = f'{m["h"]}|{m["a"]}'
        del m["o"]
    if len({m["id"] for m in ms}) != 380:
        raise RuntimeError("Дубли пар «хозяева|гости» — проверьте названия команд")
    return ms


# ---------- история (макс. серии по прошлым сезонам) ----------
CATS = {
    "btts": lambda h, a: h > 0 and a > 0, "h15": lambda h, a: h > 1.5, "w1": lambda h, a: h > a,
    "o25": lambda h, a: h + a > 2.5, "h_nw15": lambda h, a: not (h - a > 1.5), "h_nl": lambda h, a: h >= a,
    "h_sc": lambda h, a: h >= 1, "h_ns": lambda h, a: h == 0, "a_nw15": lambda h, a: not (a - h > 1.5),
    "a_nl": lambda h, a: a >= h, "a_sc": lambda h, a: a >= 1, "a_ns": lambda h, a: a == 0,
    "a15": lambda h, a: a > 1.5, "w2": lambda h, a: a > h, "dr": lambda h, a: h == a,
    "nbtts": lambda h, a: h == 0 or a == 0,
}


def max_run(bools):
    best = cur = 0
    for b in bools:
        cur = cur + 1 if b else 0
        best = max(best, cur)
    return best


def build_history():
    seasons = {}
    for s in HIST_SEASONS:
        raw, _ = http_get(OF_URL.format(s=s))
        ms = parse_openfootball(raw.decode("utf-8"), int(s[:4]))
        if len(ms) != 380:
            raise RuntimeError(f"{s}: разобрано {len(ms)} матчей вместо 380")
        lab = s[2:4] + "-" + s[5:]
        seasons[lab] = dict(
            season=lab, n=380,
            w1=sum(m["hg"] > m["ag"] for m in ms), dr=sum(m["hg"] == m["ag"] for m in ms), w2=sum(m["hg"] < m["ag"] for m in ms),
            goals=sum(m["hg"] + m["ag"] for m in ms), o25=sum(m["hg"] + m["ag"] > 2.5 for m in ms),
            btts=sum(m["hg"] > 0 and m["ag"] > 0 for m in ms),
            maxrun={k: max_run([f(m["hg"], m["ag"]) for m in ms]) for k, f in CATS.items()},
        )
    return seasons


# ---------- The Odds API ----------
def api_json(path, params):
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    try:
        raw, headers = http_get(url)
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode("utf-8", "replace")
        raise RuntimeError(f"The Odds API: HTTP {e.code} {body}")
    left = headers.get("x-requests-remaining")
    if left is not None:
        print(f"  The Odds API: осталось кредитов {left}, использовано {headers.get('x-requests-used')}")
    return json.loads(raw)


def consensus(ev):
    """К1/Н/К2 события: среднее по букмекерам (или один букмекер, если задан ODDS_BOOKMAKER)."""
    h, a = ev["home_team"], ev["away_team"]
    rows = []
    for b in ev.get("bookmakers", []):
        if BOOKMAKER and b.get("key") != BOOKMAKER:
            continue
        for m in b.get("markets", []):
            if m.get("key") != "h2h":
                continue
            p = {o["name"]: o["price"] for o in m.get("outcomes", [])}
            if h in p and a in p and "Draw" in p:
                rows.append((p[h], p["Draw"], p[a]))
    if not rows:
        return None, 0
    return [round(sum(r[i] for r in rows) / len(rows), 2) for i in range(3)], len(rows)


def match_event(ev, by_id):
    """Возвращает (id фикстуры, swapped) для события API. Дата матча должна совпасть с календарём ±4 дня."""
    h, a = canon(ev["home_team"]), canon(ev["away_team"])
    start = dt.datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")).date()

    def near(fid):
        return abs((dt.date.fromisoformat(by_id[fid]["d"]) - start).days) <= 4

    if f"{h}|{a}" in by_id and near(f"{h}|{a}"):
        return f"{h}|{a}", False
    if f"{a}|{h}" in by_id and near(f"{a}|{h}"):
        return f"{a}|{h}", True
    return None, False


def snapshot_odds(key, fixtures, store, now, warnings):
    by_id = {f["id"]: f for f in fixtures}
    data = api_json(f"/sports/{SPORT}/odds/", dict(apiKey=key, regions=REGIONS, markets="h2h", oddsFormat="decimal", dateFormat="iso"))
    saved = 0
    for ev in data:
        start = dt.datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if start <= now:
            continue  # матч уже начался — live-коэффициенты нам не нужны
        fid, swapped = match_event(ev, by_id)
        if not fid:
            warnings.append(f'нет в календаре: {ev["home_team"]} — {ev["away_team"]}')
            continue
        if by_id[fid]["hg"] is not None:
            continue
        odds, n = consensus(ev)
        if not odds:
            continue
        if swapped:
            warnings.append(f"хозяева/гости перепутаны в API: {ev['home_team']} — {ev['away_team']}, пропущено")
            continue
        store[fid] = dict(o=odds, n=n, t=now.strftime("%Y-%m-%dT%H:%M:%SZ"), src="api")
        saved += 1
    print(f"  коэффициенты обновлены у {saved} ближайших матчей")


def backfill(key, fixtures, store, warnings):
    if ZoneInfo is None:
        warnings.append("нет zoneinfo — backfill пропущен"); return
    tz = ZoneInfo("Europe/Madrid")
    by_id = {f["id"]: f for f in fixtures}
    need = [f for f in fixtures if f["hg"] is not None and f["id"] not in store]
    days = {}
    for f in need:
        days.setdefault(f["d"], []).append(f)
    print(f"  backfill: игровых дней без коэффициентов — {len(days)} (≈{10 * len(days)} кредитов)")
    for day, fs in sorted(days.items()):
        first = min([f["t"] for f in fs if f["t"]] or ["12:00"])
        local = dt.datetime.fromisoformat(f"{day}T{first}:00").replace(tzinfo=tz)
        snap = (local - dt.timedelta(minutes=20)).astimezone(dt.timezone.utc)
        stamp = snap.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            resp = api_json(f"/historical/sports/{SPORT}/odds/", dict(apiKey=key, regions=REGIONS, markets="h2h", oddsFormat="decimal", dateFormat="iso", date=stamp))
        except RuntimeError as e:
            warnings.append(f"backfill {day}: {e}"); break
        want = {f["id"] for f in fs}
        for ev in resp.get("data", []):
            fid, swapped = match_event(ev, by_id)
            if fid in want and not swapped:
                odds, n = consensus(ev)
                if odds:
                    store[fid] = dict(o=odds, n=n, t=stamp, src="hist")


# ---------- main ----------
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def dump_json(path, obj, indent=None):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":") if indent is None else None, indent=indent)


def main():
    args = set(sys.argv[1:])
    DATA.mkdir(exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    warnings = []

    fixtures = load_fixtures()
    played = sum(f["hg"] is not None for f in fixtures)
    print(f"Календарь: {len(fixtures)} матчей, сыграно {played}")

    history = load_json(DATA / "history.json", None)
    if not history:
        print("Считаю историю прошлых сезонов…")
        history = build_history()
        dump_json(DATA / "history.json", history)

    store = load_json(DATA / "odds.json", {})
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if key and "--no-odds" not in args:
        try:
            snapshot_odds(key, fixtures, store, now, warnings)
            if "--backfill" in args:
                backfill(key, fixtures, store, warnings)
        except Exception as e:  # результаты обновляем в любом случае
            warnings.append(str(e))
    elif not key:
        print("ODDS_API_KEY не задан — коэффициенты не обновляются")

    manual = load_json(DATA / "manual_odds.json", {})
    have = 0
    for f in fixtures:
        o = manual.get(f["id"]) or (store.get(f["id"]) or {}).get("o")
        f["odds"] = o if o and len(o) == 3 else None
        have += f["odds"] is not None
    dump_json(DATA / "odds.json", store, indent=1)

    out = dict(updated=now.strftime("%Y-%m-%dT%H:%M:%SZ"), season="2026/27", fixtures=fixtures, seasons=history, warnings=warnings[:20])
    dump_json(DATA / "laliga.json", out)
    print(f"Готово: коэффициенты есть у {have} матчей")
    for w in warnings:
        print("  ! " + w)


if __name__ == "__main__":
    main()
