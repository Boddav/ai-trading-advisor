# Zorro + Jev — mappák

| Mappa | Tartalom |
|---|---|
| `server/` | `JevServer.py` (helyi Jev szerver, port 5003), `start_jev.bat`, `test_jev.bat`, `prefetch_jev.bat`, `jev_key.example.txt` |
| `jevtrader/` | `JevTrader.c` — Zorro stratégia, a Jev dönt (H1, EUR/USD, GBP/USD, XAU/USD) |
| `deep/` | `JevTradeDeep.c` — ugyanez + Depth of Market (kell hozzá a `cTraderJev.dll`); `JevTradeDeepNoJev.c` — a regime mérés legjobb szabálya (sávszél, visszafordulás) Jev nélkül, évekre backtestelhető, pénzfeldobás és trendkövetés ellenpróbával |
| `tools/` | `calibrate_jev.py` — a Jev válaszainak kiértékelése a backtest adatokon; `calibrate_regime.py` — a JevTradeDeep "regime" módjának mérése (Jev piac-jelleg vs. véletlen, képlet, mindig trend / mindig sáv) |
| `kronos/` | Kronos előrejelző modell a Jev mellé: `kronos_bridge.py`, `calibrate_kronos.py`, `start_jev_kronos.bat` ([leírás](kronos/README.md)) |
| `lotto/` | `lotto_jev.py`, `lotto_popularity.py` — hatoslottó kísérletek |
| `archive/` | feltöltött anyagok (pl. `grok-workspace.zip`), a működéshez nem kell |

A Jev-es cTrader plugin **külön plugin**: `cTraderJev.dll`, a **Boddav/ctrader-zorro-plugin** repó
`source/repos/zorro-plugin-jev/` mappájában (a v4.12 `cTrader.dll` érintetlen). Modulok:
`jevgate/` (Jev kapuőr minden új kötés előtt, `Plugin\JevGate\JevGate.ini`) és
`jevdepth/` (Depth of Market a `GET_BOOK` paranccsal). Az `accounts.csv` `Plugin` oszlopában
választható, melyik Zorro-ablak melyik DLL-t használja.

## Telepítés / frissítés a Zorro gépen

A Zorro `Strategy` mappájába **minden fájl egy helyre** kerül (a szkriptek ott egymást megtalálják).
Egy parancs, PowerShellben (a `jev_key.txt`-hez és a mentett adatokhoz nem nyúl):

```powershell
powershell -ExecutionPolicy Bypass -Command "iwr https://raw.githubusercontent.com/Boddav/ai-trading-advisor/main/zorro/update_zorro.ps1 -OutFile $env:TEMP\u.ps1; & $env:TEMP\u.ps1 -Strategy 'C:\Users\Administrator\Desktop\z12\Strategy'"
```
