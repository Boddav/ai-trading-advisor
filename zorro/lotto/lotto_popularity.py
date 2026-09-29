# =================================================================
# Hatoslottó: NÉPSZERŰSÉG alapú szelvény (nem jóslás!)
#
# A húzás véletlen, a nyerés esélye minden szelvénynél ugyanaz. A nyereményt viszont
# a nyertesek MEGOSZTJÁK. Ha kevesen játszott számokat választasz, ugyanakkora esély
# mellett nagyobb nyereményt kapsz, ha nyersz.
#
# Honnan tudjuk, mit játszanak a többiek? A szerencsejatek.hu CSV-ben benne van, hány
# 3-as/4-es találat született húzásonként. Ha népszerű számokat húznak, sok a nyertes.
# Ebből (ridge regresszió, húzásonkénti forgalom-normalizálással) kiszámolja minden
# számra a népszerűséget, és ellenőrzi is: régi adaton tanul, újabb húzásokon mér.
#
# Futtatás (külön csomag nem kell):
#   python lotto_popularity.py hatos.csv              -> népszerűség + 5 ajánlott szelvény
#   python lotto_popularity.py hatos.csv --tickets 10
#   python lotto_popularity.py hatos.csv --jev        -> + a Jev becslését is lemérjük
#                                                       (JevServer.py és jev_key.txt mellette)
# =================================================================
import argparse
import csv
import math
import os
import random
import statistics
import sys

RIDGE = 5.0
WINDOW = 10  # ± ennyi húzás mediánjával normalizál (forgalom változása)


def to_int(x):
    try:
        return int(str(x).replace(" ", "").replace("Ft", "").replace("\xa0", ""))
    except ValueError:
        return 0


def load(path):
    """Legrégebbi elöl: dict(year, date, nums, h3, h4, p3)"""
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.reader(f, delimiter=";"):
            nums = [int(x) for x in r[14:20] if x.strip().isdigit()]
            if len(nums) == 6 and len(r) >= 14:
                rows.append({"year": to_int(r[0]), "date": r[3], "nums": sorted(nums),
                             "h4": to_int(r[10]), "h3": to_int(r[12]), "p3": to_int(r[13])})
    return [r for r in reversed(rows) if r["h3"] > 0]


def targets(rows):
    """log(3-as nyertesek / környező húzások mediánja): a forgalomtól független népszerűség."""
    h = [r["h3"] for r in rows]
    out = []
    for i in range(len(rows)):
        around = h[max(0, i - WINDOW):i] + h[i + 1:i + 1 + WINDOW]
        out.append(math.log(h[i] / statistics.median(around)))
    return out


