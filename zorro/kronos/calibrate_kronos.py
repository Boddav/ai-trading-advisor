# =================================================================
# Kronos kiértékelése a backtest exporton (Data\JevExport.jsonl) — Jev hívás NÉLKÜL.
#
# Minden exportált órára lefuttatja a Kronost (mintavételezett pályák), és a KÖVETKEZŐ órák
# valódi gyertyáiból megnézi, mi történt: TP (3xATR) vagy SL (1.5xATR) jött előbb.
# Kiírja: Kronos kalibráció + AUC, küszöb-táblázat (csak Kronos), és ha a jev_cache.jsonl-ben
# vannak Jev válaszok ugyanezekre az órákra: Jev egyedül vs. Jev+Kronos egyetértés.
#
# A Kronos eredményeit kronos_cache.jsonl-be menti, a futás megszakítható és folytatható.
#
# Futtatás (a JevServer.py, calibrate_jev.py és kronos_bridge.py mellett):
#   python calibrate_kronos.py --kronos C:\Kronos [export.jsonl] [--every 3] [--limit 1000]
# =================================================================
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for d in (HERE, os.path.join(HERE, "..", "server"), os.path.join(HERE, "..", "tools")):
    sys.path.insert(0, d)
import JevServer as JS  # noqa: E402
import calibrate_jev as CJ  # noqa: E402
import kronos_bridge as KB  # noqa: E402

RR = KB.TP_ATR / KB.SL_ATR
KCACHE = os.path.join(HERE, "kronos_cache.jsonl")


def load_kcache(path):
    out = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    out[r["k"]] = r["s"]
                except (ValueError, KeyError):
                    pass
    return out


def rows_from_export(reqs, horizon):
    """-> [(req, bars, y_long, y_short)] a flat kérésekre, a jövőbeli barokból számolt kimenettel."""
    by_asset = {}
    for r in reqs:
        if str(r.get("pos", "flat")) == "flat" and r.get("bars"):
            by_asset.setdefault(r["asset"], []).append(r)
    out = []
    for seq in by_asset.values():
        closes = [tuple(float(x) for x in r["bars"][-1][:4]) for r in seq]
        for i, r in enumerate(seq):
            bars = [[float(x) for x in b[:4]] for b in r["bars"]]
            a = KB.atr(bars)
            if not a:
                continue
            future = closes[i + 1:i + 1 + horizon]
            e = bars[-1][3]
            out.append((r, bars, KB.outcome(e, a, future, "long"), KB.outcome(e, a, future, "short")))
    return out


