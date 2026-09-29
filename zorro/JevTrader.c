// =================================================================
// JevTrader — Jev (TypeSafe AI) által vezérelt stratégia
//
// Minden lezárt H1 baron assetenként elküldi az utolsó JEV_BARS gyertyát
// a helyi Jev szervernek (JevServer.py, port 5003), ami megkérdezi a Jevet.
// Válasz: open_long / open_short / close / hold (error = hold).
// Megbízás a cTrader pluginon keresztül megy, ATR alapú Stop/TakeProfit-tal.
//
// Indítás: 1) zorro\start_jev.bat   2) Zorro: [Account]=cTrader, Script=JevTrader, Trade
//
// BACKTEST (Test gomb), JEV_TEST_MODE szerint:
//   1 = minden baron megkérdezi a Jevet a szerveren át. A szerver minden választ
//       elment (jev_cache.jsonl), így az újrafuttatás azonnali és ugyanazt adja.
//   2 = csak kiírja a kérdéseket (Data\JevExport.jsonl), nem kereskedik. Utána
//       prefetch_jev.bat párhuzamosan lekérdezi őket -> a Test (mód 1) percek helyett
//       másodpercek alatt lefut. Nagy időszakhoz ajánlott.
//   3 = KONTROLL: ugyanazok a szabályok (ATR SL/TP, MAX_OPEN), de az irányt pénzfeldobás
//       dönti el, minden pozíció nélküli baron nyit. Ha ez is hasonló eredményt hoz,
//       a Jev nem tett hozzá semmit. Szerver nem kell hozzá.
//   0 = backtestben nincs Jev.
// =================================================================

// ============ KONFIGURÁCIÓ ============
#define CFG_BARPERIOD   60        // H1 — a szerver "H1"-ként küldi a Jevnek
#define CFG_TF          "H1"
#define CFG_CAPITAL     10000
#define CFG_LEVERAGE    500

#define JEV_URL         "http://127.0.0.1:5003/decide"
#define JEV_BARS        200       // ennyi lezárt bar megy a Jevnek
#define SL_ATR          1.5       // Stop = ATR(14) * SL_ATR
#define TP_ATR          3.0       // TakeProfit = ATR(14) * TP_ATR
#define MAX_OPEN        3         // egyszerre nyitott pozíciók (összes asset)
#define MAX_ATR_PCT     3.0       // ATR(14) ennél nagyobb (% az árhoz) = hibás adat, kihagyja

#define JEV_TEST_MODE   1         // backtest: 0=nincs Jev, 1=Jev (cache-elve), 2=kérdések exportja,
                                  //           3=KONTROLL: Jev helyett pénzfeldobás (összehasonlításhoz)
#define CFG_STARTDATE   20260601  // backtest kezdete (ÉÉÉÉHHNN)
#define CFG_ENDDATE     0         // backtest vége, 0 = mostanáig
#define EXPORT_FILE     "Data\\JevExport.jsonl"
// ======================================

static char postBuf[16000];
static char numBuf[128];
static char actBuf[32];

int priceDigits()
{
	if(PIP < 0.00011) return 5;   // EUR/USD 0.0001
	if(PIP < 0.011) return 3;     // USD/JPY 0.01
	return 2;                     // XAU/USD, indexek
}

// {"asset":"EUR/USD","tf":"H1","digits":5,"pos":"long","entry":1.08,"bars":[[o,h,l,c],...]}
// bars: legrégebbi elöl, a legutolsó lezárt bar a végén
string buildRequest(string pos, var entry)
{
	int d = priceDigits();
	sprintf(postBuf, "{\"asset\":\"%s\",\"tf\":\"%s\",\"digits\":%d,\"pos\":\"%s\",\"entry\":%.5f,\"bars\":[",
		Asset, CFG_TF, d, pos, entry);
	int i;
	for(i = JEV_BARS-1; i >= 0; i--)
	{
		sprintf(numBuf, "[%.5f,%.5f,%.5f,%.5f]",
			priceOpen(i), priceHigh(i), priceLow(i), priceClose(i));
		strcat(postBuf, numBuf);
		if(i > 0) strcat(postBuf, ",");
	}
	strcat(postBuf, "]}");
	return postBuf;
}

// a válasz "action" mezője: {"action": "open_long", ...} -> "open_long"
string getAction(string resp)
{
	actBuf[0] = 0;
	char* p = strstr(resp, "\"action\"");
	if(!p) return actBuf;
	p = strchr(p + 8, '"');           // az érték nyitó idézőjele
	if(!p) return actBuf;
	p++;
	int n = 0;
	while(p[n] && p[n] != '"' && n < 31) { actBuf[n] = p[n]; n++; }
	actBuf[n] = 0;
	return actBuf;
}

