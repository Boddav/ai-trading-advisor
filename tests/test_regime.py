"""JevServer "regime" mode (JevTradeDeep.c sends "mode":"regime") and calibrate_regime."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent / "zorro"
spec = importlib.util.spec_from_file_location("JevServer_r", ROOT / "server" / "JevServer.py")
JS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(JS)

UP = [[1.0 + i * 1e-3, 1.0 + i * 1e-3 + 8e-4, 1.0 + i * 1e-3 - 8e-4, 1.0 + i * 1e-3 + 5e-4] for i in range(200)]


def _flat(n=200, top=False):
    import math
    bars = []
    for i in range(n):
        c = 1.1 + 0.002 * math.sin(i / 3.0)
        bars.append([c, c + 4e-4, c - 4e-4, c])
    if top:  # utolsó bar a sáv tetején
        c = 1.1025
        bars[-1] = [c - 2e-4, c + 1e-4, c - 3e-4, c]
    return bars


def _jev(label, p=0.7):
    calls = []

    def call(key, state, questions):
        calls.append((state, questions))
        rest = (1 - p) / 2
        return {"answers": {"regime": {"choice": label, "probabilities":
                                       {k: (p if k == label else rest) for k in JS.REGIMES}}}}
    return call, calls


def req(bars, pos="flat"):
    return {"asset": "EUR/USD", "tf": "H1", "digits": 5, "pos": pos, "entry": 0, "bars": bars, "mode": "regime"}


def test_features_are_relative():
    f = JS.regime_features(UP)
    assert f["sma20_above_sma50"] is True and f["returns_atr"]["last20"] > 0
    assert f["efficiency_ratio20"] == 1.0 and f["range_pos20"] > 0.9
    assert len(f["recent_bars_atr"]) == 24 and f["recent_bars_atr"][-1][3] == 0.0


def test_trending_follows_trend_and_state_has_no_absolute_prices():
    call, calls = _jev("trending")
    out = JS.handle_decide(req(UP), "K", call=call)
    assert out["action"] == "open_long" and out["regime"] == "trending"
    state, q = calls[0]
    assert "last_close" not in state and "recent_bars" not in state
    assert set(q) == {"regime"} and set(q["regime"]["criteria"]) == set(JS.REGIMES)


def test_ranging_reverts_from_edge_and_choppy_holds():
    call, _ = _jev("ranging")
    out = JS.handle_decide(req(_flat(top=True)), "K", call=call)
    assert out["action"] == "open_short", out
    call, _ = _jev("choppy")
    assert JS.handle_decide(req(UP), "K", call=call)["action"] == "hold"
    call, _ = _jev("trending", p=0.40)  # bizonytalan
    assert JS.handle_decide(req(UP), "K", call=call)["action"] == "hold"


def test_open_position_no_jev_call_and_dom():
    call, calls = _jev("trending")
    assert JS.handle_decide(req(UP, "long"), "K", call=call)["action"] == "hold" and not calls
    r = req(UP)
    r["dom"] = {"best_bid": 1.1990, "best_ask": 1.1991, "imbalance": 0.2, "bids": [], "asks": []}
    JS.handle_decide(r, "K", call=call)
    assert calls[0][0]["depth_of_market"]["imbalance"] == 0.2


def test_calibrate_regime_end_to_end(tmp_path, monkeypatch, capsys):
    bars = [[1.0 + i * 2e-3, 1.0 + i * 2e-3 + 1e-3, 1.0 + i * 2e-3 - 1e-3, 1.0 + i * 2e-3 + 5e-4] for i in range(400)]
    rows = [{"asset": "EUR/USD", "tf": "H1", "digits": 5, "pos": "flat", "entry": 0, "bars": bars[i - 200:i]}
            for i in range(200, 400)]
    exp = tmp_path / "JevExport.jsonl"
    exp.write_text("\n".join(json.dumps(r) for r in rows))
    sys.path.insert(0, str(ROOT / "tools"))
    import calibrate_regime as CR
    call, _ = _jev("trending")
    monkeypatch.setattr(CR.JS, "jev_call", call)
    monkeypatch.setattr(CR.JS.handle_regime, "__defaults__", (call, None))
    monkeypatch.setattr(CR.JS, "api_key", lambda: "K")
    monkeypatch.setattr(sys, "argv", ["calibrate_regime.py", str(exp), "--fetch", "--threads", "2",
                                      "--horizon", "40", "--cache", str(tmp_path / "c.jsonl")])
    CR.main()
    out = capsys.readouterr().out
    assert "Jev válasz: 200" in out and "Jev: trending" in out and "véletlen rezsim" in out
    assert "Jev rezsim" in out and "kötés" in out
