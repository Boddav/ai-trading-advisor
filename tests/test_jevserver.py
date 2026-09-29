"""JevServer: the HTTP bridge JevTrader.c calls."""
import importlib.util
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

spec = importlib.util.spec_from_file_location("JevServer", Path(__file__).parent.parent / "zorro" / "JevServer.py")
JS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(JS)


class FakeJev:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, api_key, state, questions):
        assert api_key == "KEY"
        self.calls.append((state, questions))
        return {"answers": self.answers}


def _post(port, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/decide", data=json.dumps(body).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def _serve(jev):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), JS.make_handler("KEY", jev))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


import pytest


@pytest.fixture(autouse=True)
def classic_mode(monkeypatch):
    """Most tests exercise the classic questions; tp_first has its own tests."""
    monkeypatch.setattr(JS, "QUESTION_MODE", "classic")


BARS = [[1.08 + i * 1e-4, 1.0806 + i * 1e-4, 1.0797 + i * 1e-4, 1.0803 + i * 1e-4] for i in range(200)]


def test_decide_open_long_like_zorro_request():
    jev = FakeJev({"bias": {"choice": "long", "probabilities": {"long": 0.8, "short": 0.2}},
                   "intent": {"choice": "open", "probabilities": {"open": 0.9, "hold": 0.1}}})
    srv = _serve(jev)
    try:
        out = _post(srv.server_address[1], {"asset": "EUR/USD", "tf": "H1", "digits": 5, "pos": "flat",
                                             "entry": 0, "bars": BARS})
    finally:
        srv.shutdown()
    assert out["action"] == "open_long" and out["p_long"] == 0.8 and out["p_intent"] == 0.9
    state, questions = jev.calls[0]
    assert state["symbol"] == "EUR/USD" and state["last_close"] == 1.1002
    assert set(questions["intent"]["criteria"]) == {"open", "hold"}


def test_decide_close_and_bad_request():
    jev = FakeJev({"intent": {"choice": "close", "probabilities": {"close": 0.8, "hold": 0.2}}})
    srv = _serve(jev)
    try:
        port = srv.server_address[1]
        out = _post(port, {"asset": "GBP/USD", "pos": "short", "entry": 1.09, "bars": BARS})
        bad = _post(port, {"asset": "GBP/USD", "pos": "flat", "bars": BARS[:10]})
    finally:
        srv.shutdown()
    assert out["action"] == "close"
    assert jev.calls[0][0]["position"]["side"] == "short"
    assert bad["action"] == "error" and "50" in bad["reason"]


def test_standalone_server_matches_advisor_strategy():
    """The Zorro server inlines advisor.strategy; both must ask and decide the same."""
    from advisor.ctrader import Bar
    from advisor.strategy import build_questions, build_state, decide

    bars = [Bar(i, *b, 0) for i, b in enumerate(BARS)]
    assert JS.build_state("EUR/USD", "H1", BARS, "flat", 0, 5) == build_state("EUR/USD", "H1", bars, None, 5)
    assert JS.build_questions("EUR/USD", "H1", "flat") == build_questions("EUR/USD", "H1", None, 1.5, 3.0)
    ans = {"bias": {"probabilities": {"long": 0.3, "short": 0.7}}, "intent": {"probabilities": {"open": 0.8, "hold": 0.2}}}
    assert JS.decide(ans, "flat")[0] == decide(ans, None, 0.6, 0.55).action == "open_short"


def test_cache_and_prefetch(tmp_path):
    """Export -> parallel prefetch -> the backtest's identical requests hit the cache."""
    jev = FakeJev({"bias": {"probabilities": {"long": 0.7, "short": 0.3}},
                   "intent": {"probabilities": {"open": 0.8, "hold": 0.2}}})
    export = tmp_path / "JevExport.jsonl"
    reqs = [{"asset": a, "tf": "H1", "digits": 5, "pos": "flat", "entry": 0, "bars": BARS[i:i + 150]}
            for a in ("EUR/USD", "GBP/USD") for i in range(10)]
    export.write_text("".join(json.dumps(r) + "\n" for r in reqs) + "{broken\n")
    cache = JS.JevCache(str(tmp_path / "jev_cache.jsonl"))
    JS.prefetch(str(export), "KEY", threads=4, call=jev, cache=cache)
    assert len(jev.calls) == 20 and len(cache) == 20

    # a later server run loads the file and answers without calling Jev
    cache2 = JS.JevCache(str(tmp_path / "jev_cache.jsonl"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), JS.make_handler("KEY", jev, cache2))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        out = _post(srv.server_address[1], reqs[3])
        miss = _post(srv.server_address[1], {**reqs[3], "pos": "long", "entry": 1.08})
    finally:
        srv.shutdown()
    assert out["cached"] is True and out["action"] == "open_long"
    assert miss["cached"] is False and len(jev.calls) == 21

    # prefetch again: nothing left to do
    JS.prefetch(str(export), "KEY", threads=4, call=jev, cache=cache2)
    assert len(jev.calls) == 21


def test_tp_first_mode(monkeypatch):
    monkeypatch.setattr(JS, "QUESTION_MODE", "tp_first")
    q = JS.build_questions("EUR/USD", "H1", "flat")
    assert set(q) == {"long_tp_first", "short_tp_first"}
    assert q["long_tp_first"]["type"] == "noul" and set(q["long_tp_first"]["criteria"]) == {"true", "false"}
    assert "above" in q["long_tp_first"]["instructions"]["question"].split("take profit")[1].split("stop")[0]

    assert JS.decide({"long_tp_first": {"noul": 0.31}, "short_tp_first": {"noul": 0.46}}, "flat")[0] == "open_short"
    assert JS.decide({"long_tp_first": {"noul": 0.35}, "short_tp_first": {"noul": 0.30}}, "flat")[0] == "hold"
    # no direction edge: both sides alike -> hold (the GBP/USD 0.46 / 0.46 case)
    assert JS.decide({"long_tp_first": {"noul": 0.46}, "short_tp_first": {"noul": 0.46}}, "flat")[0] == "hold"
    assert JS.decide({"long_tp_first": {"noul": 0.45}, "short_tp_first": {"noul": 0.43}}, "flat")[0] == "hold"
    assert JS.decide({"long_tp_first": {"noul": 0.35}, "short_tp_first": {"noul": 0.53}}, "flat")[0] == "open_short"
    assert JS.decide({}, "flat")[0] == "hold"
    # with an open position the close/hold question is still used
    assert set(JS.build_questions("EUR/USD", "H1", "long")) == {"bias", "intent"}
    assert JS.decide({"intent": {"probabilities": {"close": 0.7, "hold": 0.3}}}, "long")[0] == "close"


def test_tp_first_over_http_and_cache_key_depends_on_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(JS, "QUESTION_MODE", "tp_first")
    jev = FakeJev({"long_tp_first": {"type": "noul", "noul": 0.52}, "short_tp_first": {"type": "noul", "noul": 0.2}})
    req = {"asset": "EUR/USD", "tf": "H1", "digits": 5, "pos": "flat", "entry": 0, "bars": BARS}
    k_tp = JS.JevCache.key(req)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), JS.make_handler("KEY", jev, JS.JevCache(str(tmp_path / "c.jsonl"))))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        out = _post(srv.server_address[1], req)
    finally:
        srv.shutdown()
    assert out["action"] == "open_long" and out["p_long"] == 0.52
    monkeypatch.setattr(JS, "QUESTION_MODE", "classic")
    assert JS.JevCache.key(req) != k_tp


