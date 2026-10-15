# =================================================================
# "Fordított Jev" mérés régebbi, független időszakon (alapból 2019-2023, EUR/USD + GBP/USD).
#
# A 2026-os mérésekben a Jev a jövőre vonatkozó kérdésekre rendszeresen fordítva tippelt (AUC ~0.43).
# Itt azt nézzük, hogy ez más időszakon is így van-e, és a fordított jel a spread után is nyer-e.
#
# Adat: a Zorro History M1 .t6 fájljaiból H1 gyertyák (pontos időbélyeggel). Véletlen órák,
# állapot = az utolsó 200 H1 bar (ATR-hez viszonyítva, mint a JevServer regime_features), kérdés =
# a JevTrader tp_first kérdése long és short irányra. Kimenet: a következő 120 bar, TP 3xATR / SL 1.5xATR.
#
#   python inverse_jev_oos.py --history C:\...\z12\History [--from 2019 --to 2023 --n 4000]
# =================================================================
import argparse
import datetime as dt
import os
import random
import struct
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
for d in (HERE, os.path.join(HERE, "..", "server")):
    sys.path.insert(0, d)
import JevServer as JS  # noqa: E402
from calibrate_vol import auc  # noqa: E402
from calibrate_regime import outcome  # noqa: E402

OLE0 = dt.datetime(1899, 12, 30)
SPREAD_PIPS = {"EURUSD": 1.0, "GBPUSD": 1.5}
PIP = 0.0001
INPUTS = ("H1 closed bars. All price values are relative, in units of atr14: recent_bars_atr are "
          "[open, high, low, close] minus the last close, divided by atr14, oldest first.")


def tp_q(side):
    up = side == "long"
    return {"type": "noul", "instructions": {
        "question": "If a %s trade is opened at the last close now, will price reach the take profit (3x atr14 %s) "
                    "before the stop loss (1.5x atr14 %s)?" % (side, "above" if up else "below", "below" if up else "above"),
        "inputs": INPUTS}, "criteria": {"true": "take profit is hit first", "false": "stop loss is hit first"}}


QUESTIONS = {"long": tp_q("long"), "short": tp_q("short")}


