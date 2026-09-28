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
// Backtestben a Jev NEM hívódik (pénzbe kerül és nem reprodukálható),
// kivéve JEV_IN_TEST 1 esetén — ekkor minden JEV_TEST_EVERY. baron.
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

#define JEV_IN_TEST     0         // 1 = backtestben is hívja (lassú, fizetős!)
#define JEV_TEST_EVERY  24        // backtestben csak minden N. baron
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
	Capital = CFG_CAPITAL;
	Leverage = CFG_LEVERAGE;
	Hedge = 0;
	EndWeek = 52200;

	int LotsSlider = slider(1, 1, 1, 100, "Lots", "Lots per trade");

	while(asset(loop("EUR/USD", "GBP/USD", "XAU/USD")))
	{
		var atr14 = ATR(14);

		if(is(LOOKBACK) || atr14 <= 0) continue;

		int callJev = 0;
		if(is(TRADEMODE)) callJev = 1;
		if(!is(TRADEMODE) && JEV_IN_TEST && (Bar % JEV_TEST_EVERY) == 0) callJev = 1;
		if(!callJev) continue;

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

		string resp = http_transfer(JEV_URL, buildRequest(pos, entry));
		if(!resp)
		{
			printf("\n[JEV] %s: nincs válasz a szervertől (fut a start_jev.bat?)", Asset);
			continue;
		}

		string act = getAction(resp);
		if(strstr(act, "open_long"))
		{
			if(countOpenAll() >= MAX_OPEN)
				printf("\n[JEV] %s LONG kihagyva: MAX_OPEN=%d", Asset, MAX_OPEN);
			else {
				Lots = LotsSlider;
				Stop = atr14 * SL_ATR;
				TakeProfit = atr14 * TP_ATR;
				enterLong();
				printf("\n[JEV] %s LONG @ %.5f SL=%.5f TP=%.5f", Asset, priceClose(), Stop, TakeProfit);
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
				printf("\n[JEV] %s SHORT @ %.5f SL=%.5f TP=%.5f", Asset, priceClose(), Stop, TakeProfit);
			}
		}
		else if(strstr(act, "close"))
		{
			exitLong();
			exitShort();
			printf("\n[JEV] %s CLOSE (%s)", Asset, pos);
		}
		else if(strstr(act, "error") || !act[0])
			printf("\n[JEV] %s hiba: %s", Asset, resp);
		else
			printf("\n[JEV] %s HOLD (%s)", Asset, pos);
	}
}
