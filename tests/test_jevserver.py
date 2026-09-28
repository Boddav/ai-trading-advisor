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
