# Zorro + Jev — mappák

| Mappa | Tartalom |
|---|---|
| `server/` | `JevServer.py` (helyi Jev szerver, port 5003), `start_jev.bat`, `test_jev.bat`, `prefetch_jev.bat`, `jev_key.example.txt` |
| `jevtrader/` | `JevTrader.c` — Zorro stratégia, a Jev dönt (H1, EUR/USD, GBP/USD, XAU/USD) |
| `deep/` | `JevTradeDeep.c` — ugyanez + Depth of Market (kell hozzá a plugin `jevdepth` modulja) |
| `tools/` | `calibrate_jev.py` — a Jev válaszainak kiértékelése a backtest adatokon |
| `lotto/` | `lotto_jev.py`, `lotto_popularity.py` — hatoslottó kísérletek |
| `archive/` | feltöltött anyagok (pl. `grok-workspace.zip`), a működéshez nem kell |

A cTrader plugin kapcsolódó részei a **Boddav/ctrader-zorro-plugin** repóban vannak,
`source/repos/zorro-plugin-windows-32-4/` alatt, külön mappákban:
`jevgate/` (Jev kapuőr minden új kötés előtt, `Plugin\JevGate\JevGate.ini`) és
`jevdepth/` (Depth of Market a `GET_BOOK` paranccsal).

## Telepítés / frissítés a Zorro gépen

A Zorro `Strategy` mappájába **minden fájl egy helyre** kerül (a szkriptek ott egymást megtalálják).
Egy parancs, PowerShellben (a `jev_key.txt`-hez és a mentett adatokhoz nem nyúl):

```powershell
powershell -ExecutionPolicy Bypass -Command "iwr https://raw.githubusercontent.com/Boddav/ai-trading-advisor/main/zorro/update_zorro.ps1 -OutFile $env:TEMP\u.ps1; & $env:TEMP\u.ps1 -Strategy 'C:\Users\Administrator\Desktop\z12\Strategy'"
```
