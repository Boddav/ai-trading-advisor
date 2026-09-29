# JevTradeDeep

A `JevTrader.c` változata, ami élő kereskedésben a cTrader ajánlati könyvét (DoM) is elküldi a Jevnek:
legjobb bid/ask, spread, a legjobb 5 szint mennyisége oldalanként, egyensúly (imbalance).

- Kell hozzá a **`cTraderJev.dll`** plugin (`jevdepth` modul, `GET_BOOK`) — Boddav/ctrader-zorro-plugin,
  `source/repos/zorro-plugin-jev/`. A v4.12 `cTrader.dll`-lel nincs DoM (a könyv üres marad).
- `JevTradeDeep.c` → Zorro `Strategy\` mappa; a szerver ugyanaz (`JevServer.py`, `start_jev.bat`).
- A szerver minden DoM-os kérdést naplóz: `jev_dom_log.jsonl` (idő, eszköz, DoM, Jev válasz) —
  ebből később mérhető, segít-e a DoM.
- Backtestben nincs DoM (a History nem tárolja), ott úgy fut, mint a `JevTrader.c`.