// Az előtörténet ép-e: nincs nulla/negatív ár, high >= low, és az ATR ésszerű.
// (Hiányzó history esetén a Zorro 0 árú barokat ad -> óriási ATR -> értelmetlen SL/TP.)
int dataOK(var atr14)
{
	int i;
	for(i = 0; i < JEV_BARS; i++)
	{
		if(priceOpen(i) <= 0 || priceHigh(i) <= 0 || priceLow(i) <= 0 || priceClose(i) <= 0)
			return 0;
		if(priceHigh(i) < priceLow(i))
			return 0;
	}
	if(atr14 <= 0 || atr14 > priceClose() * MAX_ATR_PCT / 100)
		return 0;
	return 1;
}

int countOpenAll()
{
	int n = 0;
	for(open_trades) n++;
	return n;
}

function run()
{
	set(LOGFILE);

	BarPeriod = CFG_BARPERIOD;
	LookBack = JEV_BARS + 20;
	StartDate = CFG_STARTDATE;
	if(CFG_ENDDATE) EndDate = CFG_ENDDATE;

	if(is(INITRUN) && !is(TRADEMODE) && JEV_TEST_MODE == 2)
		file_delete(EXPORT_FILE);
	if(is(INITRUN)) seed(12345);   // a kontroll mód ismételhető legyen
	Capital = CFG_CAPITAL;
	Leverage = CFG_LEVERAGE;
	Hedge = 0;
	EndWeek = 52200;

	int LotsSlider = slider(1, 1, 1, 100, "Lots", "Lots per trade");

	while(asset(loop("EUR/USD", "GBP/USD", "XAU/USD")))
	{
		var atr14 = ATR(14);

		if(is(LOOKBACK)) continue;
		if(!dataOK(atr14))
		{
			printf("\n[JEV] %s kihagyva: hibas elotortenet (0 ar / hianyzo bar), ATR=%.5f", Asset, atr14);
			continue;
		}

		if(!is(TRADEMODE) && JEV_TEST_MODE == 0) continue;

		// saját pozíció erre az assetre
		string pos = "flat";
		var entry = 0;
		for(current_trades)
		{
			if(TradeIsOpen)
			{
				if(TradeIsLong) pos = "long"; else pos = "short";
				entry = TradePriceOpen;
			}
		}

		// export mód: csak a "nincs pozíció" kérdést írjuk ki minden barra, kereskedés nélkül
		if(!is(TRADEMODE) && JEV_TEST_MODE == 2)
		{
			file_append(EXPORT_FILE, buildRequest("flat", 0));
			file_append(EXPORT_FILE, "\n");
			continue;
		}

		string resp = "";
		string act = "hold";
		if(!is(TRADEMODE) && JEV_TEST_MODE == 3)
		{
			// kontroll: pénzfeldobás, csak pozíció nélkül nyit, zárni az SL/TP zár
			if(strstr(pos, "flat"))
			{
				if(random(1) > 0.5) act = "open_long"; else act = "open_short";
			}
		}
		else
		{
			resp = http_transfer(JEV_URL, buildRequest(pos, entry));
			if(!resp)
			{
				printf("\n[JEV] %s: nincs valasz a szervertol (fut a start_jev.bat?)", Asset);
				continue;
			}
			act = getAction(resp);
		}
		if(strstr(act, "open_long"))
		{
			if(countOpenAll() >= MAX_OPEN)
				printf("\n[JEV] %s LONG kihagyva: MAX_OPEN=%d", Asset, MAX_OPEN);
			else {
				Lots = LotsSlider;
				Stop = atr14 * SL_ATR;
				TakeProfit = atr14 * TP_ATR;
				enterLong();
				printf("\n[JEV] %s LONG @ %.5f SL=%.5f TP=%.5f (ATR=%.5f)", Asset, priceClose(),
					priceClose() - Stop, priceClose() + TakeProfit, atr14);
			}
		}
		else if(strstr(act, "open_short"))
		{
			if(countOpenAll() >= MAX_OPEN)
				printf("\n[JEV] %s SHORT kihagyva: MAX_OPEN=%d", Asset, MAX_OPEN);
			else {
				Lots = LotsSlider;
				Stop = atr14 * SL_ATR;
				TakeProfit = atr14 * TP_ATR;
				enterShort();
				printf("\n[JEV] %s SHORT @ %.5f SL=%.5f TP=%.5f (ATR=%.5f)", Asset, priceClose(),
					priceClose() + Stop, priceClose() - TakeProfit, atr14);
			}
		}
		else if(strstr(act, "close"))
		{
			exitLong();
			exitShort();
			printf("\n[JEV] %s CLOSE (%s)", Asset, pos);
		}
		else if(strstr(act, "error") || !act[0])
			printf("\n[JEV] %s HIBA: %s", Asset, resp);
		else
			printf("\n[JEV] %s HOLD (%s)", Asset, pos);
	}
}
