# =================================================================
# NFP kutatás: mit csinált az EUR/USD és a GBP/USD a jelentés után, és ki tippelte meg jobban
# az irányt: egyszerű szabály vagy a Jev?
#
# Adat: nfp_mql5.csv (az MQL5 gazdasági naptárából: tényleges, MQL5 előrejelzés, előző — ez utóbbi
# az MQL5 oldalon a felülvizsgált előző hónap). Az MQL5 "forecast" az MQL5 saját modellje, NEM a piaci
# konszenzus (pl. 2026 okt: MQL5 52K, FXStreet konszenzus 90K) — a "meglepetés" így zajos.
# Árak: a Zorro History M1 .t6 fájljai (EURUSD_ÉÉÉÉ.t6, GBPUSD_ÉÉÉÉ.t6).
#
# A Jev csak a számokat kapja, dátumot NEM (hogy ne emlékezhessen a múltbeli piaci reakcióra).
#
#   python nfp_study.py --history C:\...\z12\History [--jev]
# =================================================================
import argparse
import csv
import datetime as dt
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for d in (HERE, os.path.join(HERE, "..", "server")):
    sys.path.insert(0, d)

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # régi Python: nyári időszámítás kézzel (márc. 2. vasárnap - nov. 1. vasárnap)
    NY = None

OLE0 = dt.datetime(1899, 12, 30)
PIP = {"EURUSD": 0.0001, "GBPUSD": 0.0001}


def release_utc(date_s):
    d = dt.date.fromisoformat(date_s)
    if NY:
        return dt.datetime(d.year, d.month, d.day, 8, 30, tzinfo=NY).astimezone(dt.timezone.utc).replace(tzinfo=None)
    m2 = dt.date(d.year, 3, 8 + (6 - dt.date(d.year, 3, 8).weekday()) % 7)
    n1 = dt.date(d.year, 11, 1 + (6 - dt.date(d.year, 11, 1).weekday()) % 7)
    return dt.datetime(d.year, d.month, d.day, 12 if m2 <= d < n1 else 13, 30)


_cache = {}


def minute_closes(hist, sym, year):
    """{datetime(UTC, perc): close} egy év M1 adatából."""
    k = (sym, year)
    if k not in _cache:
        out = {}
        path = os.path.join(hist, "%s_%d.t6" % (sym, year))
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
            for i in range(0, len(data) - 31, 32):
                t, hi, lo, op, cl, _, _ = struct.unpack_from("<d6f", data, i)
                out[(OLE0 + dt.timedelta(days=t)).replace(second=0, microsecond=0)] = cl
        _cache[k] = out
    return _cache[k]


def price_at(hist, sym, when, back=5):
    """A 'when' percben (vagy legfeljebb 'back' perccel előtte) záró ár."""
    m = minute_closes(hist, sym, when.year)
    for k in range(back + 1):
        p = m.get(when - dt.timedelta(minutes=k))
        if p:
            return p
    return None


def jev_direction(key, row, cache):
    import JevServer as JS
    req = {"nfp": [row["actual_k"], row["forecast_k"], row["previous_k"]]}
    k = JS.JevCache.key(req)
    a = cache.get(k)
    if a is None:
        state = {"report": "US Nonfarm Payrolls (monthly change in jobs, thousands)",
                 "actual_k": float(row["actual_k"]), "forecast_k": float(row["forecast_k"]),
                 "previous_month_revised_k": float(row["previous_k"])}
        q = {"usd": {"type": "choice",
                     "instructions": {"question": "In the hour after this US jobs report is released, does the US dollar "
                                                  "strengthen or weaken against the euro and the pound?",
                                      "inputs": "forecast_k is a model forecast published before the release; "
                                                "previous_month_revised_k is the prior month's figure."},
                     "criteria": {"stronger": "the US dollar strengthens", "weaker": "the US dollar weakens"}}}
        a = JS.jev_call(key, state, q).get("answers", {})
        cache.put(k, a)
    p = JS._probs(a.get("usd"), ("stronger", "weaker"))
    return 1 if p["stronger"] >= p["weaker"] else -1, p["stronger"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", required=True)
    ap.add_argument("--jev", action="store_true")
    ap.add_argument("--spread", type=float, default=2.0, help="költség pipben kötésenként (hír utáni spread)")
    args = ap.parse_args()
    rows = list(csv.DictReader(open(os.path.join(HERE, "nfp_mql5.csv"), encoding="utf-8")))
    cache = key = None
    if args.jev:
        import JevServer as JS
        key = JS.api_key()
        cache = JS.JevCache(os.path.join(HERE, "jev_nfp_cache.jsonl"))

    res = []
    for r in rows:
        t = release_utc(r["release_date"])
        usd_moves = {}
        for sym in ("EURUSD", "GBPUSD"):
            p0 = price_at(args.history, sym, t - dt.timedelta(minutes=1))
            p1 = price_at(args.history, sym, t + dt.timedelta(minutes=1))
            p15 = price_at(args.history, sym, t + dt.timedelta(minutes=15))
            p60 = price_at(args.history, sym, t + dt.timedelta(minutes=60))
            if None in (p0, p1, p15, p60):
                continue
            # USD erősödés = EURUSD/GBPUSD esés -> előjel megfordítva, pipben
            usd_moves[sym] = {"m15": -(p15 - p0) / PIP[sym], "m60": -(p60 - p0) / PIP[sym],
                              "after": -(p60 - p1) / PIP[sym], "abs15": abs(p15 - p0) / PIP[sym]}
        if not usd_moves:
            continue
        a, f, pv = float(r["actual_k"]), float(r["forecast_k"]), float(r["previous_k"])
        sig = {"tény > MQL5 előrejelzés": 1 if a > f else -1,
               "tény > előző hónap": 1 if a > pv else -1}
        if args.jev:
            sig["Jev"], _ = jev_direction(key, r, cache)
        res.append((r["release_date"], usd_moves, sig))

    print("NFP jelentés árral: %d (az MQL5 előzményekből, 2022-07 .. 2026-09)\n" % len(res))
    for sym in ("EURUSD", "GBPUSD"):
        mv = [m[sym]["abs15"] for _, m, _ in res if sym in m]
        if mv:
            mv.sort()
            print("%s: mozgás 15 perc alatt (pip): medián %.0f, átlag %.0f, legnagyobb %.0f"
                  % (sym, mv[len(mv) // 2], sum(mv) / len(mv), mv[-1]))
    print("\nIrány-találat (USD erősödik/gyengül), mindkét devizán összesen:")
    print("  %-26s %8s %8s   %s" % ("szabály", "15 perc", "60 perc",
                                    "kötés 1 perccel a hír után, 60 percig, -%.0f pip költség" % args.spread))
    for name in res[0][2]:
        h15 = h60 = n = 0
        pnl = []
        for _, m, s in res:
            for sym, x in m.items():
                n += 1
                h15 += (s[name] * x["m15"]) > 0
                h60 += (s[name] * x["m60"]) > 0
                pnl.append(s[name] * x["after"] - args.spread)
        wins = sum(1 for p in pnl if p > 0)
        print("  %-26s %7.0f%% %7.0f%%   átlag %+.1f pip/kötés, összesen %+.0f pip, nyerő %d/%d"
              % (name, 100.0 * h15 / n, 100.0 * h60 / n, sum(pnl) / len(pnl), sum(pnl), wins, len(pnl)))
    print("\n(50%% = pénzfeldobás. %d jelentés x 2 deviza kevés: +-10%% eltérés még lehet véletlen.)" % len(res))


if __name__ == "__main__":
    main()
