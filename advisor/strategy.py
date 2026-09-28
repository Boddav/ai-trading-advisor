"""Market state for Jev, the questions asked, and mapping answers to an action."""
from __future__ import annotations

from dataclasses import dataclass

from . import indicators as ind
from .ctrader import Bar, Position
from .jev import choice


@dataclass(frozen=True)
class Decision:
    action: str            # "open_long" | "open_short" | "close" | "hold"
    reason: str
    probabilities: dict


def _r(x: float | None, nd: int = 5) -> float | None:
    return None if x is None else round(x, nd)


def build_state(symbol: str, timeframe: str, bars: list[Bar], position: Position | None, digits: int) -> dict:
    closes = [b.close for b in bars]
    last = bars[-1]
    atr14 = ind.atr(bars, 14)
    high20 = max(b.high for b in bars[-20:])
    low20 = min(b.low for b in bars[-20:])
    state = {
        "symbol": symbol,
        "timeframe": timeframe,
        "last_close": round(last.close, digits),
        "returns_bps": {
            "last1": _r(ind.return_bps(closes, 1), 1),
            "last5": _r(ind.return_bps(closes, 5), 1),
            "last20": _r(ind.return_bps(closes, 20), 1),
            "last100": _r(ind.return_bps(closes, 100), 1),
        },
        "indicators": {
            "sma20": _r(ind.sma(closes, 20), digits),
            "sma50": _r(ind.sma(closes, 50), digits),
            "sma200": _r(ind.sma(closes, 200), digits),
            "ema20": _r(ind.ema(closes, 20), digits),
            "rsi14": _r(ind.rsi(closes, 14), 1),
            "atr14": _r(atr14, digits),
            "high20": round(high20, digits),
            "low20": round(low20, digits),
            "range_pos20": _r((last.close - low20) / (high20 - low20), 2) if high20 > low20 else None,
        },
        "recent_bars": [
            [round(b.open, digits), round(b.high, digits), round(b.low, digits), round(b.close, digits)]
            for b in bars[-12:]
        ],
        "position": {"side": "flat"},
    }
    if position is not None:
        state["position"] = {
            "side": position.side,
            "entry": round(position.entry, digits),
            "move_vs_entry_atr": _r((last.close - position.entry) / atr14 * (1 if position.side == "long" else -1), 2)
            if atr14 else None,
        }
    return state


def build_questions(symbol: str, timeframe: str, position: Position | None, sl_atr: float, tp_atr: float) -> dict:
    ctx = (f"{symbol} {timeframe} closed bars. recent_bars are [open, high, low, close], oldest first. "
           f"A trade uses a stop {sl_atr}x ATR and a target {tp_atr}x ATR away from entry.")
    bias = choice(
        {"question": f"Over the next few {timeframe} bars, is {symbol} more likely to move up or down?",
         "inputs": ctx},
        {"long": f"{symbol} rises", "short": f"{symbol} falls"},
    )
    if position is None:
        intent = choice(
            {"question": f"Open a new {symbol} trade now, or wait?", "goal": "position is flat", "inputs": ctx},
            {"open": "open a trade in the expected direction now", "hold": "stay flat"},
        )
    else:
        intent = choice(
            {"question": f"Close the open {position.side} {symbol} position now, or keep it?",
             "goal": f"position is {position.side}", "inputs": ctx},
            {"close": "close the position now", "hold": "keep the position"},
        )
    return {"bias": bias, "intent": intent}


def _probs(answer: dict | None, keys: tuple[str, ...]) -> dict[str, float]:
    """Normalized probabilities over keys; falls back to the picked label."""
    answer = answer or {}
    picked = str(answer.get("choice", "")).strip().lower()
    raw = answer.get("probabilities") or {}
    vals = [max(0.0, float(raw.get(k, 1.0 if picked == k else 0.0))) for k in keys]
    total = sum(vals)
    if total <= 0:
        return {k: 1.0 / len(keys) for k in keys}
    return {k: v / total for k, v in zip(keys, vals)}


def decide(answers: dict, position: Position | None, min_confidence: float, min_bias: float) -> Decision:
    bias = _probs(answers.get("bias"), ("long", "short"))
    if position is None:
        intent = _probs(answers.get("intent"), ("open", "hold"))
        probs = {**bias, **intent}
        side = "long" if bias["long"] >= bias["short"] else "short"
        if intent["open"] < min_confidence:
            return Decision("hold", f"open p={intent['open']:.2f} < {min_confidence}", probs)
        if bias[side] < min_bias:
            return Decision("hold", f"direction unclear ({side} p={bias[side]:.2f} < {min_bias})", probs)
        return Decision(f"open_{side}", f"open p={intent['open']:.2f}, {side} p={bias[side]:.2f}", probs)

    intent = _probs(answers.get("intent"), ("close", "hold"))
    probs = {**bias, **intent}
    if intent["close"] >= min_confidence:
        return Decision("close", f"close p={intent['close']:.2f}", probs)
    return Decision("hold", f"keep {position.side} (close p={intent['close']:.2f})", probs)
