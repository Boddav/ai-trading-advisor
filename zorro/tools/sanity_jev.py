# =================================================================
# Ellenőrző teszt: a Jev jól olvassa-e az inputot, és nem a kérdés/kód hibás-e?
#
# Egy hívásban 5 kérdés, ugyanarra az állapotra, mint a calibrate_vol.py-ban:
#   A) past_wider:   "Az elmúlt 24 bar sávja szélesebb volt, mint az azelőtti 24-é?"  (a válasz BENNE van az inputban)
#   B) up20:         "Az utolsó záró magasabb, mint 20 barral korábban?"               (returns_atr.last20 előjele)
#   C) ratio_gt1:    "range24_vs_100bar_avg nagyobb, mint 1?"                           (egy szám kiolvasása)
#   D) next_wider:   "A következő 24 bar sávja szélesebb lesz?"   (ugyanaz, mint a calibrate_vol, jövő)
#   E) next_calmer:  "A következő 24 bar nyugodtabb lesz?"       (D fordított megfogalmazásban)
# Ha A-C jó (AUC ~1), a Jev olvassa az inputot, és a D fordítottsága a Jev "hite" (perzisztencia),
# nem kódhiba. Ha D és E egyszerre < 0.5, a Jev a megfogalmazástól függetlenül egy irányba húz.
#
#   python sanity_jev.py [export.jsonl] [--n 600]
# =================================================================
import argparse
import os
import random
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
for d in (HERE, os.path.join(HERE, "..", "server")):
    sys.path.insert(0, d)
import JevServer as JS  # noqa: E402
from calibrate_vol import auc, rng  # noqa: E402

N = 24


def yn(q, t, f):
    return {"type": "noul", "instructions": {"question": q, "inputs": INPUTS}, "criteria": {"true": t, "false": f}}


INPUTS = ("All price values are relative, in units of atr14. recent_bars_atr are the last 48 H1 bars as "
          "[open, high, low, close] minus the last close, divided by atr14, oldest first (the last 24 entries are "
          "the most recent 24 bars). range24_vs_100bar_avg = last 24-bar range / average 24-bar range over the last "
          "100 bars. returns_atr.last20 = (last close - close 20 bars ago) / atr14.")
QUESTIONS = {
    "past_wider": yn("Was the price range (highest high minus lowest low) of the most recent 24 bars wider than the "
                     "range of the 24 bars before them?", "the most recent 24 bars had the wider range",
                     "the earlier 24 bars had the wider range"),
    "up20": yn("Is the last close higher than the close 20 bars ago?", "yes, higher", "no, lower or equal"),
    "ratio_gt1": yn("Is range24_vs_100bar_avg greater than 1?", "greater than 1", "1 or less"),
    "next_wider": yn("Will the price range of the next 24 H1 bars be wider than the range of the most recent 24 bars?",
                     "the next 24 bars move in a wider range", "the next 24 bars are calmer"),
    "next_calmer": yn("Will the next 24 H1 bars be calmer, i.e. move in a narrower range than the most recent 24 bars?",
                      "the next 24 bars are calmer", "the next 24 bars move in a wider range"),
}


def main():
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("export", nargs="?", default=JS.DEFAULT_EXPORT)
    ap.add_argument("--n", type=int, default=600)
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()
    reqs = []
    with open(os.path.abspath(args.export), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                reqs.append(json.loads(line))
    by = {}
    for r in reqs:
        by.setdefault(r["asset"], []).append(r)
    cand = []
    for seq in by.values():
        last = [[float(x) for x in r["bars"][-1][:4]] for r in seq]
        for i in range(len(seq) - N):
            bars = [[float(x) for x in b[:4]] for b in seq[i]["bars"]]
            if any(min(b) <= 0 or b[1] < b[2] for b in bars):
                continue
            cand.append((seq[i]["asset"], bars, last[i + 1:i + 1 + N]))
    random.Random(7).shuffle(cand)
    cand = cand[:args.n]

    rows, lock = [], threading.Lock()
    key = JS.api_key()

    def one(c):
        asset, bars, fut = c
        a14 = JS.atr(bars, 14)
        feats = JS.regime_features(bars)
        last = bars[-1][3]
        feats["recent_bars_atr"] = [[round((x - last) / a14, 2) for x in b] for b in bars[-48:]]
        past, prev = rng(bars[-N:]), rng(bars[-2 * N:-N])
        avg = sum(rng(bars[-N - k:len(bars) - k]) for k in range(0, 100, N)) / len(range(0, 100, N))
        feats["range24_vs_100bar_avg"] = round(past / avg, 2)
        truth = {"past_wider": past > prev, "up20": bars[-1][3] > bars[-21][3],
                 "ratio_gt1": past / avg > 1, "next_wider": rng(fut) > past, "next_calmer": rng(fut) <= past}
        state = {"symbol": asset, "timeframe": "H1"}
        state.update(feats)
        try:
            ans = JS.jev_call(key, state, QUESTIONS).get("answers", {})
        except JS.JevError as e:
            print("hiba:", e)
            return
        with lock:
            rows.append({k: (JS._noul(ans.get(k)), int(truth[k])) for k in QUESTIONS})

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=args.threads) as ex:
        list(ex.map(one, cand))

    print("Minta: %d óra\n" % len(rows))
    print("  %-12s %7s %10s %12s %14s" % ("kérdés", "AUC", "találat%", "átlag p", "valódi igen%"))
    for k in QUESTIONS:
        pairs = [r[k] for r in rows]
        hit = sum(1 for p, y in pairs if (p >= 0.5) == bool(y)) / len(pairs)
        print("  %-12s %7.3f %9.1f%% %12.2f %13.1f%%" % (k, auc(pairs), 100 * hit,
              sum(p for p, _ in pairs) / len(pairs), 100.0 * sum(y for _, y in pairs) / len(pairs)))
    d = [r["next_wider"][0] for r in rows]
    e = [r["next_calmer"][0] for r in rows]
    md, me = sum(d) / len(d), sum(e) / len(e)
    cov = sum((x - md) * (y - me) for x, y in zip(d, e))
    sd = (sum((x - md) ** 2 for x in d) * sum((y - me) ** 2 for y in e)) ** 0.5
    print("\n  next_wider vs next_calmer korreláció: %.2f  (-1 = következetes, 0 = a megfogalmazás dönt)"
          % (cov / sd if sd else 0))


if __name__ == "__main__":
    main()
