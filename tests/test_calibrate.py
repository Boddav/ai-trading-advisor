"""calibrate_jev.py: outcomes from later export rows, calibration and AUC."""
import importlib.util
import json
import random
import sys
from pathlib import Path

ZORRO = Path(__file__).parent.parent / "zorro"
sys.path.insert(0, str(ZORRO))
spec = importlib.util.spec_from_file_location("calibrate_jev", ZORRO / "calibrate_jev.py")
CJ = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CJ)
JS = CJ.JS


def _export(tmp_path, n_bars=700, seed=1):
    rnd = random.Random(seed)
    series = {}
    for asset in ("EUR/USD", "GBP/USD"):
        c, bars = 1.1, []
        for _ in range(n_bars):
            o = c
            c = o + rnd.gauss(0, 0.001)
            bars.append([round(o, 5), round(max(o, c) + 0.0004, 5), round(min(o, c) - 0.0004, 5), round(c, 5)])
        series[asset] = bars
    reqs = []
    for i in range(200, n_bars + 1):  # Zorro order: per bar, all assets
        for asset, bars in series.items():
            reqs.append({"asset": asset, "tf": "H1", "digits": 5, "pos": "flat", "entry": 0, "bars": bars[i - 200:i]})
    path = tmp_path / "JevExport.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in reqs))
    return reqs, path


def _cache(tmp_path, reqs, informed, monkeypatch):
    monkeypatch.setattr(JS, "QUESTION_MODE", "tp_first")
    rows, _, _ = CJ.build_rows(reqs, {JS.JevCache.key(r): {} for r in reqs}, 120)  # outcomes only
    cache = JS.JevCache(str(tmp_path / "jev_cache.jsonl"))
    rnd = random.Random(7)
    by_asset = {}
    for r in reqs:
        by_asset.setdefault(r["asset"], []).append(r)
    idx = {"EUR/USD": 0, "GBP/USD": 0}
    for row in rows:  # rows are in per-asset request order
        r = by_asset[row["asset"]][idx[row["asset"]]]
        idx[row["asset"]] += 1
        if informed:
            pl = 0.55 if row["y_long"] == 1 else 0.30
            ps = 0.55 if row["y_short"] == 1 else 0.30
        else:
            pl, ps = rnd.uniform(0.2, 0.6), rnd.uniform(0.2, 0.6)
        cache.put(JS.JevCache.key(r), {"long_tp_first": {"noul": pl}, "short_tp_first": {"noul": ps}})
    return cache


def test_outcome_rules():
    assert CJ.outcome(1.0, 0.01, [(1, 1.031, 0.99, 1.03)], "long") == 1
    assert CJ.outcome(1.0, 0.01, [(1, 1.01, 0.984, 1.0)], "long") == 0
    assert CJ.outcome(1.0, 0.01, [(1, 1.031, 0.984, 1.0)], "long") == 0  # both in one bar: SL
    assert CJ.outcome(1.0, 0.01, [(1, 1.01, 0.99, 1.0)], "short") is None
    assert CJ.outcome(1.0, 0.01, [(1, 1.0, 0.969, 0.97)], "short") == 1
    assert CJ.auc([(0.9, 1), (0.1, 0)]) == 1.0 and CJ.auc([(0.5, 1), (0.5, 0)]) == 0.5


def test_informed_vs_random_jev(tmp_path, monkeypatch, capsys):
    reqs, path = _export(tmp_path)
    for informed, lo, hi in ((True, 0.99, 1.0), (False, 0.4, 0.6)):
        cache = _cache(tmp_path / ("i" if informed else "r"), reqs, informed, monkeypatch) \
            if (tmp_path / ("i" if informed else "r")).mkdir() is None else None
        rows, missing, gaps = CJ.build_rows(CJ.load_requests(path), cache, 120)
        assert missing == 0 and gaps == 0 and len(rows) > 800
        pairs = [(r["p_long"], r["y_long"]) for r in rows if r["y_long"] is not None]
        assert lo <= CJ.auc(pairs) <= hi
        CJ.report(rows)
    out = capsys.readouterr().out
    assert "KÜSZÖB-TÁBLÁZAT" in out and "VAK belépés" in out
