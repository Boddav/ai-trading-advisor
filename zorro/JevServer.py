# =================================================================
# JEV SERVER — Zorro lite-C  <->  TypeSafe Jev bridge
#
# JevTrader.c POST-ol ide (http_transfer), a szerver megkérdezi a Jevet,
# és visszaadja a döntést: open_long / open_short / close / hold.
#
# Endpoints:
#   POST /decide   {"asset","tf","digits","pos","entry","bars":[[o,h,l,c],...]}
#                  -> {"action","p_long","p_short","p_intent","reason"}
#   GET  /health
#
# API kulcs: TYPESAFE_API_KEY környezeti változó, vagy jev_key.txt a szerver mellett.
# Port: 5003 (nem ütközik: MLDRIVEN 5001, UltOsc 5002)
#
# Usage: python JevServer.py [--port 5003]
# =================================================================
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # repo root -> advisor package

from advisor.ctrader import Bar, Position  # noqa: E402
from advisor.jev import JevClient, JevError  # noqa: E402
from advisor.strategy import build_questions, build_state, decide  # noqa: E402

log = logging.getLogger("jevserver")


def load_settings() -> dict:
    s = {"model": "jev-latest", "min_confidence": 0.6, "min_bias": 0.55, "sl_atr": 1.5, "tp_atr": 3.0}
    cfg = HERE.parent / "config.toml"
    if cfg.exists():
        raw = tomllib.loads(cfg.read_text(encoding="utf-8"))
        s.update({k: v for k, v in raw.get("jev", {}).items() if k in s})
        s.update({k: v for k, v in raw.get("strategy", {}).items() if k in s})
    return s


def api_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key and (HERE / "jev_key.txt").exists():
        key = (HERE / "jev_key.txt").read_text(encoding="utf-8").strip()
    if not key:
        sys.exit("TYPESAFE_API_KEY hiányzik (környezeti változó vagy zorro/jev_key.txt)")
    return key


def handle_decide(req: dict, jev: JevClient, s: dict) -> dict:
    asset = str(req["asset"])
    tf = str(req.get("tf", "H1"))
    digits = int(req.get("digits", 5))
    bars = [Bar(i, float(o), float(h), float(l), float(c), 0) for i, (o, h, l, c) in enumerate(req["bars"])]
    if len(bars) < 50:
        raise ValueError(f"legalább 50 bar kell, jött: {len(bars)}")
    side = str(req.get("pos", "flat")).lower()
    pos = None if side not in ("long", "short") else Position(0, 0, side, 0, float(req.get("entry", 0)), "")
    state = build_state(asset, tf, bars, pos, digits)
    res = jev.system_one(state, build_questions(asset, tf, pos, s["sl_atr"], s["tp_atr"]))
    d = decide(res.get("answers", {}), pos, s["min_confidence"], s["min_bias"])
    p = d.probabilities
    return {
        "action": d.action,
        "p_long": round(p.get("long", 0), 4),
        "p_short": round(p.get("short", 0), 4),
        "p_intent": round(p.get("open" if pos is None else "close", 0), 4),
        "reason": d.reason,
    }


def make_handler(jev: JevClient, s: dict):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                self._reply(200, {"status": "ok", "model": jev.model})
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/decide":
                return self._reply(404, {"error": "not found"})
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                out = handle_decide(req, jev, s)
                log.info("%s pos=%s -> %s (%s)", req.get("asset"), req.get("pos"), out["action"], out["reason"])
                self._reply(200, out)
            except (JevError, ValueError, KeyError, TypeError) as e:
                log.error("decide failed: %s", e)
                # Zorro side treats anything that is not an action as hold
                self._reply(200, {"action": "error", "reason": str(e)[:200]})

        def log_message(self, *args):
            pass

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5003)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = load_settings()
    jev = JevClient(api_key(), s["model"])
    log.info("Jev server on 127.0.0.1:%d (model=%s, min_confidence=%s, min_bias=%s)",
             args.port, s["model"], s["min_confidence"], s["min_bias"])
    ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(jev, s)).serve_forever()


if __name__ == "__main__":
    main()
