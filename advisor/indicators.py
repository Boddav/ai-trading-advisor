"""Plain-Python indicators over closed bars (oldest first)."""
from __future__ import annotations

from .ctrader import Bar


def sma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def ema(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    k = 2 / (n + 1)
    e = sum(values[:n]) / n
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e


def rsi(values: list[float], n: int = 14) -> float | None:
    """Wilder's RSI."""
    if len(values) <= n:
        return None
    gains = losses = 0.0
    for a, b in zip(values[:n], values[1:n + 1]):
        d = b - a
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g, avg_l = gains / n, losses / n
    for a, b in zip(values[n:], values[n + 1:]):
        d = b - a
        avg_g = (avg_g * (n - 1) + max(d, 0.0)) / n
        avg_l = (avg_l * (n - 1) + max(-d, 0.0)) / n
    if avg_l == 0:
        return 100.0
    return 100 - 100 / (1 + avg_g / avg_l)


def atr(bars: list[Bar], n: int = 14) -> float | None:
    """Wilder's ATR."""
    if len(bars) <= n:
        return None
    trs = [max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close)) for p, b in zip(bars, bars[1:])]
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def return_bps(values: list[float], lookback: int) -> float | None:
    if len(values) <= lookback or values[-1 - lookback] == 0:
        return None
    return (values[-1] / values[-1 - lookback] - 1) * 10_000
