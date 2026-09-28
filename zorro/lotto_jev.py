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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--check", type=int, default=0, help="ellenőrzés az utolsó N húzáson")
    args = ap.parse_args()
    key = JevServer.api_key()
    draws = load_draws(args.csv)
    print("Beolvasva: %d húzás, legutóbbi: %s %s" % (len(draws), draws[0][0], draws[0][1]))

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
