# =================================================================
# A "regime" mód mérése a backtest exporton (Data\JevExport.jsonl).
#
# A Jev minden exportált órára megmondja, milyen a piac (trending / ranging / choppy),
# a kód ebből dönt (JevServer.regime_policy). Két kérdésre ad választ:
#   1) Eltalálja-e a Jev a piac jellegét? -> a következő 20 bar valódi "efficiency ratio"-ja
#      (1 = egyenes trend, 0 = helyben toporgás) rezsimenként. Ha a "trending" órák utáni
#      ER nem nagyobb, mint a "ranging" órák utáni, a Jev nem látja a piac jellegét.
#   2) Hoz-e pénzt? Ugyanazokon az órákon, ugyanazzal a szabállyal és SL/TP-vel:
#        Jev rezsim | mindig trend | mindig oldalazás | véletlen rezsim (a Jev arányaival)
#        | egyszerű képlet (ER20 múltbeli értéke alapján)
#
# Futtatás a JevServer.py mellett (Strategy mappa) vagy a repóból:
#   python calibrate_regime.py [export.jsonl] --fetch     (hiányzó Jev válaszok lekérése, 8 szálon)
#   python calibrate_regime.py [export.jsonl]             (csak a cache-ből)
# =================================================================
import argparse
import os
import random
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for d in (HERE, os.path.join(HERE, "..", "server")):
    sys.path.insert(0, d)
import JevServer as JS  # noqa: E402

RR = JS.TP_ATR / JS.SL_ATR


def load(path):
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
                if str(r.get("pos", "flat")) == "flat" and r.get("bars"):
                    r["mode"] = "regime"
                    out.append(r)
    return out


def outcome(entry, a, path, side):
    up = side == "long"
    tp = entry + JS.TP_ATR * a if up else entry - JS.TP_ATR * a
    sl = entry - JS.SL_ATR * a if up else entry + JS.SL_ATR * a
    for _, h, l, _ in path:
        if (l <= sl) if up else (h >= sl):
            return 0
        if (h >= tp) if up else (l <= tp):
            return 1
    return None


def future_er(closes):
    path = sum(abs(b - a) for a, b in zip(closes, closes[1:]))
    return abs(closes[-1] - closes[0]) / path if path > 0 else None


def rows(reqs, horizon):
    """-> [(req, bars, feats, y_long, y_short, er_next20)]"""
    by_asset = {}
    for r in reqs:
        by_asset.setdefault(r["asset"], []).append(r)
    out = []
    for seq in by_asset.values():
        last = [[float(x) for x in r["bars"][-1][:4]] for r in seq]
        for i, r in enumerate(seq):
            bars = [[float(x) for x in b[:4]] for b in r["bars"]]
            if any(min(b) <= 0 or b[1] < b[2] for b in bars):
                continue
            feats = JS.regime_features(bars)
            if feats is None:
                continue
            a = JS.atr(bars, 14)
            fut = last[i + 1:i + 1 + horizon]
            e = bars[-1][3]
            er = future_er([e] + [b[3] for b in fut[:20]]) if len(fut) >= 20 else None
            out.append((r, bars, feats, outcome(e, a, fut, "long"), outcome(e, a, fut, "short"), er))
    return out


def act_for(label, feats):
    """Ugyanaz a szabály, mint a JevServer.regime_policy, de adott rezsim címkére."""
    fake = {"regime": {"choice": label, "probabilities": {label: 1.0}}}
    return JS.regime_policy(fake, feats)[0]


def score(name, picks):
    """picks: [(action, y_long, y_short)]"""
    res = [(yl if a == "open_long" else ys) for a, yl, ys in picks if a.startswith("open_")]
    res = [y for y in res if y is not None]
    if not res:
        print("  %-34s  nincs kötés" % name)
        return
    wr = sum(res) / len(res)
    ex = wr * RR - (1 - wr)
    print("  %-34s  %5d kötés  TP %5.1f%%  várható R %+.3f  összes R %+7.1f" % (name, len(res), 100 * wr, ex, ex * len(res)))


