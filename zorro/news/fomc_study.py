# =================================================================
# FOMC-közlemény hangvétele: Jev vs. egyszerű szabályok, EUR/USD és GBP/USD reakció.
#
# 1) Letölti a Fed FOMC-közleményeit (federalreserve.gov), és elmenti: fomc_statements.jsonl
# 2) Minden közleményre három jel (USD szempontjából: +1 szigorúbb/hawkish, -1 lazább/dovish, 0 semleges):
#      - Jev: "az előzőhöz képest szigorúbb, változatlan vagy lazább?" (csak a két szöveg, dátum NÉLKÜL)
#      - kamat: a célsáv emelése (+1) / csökkentése (-1) / változatlan (0)
#      - kulcsszó: (szigorító - lazító szavak) a mostaniban mínusz az előzőben
# 3) Ár: Zorro History M1 (.t6), a közlés (14:00 New York) előtti perc -> +15 / +60 / +240 perc,
#    és egy kötés 1 perccel a közlés után, 60 / 240 percig, hír-spread levonva.
#
#   python fomc_study.py --history C:\...\z12\History [--from 2019] [--jev]
# =================================================================
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
for d in (HERE, os.path.join(HERE, "..", "server")):
    sys.path.insert(0, d)
from nfp_study import price_at  # noqa: E402

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:
    NY = None

BASE = "https://www.federalreserve.gov"
STORE = os.path.join(HERE, "fomc_statements.jsonl")
PIP = 0.0001
SPREAD = 1.5   # pip, hír alatt

HAWK = ["inflation remains elevated", "elevated inflation", "additional policy firming", "further increases",
        "ongoing increases", "raise the target", "tighten", "firming", "strong", "robust", "upside risks to inflation",
        "inflation pressures", "reduce its holdings", "solid"]
DOVE = ["lower the target", "cut", "reduce the target", "easing", "accommodative", "weaken", "softened", "slowed",
        "downside risks", "moderated", "below 2 percent", "cooled", "increase its holdings", "patient"]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research script)"})
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", errors="replace")


def statement_text(page):
    m = re.search(r'<div[^>]+id="article"[^>]*>(.*?)<div[^>]+id="lastUpdate"', page, re.S) or \
        re.search(r'<div[^>]+id="article"[^>]*>(.*)', page, re.S)
    body = m.group(1) if m else page
    paras = [html.unescape(re.sub(r"<[^>]+>", " ", p)) for p in re.findall(r"<p[^>]*>(.*?)</p>", body, re.S)]
    paras = [re.sub(r"\s+", " ", p).strip() for p in paras]
    out = []
    for p in paras:
        if not p or p.startswith(("For media inquiries", "Implementation Note", "For release at")):
            continue
        if p.startswith("Voting for") or p.startswith("Voting against"):
            break
        out.append(p)
    return "\n".join(out)


def fetch(y0):
    have = {}
    if os.path.exists(STORE):
        for line in open(STORE, encoding="utf-8"):
            r = json.loads(line)
            have[r["date"]] = r
    idx = json.loads(get(BASE + "/json/ne-press.json").encode().decode("utf-8-sig"))
    items = [d for d in idx if re.search(r"FOMC statement", d.get("t", ""), re.I)]
    with open(STORE, "a", encoding="utf-8") as f:
        for d in items:
            try:
                when = dt.datetime.strptime(d["d"], "%m/%d/%Y %I:%M:%S %p")
            except ValueError:  # régi tételek csak dátummal
                when = dt.datetime.strptime(d["d"].split()[0], "%m/%d/%Y").replace(hour=14)
            if when.year < y0 - 1:
                continue
            key = when.strftime("%Y-%m-%d %H:%M")
            if key in have:
                continue
            txt = statement_text(get(BASE + d["l"]))
            r = {"date": key, "url": BASE + d["l"], "text": txt}
            f.write(json.dumps(r) + "\n")
            have[key] = r
            print("  letöltve:", key, len(txt), "karakter")
    return [have[k] for k in sorted(have)]


def target_range(txt):
    m = re.search(r"target range for the federal funds rate[^.]*?(\d[\d/\-–\u2011]*) to (\d[\d/\-–\u2011]*) percent", txt)
    if not m:
        return None

    def num(s):  # "5-1/2" -> 5.5, "1/4" -> 0.25, "3" -> 3.0
        s = s.strip().replace("–", "-").replace("\u2011", "-").replace(" ", "")
        whole, _, frac = s.partition("-") if "/" in s and "-" in s else ("", "", s) if "/" in s else (s, "", "")
        v = float(whole) if whole else 0.0
        if frac:
            a, b = frac.split("/")
            v += float(a) / float(b)
        return v
    try:
        return num(m.group(2))
    except ValueError:
        return None


def kw_score(txt):
    t = txt.lower()
    return sum(t.count(w) for w in HAWK) - sum(t.count(w) for w in DOVE)


def release_utc(local):
    if NY:
        return local.replace(tzinfo=NY).astimezone(dt.timezone.utc).replace(tzinfo=None)
    return local + dt.timedelta(hours=4 if 3 <= local.month <= 10 else 5)


DATE_LINE = re.compile(r"^(January|February|March|April|May|June|July|August|September|October|November|December) "
                       r"\d{1,2}, \d{4}\s*\n?")


def strip_date(txt):
    """A Jev ne lássa a dátumot (ne emlékezhessen a piaci reakcióra)."""
    return DATE_LINE.sub("", txt.strip())


