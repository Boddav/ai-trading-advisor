# =================================================================
# Jev kalibráció a backtest adatokon — új Jev hívás NÉLKÜL
#
# Minden exportált kérdésnél (Data\JevExport.jsonl, JEV_TEST_MODE 2) a jev_cache.jsonl-ből
# kiveszi, mit mondott a Jev ("long/short: TP előbb, mint SL?"), a KÖVETKEZŐ órák
# gyertyáiból pedig megnézi, mi történt valójában: TP (3xATR) vagy SL (1.5xATR) jött előbb.
#
# Kiírja:
#   - kalibráció: amikor a Jev X-et mondott, ténylegesen hányszor jött előbb a TP
#   - rangsor erő (AUC): 0.50 = semmit sem tud, 1.00 = tökéletes
#   - küszöb-táblázat: kötésszám, találati arány, várható eredmény R-ben
#     (TP = +2R, SL = -1R, költségek nélkül) a MIN_TP_FIRST / MIN_DIR_EDGE szerint
#
# Futtatás a JevServer.py mellett:  python calibrate_jev.py [export.jsonl] [--horizon 120]
# =================================================================
import argparse
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # a Zorro Strategy mappában minden egy helyen van
sys.path.insert(0, os.path.join(HERE, "..", "server"))  # a repóban: zorro/server/
import JevServer as JS  # noqa: E402

RR = JS.TP_ATR / JS.SL_ATR  # 2.0


def load_requests(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def outcome(entry, atr, future, side):
    """1 = TP előbb, 0 = SL előbb (egy baron belül mindkettő: SL, óvatosan), None = nem dőlt el."""
    if side == "long":
        tp, sl = entry + JS.TP_ATR * atr, entry - JS.SL_ATR * atr
        for _, h, l, _ in future:
            if l <= sl:
                return 0
            if h >= tp:
                return 1
    else:
        tp, sl = entry - JS.TP_ATR * atr, entry + JS.SL_ATR * atr
        for _, h, l, _ in future:
            if h >= sl:
                return 0
            if l <= tp:
                return 1
    return None


def auc(pairs):
    """Rangsor erő: annak esélye, hogy egy TP-first eset nagyobb p-t kapott, mint egy SL-first."""
    pos = [p for p, y in pairs if y == 1]
    neg = [p for p, y in pairs if y == 0]
    if not pos or not neg:
        return None
    ranked = sorted([(p, 1) for p in pos] + [(p, 0) for p in neg])
    rank_sum, i = 0.0, 0
    while i < len(ranked):  # átlagrang a döntetlenekre
        j = i
        while j < len(ranked) and ranked[j][0] == ranked[i][0]:
            j += 1
        avg = (i + 1 + j) / 2.0
        rank_sum += avg * sum(1 for k in range(i, j) if ranked[k][1] == 1)
        i = j
    return (rank_sum - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))


def build_rows(reqs, cache, horizon):
    """-> list of dict(asset, p_long, p_short, y_long, y_short)"""
    by_asset = defaultdict(list)
    for r in reqs:
        if str(r.get("pos", "flat")) == "flat" and r.get("bars"):
            by_asset[r["asset"]].append(r)
    rows, missing, gaps = [], 0, 0
    for asset, seq in by_asset.items():
        closes = [tuple(float(x) for x in r["bars"][-1][:4]) for r in seq]
        for i, r in enumerate(seq):
            answers = cache.get(JS.JevCache.key(r))
            if answers is None:
                missing += 1
                continue
            # a következő kérések utolsó barja = a következő órák; ellenőrizzük a folytonosságot
            if i + 1 < len(seq) and tuple(float(x) for x in seq[i + 1]["bars"][-2][:4]) != closes[i]:
                gaps += 1
                continue
            bars = [[float(x) for x in b[:4]] for b in r["bars"]]
            a = JS.atr(bars, 14)
            if not a:
                continue
            future = closes[i + 1:i + 1 + horizon]
            entry = bars[-1][3]
            rows.append({
                "asset": asset,
                "p_long": JS._noul(answers.get("long_tp_first")),
                "p_short": JS._noul(answers.get("short_tp_first")),
                "y_long": outcome(entry, a, future, "long"),
                "y_short": outcome(entry, a, future, "short"),
            })
    return rows, missing, gaps


