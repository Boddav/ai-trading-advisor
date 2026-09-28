from advisor import indicators as ind
from advisor.ctrader import Bar, Position, SymbolInfo, decode_trendbar, lots_to_volume, price_distance_points
from advisor.strategy import build_questions, decide

EURUSD = SymbolInfo(1, "EURUSD", 5, lot_size=10_000_000, min_volume=100_000, max_volume=10_000_000_000,
                    step_volume=100_000)


def test_decode_trendbar_delta_encoding():
    b = decode_trendbar({"low": 108000, "deltaOpen": 20, "deltaHigh": 50, "deltaClose": 30,
                         "volume": 7, "utcTimestampInMinutes": 29000000})
    assert (b.open, b.high, b.low, b.close) == (1.0802, 1.0805, 1.08, 1.0803)
    assert b.ts_minutes == 29000000 and b.volume == 7


def test_lots_to_volume():
    assert lots_to_volume(0.01, EURUSD) == 100_000        # 1000 units
    assert lots_to_volume(0.015, EURUSD) == 200_000       # rounded to step (150k -> 200k, banker's rounding of 1.5)
    assert lots_to_volume(0.001, EURUSD) == 0             # below min volume


def test_price_distance_points_rounds_to_symbol_digits():
    assert price_distance_points(0.001234, 5) == 123
    assert price_distance_points(1.2345, 2) == 123000     # XAUUSD-like: 2 digits -> multiples of 1000
    assert price_distance_points(0.0, 5) == 1


def test_indicators():
    vals = [float(i) for i in range(1, 31)]
    assert ind.sma(vals, 10) == 25.5
    assert ind.rsi(vals, 14) == 100.0
    assert round(ind.return_bps([100.0, 101.0], 1), 6) == 100.0
    bars = [Bar(i, 1.0, 1.1, 0.9, 1.0, 0) for i in range(20)]
    assert abs(ind.atr(bars, 14) - 0.2) < 1e-12


def test_decide_flat():
    ans = {"bias": {"choice": "short", "probabilities": {"long": 0.3, "short": 0.7}},
           "intent": {"choice": "open", "probabilities": {"open": 0.8, "hold": 0.2}}}
    assert decide(ans, None, 0.6, 0.55).action == "open_short"
    ans["intent"]["probabilities"] = {"open": 0.5, "hold": 0.5}
    assert decide(ans, None, 0.6, 0.55).action == "hold"
    ans = {"bias": {"probabilities": {"long": 0.52, "short": 0.48}},
           "intent": {"probabilities": {"open": 0.9, "hold": 0.1}}}
    assert decide(ans, None, 0.6, 0.55).action == "hold"   # direction too unclear


def test_decide_in_position_and_malformed_answers():
    pos = Position(5, 1, "long", 100_000, 1.08, "jev_EURUSD")
    assert decide({"intent": {"choice": "close", "probabilities": {"close": 0.7, "hold": 0.3}}},
                  pos, 0.6, 0.55).action == "close"
    assert decide({"intent": {"choice": "close"}}, pos, 0.6, 0.55).action == "close"  # no probs: use pick
    assert decide({}, pos, 0.6, 0.55).action == "hold"
    assert decide({}, None, 0.6, 0.55).action == "hold"


def test_questions_match_position():
    assert set(build_questions("EURUSD", "H1", None, 1.5, 3)["intent"]["criteria"]) == {"open", "hold"}
    pos = Position(5, 1, "short", 1, 1.0, "jev_EURUSD")
    q = build_questions("EURUSD", "H1", pos, 1.5, 3)
    assert set(q["intent"]["criteria"]) == {"close", "hold"}
    assert q["bias"]["type"] == "choice"
