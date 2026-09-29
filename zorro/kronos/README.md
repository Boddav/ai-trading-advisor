# Kronos + Jev

[Kronos](https://github.com/shiyu-coder/Kronos) nyílt forrású, gyertyákon tanított előrejelző modell.
Az utolsó H1 gyertyákból több lehetséges jövőbeli árpályát mintavételez (alapból 20 pálya × 24 óra).
Ezekből a `kronos_bridge.py` kiszámolja ugyanazt, amit a Jevtől kérdezünk:

- `p_tp_first_long` / `p_tp_first_short` — a pályák hányadában ért el a TP (3×ATR) előbb, mint az SL (1,5×ATR);
- `p_up` — a pályák hányada végződik feljebb; `exp_move_atr` — átlagos elmozdulás ATR-ben.

| Fájl | Szerep |
|---|---|
| `kronos_bridge.py` | Kronos betöltése és a fenti valószínűségek számítása |
| `calibrate_kronos.py` | Mérés a backtest exporton: Kronos egyedül / Jev egyedül / Jev+Kronos egyetért / vak belépés |
| `start_jev_kronos.bat` | `JevServer.py` indítása Kronos-szal |

## Telepítés (egyszer, a Zorro gépen)

```bat
git clone https://github.com/shiyu-coder/Kronos C:\Kronos
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install numpy pandas einops huggingface_hub safetensors tqdm
```

Ehhez Python 3.10+ kell (a sima JevServer 3.7-tel is megy, csak a Kronos igényli). Az első futás a
Hugging Face-ről letölti a `NeoQuasar/Kronos-small` modellt és a tokenizert.

## Szerver módok (`JevServer.py --kronos C:\Kronos --kronos-mode ...`)

| Mód | Mit csinál | Jev költség |
|---|---|---|
| `context` | a Jev megkapja a Kronos előrejelzést (`kronos_forecast`) a state-ben, a Jev dönt | van |
| `agree` | a Jev dönt, de csak akkor nyit, ha a Kronos ugyanazt az irányt látja jobbnak, és annak TP-first esélye ≥ `MIN_KRONOS` (0,40) | van |
| `kronos_only` | csak a Kronos dönt (ugyanazokkal a küszöbökkel), a Jev nincs megkérdezve | nincs |

Ha a Kronos hibát dob, a szerver a Kronos nélküli Jev döntéssel megy tovább. A Zorro `.c` fájlokon
nem kell változtatni.

## Először mérni

```bat
python calibrate_kronos.py --kronos C:\Kronos --every 3
```

A bemenet a `JEV_TEST_MODE 2` exportja (`Data\JevExport.jsonl`). CPU-n egy óra kiértékelése néhány
másodperc, ezért `--every 3` csak minden harmadik órát nézi, `--limit` pedig felső korlátot ad.
Az eredmények a `kronos_cache.jsonl`-be kerülnek, a futás megszakítható és folytatható.
Ha a `jev_cache.jsonl`-ben vannak tp_first Jev válaszok ugyanezekre az órákra, a Jev egyedül és a
Jev+Kronos egyetértés táblázata is megjelenik.

Élesben csak akkor érdemes használni, ha valamelyik táblázatban a várható R/kötés **pozitív** és jobb a
vak belépésnél, elég sok kötésen. A költségek (spread, swap) nincsenek benne.
