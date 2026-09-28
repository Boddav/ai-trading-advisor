"""One trading pass: for every configured symbol ask Jev, then open/close/hold on cTrader.

Run: python -m advisor.main [--config config.toml]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from . import indicators as ind
from .config import load_config, load_secrets, trading_enabled
from .ctrader import CTraderClient, lots_to_volume
from .jev import JevClient, JevError
from .strategy import build_questions, build_state, decide

log = logging.getLogger("advisor")


def write_summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


async def run(config_path: str) -> int:
    cfg = load_config(config_path)
    sec = load_secrets()
    live_orders = trading_enabled()
    jev = JevClient(sec.typesafe_api_key, cfg.jev_model)
    mode = "LIVE ORDERS" if live_orders else "DRY RUN (TRADING_ENABLED != true)"
    log.info("env=%s mode=%s timeframe=%s", cfg.environment, mode, cfg.timeframe)

    summary = [f"## Jev run — {cfg.environment} account, {mode}", "",
               "| Symbol | Position | Decision | Reason | Result |", "|---|---|---|---|---|"]
    failures = 0

    async with CTraderClient(cfg.environment, sec.account_id, url=os.environ.get("CTRADER_WS_URL")) as ct:
        await ct.authenticate(sec.client_id, sec.client_secret, sec.access_token)
        symbols = await ct.symbols([s.name for s in cfg.symbols])
        prefix = f"{cfg.label_prefix}_"
        mine = [p for p in await ct.positions() if p.label.startswith(prefix)]
        open_count = len(mine)
        log.info("balance=%.2f, own open positions=%d", await ct.balance(), open_count)

        for sc in cfg.symbols:
            sym = symbols[sc.name]
            pos = next((p for p in mine if p.symbol_id == sym.symbol_id), None)
            pos_txt = "flat" if pos is None else f"{pos.side} @ {pos.entry}"
            try:
                bars = await ct.trendbars(sym.symbol_id, cfg.timeframe, cfg.bars)
                atr = ind.atr(bars, 14)
                if len(bars) < 50 or not atr:
                    raise RuntimeError(f"not enough history ({len(bars)} bars)")
                state = build_state(sc.name, cfg.timeframe, bars, pos, sym.digits)
                questions = build_questions(sc.name, cfg.timeframe, pos, cfg.sl_atr, cfg.tp_atr)
                res = jev.system_one(state, questions)
                d = decide(res.get("answers", {}), pos, cfg.min_confidence, cfg.min_bias)
                log.info("%s %s -> %s (%s) probs=%s", sc.name, pos_txt, d.action, d.reason,
                         {k: round(v, 3) for k, v in d.probabilities.items()})

                result = "-"
                if d.action.startswith("open_"):
                    volume = lots_to_volume(sc.lots, sym)
                    if open_count >= cfg.max_open_positions:
                        result = f"skipped: max_open_positions={cfg.max_open_positions}"
                    elif volume == 0:
                        result = f"skipped: {sc.lots} lots below minimum volume"
                    elif not live_orders:
                        result = "dry run"
                    else:
                        side = d.action.removeprefix("open_")
                        ev = await ct.market_order(sym, side, volume, f"{prefix}{sc.name}",
                                                   atr * cfg.sl_atr, atr * cfg.tp_atr)
                        open_count += 1
                        result = f"opened {side} {sc.lots} lots, position {ev.get('position', {}).get('positionId', '?')}"
                elif d.action == "close" and pos is not None:
                    if not live_orders:
                        result = "dry run"
                    else:
                        await ct.close_position(pos)
                        open_count -= 1
                        result = f"closed position {pos.position_id}"
                log.info("%s result: %s", sc.name, result)
            except (JevError, RuntimeError, TimeoutError) as e:
                failures += 1
                d_action, d_reason, result = "error", str(e)[:120], "-"
                log.error("%s failed: %s", sc.name, e)
                summary.append(f"| {sc.name} | {pos_txt} | {d_action} | {d_reason} | {result} |")
                continue
            summary.append(f"| {sc.name} | {pos_txt} | {d.action} | {d.reason} | {result} |")

    write_summary(summary)
    return 1 if failures == len(cfg.symbols) else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="config.toml")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(asyncio.run(run(args.config)))


if __name__ == "__main__":
    main()
