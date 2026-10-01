# =================================================================
# Kutatás: tudja-e a Jev a VOLATILITÁST (nem az irányt)?
#
# Kérdés minden exportált órára (noul): "A következő 24 H1 bar ársávja (legmagasabb high -
# legalacsonyabb low) szélesebb lesz-e, mint az elmúlt 24 baré?"
# A volatilitás csomósodik és visszahúz az átlagához, ezért ez egy egyszerű képlettel is jósolható.
# A Jev akkor ér valamit, ha jobban rangsorol (AUC), mint a képlet:
#   képlet = -(elmúlt 24 bar sávja / a 100 bares átlagos 24 bares sáv)   (visszahúzás az átlaghoz)
#
# Futtatás a JevServer.py mellett (Strategy mappa) vagy a repóból:
#   python calibrate_vol.py [export.jsonl] --fetch [--every 2]
# =================================================================
import argparse
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for d in (HERE, os.path.join(HERE, "..", "server")):
    sys.path.insert(0, d)
import JevServer as JS  # noqa: E402

N = 24


def rng(bars):
    return max(b[1] for b in bars) - min(b[2] for b in bars)


def load(path, every):
    import json
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("bars"):
                    out.append(r)
    by = {}
    for r in out:
        by.setdefault(r["asset"], []).append(r)
    rows = []
    for seq in by.values():
        last = [[float(x) for x in r["bars"][-1][:4]] for r in seq]
        for i in range(0, len(seq) - N, max(1, every)):
            r = seq[i]
            bars = [[float(x) for x in b[:4]] for b in r["bars"]]
            if any(min(b) <= 0 or b[1] < b[2] for b in bars):
                continue
            past, fut = rng(bars[-N:]), rng(last[i + 1:i + 1 + N])
            avg = sum(rng(bars[-N - k:len(bars) - k]) for k in range(0, 100, N)) / len(range(0, 100, N))
            feats = JS.regime_features(bars)
            if feats is None or avg <= 0:
                continue
            feats["range24_atr"] = round(past / JS.atr(bars, 14), 2)
            feats["range24_vs_100bar_avg"] = round(past / avg, 2)
            req = {"asset": r["asset"], "tf": "H1", "vol_q": 1, "bars": r["bars"]}
            rows.append((req, feats, 1 if fut > past else 0, -past / avg))
    return rows


QUESTION = {"wider": {
    "type": "noul",
    "instructions": {
        "question": "Will the price range (highest high minus lowest low) of the next 24 H1 bars be wider than "
                    "the range of the last 24 bars?",
        "inputs": "All price values are relative, in units of atr14. range24_atr = range of the last 24 bars / atr14; "
                  "range24_vs_100bar_avg = last 24-bar range / average 24-bar range over the last 100 bars. "
                  "recent_bars_atr are [open, high, low, close] minus the last close, divided by atr14, oldest first."},
    "criteria": {"true": "the next 24 bars move in a wider range", "false": "the next 24 bars are calmer"}}}


def ask(key, req, feats, cache):
    k = JS.JevCache.key(req)
    a = cache.get(k)
    if a is None:
        state = {"symbol": req["asset"], "timeframe": "H1"}
        state.update(feats)
        a = JS.jev_call(key, state, QUESTION).get("answers", {})
        cache.put(k, a)
    return JS._noul(a.get("wider"))


def auc(pairs):
    pos = [p for p, y in pairs if y == 1]
    neg = [p for p, y in pairs if y == 0]
    if not pos or not neg:
        return None
    ranked = sorted([(p, 1) for p in pos] + [(p, 0) for p in neg])
    s, i = 0.0, 0
    while i < len(ranked):
        j = i
        while j < len(ranked) and ranked[j][0] == ranked[i][0]:
            j += 1
        s += (i + 1 + j) / 2.0 * sum(1 for t in ranked[i:j] if t[1] == 1)
        i = j
    return (s - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export", nargs="?", default=JS.DEFAULT_EXPORT)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--every", type=int, default=2)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--cache", default=os.path.join(HERE, "jev_vol_cache.jsonl"))
    args = ap.parse_args()
    rows = load(os.path.abspath(args.export), args.every)
    cache = JS.JevCache(os.path.abspath(args.cache))
    print("Kiértékelhető óra: %d (minden %d.)" % (len(rows), args.every))
    if args.fetch:
        from concurrent.futures import ThreadPoolExecutor
        key = JS.api_key()
        done = [0]
        lock = threading.Lock()
        t0 = time.time()

        def one(row):
            try:
                ask(key, row[0], row[1], cache)
            except JS.JevError as e:
                print("  hiba:", e)
            with lock:
                done[0] += 1
                if done[0] % 300 == 0 or done[0] == len(rows):
                    print("  %d / %d (%.0f mp)" % (done[0], len(rows), time.time() - t0))
        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            list(ex.map(one, rows))
    jev, base, ys = [], [], []
    for req, feats, y, b in rows:
        a = cache.get(JS.JevCache.key(req))
        if a is None:
            continue
        jev.append((JS._noul(a.get("wider")), y))
        base.append((b, y))
        ys.append(y)
    if not jev:
        print("Nincs Jev válasz, futtasd --fetch-csel.")
        return
    print("\nAlapgyakoriság: a következő 24 bar szélesebb %.1f%%-ban" % (100.0 * sum(ys) / len(ys)))
    print("AUC (0.5 = vak találgatás):")
    print("  Jev                        %.3f" % auc(jev))
    print("  képlet (visszahúzás)       %.3f" % auc(base))
    comb = [((p - 0.5) + 0.5 * (b + 1), y) for (p, y), (b, _) in zip(jev, base)]
    print("  Jev + képlet (átlag)       %.3f" % auc(comb))
    print("\nKalibráció (Jev esély -> valóban szélesebb):")
    for lo, hi in ((0, .3), (.3, .4), (.4, .5), (.5, .6), (.6, .7), (.7, 1.01)):
        b = [y for p, y in jev if lo <= p < hi]
        if b:
            print("  p %.1f-%.1f: %5d eset, valóban szélesebb %5.1f%%" % (lo, min(hi, 1), len(b), 100.0 * sum(b) / len(b)))


if __name__ == "__main__":
    main()
