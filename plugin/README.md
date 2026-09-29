# cTrader plugin a Jevhez (cTraderJev)

| Mappa / fájl | Tartalom |
|---|---|
| `cTraderJev/` | A plugin forrása (a Boddav/ctrader-zorro-plugin v4.12 másolata + `jevgate/` és `jevdepth/` modul) |
| `bin/cTraderJev.dll` | A lefordított plugin — a GitHub Actions (`Build cTraderJev plugin`) teszi ide minden változás után |
| `cTrader.ini.example` | Minta a `MaxMarginPct` beállításhoz (közös a v4.12-vel) |

A forrás eredeti helye: Boddav/ctrader-zorro-plugin, `source/repos/zorro-plugin-jev/`.
A v4.12 `cTrader.dll` érintetlen, a kettő egymás mellett használható.

## Telepítés
1. `bin/cTraderJev.dll` → a Zorro `Plugin` mappájába (a `cTrader.dll` mellé).
2. `accounts.csv`: új sor, a `Plugin` oszlopban `cTraderJev.dll` (a többi oszlop, mint a meglévő cTrader sorodnál).
3. Jev kapuőr (opcionális): `cTraderJev/jevgate/JevGate.ini` → `<Zorro>\Plugin\JevGate\JevGate.ini`, először `Mode = log`.
4. Depth of Market: a `zorro/deep/JevTradeDeep.c` ezen a pluginon keresztül kapja (`GET_BOOK`).

Részletek: `cTraderJev/README.md`, `cTraderJev/jevgate/README.md`, `cTraderJev/jevdepth/README.md`.

## Client ID / Secret
Ebben a másolatban **nincs beégetett cTrader Client ID / Secret** (az eredeti v4.12 forrásában benne
van, és az a repó nyilvános). A plugin a `accounts.csv` `User` (Client ID) és `Pass` (Client Secret)
oszlopából, vagy a meglévő `oauth_token.json`-ból veszi őket. Ha mégis beépítve kell: másold az
`cTraderJev/include/builtin_creds.example.h`-t `builtin_creds.h` néven és töltsd ki — ez a fájl
nem kerül fel a gitbe, és a GitHub Actions build nem is látja.
