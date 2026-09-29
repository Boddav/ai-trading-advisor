"""Kronos + Jev: the bridge maths, the JevServer modes and calibrate_kronos, with a fake Kronos."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent / "zorro"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


KB = _load("kronos_bridge_t", ROOT / "kronos" / "kronos_bridge.py")
JS = _load("JevServer_t", ROOT / "server" / "JevServer.py")

BARS = [[1.08 + i * 1e-4, 1.0806 + i * 1e-4, 1.0797 + i * 1e-4, 1.0803 + i * 1e-4] for i in range(200)]


def test_outcome_and_path_stats():
    a = KB.atr(BARS)
    e = BARS[-1][3]
    up = [[e, e + 4 * a, e - 0.1 * a, e + 3.5 * a]]
    down = [[e, e + 0.1 * a, e - 2 * a, e - 1.8 * a]]
    flat = [[e, e + 0.1 * a, e - 0.1 * a, e]] * 5
    assert KB.outcome(e, a, up, "long") == 1 and KB.outcome(e, a, up, "short") == 0
    assert KB.outcome(e, a, down, "long") == 0 and KB.outcome(e, a, down, "short") is None
    assert KB.outcome(e, a, flat, "long") is None
    s = KB.path_stats(BARS, [up, up, down, flat])
    assert s["p_tp_first_long"] == 0.5 and s["p_sl_first_long"] == 0.25
    assert s["p_tp_first_short"] == 0.0 and s["p_sl_first_short"] == 0.5
    assert s["p_up"] == 0.5 and s["paths"] == 4
    assert KB.path_stats(BARS[:10], [up]) is None


def test_times_skip_weekend():
    import datetime as dt
    t = KB.hourly_times(30, dt.datetime(2026, 9, 28, 5))  # hétfő
    assert len(t) == 30 and all(x.weekday() < 5 for x in t) and t[-1].hour == 5
    f = KB.future_times(dt.datetime(2026, 9, 25, 22), 3)  # péntek este
    assert [x.weekday() for x in f] == [4, 0, 0]  # péntek 23:00, majd hétfő 00:00, 01:00


class FakeKronos:
    tag = "fake/1/1"

    def __init__(self, stats):
        self.s = stats

    def stats(self, bars):
        return dict(self.s)


def _jev(pl, ps):
    calls = []

    def call(key, state, questions):
        calls.append((state, questions))
        return {"answers": {"long_tp_first": {"noul": pl}, "short_tp_first": {"noul": ps}}}
    return call, calls


REQ = {"asset": "EUR/USD", "tf": "H1", "digits": 5, "pos": "flat", "entry": 0, "bars": BARS}


@pytest.fixture(autouse=True)
def tp_first(monkeypatch):
    monkeypatch.setattr(JS, "QUESTION_MODE", "tp_first")


def test_context_mode_passes_forecast(monkeypatch):
    monkeypatch.setattr(JS, "KRONOS", FakeKronos({"p_tp_first_long": 0.2, "p_tp_first_short": 0.5}))
    monkeypatch.setattr(JS, "KRONOS_MODE", "context")
    call, calls = _jev(0.50, 0.30)
    out = JS.handle_decide(REQ, "K", call=call)
    assert out["action"] == "open_long" and out["kronos"]["p_tp_first_short"] == 0.5
    state, q = calls[0]
    assert state["kronos_forecast"]["p_tp_first_long"] == 0.2
    assert "Kronos" in q["long_tp_first"]["instructions"]["inputs"]


def test_agree_mode_vetoes(monkeypatch):
    monkeypatch.setattr(JS, "KRONOS_MODE", "agree")
    call, _ = _jev(0.50, 0.30)
    monkeypatch.setattr(JS, "KRONOS", FakeKronos({"p_tp_first_long": 0.2, "p_tp_first_short": 0.5}))
    assert JS.handle_decide(REQ, "K", call=call)["action"] == "hold"
    monkeypatch.setattr(JS, "KRONOS", FakeKronos({"p_tp_first_long": 0.45, "p_tp_first_short": 0.3}))
    assert JS.handle_decide(REQ, "K", call=call)["action"] == "open_long"


def test_kronos_only_no_jev_call(monkeypatch):
    monkeypatch.setattr(JS, "KRONOS_MODE", "kronos_only")
    monkeypatch.setattr(JS, "KRONOS", FakeKronos({"p_tp_first_long": 0.25, "p_tp_first_short": 0.48}))
    call, calls = _jev(0.9, 0.1)
    assert JS.handle_decide(REQ, "K", call=call)["action"] == "open_short" and not calls
    assert JS.handle_decide(dict(REQ, pos="long", entry=1.1), "K", call=call)["action"] == "hold"


def test_kronos_error_falls_back_to_jev(monkeypatch):
    class Broken:
        tag = "x"

        def stats(self, bars):
            raise RuntimeError("cuda")
    monkeypatch.setattr(JS, "KRONOS", Broken())
    monkeypatch.setattr(JS, "KRONOS_MODE", "agree")
    call, _ = _jev(0.50, 0.30)
    out = JS.handle_decide(REQ, "K", call=call)
    assert out["action"] == "open_long" and "kronos" not in out


def test_calibrate_kronos_end_to_end(tmp_path, monkeypatch, capsys):
    # emelkedő piac: minden órában long TP jön előbb
    rows = []
    bars = [[1.0 + i * 2e-3, 1.0 + i * 2e-3 + 1e-3, 1.0 + i * 2e-3 - 1e-3, 1.0 + i * 2e-3 + 5e-4] for i in range(400)]
    for i in range(200, 400):
        rows.append({"asset": "EUR/USD", "tf": "H1", "digits": 5, "pos": "flat", "entry": 0, "bars": bars[i - 200:i]})
    exp = tmp_path / "JevExport.jsonl"
    exp.write_text("\n".join(json.dumps(r) for r in rows))

    sys.path.insert(0, str(ROOT / "kronos"))
    import calibrate_kronos as CK
    monkeypatch.setattr(CK, "KCACHE", str(tmp_path / "kronos_cache.jsonl"))
    monkeypatch.setattr(CK.KB, "KronosForecaster",
                        lambda repo: FakeKronos({"p_tp_first_long": 0.6, "p_tp_first_short": 0.1}))
    monkeypatch.setattr(sys, "argv", ["calibrate_kronos.py", str(exp), "--kronos", "X", "--every", "2",
                                      "--horizon", "30", "--cache", str(tmp_path / "none.jsonl")])
    CK.main()
    out = capsys.readouterr().out
    assert "KRONOS KALIBR" in out and "CSAK KRONOS" in out and "Vak bel" in out
    assert len((tmp_path / "kronos_cache.jsonl").read_text().splitlines()) == 100
    CK.main()  # második futás a cache-ből
    assert len((tmp_path / "kronos_cache.jsonl").read_text().splitlines()) == 100
