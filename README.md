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

Telepítés Windowson:

1. Klónozd a repót (pl. `C:\Users\Administrator\source\repos\ai-trading-advisor`).
   A szervernek nem kell külön csomag (Python 3.11+).
2. API kulcs: `setx TYPESAFE_API_KEY "..."` (új ablakban érvényes), vagy írd a kulcsot
   `zorro\jev_key.txt`-be (gitignore-ban van, nem kerül fel).
3. Futtasd a `zorro\start_jev.bat`-ot, ellenőrzés: böngészőben `http://127.0.0.1:5003/health`.
4. Másold a `zorro\JevTrader.c`-t a Zorro `Strategy\` mappájába, Account = cTrader, Trade.

A küszöbök (`min_confidence`, `min_bias`, `sl_atr`, `tp_atr`) a `config.toml`-ból jönnek;
a `.c` fájl tetején a `SL_ATR`/`TP_ATR`/`MAX_OPEN` és az assetlista állítható.
Backtestben a Jev alapból nem hívódik (fizetős és nem reprodukálható) — `JEV_IN_TEST 1`-gyel bekapcsolható.

**Fontos:** ha a GitHub bot és a Zorro ugyanazon a számlán ugyanazt az assetet kereskedi,
két külön pozíció lesz. Egy assetet csak az egyik kezeljen.
