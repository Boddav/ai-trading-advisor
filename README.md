# ai-trading-advisor — Jev + cTrader

Automata kereskedő bot. A döntést a **Jev** (TypeSafe AI, System One) hozza, a
megbízást a **cTrader Open API** hajtja végre. A bot **GitHub Actionsben fut**,
nem kell hozzá saját gép.

Az API-hívások (JSON WebSocketen, 5036-os port, üzenettípusok, árskálázás,
trendbar-dekódolás, `relativeStopLoss`) a
[ctrader-zorro-plugin](https://github.com/Boddav/ctrader-zorro-plugin)
megoldásait követik, Pythonban.

## Működés

Óránként (H1 gyertyazárás után) egy futás:

1. Bejelentkezés a cTrader számlára, a bot saját pozícióinak lekérése
   (`jev_<SYMBOL>` címke; a Zorro- és a kézi pozíciókhoz **nem nyúl**).
2. Szimbólumonként az utolsó 200 lezárt gyertya → indikátorok (SMA, EMA, RSI, ATR,
   hozamok, 20 bares sáv).
3. Két kérdés a Jevnek (`choice` típusúak, valószínűséggel):
   - `bias`: long vagy short?
   - `intent`: nyitás/várakozás (ha nincs pozíció), illetve zárás/tartás (ha van).
4. Döntés a `config.toml` küszöbei alapján:
   - nyitás piaci megbízással, ATR-alapú SL/TP-vel;
   - zárás;
   - vagy tartás.
5. Összefoglaló táblázat a GitHub Actions futás oldalán.

## Beállítás

### 1. GitHub secrets (Settings → Secrets and variables → Actions → Secrets)

| Név | Mi ez |
|---|---|
| `TYPESAFE_API_KEY` | TypeSafe (Jev) API-kulcs |
| `CTRADER_CLIENT_ID` | cTrader Open API alkalmazás Client ID |
| `CTRADER_CLIENT_SECRET` | cTrader Open API alkalmazás Client Secret |
| `CTRADER_ACCOUNT_ID` | `ctidTraderAccountId` (ugyanaz a szám, mint a plugin `accounts.csv`-jében) |
| `CTRADER_ACCESS_TOKEN` | OAuth access token |
| `CTRADER_REFRESH_TOKEN` | OAuth refresh token (az automatikus megújításhoz) |
| `GH_SECRETS_PAT` | Fine-grained GitHub token erre a repóra, **Secrets: Read and write** joggal (a token-megújító workflow ezzel írja vissza az új tokeneket) |

**Tokenpár a botnak.** Saját gépen futtasd egyszer:

```sh
pip install -r requirements.txt
python -m advisor.tokens authorize
```

Ez kiírja az access és a refresh tokent. A botnak **külön tokenpár** kell, ne a
Zorro plugin `oauth_token.json`-jából vedd. A cTrader refresh tokene egyszer
használatos, így ha a plugin és a bot ugyanazt frissíti, az egyik elveszíti a
hozzáférést.

### 2. Repository variable (Settings → Secrets and variables → Actions → Variables)

| Név | Érték |
|---|---|
| `TRADING_ENABLED` | `true` = valódi megbízások. Ha nincs beállítva vagy más az értéke, a bot **csak szimulál** (dry run): kiírja, mit tenne, de nem küld megbízást. |

### 3. `config.toml`

- demó vagy éles számla (`environment`);
- szimbólumok és lotméret;
- idősík;
- SL/TP ATR-szorzók;
- Jev-küszöbök (`min_confidence`, `min_bias`);
- egyszerre nyitható pozíciók száma.

Ha az idősíkot módosítod, a `.github/workflows/trade.yml` ütemezését is igazítsd hozzá.

### Javasolt sorrend

1. `environment = "demo"`, a `TRADING_ENABLED` még nincs beállítva. Actions →
   **Jev trade** → *Run workflow*. Ellenőrizd az összefoglalót.
2. `TRADING_ENABLED = true` → a bot a **demó** számlán kereskedik. Néhány napig figyeld.
3. Csak ezután válts `environment = "live"`-ra.

## Workflow-k

- **Jev trade** (`trade.yml`): óránként, a forex-héten (vas 22:00 – p 22:00 UTC),
  illetve kézzel is indítható.
- **Refresh cTrader token** (`refresh-token.yml`): hetente megújítja a cTrader
  tokeneket, és visszaírja őket a secretekbe.
- **Tests** (`tests.yml`): minden pushnál egységtesztek, valamint egy teljes futás egy hamis
  cTrader-szerver ellen.

## Tudnivalók

- A GitHub ütemezett futásai néhány percet késhetnek, terhelésnél ki is maradhatnak.
  Ez óránkénti stratégiához elfogadható, skalpoláshoz nem.
- Nyilvános repóban 60 nap aktivitás nélkül a GitHub leállítja az ütemezett workflow-kat.
- Nyilvános repóban az Actions-naplók (a döntések is) bárki számára láthatók.
  A secretek maszkolva vannak.

## Helyi futtatás

```sh
pip install -r requirements.txt pytest
python -m pytest -q
# élő futás (dry run, amíg TRADING_ENABLED != true):
export TYPESAFE_API_KEY=... CTRADER_CLIENT_ID=... CTRADER_CLIENT_SECRET=... \
       CTRADER_ACCESS_TOKEN=... CTRADER_ACCOUNT_ID=...
python -m advisor.main
```

## Zorro változat (`zorro/`)

Ugyanez a Jev-döntés Zorróból, a cTrader pluginon keresztül, a MLDRIVEN/UltOsc
mintájára (lite-C `http_transfer` → helyi Python szerver):

| Fájl | Szerep |
|---|---|
| `zorro/JevTrader.c` | Zorro stratégia: H1 baronként elküldi az utolsó 200 bart, a válasz alapján `enterLong` / `enterShort` / `exitLong+exitShort`, ATR Stop/TakeProfit |
| `zorro/JevServer.py` | Helyi szerver (port 5003): megkérdezi a Jevet, visszaadja: `open_long` / `open_short` / `close` / `hold` |
| `zorro/start_jev.bat` | Szerver indítása (a régi 5003-as folyamatot leállítja) |
| `zorro/test_jev.bat` | Egy próba Jev hívás + szerver ellenőrzés |
| `zorro/prefetch_jev.bat` | Backtest kérdések párhuzamos előtöltése |

Telepítés Windowson (Python 3.7+ elég, külön csomag nem kell):

1. Másold a Zorro `Strategy\` mappájába: `JevTrader.c`, `JevServer.py`, `start_jev.bat`,
   `test_jev.bat`, `jev_key.example.txt`.
2. Nevezd át a `jev_key.example.txt`-t `jev_key.txt`-re, és a mintaszöveg helyére írd a
   kulcsot (a `jev_key.txt` gitignore-ban van, nem kerül fel).
3. `test_jev.bat` → egy próba Jev hívás; `start_jev.bat` → szerver indítása.
4. Zorro: Account = cTrader, Script = JevTrader, Trade.

A Jev küszöbök (`MIN_CONFIDENCE`, `MIN_BIAS`) a `JevServer.py` tetején állíthatók;
a `.c` fájl tetején a `SL_ATR`/`TP_ATR`/`MAX_OPEN` és az assetlista állítható.
**Backtest (Zorro Test gomb)** — `JEV_TEST_MODE` a `.c` tetején, időszak: `CFG_STARTDATE`:

1. `JEV_TEST_MODE 2` → Test: a Zorro csak kiírja a kérdéseket (`Data\JevExport.jsonl`), nem kereskedik.
2. `prefetch_jev.bat` → a szerver 8 szálon lekérdezi őket a Jevtől, és elmenti (`jev_cache.jsonl`).
3. `JEV_TEST_MODE 1`, `start_jev.bat`, Test → a válaszok a cache-ből jönnek, gyors és megismételhető.
   Nyitott pozíció közben a "zárjam?" kérdés élőben megy (utána az is cache-ben marad).

Az 1-es mód előtöltés nélkül is működik, csak lassabb (~0,7 mp/bar/asset az első futásnál).
Figyelem: a Jev a múltbeli időszakot a tanítóadatából ismerheti, ezért a backtest eredménye
optimistább lehet a valóságnál. A megbízható próba a demó számla.

**Fontos:** ha a GitHub bot és a Zorro ugyanazon a számlán ugyanazt az assetet kereskedi,
két külön pozíció lesz. Egy assetet csak az egyik kezeljen.