def report(rows):
    print("\n=== KALIBRÁCIÓ: amikor a Jev ennyit mondott -> ténylegesen ennyiszer jött előbb a TP ===")
    print("(nullszaldó 1:%.0f aránynál %.1f%%, költségek nélkül)" % (RR, 100 / (1 + RR)))
    for side in ("long", "short"):
        pairs = [(r["p_" + side], r["y_" + side]) for r in rows if r["y_" + side] is not None]
        if not pairs:
            continue
        base = sum(y for _, y in pairs) / len(pairs)
        a = auc(pairs)
        print("\n%s: %d eset, átlag Jev p=%.3f, valódi TP-first arány=%.1f%%, AUC=%s"
              % (side.upper(), len(pairs), sum(p for p, _ in pairs) / len(pairs), 100 * base,
                 "%.3f" % a if a is not None else "n/a"))
        for lo, hi in ((0, .2), (.2, .3), (.3, .35), (.35, .4), (.4, .45), (.45, .5), (.5, .6), (.6, 1.01)):
            b = [y for p, y in pairs if lo <= p < hi]
            if b:
                print("  p %.2f-%.2f: %5d eset, TP előbb %5.1f%%" % (lo, min(hi, 1), len(b), 100.0 * sum(b) / len(b)))

    print("\n=== KÜSZÖB-TÁBLÁZAT (a JevServer döntési szabálya szerint) ===")
    print("  MIN_TP_FIRST  MIN_DIR_EDGE  kötés   TP%    várható R/kötés  összes R")
    for min_tp in (0.35, 0.40, 0.45, 0.50, 0.55):
        for edge in (0.0, 0.05, 0.10):
            res = []
            for r in rows:
                d, p, other = ("long", r["p_long"], r["p_short"]) if r["p_long"] >= r["p_short"] \
                    else ("short", r["p_short"], r["p_long"])
                y = r["y_" + d]
                if p >= min_tp and p - other >= edge and y is not None:
                    res.append(y)
            if res:
                wr = sum(res) / len(res)
                exp = wr * RR - (1 - wr)
                print("  %11.2f  %12.2f  %5d  %5.1f%%  %+14.3f  %+8.1f" % (min_tp, edge, len(res), 100 * wr, exp, exp * len(res)))
    allbars = [r["y_long"] for r in rows if r["y_long"] is not None] + [r["y_short"] for r in rows if r["y_short"] is not None]
    if allbars:
        wr = sum(allbars) / len(allbars)
        print("\nÖsszehasonlításul, VAK belépés minden órában (long és short): TP%% = %.1f%%, várható R = %+.3f"
              % (100 * wr, wr * RR - (1 - wr)))
    print("\nOlvasat: ha a TP% a küszöb felett nem jobb, mint a vak belépésé, és az AUC ~0.50,")
    print("a Jevnek ezen az adaton nincs előrejelző ereje. A spread/jutalék még le is jön a várható R-ből.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export", nargs="?", default=JS.DEFAULT_EXPORT)
    ap.add_argument("--cache", default=JS.CACHE_FILE)
    ap.add_argument("--horizon", type=int, default=120, help="max ennyi órát vár a TP/SL-re")
    args = ap.parse_args()
    if JS.QUESTION_MODE != "tp_first":
        sys.exit("A JevServer.py QUESTION_MODE legyen 'tp_first' (ehhez a módhoz készült az elemzés).")
    reqs = load_requests(os.path.abspath(args.export))
    cache = JS.JevCache(os.path.abspath(args.cache))
    rows, missing, gaps = build_rows(reqs, cache, args.horizon)
    print("Export: %d kérdés, cache: %d válasz, kiértékelhető: %d (nincs cache-ben: %d, nem folytonos: %d)"
          % (len(reqs), len(cache), len(rows), missing, gaps))
    if not rows:
        sys.exit("Nincs kiértékelhető eset: futott a prefetch_jev.bat tp_first módban ugyanerre az exportra?")
    report(rows)


if __name__ == "__main__":
    main()
