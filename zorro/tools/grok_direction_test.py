# =================================================================
# A Grok által javasolt "tiszta" irány-kérdés mérése, pontosan a leírása szerint:
#   - csak az instrumentum neve + az utolsó 48 H1 gyertya, az utolsó záróhoz viszonyítva
#     [o/c_last-1, h/c_last-1, l/c_last-1, c/c_last-1], legrégebbi elöl — semmi indikátor
#   - 3 megfogalmazás egy hívásban (direction, direction_alt1, direction_alt2)
#   - döntés: long ha pL>=0.58 és pL-pS>=0.12, short fordítva, különben kimarad
#   - cél: a KÖVETKEZŐ H1 záró magasabb-e; kötés 1 barig, spread levonva (EUR 1.0, GBP 1.5 pip)
#   - ellenpróba: pénzfeldobás, momentum (close[0]-close[5] előjele)
#
#   python grok_direction_test.py --history C:\...\z12\History [--n 3000]
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
from calibrate_vol import auc  # noqa: E402
from inverse_jev_oos import h1_bars  # noqa: E402

SPREAD = {"EURUSD": 1.0, "GBPUSD": 1.5}
PIP = 0.0001
N = 48
INPUTS = "instrument name and the ordered list of [open, high, low, close] candles (oldest first)"
QUESTIONS = {
    "direction": {"type": "choice", "instructions": {
        "question": "From the sequence of the last N H1 OHLC candles only, what is the more probable direction of the "
                    "next H1 close relative to the current close?", "inputs": INPUTS},
        "criteria": {"long": "Next H1 close is more likely to be higher than the current close",
                     "short": "Next H1 close is more likely to be lower than the current close"}},
    "direction_alt1": {"type": "choice", "instructions": {
        "question": "Looking only at the supplied raw H1 candle sequence, does the price path show stronger evidence of "
                    "continuation higher or continuation lower over the next bar?",
        "inputs": "instrument and the ordered [open, high, low, close] list"},
        "criteria": {"long": "Stronger evidence of upward continuation",
                     "short": "Stronger evidence of downward continuation"}},
    "direction_alt2": {"type": "noul", "instructions": {
        "question": "Based solely on the last N raw H1 OHLC candles, is the probability that the next H1 close finishes "
                    "above the current close greater than 0.5?", "inputs": "instrument and ordered candle list"},
        "criteria": {"true": "Yes, next close more likely above current close",
                     "false": "No, next close more likely at or below current close"}},
}


def p_long(ans, k):
    if k == "direction_alt2":
        return JS._noul(ans.get(k))
    p = JS._probs(ans.get(k), ("long", "short"))
    return p["long"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", required=True)
    ap.add_argument("--n", type=int, default=3000, help="óra időszakonként")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--cache", default=os.path.join(HERE, "jev_grokdir_cache.jsonl"))
    args = ap.parse_args()
    periods = [("2019-2023", 2019, 2023), ("2024-2026", 2024, 2026)]
    series = {}
    for sym, asset in (("EURUSD", "EUR/USD"), ("GBPUSD", "GBP/USD")):
        series[sym] = (asset, h1_bars(args.history, sym, 2019, 2026))
    cache = JS.JevCache(os.path.abspath(args.cache))
    key = JS.api_key()

    for pname, y0, y1 in periods:
        cand = []
        for sym, (asset, bars) in series.items():
            for i in range(N, len(bars) - 1):
                if y0 <= bars[i][0].year <= y1:
                    cand.append((sym, asset, i, bars))
        random.Random(31 + y0).shuffle(cand)
        cand = cand[:args.n]
        rows, lock = [], threading.Lock()

        def one(c):
            sym, asset, i, bars = c
            win = [b for _, b in bars[i - N + 1:i + 1]]
            cl = win[-1][3]
            rel = [[round(x / cl - 1, 6) for x in b] for b in win]
            req = {"grokdir": 1, "sym": sym, "t": bars[i][0].isoformat()}
            k = JS.JevCache.key(req)
            ans = cache.get(k)
            if ans is None:
                try:
                    ans = JS.jev_call(key, {"instrument": asset, "candles": rel}, QUESTIONS).get("answers", {})
                except JS.JevError as e:
                    print("hiba:", e)
                    return
                cache.put(k, ans)
            nxt = bars[i + 1][1][3]
            move = (nxt - cl) / PIP
            mom = cl - win[-6][3]
            with lock:
                rows.append({"p": {q: p_long(ans, q) for q in QUESTIONS}, "move": move, "y": int(nxt > cl),
                             "mom": mom, "cost": SPREAD[sym]})

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            list(ex.map(one, cand))

        print("\n=== %s: %d óra (EUR/USD + GBP/USD), cél: a következő H1 záró feljebb? (valóban: %.1f%%) ==="
              % (pname, len(rows), 100.0 * sum(r["y"] for r in rows) / len(rows)))
        print("  %-16s %6s %8s   %s" % ("", "AUC", "átlag pL", "szabály (0.58 / 0.12): kötés, találat, pip/kötés spread után"))
        for q in QUESTIONS:
            pairs = [(r["p"][q], r["y"]) for r in rows]
            tr = []
            for r in rows:
                pl = r["p"][q]
                ps = 1 - pl
                d = 1 if (pl >= 0.58 and pl - ps >= 0.12) else -1 if (ps >= 0.58 and ps - pl >= 0.12) else 0
                if d:
                    tr.append(d * r["move"] - r["cost"])
            ex_s = ("%5d kötés, találat %5.1f%%, %+.2f pip" % (len(tr), 100.0 * sum(1 for t in tr if t + 0 > -1e9 and t > 0) / len(tr),
                                                               sum(tr) / len(tr))) if tr else "nincs kötés (a Jev sosem ennyire biztos)"
            arg = [(1 if r["p"][q] >= 0.5 else -1) * r["move"] - r["cost"] for r in rows]
            print("  %-16s %6.3f %8.2f   %s | mindig a valószínűbb irány: %+.2f pip"
                  % (q, auc(pairs), sum(p for p, _ in pairs) / len(pairs), ex_s, sum(arg) / len(arg)))
        rng = random.Random(5)
        coin = [(1 if rng.random() < 0.5 else -1) * r["move"] - r["cost"] for r in rows]
        mom = [((r["mom"] > 0) - (r["mom"] < 0)) * r["move"] - r["cost"] for r in rows if r["mom"] != 0]
        print("  %-16s %6s %8s   minden órában: %+.2f pip/kötés" % ("pénzfeldobás", "0.500", "", sum(coin) / len(coin)))
        print("  %-16s %6.3f %8s   minden órában: %+.2f pip/kötés" % ("momentum (5 bar)", auc([(r["mom"], r["y"]) for r in rows]), "",
                                                               sum(mom) / len(mom)))


if __name__ == "__main__":
    main()
