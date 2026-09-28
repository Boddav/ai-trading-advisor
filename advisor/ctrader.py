"""Minimal async cTrader Open API client (JSON over WebSocket, port 5036).

Message shapes, payload types and scaling follow Boddav/ctrader-zorro-plugin
(src/protocol.cpp, src/trading.cpp, src/symbols.cpp, src/dllmain.cpp).
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from dataclasses import dataclass

import websockets

log = logging.getLogger("ctrader")

HOSTS = {"demo": "demo.ctraderapi.com", "live": "live.ctraderapi.com"}
PORT = 5036
PRICE_SCALE = 100_000  # prices and relative SL/TP are integers in 1/100000
MAX_CHUNK_MS = 7 * 24 * 3600 * 1000  # trendbar request window limit

# Payload types (spotware OpenApiModelMessages.proto)
HEARTBEAT = 51
APP_AUTH_REQ, APP_AUTH_RES = 2100, 2101
ACCOUNT_AUTH_REQ, ACCOUNT_AUTH_RES = 2102, 2103
NEW_ORDER_REQ = 2106
CLOSE_POSITION_REQ = 2111
SYMBOLS_LIST_REQ, SYMBOLS_LIST_RES = 2114, 2115
SYMBOL_BY_ID_REQ, SYMBOL_BY_ID_RES = 2116, 2117
TRADER_REQ, TRADER_RES = 2121, 2122
RECONCILE_REQ, RECONCILE_RES = 2124, 2125
EXECUTION_EVENT = 2126
ORDER_ERROR_EVENT = 2132
GET_TRENDBARS_REQ, GET_TRENDBARS_RES = 2137, 2138
ERROR_RES = 2142

BUY, SELL = 1, 2
MARKET = 1
EXEC_FILLED, EXEC_REJECTED, EXEC_PARTIAL = 3, 7, 11

PERIODS = {  # name -> (ProtoOATrendbarPeriod, minutes)
    "M1": (1, 1), "M5": (5, 5), "M15": (7, 15), "M30": (8, 30),
    "H1": (9, 60), "H4": (10, 240), "D1": (12, 1440),
}


class CTraderError(RuntimeError):
    pass


@dataclass(frozen=True)
class SymbolInfo:
    symbol_id: int
    name: str
    digits: int
    lot_size: int      # volume units (cents of base units) per 1 lot
    min_volume: int
    max_volume: int
    step_volume: int


@dataclass(frozen=True)
class Bar:
    ts_minutes: int
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class Position:
    position_id: int
    symbol_id: int
    side: str          # "long" | "short"
    volume: int
    entry: float
    label: str


def decode_trendbar(tb: dict) -> Bar:
    """Trendbars are delta-encoded: low is absolute, the rest relative to low."""
    low = int(tb.get("low", 0))
    ts = int(tb.get("utcTimestampInMinutes", 0)) or int(tb.get("timestamp", 0)) // 60000
    return Bar(
        ts_minutes=ts,
        open=(low + int(tb.get("deltaOpen", 0))) / PRICE_SCALE,
        high=(low + int(tb.get("deltaHigh", 0))) / PRICE_SCALE,
        low=low / PRICE_SCALE,
        close=(low + int(tb.get("deltaClose", 0))) / PRICE_SCALE,
        volume=int(tb.get("volume", 0)),
    )


def lots_to_volume(lots: float, sym: SymbolInfo) -> int:
    """Convert lots to cTrader volume, rounded to stepVolume. 0 if below minVolume."""
    vol = round(lots * sym.lot_size)
    if sym.step_volume > 0:
        vol = round(vol / sym.step_volume) * sym.step_volume
    if vol < sym.min_volume:
        return 0
    return min(vol, sym.max_volume) if sym.max_volume > 0 else vol


def price_distance_points(distance: float, digits: int) -> int:
    """Price distance -> 1/100000 integer, rounded to the symbol's price precision."""
    step = 10 ** max(0, 5 - digits)
    return max(step, round(distance * PRICE_SCALE / step) * step)