def h1_bars(hist, sym, y0, y1):
    """[(datetime, [o,h,l,c])] H1, legrégebbi elöl, a megadott évekre (+1 év előtte a 200 bar miatt)."""
    agg = {}
    for y in range(y0 - 1, y1 + 2):
        p = os.path.join(hist, "%s_%d.t6" % (sym, y))
        if not os.path.exists(p):
            continue
        data = open(p, "rb").read()
        for i in range(0, len(data) - 31, 32):
            t, hi, lo, op, cl, _, _ = struct.unpack_from("<d6f", data, i)
            if hi <= 0 or lo <= 0:
                continue
            ts = OLE0 + dt.timedelta(days=t)
            k = ts.replace(minute=0, second=0, microsecond=0)
            m = ts  # minute stamp, the file is newest first
            b = agg.get(k)
            if b is None:
                agg[k] = [m, op, hi, lo, cl, m]  # first_min, open, high, low, close, last_min
            else:
                if m < b[0]:
                    b[0], b[1] = m, op
                if m > b[5]:
                    b[5], b[4] = m, cl
                b[2] = max(b[2], hi)
                b[3] = min(b[3], lo)
    return [(k, [b[1], b[2], b[3], b[4]]) for k, b in sorted(agg.items())]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", required=True)
    ap.add_argument("--from", dest="y0", type=int, default=2019)
    ap.add_argument("--to", dest="y1", type=int, default=2023)
    ap.add_argument("--n", type=int, default=4000)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--cache", default=os.path.join(HERE, "jev_oos_cache.jsonl"))
    args = ap.parse_args()

    cand = []
    for sym, asset in (("EURUSD", "EUR/USD"), ("GBPUSD", "GBP/USD")):
        bars = h1_bars(args.history, sym, args.y0, args.y1)
        print("%s: %d H1 bar" % (sym, len(bars)))
        for i in range(200, len(bars) - 120):
            if not (args.y0 <= bars[i][0].year <= args.y1):
                continue
            cand.append((sym, asset, i, bars))
    rng = random.Random(23)
    rng.shuffle(cand)
    cand = cand[:args.n]

    cache = JS.JevCache(os.path.abspath(args.cache))
    key = JS.api_key()
    rows, lock = [], threading.Lock()

    def one(c):
        sym, asset, i, bars = c
        win = [b for _, b in bars[i - 199:i + 1]]
        fut = [b for _, b in bars[i + 1:i + 121]]
        a = JS.atr(win, 14)
        if not a:
            return
        feats = JS.regime_features(win)
        req = {"oos": 1, "sym": sym, "t": bars[i][0].isoformat()}
        k = JS.JevCache.key(req)
        ans = cache.get(k)
        if ans is None:
            state = {"symbol": asset, "timeframe": "H1"}
            state.update(feats)
            try:
                ans = JS.jev_call(key, state, QUESTIONS).get("answers", {})
            except JS.JevError as e:
                print("hiba:", e)
                return
            cache.put(k, ans)
        e = win[-1][3]
        cost_r = SPREAD_PIPS[sym] * PIP / (JS.SL_ATR * a)
        with lock:
            rows.append({"t": bars[i][0], "pl": JS._noul(ans.get("long")), "ps": JS._noul(ans.get("short")),
                         "yl": outcome(e, a, fut, "long"), "ys": outcome(e, a, fut, "short"),
                         "pos": feats.get("range_pos20"), "cost": cost_r})

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=args.threads) as ex:
        list(ex.map(one, cand))

    rr = JS.TP_ATR / JS.SL_ATR
    print("\nMinta: %d óra, %d-%d, EUR/USD + GBP/USD\n" % (len(rows), args.y0, args.y1))
    for side, p, y in (("long", "pl", "yl"), ("short", "ps", "ys")):
        pairs = [(r[p], r[y]) for r in rows if r[y] is not None]
        base = [((-r["pos"] if side == "long" else r["pos"]), r[y]) for r in rows if r[y] is not None and r["pos"] is not None]
        print("%s: AUC Jev %.3f (fordítva %.3f)   |  AUC sávszél-képlet %.3f" % (side.upper(), auc(pairs), 1 - auc(pairs), auc(base)))

    print("\nFordított Jev: kötés abba az irányba, amelyikre a Jev a LEGKISEBB TP-esélyt adja, csak a legalsó X%-ban")
    print("(SL 1.5xATR, TP 3xATR, spread levonva; nullszaldó TP% költség nélkül 33.3%)")
    allp = sorted([r["pl"] for r in rows] + [r["ps"] for r in rows])
    for q in (0.05, 0.10, 0.20, 0.30):
        thr = allp[int(len(allp) * q)]
        res = []
        for r in rows:
            for p, y in (("pl", "yl"), ("ps", "ys")):
                if r[p] <= thr and r[y] is not None:
                    res.append((r[y] * rr - (1 - r[y])) - r["cost"])
        if res:
            wins = sum(1 for x in res if x > 0)
            print("  legalsó %2d%% (p<=%.2f): %5d kötés, TP %5.1f%%, átlag %+.3f R/kötés"
                  % (q * 100, thr, len(res), 100.0 * wins / len(res), sum(res) / len(res)))
    blind = [((r[y] * rr - (1 - r[y])) - r["cost"]) for r in rows for y in ("yl", "ys") if r[y] is not None]
    print("  vak belépés (minden óra, mindkét irány): átlag %+.3f R/kötés" % (sum(blind) / len(blind)))
    print("\nÉvenként (fordított Jev, legalsó 20%):")
    thr = allp[int(len(allp) * 0.20)]
    for yr in range(args.y0, args.y1 + 1):
        res = [((r[y] * rr - (1 - r[y])) - r["cost"]) for r in rows if r["t"].year == yr
               for p, y in (("pl", "yl"), ("ps", "ys")) if r[p] <= thr and r[y] is not None]
        if res:
            print("  %d: %4d kötés, átlag %+.3f R" % (yr, len(res), sum(res) / len(res)))


if __name__ == "__main__":
    main()