def table(title, picks):
    """picks: [(p_best, p_other, y)] a választott irányra."""
    print("\n=== %s ===" % title)
    print("  küszöb  irány-előny  kötés   TP%    várható R/kötés  összes R")
    for mn in (0.35, 0.40, 0.45, 0.50, 0.55, 0.60):
        for edge in (0.0, 0.05, 0.10):
            res = [y for p, o, y in picks if p >= mn and p - o >= edge and y is not None]
            if len(res) >= 10:
                wr = sum(res) / len(res)
                ex = wr * RR - (1 - wr)
                print("  %6.2f  %11.2f  %5d  %5.1f%%  %+14.3f  %+8.1f" % (mn, edge, len(res), 100 * wr, ex, ex * len(res)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export", nargs="?", default=JS.DEFAULT_EXPORT)
    ap.add_argument("--kronos", required=True, help="a klónozott Kronos repó mappája")
    ap.add_argument("--every", type=int, default=3, help="csak minden N. órát (CPU idő)")
    ap.add_argument("--limit", type=int, default=0, help="legfeljebb ennyi órát")
    ap.add_argument("--horizon", type=int, default=120)
    ap.add_argument("--cache", default=JS.CACHE_FILE, help="Jev válaszok (összehasonlításhoz)")
    args = ap.parse_args()

    reqs = CJ.load_requests(os.path.abspath(args.export))
    rows = rows_from_export(reqs, args.horizon)[::max(1, args.every)]
    if args.limit:
        rows = rows[:args.limit]
    print("Export: %d kérdés, kiértékelendő óra: %d" % (len(reqs), len(rows)))

    kf = KB.KronosForecaster(args.kronos)
    kc = load_kcache(KCACHE)
    jc = JS.JevCache(os.path.abspath(args.cache))
    JS.QUESTION_MODE = "tp_first"
    t0 = time.time()
    data = []
    with open(KCACHE, "a", encoding="utf-8") as kf_out:
        for n, (r, bars, yl, ys) in enumerate(rows, 1):
            k = JS.JevCache.key({"kronos": kf.tag, "asset": r["asset"], "bars": r["bars"]})
            s = kc.get(k)
            if s is None:
                s = kf.stats(bars)
                kf_out.write(json.dumps({"k": k, "s": s}) + "\n")
                kf_out.flush()
            if s:
                ja = jc.get(JS.JevCache.key(r))
                data.append((s, ja, yl, ys))
            if n % 50 == 0 or n == len(rows):
                print("  %d / %d  (%.0f mp)" % (n, len(rows), time.time() - t0))

    print("\n=== KRONOS KALIBRÁCIÓ: amikor a Kronos ennyit mondott -> valójában TP előbb ===")
    for side, idx in (("long", 2), ("short", 3)):
        pairs = [(d[0]["p_tp_first_" + side], d[idx]) for d in data if d[idx] is not None]
        if not pairs:
            continue
        a = CJ.auc(pairs)
        print("%s: %d eset, átlag Kronos p=%.3f, valódi TP-first=%.1f%%, AUC=%s" % (
            side.upper(), len(pairs), sum(p for p, _ in pairs) / len(pairs),
            100 * sum(y for _, y in pairs) / len(pairs), "%.3f" % a if a is not None else "n/a"))
        for lo, hi in ((0, .2), (.2, .3), (.3, .4), (.4, .5), (.5, .6), (.6, 1.01)):
            b = [y for p, y in pairs if lo <= p < hi]
            if b:
                print("  p %.2f-%.2f: %5d eset, TP előbb %5.1f%%" % (lo, min(hi, 1), len(b), 100.0 * sum(b) / len(b)))

    def pick(pl, ps, yl, ys):
        return (pl, ps, yl) if pl >= ps else (ps, pl, ys)

    table("CSAK KRONOS (küszöb = Kronos TP-first esély)",
          [pick(s["p_tp_first_long"], s["p_tp_first_short"], yl, ys) for s, _, yl, ys in data])

    jd = [(s, ja, yl, ys) for s, ja, yl, ys in data if ja]
    if jd:
        jev = [pick(JS._noul(ja.get("long_tp_first")), JS._noul(ja.get("short_tp_first")), yl, ys)
               for _, ja, yl, ys in jd]
        table("CSAK JEV, ugyanezekre az órákra (%d)" % len(jd), jev)
        agree = []
        for s, ja, yl, ys in jd:
            jl, js_ = JS._noul(ja.get("long_tp_first")), JS._noul(ja.get("short_tp_first"))
            d = "long" if jl >= js_ else "short"
            kd = "long" if s["p_tp_first_long"] >= s["p_tp_first_short"] else "short"
            if d == kd and s["p_tp_first_" + d] >= 0.40:
                agree.append((max(jl, js_), min(jl, js_), yl if d == "long" else ys))
        table("JEV + KRONOS EGYETÉRT (Kronos >= 0.40, küszöb = Jev esély)", agree)

    allb = [y for _, _, yl, ys in data for y in (yl, ys) if y is not None]
    if allb:
        wr = sum(allb) / len(allb)
        print("\nVak belépés ugyanezekben az órákban: TP%% = %.1f%%, várható R = %+.3f (költségek nélkül)"
              % (100 * wr, wr * RR - (1 - wr)))


if __name__ == "__main__":
    main()
