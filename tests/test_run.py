"""End-to-end pass against a fake cTrader WebSocket server and a stubbed Jev."""
import asyncio
import json
import time

import websockets

from advisor import ctrader as C
from advisor import main as M

ACCOUNT = 42


def _bars(n, minutes=60):
    now_min = int(time.time() // 60) // minutes * minutes
    out = []
    for i in range(n):
        low = 108000 + (i % 17) * 10
        out.append({"low": low, "deltaOpen": 5, "deltaHigh": 40, "deltaClose": 20 + (i % 3),
                    "volume": 100, "utcTimestampInMinutes": now_min - (n - i) * minutes})
    return out


class FakeServer:
    def __init__(self):
        self.orders, self.closes = [], []

    async def handler(self, ws):
        async for raw in ws:
            m = json.loads(raw)
            pt, p, mid = m["payloadType"], m.get("payload", {}), m.get("clientMsgId")
            send = lambda t, body: ws.send(json.dumps({"clientMsgId": mid, "payloadType": t, "payload": body}))
            if pt == C.HEARTBEAT:
                continue
            if pt == C.APP_AUTH_REQ:
                await send(C.APP_AUTH_RES, {})
            elif pt == C.ACCOUNT_AUTH_REQ:
                assert p["ctidTraderAccountId"] == ACCOUNT and p["accessToken"] == "tok"
                await send(C.ACCOUNT_AUTH_RES, {"ctidTraderAccountId": ACCOUNT})
            elif pt == C.SYMBOLS_LIST_REQ:
                await send(C.SYMBOLS_LIST_RES, {"symbol": [{"symbolId": 1, "symbolName": "EURUSD"},
                                                           {"symbolId": 2, "symbolName": "GBPUSD"}]})
            elif pt == C.SYMBOL_BY_ID_REQ:
                await send(C.SYMBOL_BY_ID_RES, {"symbol": [
                    {"symbolId": i, "digits": 5, "lotSize": 10_000_000, "minVolume": 100_000,
                     "maxVolume": 10_000_000_000, "stepVolume": 100_000} for i in p["symbolId"]]})
            elif pt == C.TRADER_REQ:
                await send(C.TRADER_RES, {"trader": {"balance": 1234567, "moneyDigits": 2}})
            elif pt == C.RECONCILE_REQ:
                await send(C.RECONCILE_RES, {"position": [
                    {"positionId": 77, "price": 1.08, "tradeData": {"symbolId": 2, "volume": 100_000,
                                                                     "tradeSide": 2, "label": "jev_GBPUSD"}},
                    {"positionId": 88, "price": 1.08, "tradeData": {"symbolId": 1, "volume": 100_000,
                                                                     "tradeSide": 1, "label": "z_1__zorro"}}]})
            elif pt == C.GET_TRENDBARS_REQ:
                span = (p["toTimestamp"] - p["fromTimestamp"]) // 3_600_000
                bars = [b for b in _bars(400) if p["fromTimestamp"] <= b["utcTimestampInMinutes"] * 60000 < p["toTimestamp"]]
                assert span <= 7 * 24
                await send(C.GET_TRENDBARS_RES, {"trendbar": bars})
            elif pt == C.NEW_ORDER_REQ:
                self.orders.append(p)
                await send(C.EXECUTION_EVENT, {"executionType": 2})
                await send(C.EXECUTION_EVENT, {"executionType": 3, "position": {"positionId": 99}})
            elif pt == C.CLOSE_POSITION_REQ:
                self.closes.append(p)
                await send(C.EXECUTION_EVENT, {"executionType": 3})


def _run(monkeypatch, tmp_path, trading):
    fake = FakeServer()
    answers = {
        "EURUSD": {"bias": {"choice": "long", "probabilities": {"long": 0.8, "short": 0.2}},
                   "intent": {"choice": "open", "probabilities": {"open": 0.9, "hold": 0.1}}},
        "GBPUSD": {"bias": {"choice": "long", "probabilities": {"long": 0.6, "short": 0.4}},
                   "intent": {"choice": "close", "probabilities": {"close": 0.75, "hold": 0.25}}},
    }
    seen = []

    def fake_jev(self, state, questions):
        seen.append((state, questions))
        return {"answers": answers[state["symbol"]], "usage": {}}

    cfg = tmp_path / "config.toml"
    cfg.write_text('[strategy]\ntimeframe="H1"\nbars=200\n'
                   '[[symbols]]\nname="EURUSD"\nlots=0.01\n[[symbols]]\nname="GBPUSD"\nlots=0.01\n')
    summary = tmp_path / "summary.md"

    async def go():
        async with websockets.serve(fake.handler, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            monkeypatch.setenv("CTRADER_WS_URL", f"ws://127.0.0.1:{port}")
            return await M.run(str(cfg))

    for k, v in {"TYPESAFE_API_KEY": "k", "CTRADER_CLIENT_ID": "id", "CTRADER_CLIENT_SECRET": "s",
                 "CTRADER_ACCESS_TOKEN": "tok", "CTRADER_ACCOUNT_ID": str(ACCOUNT),
                 "TRADING_ENABLED": "true" if trading else "", "GITHUB_STEP_SUMMARY": str(summary)}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(M.JevClient, "system_one", fake_jev)
    rc = asyncio.run(go())
    return rc, fake, seen, summary.read_text()


def test_live_pass_opens_and_closes_only_own_positions(monkeypatch, tmp_path):
    rc, fake, seen, summary = _run(monkeypatch, tmp_path, trading=True)
    assert rc == 0
    # EURUSD: the Zorro position is ignored, so the bot sees flat and opens a long
    assert len(fake.orders) == 1
    o = fake.orders[0]
    assert (o["symbolId"], o["tradeSide"], o["volume"], o["label"]) == (1, C.BUY, 100_000, "jev_EURUSD")
    assert o["relativeStopLoss"] > 0 and o["relativeTakeProfit"] > o["relativeStopLoss"]
    # GBPUSD: own short position gets closed
    assert fake.closes == [{"ctidTraderAccountId": ACCOUNT, "positionId": 77, "volume": 100_000}]
    eur_state = next(s for s, _ in seen if s["symbol"] == "EURUSD")
    assert eur_state["position"] == {"side": "flat"} and eur_state["indicators"]["sma200"] is not None
    assert "opened long" in summary and "closed position 77" in summary


def test_dry_run_sends_no_orders(monkeypatch, tmp_path):
    rc, fake, _, summary = _run(monkeypatch, tmp_path, trading=False)
    assert rc == 0 and fake.orders == [] and fake.closes == []
    assert "DRY RUN" in summary and "dry run" in summary
