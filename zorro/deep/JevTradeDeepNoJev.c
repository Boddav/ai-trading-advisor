// =================================================================
// JevTradeDeepNoJev — a JevTradeDeep "regime" mérés legjobb szabálya, Jev NÉLKÜL
//
// A calibrate_regime.py mérésén (4775 óra, 2026 jún-szept) a "mindig ranging" szabály volt a
// legjobb (+0.126 R/kötés, költségek nélkül), jobb, mint a Jev rezsim és a véletlen.
// Szabály (ugyanaz, mint a JevServer.regime_policy "ranging" ága):
//   pos20 = (close - legalacsonyabb low) / (legmagasabb high - legalacsonyabb low), az utolsó 20 H1 baron
//   pos20 <= RANGE_EDGE      -> LONG  (a sáv aljáról vissza)
//   pos20 >= 1 - RANGE_EDGE  -> SHORT (a sáv tetejéről vissza)
//   Stop = SL_ATR x ATR(14), TakeProfit = TP_ATR x ATR(14), assetenként egy pozíció.
// Nincs szerver, nincs Jev hívás: évekre visszamenőleg, spreaddel együtt backtestelhető.
//
// STRAT_MODE:
//   0 = sávszél (a fenti szabály)
//   1 = KONTROLL: pénzfeldobás, ugyanazokkal az SL/TP-vel, minden pozíció nélküli baron nyit
//   2 = ellenpróba: trendkövetés (sma20 vs sma50 és a 20 bares hozam iránya) — a mérésen ez veszített
//
// Backtest: Test gomb. EUR/USD és GBP/USD History 2017-től teljes; az XAU/USD History hiányos,
// ezért backtestben alapból kimarad (USE_GOLD). Élesben (Trade) mindhárom fut.
// Az első próba legyen 2018-2025 (a mérés 2026-os, ez független adat), utána 2026.
// =================================================================

// ============ KONFIGURÁCIÓ ============
#define STRAT_MODE      0         // 0 = sávszél, 1 = pénzfeldobás kontroll, 2 = trendkövetés ellenpróba
#define CFG_STARTDATE   2018      // backtest kezdete (év vagy ÉÉÉÉHHNN)
#define CFG_ENDDATE     2025      // backtest vége (0 = mostanáig)
#define USE_GOLD        0         // backtestben az XAU/USD is (csak ha van teljes History)
#define CFG_CAPITAL     10000
#define CFG_LEVERAGE    500

#define RANGE_BARS      20
#define RANGE_EDGE      0.20
#define SL_ATR          1.5
#define TP_ATR          3.0
#define MAX_OPEN        3         // egyszerre nyitott pozíciók (összes asset)
#define MAX_ATR_PCT     3.0       // ATR(14) ennél nagyobb (% az árhoz) = hibás adat, kihagyja
#define BREAK_HOUR_UTC  21        // élesben: napi piaci szünet órája (UTC), ilyenkor nem nyit (-1 = ki)
// ======================================

int dataOK(var atr14)
{
	int i;
	for(i = 0; i < 60; i++)
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

int countOpenAsset()
{
	int n = 0;
	for(current_trades)
		if(TradeIsOpen) n++;
	return n;
}

function openTrade(int dir, var atr14, var pos20, int lots)
{
	if(countOpenAll() >= MAX_OPEN) {
		printf("\n[NOJEV] %s kihagyva: MAX_OPEN=%d", Asset, MAX_OPEN);
		return;
	}
	Lots = lots;
	Stop = atr14 * SL_ATR;
	TakeProfit = atr14 * TP_ATR;
	if(dir > 0) {
		if(!enterLong())
			printf("\n[NOJEV] %s LONG NEM NYILT MEG (broker/plugin elutasitotta)", Asset);
		else if(is(TRADEMODE))
			printf("\n[NOJEV] %s LONG @ %.5f pos20=%.2f SL=%.5f TP=%.5f", Asset, priceClose(), pos20,
				priceClose() - Stop, priceClose() + TakeProfit);
	} else {
		if(!enterShort())
			printf("\n[NOJEV] %s SHORT NEM NYILT MEG (broker/plugin elutasitotta)", Asset);
		else if(is(TRADEMODE))
			printf("\n[NOJEV] %s SHORT @ %.5f pos20=%.2f SL=%.5f TP=%.5f", Asset, priceClose(), pos20,
				priceClose() + Stop, priceClose() - TakeProfit);
	}
}

function run()
{
	set(LOGFILE);
	BarPeriod = 60;
	LookBack = 220;
	StartDate = CFG_STARTDATE;
	if(CFG_ENDDATE) EndDate = CFG_ENDDATE;
	if(is(INITRUN)) seed(12345);   // a kontroll mód ismételhető legyen
	Capital = CFG_CAPITAL;
	Leverage = CFG_LEVERAGE;
	Hedge = 0;
	EndWeek = 52200;

	int LotsSlider = slider(1, 1, 1, 100, "Lots", "Lots per trade");

	while(asset(loop("EUR/USD", "GBP/USD", "XAU/USD")))
	{
		if(!is(TRADEMODE) && !USE_GOLD && strstr(Asset, "XAU")) continue;

		vars C = series(priceClose());
		var atr14 = ATR(14);
		var sma20 = SMA(C, 20);
		var sma50 = SMA(C, 50);
		if(is(LOOKBACK)) continue;
		if(!dataOK(atr14)) continue;
		if(is(TRADEMODE) && BREAK_HOUR_UTC >= 0 && hour() == BREAK_HOUR_UTC) continue;
		if(countOpenAsset() > 0) continue;   // assetenként egy pozíció, zárni az SL/TP zár

		var hi = HH(RANGE_BARS, 0);
		var lo = LL(RANGE_BARS, 0);
		if(hi <= lo) continue;
		var pos20 = (priceClose() - lo) / (hi - lo);

		if(STRAT_MODE == 0)
		{
			if(pos20 <= RANGE_EDGE) openTrade(1, atr14, pos20, LotsSlider);
			else if(pos20 >= 1 - RANGE_EDGE) openTrade(-1, atr14, pos20, LotsSlider);
		}
		else if(STRAT_MODE == 1)
		{
			if(random(1) > 0.5) openTrade(1, atr14, pos20, LotsSlider);
			else openTrade(-1, atr14, pos20, LotsSlider);
		}
		else if(STRAT_MODE == 2)
		{
			var ret20 = priceClose() - priceClose(20);
			if(sma20 > sma50 && ret20 > 0) openTrade(1, atr14, pos20, LotsSlider);
			else if(sma20 < sma50 && ret20 < 0) openTrade(-1, atr14, pos20, LotsSlider);
		}
	}
}
