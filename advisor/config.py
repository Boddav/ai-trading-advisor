"""Configuration: config.toml for strategy settings, environment for secrets."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SymbolConfig:
    name: str
    lots: float


@dataclass(frozen=True)
class Config:
    environment: str              # "demo" | "live"
    label_prefix: str
    jev_model: str
    min_confidence: float
    min_bias: float
    timeframe: str
    bars: int
    sl_atr: float
    tp_atr: float
    max_open_positions: int
    symbols: list[SymbolConfig] = field(default_factory=list)


@dataclass(frozen=True)
class Secrets:
    typesafe_api_key: str
    client_id: str
    client_secret: str
    access_token: str
    account_id: int


def load_config(path: str | Path = "config.toml") -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    ct, jev, st = raw.get("ctrader", {}), raw.get("jev", {}), raw.get("strategy", {})
    env = ct.get("environment", "demo").lower()
    if env not in ("demo", "live"):
        raise ValueError("ctrader.environment must be 'demo' or 'live'")
    symbols = [SymbolConfig(s["name"].upper(), float(s["lots"])) for s in raw.get("symbols", [])]
    if not symbols:
        raise ValueError("config.toml has no [[symbols]]")
    return Config(
        environment=env,
        label_prefix=ct.get("label_prefix", "jev"),
        jev_model=jev.get("model", "jev-latest"),
        min_confidence=float(jev.get("min_confidence", 0.6)),
        min_bias=float(jev.get("min_bias", 0.55)),
        timeframe=st.get("timeframe", "H1").upper(),
        bars=int(st.get("bars", 200)),
        sl_atr=float(st.get("sl_atr", 1.5)),
        tp_atr=float(st.get("tp_atr", 3.0)),
        max_open_positions=int(st.get("max_open_positions", 3)),
        symbols=symbols,
    )


def _require(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(f"missing environment variable / GitHub secret: {name}")
    return v


def load_secrets() -> Secrets:
    return Secrets(
        typesafe_api_key=_require("TYPESAFE_API_KEY"),
        client_id=_require("CTRADER_CLIENT_ID"),
        client_secret=_require("CTRADER_CLIENT_SECRET"),
        access_token=_require("CTRADER_ACCESS_TOKEN"),
        account_id=int(_require("CTRADER_ACCOUNT_ID")),
    )


def trading_enabled() -> bool:
    """Orders are only sent when TRADING_ENABLED=true; otherwise the run is a dry run."""
    return os.environ.get("TRADING_ENABLED", "").strip().lower() == "true"