def test_rejects_zero_price_bars():
    jev = FakeJev({})
    bad = [list(b) for b in BARS]
    bad[21] = [0.0, 0.0, 0.0, 0.0]  # like Zorro's "gap at #21 (NaD) 0->0.00000"
    srv = _serve(jev)
    try:
        out = _post(srv.server_address[1], {"asset": "EUR/USD", "pos": "flat", "bars": bad})
    finally:
        srv.shutdown()
    assert out["action"] == "error" and "0 ár" in out["reason"] and jev.calls == []


def test_gate_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(JS, "GATE_LOG", str(tmp_path / "gate.csv"))
    jev = FakeJev({"entry_ok": {"type": "noul", "noul": 0.43}})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), JS.make_handler("KEY", jev, JS.JevCache(str(tmp_path / "c.jsonl"))))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def post(path, body):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=json.dumps(body).encode(),
                                     method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    try:
        body = {"asset": "EUR/USD", "side": "short", "strategy": "Z12", "digits": 5, "tf": "H1", "bars": BARS}
        out = post("/gate", body)
        again = post("/gate", body)
        bad = post("/gate", {**body, "side": "up"})
    finally:
        srv.shutdown()
    assert out["p"] == 0.43 and out["cached"] is False and again["cached"] is True
    q = jev.calls[0][1]["entry_ok"]
    assert q["type"] == "noul" and "Z12" in q["instructions"]["question"] and "short" in q["instructions"]["question"]
    assert "p" not in bad and "error" in bad
    assert (tmp_path / "gate.csv").read_text().count("Z12,EUR/USD,short") == 2