class CTraderClient:
    def __init__(self, environment: str, account_id: int, timeout: float = 15.0, url: str | None = None):
        self.url = url or f"wss://{HOSTS[environment]}:{PORT}"
        self.account_id = account_id
        self.timeout = timeout
        self._ws = None
        self._ids = itertools.count(1)
        self._waiters: dict[str, asyncio.Queue] = {}
        self._tasks: list[asyncio.Task] = []

    # ---- connection -------------------------------------------------------
    async def connect(self) -> None:
        self._ws = await websockets.connect(self.url, max_size=16 * 1024 * 1024)
        self._tasks = [asyncio.create_task(self._reader()), asyncio.create_task(self._heartbeat())]
        log.info("connected to %s", self.url)

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self._ws is not None:
            await self._ws.close()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(10)
            await self._ws.send(json.dumps({"payloadType": HEARTBEAT, "payload": {}}))

    async def _reader(self) -> None:
        async for raw in self._ws:
            msg = json.loads(raw)
            q = self._waiters.get(msg.get("clientMsgId", ""))
            if q is not None:
                q.put_nowait(msg)
            elif msg.get("payloadType") != HEARTBEAT:
                log.debug("unsolicited message type %s", msg.get("payloadType"))

    async def _send(self, payload_type: int, payload: dict) -> tuple[str, asyncio.Queue]:
        msg_id = f"jev{next(self._ids)}"
        q: asyncio.Queue = asyncio.Queue()
        self._waiters[msg_id] = q
        await self._ws.send(json.dumps({"clientMsgId": msg_id, "payloadType": payload_type, "payload": payload}))
        return msg_id, q

    async def request(self, payload_type: int, payload: dict, expect: int) -> dict:
        msg_id, q = await self._send(payload_type, payload)
        try:
            msg = await asyncio.wait_for(q.get(), self.timeout)
        finally:
            self._waiters.pop(msg_id, None)
        pt, body = msg.get("payloadType"), msg.get("payload", {})
        if pt in (ERROR_RES, ORDER_ERROR_EVENT):
            raise CTraderError(f"{body.get('errorCode')}: {body.get('description', '')}")
        if pt != expect:
            raise CTraderError(f"unexpected payloadType {pt} (expected {expect})")
        return body

    # ---- auth -------------------------------------------------------------
    async def authenticate(self, client_id: str, client_secret: str, access_token: str) -> None:
        await self.request(APP_AUTH_REQ, {"clientId": client_id, "clientSecret": client_secret}, APP_AUTH_RES)
        await self.request(ACCOUNT_AUTH_REQ,
                           {"ctidTraderAccountId": self.account_id, "accessToken": access_token},
                           ACCOUNT_AUTH_RES)
        log.info("authenticated account %s", self.account_id)

    # ---- account / symbols -----------------------------------------------
    async def balance(self) -> float:
        body = await self.request(TRADER_REQ, {"ctidTraderAccountId": self.account_id}, TRADER_RES)
        trader = body.get("trader", {})
        return int(trader.get("balance", 0)) / 10 ** int(trader.get("moneyDigits", 2))

    async def symbols(self, names: list[str]) -> dict[str, SymbolInfo]:
        body = await self.request(SYMBOLS_LIST_REQ, {"ctidTraderAccountId": self.account_id}, SYMBOLS_LIST_RES)
        by_name = {s.get("symbolName", "").upper(): int(s["symbolId"]) for s in body.get("symbol", [])}
        missing = [n for n in names if n not in by_name]
        if missing:
            raise CTraderError(f"symbols not found on account: {', '.join(missing)}")
        ids = [by_name[n] for n in names]
        body = await self.request(SYMBOL_BY_ID_REQ,
                                  {"ctidTraderAccountId": self.account_id, "symbolId": ids}, SYMBOL_BY_ID_RES)
        id_to_name = {v: k for k, v in by_name.items()}
        out = {}
        for s in body.get("symbol", []):
            sid = int(s["symbolId"])
            out[id_to_name[sid]] = SymbolInfo(
                symbol_id=sid, name=id_to_name[sid], digits=int(s.get("digits", 5)),
                lot_size=int(s.get("lotSize", 0)), min_volume=int(s.get("minVolume", 0)),
                max_volume=int(s.get("maxVolume", 0)), step_volume=int(s.get("stepVolume", 0)),
            )
        return out

    async def trendbars(self, symbol_id: int, timeframe: str, count: int) -> list[Bar]:
        """Last `count` closed bars, oldest first. Fetched in <=7 day windows."""
        period, minutes = PERIODS[timeframe]
        now_ms = int(time.time() * 1000)
        # window sized to the bars needed plus a weekend gap, so it never holds far more than asked
        window = min(MAX_CHUNK_MS, (count + 1) * minutes * 60000 + 52 * 3600 * 1000)
        end = now_ms
        bars: dict[int, Bar] = {}
        for _ in range(60):
            if len(bars) > count:
                break
            start = max(0, end - window)
            body = await self.request(GET_TRENDBARS_REQ, {
                "ctidTraderAccountId": self.account_id, "symbolId": symbol_id, "period": period,
                "fromTimestamp": start, "toTimestamp": end,
            }, GET_TRENDBARS_RES)
            for tb in body.get("trendbar", []):
                b = decode_trendbar(tb)
                bars[b.ts_minutes] = b
            end = start
        ordered = sorted(bars.values(), key=lambda b: b.ts_minutes)
        # drop the bar that is still forming
        if ordered and (ordered[-1].ts_minutes + minutes) * 60000 > now_ms:
            ordered.pop()
        return ordered[-count:]

    async def positions(self) -> list[Position]:
        body = await self.request(RECONCILE_REQ, {"ctidTraderAccountId": self.account_id}, RECONCILE_RES)
        out = []
        for p in body.get("position", []):
            td = p.get("tradeData", {})
            out.append(Position(
                position_id=int(p["positionId"]), symbol_id=int(td.get("symbolId", 0)),
                side="long" if int(td.get("tradeSide", BUY)) == BUY else "short",
                volume=int(td.get("volume", 0)), entry=float(p.get("price", 0.0)),
                label=td.get("label", ""),
            ))
        return out

    # ---- trading ----------------------------------------------------------
    async def _await_execution(self, msg_id: str, q: asyncio.Queue) -> dict:
        """Wait until the order is filled or rejected. Returns the last ExecutionEvent payload."""
        last: dict = {}
        deadline = time.monotonic() + self.timeout
        try:
            while (left := deadline - time.monotonic()) > 0:
                try:
                    msg = await asyncio.wait_for(q.get(), left)
                except asyncio.TimeoutError:
                    break
                pt, body = msg.get("payloadType"), msg.get("payload", {})
                if pt in (ERROR_RES, ORDER_ERROR_EVENT):
                    raise CTraderError(f"{body.get('errorCode')}: {body.get('description', '')}")
                if pt == EXECUTION_EVENT:
                    last = body
                    et = int(body.get("executionType", 0))
                    if et == EXEC_REJECTED:
                        raise CTraderError(f"order rejected: {body.get('errorCode', '')}")
                    if et in (EXEC_FILLED, EXEC_PARTIAL):
                        return body
        finally:
            self._waiters.pop(msg_id, None)
        if not last:
            raise CTraderError("no execution event before timeout")
        log.warning("order accepted but fill not confirmed in %.0fs", self.timeout)
        return last

    async def market_order(self, sym: SymbolInfo, side: str, volume: int, label: str,
                           sl_distance: float, tp_distance: float) -> dict:
        payload = {
            "ctidTraderAccountId": self.account_id, "symbolId": sym.symbol_id, "orderType": MARKET,
            "tradeSide": BUY if side == "long" else SELL, "volume": volume, "label": label,
        }
        if sl_distance > 0:
            payload["relativeStopLoss"] = price_distance_points(sl_distance, sym.digits)
        if tp_distance > 0:
            payload["relativeTakeProfit"] = price_distance_points(tp_distance, sym.digits)
        msg_id, q = await self._send(NEW_ORDER_REQ, payload)
        return await self._await_execution(msg_id, q)

    async def close_position(self, pos: Position) -> dict:
        msg_id, q = await self._send(CLOSE_POSITION_REQ, {
            "ctidTraderAccountId": self.account_id, "positionId": pos.position_id, "volume": pos.volume,
        })
        return await self._await_execution(msg_id, q)