def fetch(data, key, cache, threads):
    from concurrent.futures import ThreadPoolExecutor
    todo = [r for r, *_ in data if cache.get(JS.JevCache.key(r)) is None]
    print("Jev lekérdezés: %d hiányzik (%d már cache-ben)" % (len(todo), len(data) - len(todo)))
    done = [0]
    lock = threading.Lock()
    t0 = time.time()

    def one(r):
        try:
            JS.handle_regime(r, key, cache=cache)
        except (JS.JevError, ValueError) as e:
            print("  hiba %s: %s" % (r.get("asset"), e))
        with lock:
            done[0] += 1
            if done[0] % 200 == 0 or done[0] == len(todo):
                print("  %d / %d  (%.0f mp)" % (done[0], len(todo), time.time() - t0))

    with ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
        list(ex.map(one, todo))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export", nargs="?", default=JS.DEFAULT_EXPORT)
    ap.add_argument("--fetch", action="store_true", help="a hiányzó Jev válaszok lekérése")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--horizon", type=int, default=120)
    ap.add_argument("--cache", default=JS.CACHE_FILE)
    args = ap.parse_args()

    data = rows(load(os.path.abspath(args.export)), args.horizon)
    cache = JS.JevCache(os.path.abspath(args.cache))
    print("Export: %d kiértékelhető óra" % len(data))
    if args.fetch:
        fetch(data, JS.api_key(), cache, args.threads)

    jev = []
    for r, bars, feats, yl, ys, er in data:
        ans = cache.get(JS.JevCache.key(r))
        if ans is not None:
            a, _, label, p = JS.regime_policy(ans, feats)
            jev.append((label, p, a, feats, yl, ys, er))
    if not jev:
        print("Nincs Jev válasz a cache-ben ezekre az órákra. Futtasd --fetch kapcsolóval.")
        return
    print("Jev válasz: %d órára\n" % len(jev))

    print("=== 1) ELTALÁLJA-E A PIAC JELLEGÉT? (a következő 20 bar efficiency ratio-ja) ===")
    for lab in JS.REGIMES:
        ers = [er for l, _, _, _, _, _, er in jev if l == lab and er is not None]
        if ers:
            print("  Jev: %-9s %5d óra  utána ER átlag %.3f" % (lab, len(ers), sum(ers) / len(ers)))
    allr = [er for *_, er in jev if er is not None]
    print("  minden óra        %5d óra  utána ER átlag %.3f" % (len(allr), sum(allr) / len(allr)))
    past = [(f["efficiency_ratio20"] or 0, er) for _, _, _, f, _, _, er in jev if er is not None]
    past.sort()
    q = len(past) // 3
    for name, part in (("múltbeli ER alsó harmad", past[:q]), ("múltbeli ER felső harmad", past[-q:])):
        print("  képlet: %-24s utána ER átlag %.3f" % (name, sum(e for _, e in part) / len(part)))

    print("\n=== 2) KÖTÉSEK (SL %.1fxATR, TP %.1fxATR, költségek nélkül; nullszaldó TP%% = %.1f%%) ==="
          % (JS.SL_ATR, JS.TP_ATR, 100 / (1 + RR)))
    score("Jev rezsim", [(a, yl, ys) for _, _, a, _, yl, ys, _ in jev])
    score("mindig 'trending'", [(act_for("trending", f), yl, ys) for *_, f, yl, ys, _ in jev])
    score("mindig 'ranging'", [(act_for("ranging", f), yl, ys) for *_, f, yl, ys, _ in jev])
    freq = [l for l, *_ in jev]
    for s in (1, 2, 3):
        rng = random.Random(s)
        score("véletlen rezsim (Jev arányai) #%d" % s,
              [(act_for(rng.choice(freq), f), yl, ys) for *_, f, yl, ys, _ in jev])

    def formula(f):
        er = f["efficiency_ratio20"] or 0
        return "trending" if er >= 0.35 else "ranging" if er <= 0.15 else "choppy"
    score("képlet: ER20>=0.35 trend, <=0.15 sáv", [(act_for(formula(f), f), yl, ys) for *_, f, yl, ys, _ in jev])
    allb = [y for *_, yl, ys, _ in jev for y in (yl, ys) if y is not None]
    wr = sum(allb) / len(allb)
    print("  %-34s         TP %5.1f%%  várható R %+.3f" % ("vak belépés (minden óra, mindkét irány)", 100 * wr, wr * RR - (1 - wr)))
    print("\nA Jev rezsim akkor ér valamit, ha 1)-ben a 'trending' utáni ER láthatóan nagyobb, mint a 'ranging'"
          " utáni, és 2)-ben jobb a véletlen rezsimnél és a képletnél is.")


if __name__ == "__main__":
    main()
