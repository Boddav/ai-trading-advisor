# =================================================================
# Hatoslottó kérdés a Jevnek (kísérlet)
#
# Megkérdezi a Jevet: az eddigi húzások alapján melyik 6 szám jön a következő húzáson.
# Ellenőrzésként a legutóbbi N húzásra "visszamenőleg" is megkérdezi (csak az addigi
# adatot látja), és megszámolja a találatokat. Véletlen tippelésnél az elvárt átlag
# 6*6/45 = 0.80 találat húzásonként.
#
# Futtatás (a JevServer.py és a jev_key.txt mellett):
#   python lotto_jev.py hatos.csv            -> tipp a következő húzásra
#   python lotto_jev.py hatos.csv --check 20 -> + ellenőrzés az utolsó 20 húzáson
#   python lotto_jev.py hatos.csv --calibrate 20
#       KALIBRÁCIÓS TESZT: mind a 45 számra külön igen/nem kérdés ("benne lesz-e a
#       kihúzott 6-ban?"). A helyes válasz ismert: minden számra 6/45 = 0.133.
#       Megmutatja, mennyire igazak a Jev valószínűségei (a trading küszöbökhöz).
# =================================================================
import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import JevServer  # noqa: E402  (jev_call, api_key)

HISTORY = 60  # ennyi korábbi húzást lát a Jev


def load_draws(path):
    """Legújabb elöl, mint a szerencsejatek.hu CSV-ben: [(dátum, [6 szám]), ...]"""
    draws = []
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.reader(f, delimiter=";"):
            nums = [int(x) for x in row[14:20] if x.strip().isdigit()]
            if len(nums) == 6:
                draws.append((row[3] or "%s/%s" % (row[0], row[1]), sorted(nums)))
    return draws


def ask(key, past):
    """past: korábbi húzások, legújabb elöl. -> 6 szám a Jev valószínűségei szerint."""
    freq = {}
    for _, nums in past:
        for n in nums:
            freq[n] = freq.get(n, 0) + 1
    state = {
        "game": "Hungarian lotto 6 of 45, numbers 1..45, 6 drawn without replacement",
        "recent_draws_newest_first": [nums for _, nums in past[:HISTORY]],
        "frequency_in_all_%d_draws" % len(past): {str(n): freq.get(n, 0) for n in range(1, 46)},
    }
    questions = {"next": {
        "type": "choice",
        "instructions": {"question": "Which number is most likely to be drawn in the next draw?"},
        "criteria": {str(n): None for n in range(1, 46)},
    }}
    ans = JevServer.jev_call(key, state, questions).get("answers", {}).get("next", {})
    probs = {int(k): float(v) for k, v in (ans.get("probabilities") or {}).items()}
    top = sorted(probs, key=probs.get, reverse=True)[:6]
    return sorted(top), probs


TRUE_P = 6 / 45.0


def ask_each(key, past):
    """45 igen/nem kérdés egy hívásban. -> {szám: P(benne lesz a következő húzásban)}"""
    state = {
        "game": "Hungarian lotto: each draw, a lottery machine draws 6 distinct balls from 1..45",
        "recent_draws_newest_first": [nums for _, nums in past[:HISTORY]],
    }
    questions = {
        "n%d" % n: {
            "type": "noul",
            "instructions": {"question": "Will the number %d be among the 6 numbers of the next draw?" % n},
            "criteria": {"true": "%d is drawn" % n, "false": "%d is not drawn" % n},
        }
        for n in range(1, 46)
    }
    answers = JevServer.jev_call(key, state, questions).get("answers", {})
    return {n: JevServer._noul(answers.get("n%d" % n)) for n in range(1, 46)}


def calibrate(key, draws, count):
    print("\n=== KALIBRÁCIÓ: %d húzás x 45 szám ===" % count)
    print("Helyes válasz minden számra: %.3f (6/45)\n" % TRUE_P)
    rows = []          # (p, kihúzták-e)
    top_hits = 0
    for i in range(count - 1, -1, -1):
        date, actual = draws[i]
        probs = ask_each(key, draws[i + 1:])
        rows += [(probs[n], n in actual) for n in range(1, 46)]
        top = sorted(sorted(probs, key=probs.get, reverse=True)[:6])
        hit = len(set(top) & set(actual))
        top_hits += hit
        print("  %s  átlag p=%.3f  min=%.3f max=%.3f  Jev top6: %-24s húzás: %-24s találat: %d"
              % (date, sum(probs.values()) / 45, min(probs.values()), max(probs.values()), top, actual, hit))

    n = len(rows)
    mean_p = sum(p for p, _ in rows) / n
    brier = sum((p - y) ** 2 for p, y in rows) / n
    base = TRUE_P * (1 - TRUE_P)  # a konstans 6/45 tipp Brier-pontja
    print("\nÁtlagos Jev valószínűség: %.3f   (helyes: %.3f)" % (mean_p, TRUE_P))
    print("Brier pont (kisebb = jobb): Jev %.4f   vs. mindig 0.133: %.4f" % (brier, base))
    print("Top6 találat/húzás: Jev %.2f   (véletlen: 0.80)" % (top_hits / count))
    print("\nSávok: amikor a Jev ennyit mondott -> ténylegesen ennyiszer húzták ki")
    for lo, hi in ((0, .05), (.05, .10), (.10, .15), (.15, .20), (.20, .30), (.30, .50), (.50, 1.01)):
        b = [y for p, y in rows if lo <= p < hi]
        if b:
            print("  %.2f-%.2f: %4d eset, kihúzva %5.1f%%" % (lo, min(hi, 1), len(b), 100.0 * sum(b) / len(b)))
    verdict = ("jól kalibrált" if abs(mean_p - TRUE_P) < 0.03 and brier <= base * 1.02
               else "NEM kalibrált: a valószínűségei nem igazi esélyek")
    print("\nÉrtékelés: %s" % verdict)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--check", type=int, default=0, help="ellenőrzés az utolsó N húzáson")
    ap.add_argument("--calibrate", type=int, default=0, help="kalibrációs teszt az utolsó N húzáson")
    args = ap.parse_args()
    key = JevServer.api_key()
    draws = load_draws(args.csv)
    print("Beolvasva: %d húzás, legutóbbi: %s %s" % (len(draws), draws[0][0], draws[0][1]))

    if args.calibrate:
        calibrate(key, draws, args.calibrate)
        return

    if args.check:
        total = 0
        for i in range(args.check - 1, -1, -1):
            date, actual = draws[i]
            pick, _ = ask(key, draws[i + 1:])
            hit = len(set(pick) & set(actual))
            total += hit
            print("  %s  Jev: %-24s húzás: %-24s találat: %d" % (date, pick, actual, hit))
        print("Jev átlag: %.2f találat/húzás  (véletlen elvárt: 0.80)" % (total / args.check))

    pick, probs = ask(key, draws)
    top = sorted(probs.items(), key=lambda x: x[1], reverse=True)[:10]
    print("\nJev tippje a következő húzásra: %s" % pick)
    print("Legvalószínűbbnek adott 10 szám: " + ", ".join("%d (%.3f)" % (n, p) for n, p in top))
    print("(Egyenletes eloszlásnál minden szám 1/45 = 0.022. A lottó véletlen: ez nem növeli a nyerési esélyt.)")


if __name__ == "__main__":
    main()
