# =================================================================
# Megfogalmazás-teszt: ugyanaz a jövőre vonatkozó kérdés 4 különböző angol megfogalmazásban,
# ugyanazokon az órákon, ugyanazzal az állapottal (egy hívás, a kérdések egymástól függetlenül futnak).
#
#   V1 tp_first   — a JevTrader mostani kérdése: "long nyitásnál előbb a TP-t (3xATR) éri el, mint az SL-t (1.5xATR)?"
#   V2 sl_first   — ugyanez fordítva: "előbb az SL-t éri el?"  (jó modellnél V2 = 1 - V1)
#   V3 up24       — egyszerű irány: "24 bar múlva magasabban lesz az ár?"
#   V4 trader     — szakértői keret + cél leírása: "tapasztalt FX kereskedőként mekkora esélyt adsz, hogy ez a long nyer?"
#
# Mér: (a) mennyire egyezik a 4 megfogalmazás egymással (korreláció), (b) melyik mennyire jósol (AUC),
#      (c) a válaszok terjedelme (ha mind 0.4-0.5 körül van, a Jev nem lát semmit).
#
#   python phrasing_test.py [export.jsonl] [--n 600]
# =================================================================
import argparse
import json
import os
import random
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
for d in (HERE, os.path.join(HERE, "..", "server")):
    sys.path.insert(0, d)
import JevServer as JS  # noqa: E402
from calibrate_vol import auc  # noqa: E402
from calibrate_regime import outcome  # noqa: E402

INPUTS = ("EUR/USD, GBP/USD or XAU/USD H1 closed bars. All price values are relative, in units of atr14: "
          "recent_bars_atr are [open, high, low, close] minus the last close, divided by atr14, oldest first.")


def yn(q, t, f, goal=None):
    ins = {"question": q, "inputs": INPUTS}
    if goal:
        ins["goal"] = goal
    return {"type": "noul", "instructions": ins, "criteria": {"true": t, "false": f}}


QUESTIONS = {
    "V1_tp_first": yn("If a long trade is opened at the last close now, will price reach the take profit (3x atr14 above) "
                      "before the stop loss (1.5x atr14 below)?", "take profit is hit first", "stop loss is hit first"),
    "V2_sl_first": yn("If a long trade is opened at the last close now, will price reach the stop loss (1.5x atr14 below) "
                      "before the take profit (3x atr14 above)?", "stop loss is hit first", "take profit is hit first"),
    "V3_up24": yn("Will the close 24 bars from now be higher than the last close?", "higher", "lower or equal"),
    "V4_trader": yn("As an experienced FX trader looking at this chart, would a long trade opened now with a stop 1.5x atr14 "
                    "below and a target 3x atr14 above end as a winner?", "the trade wins (target reached first)",
                    "the trade loses (stop reached first)",
                    goal="Judge only from the price action shown. A win needs the target before the stop."),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export", nargs="?", default=JS.DEFAULT_EXPORT)
    ap.add_argument("--n", type=int, default=600)
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()
    reqs = [json.loads(l) for l in open(os.path.abspath(args.export), encoding="utf-8") if l.strip()]
    by = {}
    for r in reqs:
        by.setdefault(r["asset"], []).append(r)
    cand = []
    for seq in by.values():
        last = [[float(x) for x in r["bars"][-1][:4]] for r in seq]
        for i in range(len(seq) - 120):
            bars = [[float(x) for x in b[:4]] for b in seq[i]["bars"]]
            if any(min(b) <= 0 or b[1] < b[2] for b in bars):
                continue
            a = JS.atr(bars, 14)
            fut = last[i + 1:i + 121]
            e = bars[-1][3]
            y_tp = outcome(e, a, fut, "long")
            cand.append((seq[i]["asset"], bars, y_tp, int(fut[23][3] > e)))
    random.Random(11).shuffle(cand)
    cand = cand[:args.n]

    key = JS.api_key()
    rows, lock = [], threading.Lock()

    def one(c):
        asset, bars, y_tp, y_up = c
        feats = JS.regime_features(bars)
        state = {"symbol": asset, "timeframe": "H1"}
        state.update(feats)
        try:
            ans = JS.jev_call(key, state, QUESTIONS).get("answers", {})
        except JS.JevError as e:
            print("hiba:", e)
            return
        with lock:
            rows.append(({k: JS._noul(ans.get(k)) for k in QUESTIONS}, y_tp, y_up))

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=args.threads) as ex:
        list(ex.map(one, cand))

    print("Minta: %d óra (EUR/USD, GBP/USD, XAU/USD vegyesen)\n" % len(rows))
    print("  %-12s %7s %7s %7s   %s" % ("kérdés", "átlag", "min", "max", "AUC: TP előbb (long)   AUC: 24 bar múlva feljebb"))
    for k in QUESTIONS:
        ps = [r[0][k] for r in rows]
        sign = -1 if k == "V2_sl_first" else 1   # V2 fordítva értendő
        tp = [(sign * r[0][k], r[1]) for r in rows if r[1] is not None]
        up = [(sign * r[0][k], r[2]) for r in rows]
        print("  %-12s %7.2f %7.2f %7.2f   %18.3f   %24.3f" % (k, sum(ps) / len(ps), min(ps), max(ps), auc(tp), auc(up)))

    def corr(a, b):
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        c = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        s = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
        return c / s if s else 0.0

    ks = list(QUESTIONS)
    print("\nKorreláció a megfogalmazások között (V2 előjele megfordítva; 1 = ugyanazt gondolja):")
    print("  %-12s" % "" + "".join("%13s" % k for k in ks))
    for a in ks:
        va = [(-1 if a == "V2_sl_first" else 1) * r[0][a] for r in rows]
        line = "  %-12s" % a
        for b in ks:
            vb = [(-1 if b == "V2_sl_first" else 1) * r[0][b] for r in rows]
            line += "%13.2f" % corr(va, vb)
        print(line)
    v1 = [r[0]["V1_tp_first"] + r[0]["V2_sl_first"] for r in rows]
    print("\nV1 + V2 átlaga: %.2f  (logikusan 1.00 lenne: TP előbb + SL előbb = biztos)" % (sum(v1) / len(v1)))
    tps = [r[1] for r in rows if r[1] is not None]
    print("Valóság: TP előbb %.1f%% (nullszaldó 33.3%%), 24 bar múlva feljebb %.1f%%"
          % (100.0 * sum(tps) / len(tps), 100.0 * sum(r[2] for r in rows) / len(rows)))


if __name__ == "__main__":
    main()