def solve(a, b):
    """Gauss-elimináció részleges főelemkiválasztással (45x45)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c and m[r][c]:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def fit(rows, ys):
    """Ridge: számonkénti népszerűség (log-szorzó a nyertesek számára)."""
    mean = sum(ys) / len(ys)
    a = [[0.0] * 45 for _ in range(45)]
    b = [0.0] * 45
    for r, y in zip(rows, ys):
        idx = [n - 1 for n in r["nums"]]
        for i in idx:
            b[i] += y - mean
            for j in idx:
                a[i][j] += 1.0
    for i in range(45):
        a[i][i] += RIDGE
    return solve(a, b)


def score(beta, nums):
    return sum(beta[n - 1] for n in nums)


def corr(x, y):
    mx, my = sum(x) / len(x), sum(y) / len(y)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    return sxy / (sx * sy) if sx and sy else 0.0


def validate(rows, ys):
    print("\n=== ELLENŐRZÉS: régi húzásokon tanul, újabbakon mér ===")
    for cut in (2012, 2016, 2020):
        tr = [(r, y) for r, y in zip(rows, ys) if r["year"] < cut]
        te = [(r, y) for r, y in zip(rows, ys) if r["year"] >= cut]
        if len(tr) < 200 or len(te) < 50:
            continue
        beta = fit([r for r, _ in tr], [y for _, y in tr])
        c = corr([score(beta, r["nums"]) for r, _ in te], [y for _, y in te])
        print("  tanítás %d előtt (%d húzás) -> %d utáni %d húzáson: korreláció %.2f"
              % (cut, len(tr), cut, len(te), c))
    print("  (0 = nincs összefüggés, 1 = tökéletes. 0.5 felett erős, valódi hatás.)")


def tickets(beta, count, rng):
    """Alacsony népszerűségű, egymástól eltérő szelvények (max 2 közös szám)."""
    cands = []
    for _ in range(60000):
        nums = sorted(rng.sample(range(1, 46), 6))
        cands.append((score(beta, nums), nums))
    cands.sort()
    chosen = []
    for s, nums in cands:
        if all(len(set(nums) & set(c)) <= 2 for _, c in chosen):
            chosen.append((s, nums))
            if len(chosen) == count:
                break
    return chosen


def jev_check(beta, rng):
    """A Jev meg tudja-e mondani, mely szelvények népszerűek? Az adatból tudjuk a választ."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
    import JevServer  # noqa: E402
    key = JevServer.api_key()
    combos = sorted((sorted(rng.sample(range(1, 46), 6)) for _ in range(3000)), key=lambda c: score(beta, c))
    picked = combos[:8] + combos[len(combos) // 2 - 4:len(combos) // 2 + 4] + combos[-8:]
    questions = {
        "c%d" % i: {
            "type": "score",
            "instructions": {"question": "How many other Hungarian lotto (6 of 45) players are likely to "
                                         "have chosen exactly these numbers: %s?" % c},
            "criteria": ["very few", "fewer than average", "average", "more than average", "very many"],
        }
        for i, c in enumerate(picked)
    }
    state = {"game": "Hungarian lotto 6/45; players often pick birthdays, lucky numbers and patterns"}
    answers = JevServer.jev_call(key, state, questions).get("answers", {})
    jev = [float((answers.get("c%d" % i) or {}).get("score", 2.0)) for i in range(len(picked))]
    data = [score(beta, c) for c in picked]

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0] * len(v)
        for k, i in enumerate(order):
            r[i] = k
        return r
    rc = corr(ranks(jev), ranks(data))
    print("\n=== JEV TESZT: %d szelvény népszerűsége, Jev vs. valódi adat ===" % len(picked))
    for c, d, j in zip(picked, data, jev):
        print("  %-24s adat: %+.2f   Jev pontszám (0-4): %.2f" % (c, d, j))
    print("Rangkorreláció Jev vs. adat: %.2f  (1 = a Jev tökéletesen tudja, 0 = nem tudja)" % rc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--tickets", type=int, default=5)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--jev", action="store_true")
    args = ap.parse_args()
    rng = random.Random(args.seed)

    rows = load(args.csv)
    ys = targets(rows)
    print("Beolvasva: %d húzás nyertesszámokkal (%s - %s)" % (len(rows), rows[0]["date"] or rows[0]["year"], rows[-1]["date"]))
    validate(rows, ys)
    beta = fit(rows, ys)

    order = sorted(range(45), key=lambda i: beta[i])
    fmt = lambda i: "%d (%+.0f%%)" % (i + 1, 100 * (math.exp(beta[i]) - 1))
    print("\n=== SZÁMOK NÉPSZERŰSÉGE (ennyivel több/kevesebb 3-as nyertes, ha kihúzzák) ===")
    print("  Legnépszerűbb:  " + ", ".join(fmt(i) for i in reversed(order[-10:])))
    print("  Legkevésbé:     " + ", ".join(fmt(i) for i in order[:10]))

    # a nyeremény összege is ezt mutatja?
    p = [(r, y) for r, y in zip(rows, ys) if r["p3"] > 0]
    if len(p) > 100:
        c = corr([score(beta, r["nums"]) for r, _ in p], [math.log(r["p3"]) for r, _ in p])
        print("\n  Ellenőrzés a 3-as nyeremény összegével: korreláció %.2f (negatív = népszerű szám -> kisebb nyeremény)" % c)

    rand_scores = [score(beta, sorted(rng.sample(range(1, 46), 6))) for _ in range(20000)]
    avg = sum(rand_scores) / len(rand_scores)
    print("\n=== AJÁNLOTT SZELVÉNYEK (kevesen játsszák) ===")
    for s, nums in tickets(beta, args.tickets, rng):
        print("  %-24s várható nyertes-társak: %.0f%% az átlagos szelvényhez képest -> kb. %.1fx nyeremény"
              % (nums, 100 * math.exp(s - avg), math.exp(avg - s)))
    print("\nFONTOS: a nyerés esélye ugyanaz, mint bármely szelvénynél (ötösnél kb. 1 : 34 ezer,")
    print("hatosnál 1 : 8,1 millió). Csak a nyeremény nagyobb, ha nyersz. A 3-as és 4-es találatra mért")
    print("hatás a telitalálatnál is hasonló lehet, de ott kevés az adat az ellenőrzéshez.")

    if args.jev:
        jev_check(beta, rng)


if __name__ == "__main__":
    main()