def jev_tone(key, cur, prev, cache):
    import JevServer as JS
    cur, prev = strip_date(cur), strip_date(prev)
    req = {"fomc_tone": [cur[:6000], prev[:6000]]}
    k = JS.JevCache.key(req)
    a = cache.get(k)
    if a is None:
        q = {"tone": {"type": "choice",
                      "instructions": {"question": "Compared with previous_statement, is current_statement more hawkish, "
                                                   "unchanged, or more dovish about US monetary policy?",
                                       "inputs": "Both are US central bank policy statements; previous_statement is the "
                                                 "one released at the prior meeting."},
                      "criteria": {"hawkish": "tighter policy: higher rates, more concern about inflation, firming bias",
                                   "unchanged": "essentially the same stance and policy outlook",
                                   "dovish": "easier policy: lower rates, more concern about growth or jobs, easing bias"}}}
        a = JS.jev_call(key, {"current_statement": cur, "previous_statement": prev}, q).get("answers", {})
        cache.put(k, a)
    p = JS._probs(a.get("tone"), ("hawkish", "unchanged", "dovish"))
    best = max(p, key=p.get)
    return {"hawkish": 1, "unchanged": 0, "dovish": -1}[best], p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", required=True)
    ap.add_argument("--from", dest="y0", type=int, default=2019)
    ap.add_argument("--jev", action="store_true")
    args = ap.parse_args()
    sts = fetch(args.y0)
    cache = key = None
    if args.jev:
        import JevServer as JS
        key = JS.api_key()
        cache = JS.JevCache(os.path.join(HERE, "jev_fomc_cache.jsonl"))

    res = []
    for prev, cur in zip(sts, sts[1:]):
        local = dt.datetime.strptime(cur["date"], "%Y-%m-%d %H:%M")
        if local.year < args.y0 or not cur["text"] or not prev["text"]:
            continue
        t = release_utc(local)
        moves = {}
        for sym in ("EURUSD", "GBPUSD"):
            p0 = price_at(args.history, sym, t - dt.timedelta(minutes=1))
            ps = {m: price_at(args.history, sym, t + dt.timedelta(minutes=m)) for m in (1, 15, 60, 240)}
            if p0 is None or None in ps.values():
                continue
            usd = lambda a, b: -(b - a) / PIP  # USD erősödés pipben
            moves[sym] = {"m15": usd(p0, ps[15]), "m60": usd(p0, ps[60]), "m240": usd(p0, ps[240]),
                          "t60": usd(ps[1], ps[60]), "t240": usd(ps[1], ps[240])}
        if not moves:
            continue
        r0, r1 = target_range(prev["text"]), target_range(cur["text"])
        rate = 0 if r0 is None or r1 is None else (1 if r1 > r0 else -1 if r1 < r0 else 0)
        kw = kw_score(cur["text"]) - kw_score(prev["text"])
        sig = {"kamat (emelés/csökkentés)": rate, "kulcsszó-különbség": (kw > 0) - (kw < 0)}
        if args.jev:
            sig["Jev hangvétel"], probs = jev_tone(key, cur["text"], prev["text"], cache)
        res.append((cur["date"], moves, sig))

    print("\nFOMC-közlemény árral: %d (%d-től)\n" % (len(res), args.y0))
    print("Találat = a jel iránya egyezik a dollár mozgásával; csak ahol a jel nem 0. 50% = pénzfeldobás.")
    print("Kötés: belépés 1 perccel a közlés után, -%.1f pip hír-spread.\n" % SPREAD)
    print("  %-26s %5s %7s %7s %7s   %-22s %-22s" % ("jel", "db", "15p", "60p", "240p", "kötés 60p (pip/db)", "kötés 240p (pip/db)"))
    for name in res[0][2]:
        n = h15 = h60 = h240 = 0
        t60, t240 = [], []
        for _, m, s in res:
            if s[name] == 0:
                continue
            for x in m.values():
                n += 1
                h15 += s[name] * x["m15"] > 0
                h60 += s[name] * x["m60"] > 0
                h240 += s[name] * x["m240"] > 0
                t60.append(s[name] * x["t60"] - SPREAD)
                t240.append(s[name] * x["t240"] - SPREAD)
        if n:
            print("  %-26s %5d %6.0f%% %6.0f%% %6.0f%%   %+7.1f (%d/%d nyerő)    %+7.1f (%d/%d nyerő)"
                  % (name, n, 100.0 * h15 / n, 100.0 * h60 / n, 100.0 * h240 / n,
                     sum(t60) / n, sum(1 for v in t60 if v > 0), n, sum(t240) / n, sum(1 for v in t240 if v > 0), n))
        else:
            print("  %-26s     0  (mindig 0 jel)" % name)
    if args.jev:
        from collections import Counter
        print("\nJev címkék:", dict(Counter(s["Jev hangvétel"] for _, _, s in res)),
              " | egyezés a kamatjellel:",
              "%d/%d" % (sum(1 for _, _, s in res if s["Jev hangvétel"] == s["kamat (emelés/csökkentés)"]), len(res)))
    allm = [abs(x["m15"]) for _, m, _ in res for x in m.values()]
    print("\nÁtlagos mozgás 15 perc alatt: %.0f pip" % (sum(allm) / len(allm)))


if __name__ == "__main__":
    main()
