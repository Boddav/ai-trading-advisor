// =================================================================
// NFPStraddle — hírkereskedés az amerikai munkaerőpiaci jelentésre (NFP), Jev nélkül
//
// Amit a hírkereskedők csinálnak: a hír előtt két függő megbízás, egy buy stop az ár fölé és
// egy sell stop alá. Amelyik teljesül, azt megtartják (SL/TP, időkorlát), a másikat törlik.
//
// NFP: a hónap első péntekje 8:30 New York-i idő szerint. A szkript ezt szabállyal találja meg
// (péntek, a hónap 1-7. napja). Néhány hónapban a BLS a második pénteken közöl, vagy késik
// (pl. kormányzati leállás) — ezek a napok csendes pénteknek számítanak, ez rontja az eredményt.
//
// Backtest: Test gomb, M1 History kell (EUR/USD, GBP/USD 2017-től megvan).
// KONTROLL_PENTEK 1: ugyanez a hónap MÁSODIK péntekjén (nincs NFP) — ha ott is hasonló az
// eredmény, nem a hír hozza.
//
// FIGYELEM, a backtest optimista: NFP-kor a spread a szokásos többszörösére nő (EUR/USD 2-10 pip),
// a stop megbízások csúsznak, és a két irány gyors egymásutánban mindkettő teljesülhet.
// Ezt a NEWS_SPREAD és NEWS_SLIP_PIPS részben modellezi.
// =================================================================

// ============ KONFIGURÁCIÓ ============
#define CFG_STARTDATE   2018
#define CFG_ENDDATE     2026
#define KONTROLL_PENTEK 0         // 0 = NFP (1. péntek), 1 = kontroll (2. péntek, nincs NFP)

#define ENTRY_PIPS      10        // a függő megbízások távolsága az ártól (pip), mindkét irányba
#define SL_PIPS         15        // stop loss a teljesüléstől (pip)
#define TP_PIPS         30        // take profit a teljesüléstől (pip)
#define PENDING_MIN     10        // ennyi percig élnek a függő megbízások a hír után
#define LIFE_MIN        120       // a nyitott pozíciót legkésőbb ennyi perc után zárja
#define NEWS_SPREAD     3.0       // spread a hír alatt (pip) — a szokásos helyett
#define NEWS_SLIP_PIPS  2.0       // extra csúszás a stop belépésen (pip): ennyivel rosszabb a belépő
#define LOTS            1
// ======================================

int isNewsMinute()
{
	if(ldow(ET) != FRIDAY) return 0;
	int d = day();
	if(KONTROLL_PENTEK) { if(d < 8 || d > 14) return 0; }
	else if(d > 7) return 0;
	return lhour(ET) == 8 && minute() == 28;   // 2 perccel a 8:30-as hír előtt
}

function run()
{
	set(LOGFILE);
	BarPeriod = 1;
	LookBack = 60;
	StartDate = CFG_STARTDATE;
	EndDate = CFG_ENDDATE;
	Capital = 10000;
	Hedge = 2;                 // a két függő megbízás egyszerre élhet
	EndWeek = 52200;

	while(asset(loop("EUR/USD", "GBP/USD")))
	{
		if(is(LOOKBACK)) continue;

		// OCO: ha az egyik teljesült, a másik függő megbízást töröljük
		int filled = 0;
		for(current_trades) if(TradeIsOpen) filled = 1;
		if(filled) {
			for(current_trades)
				if(TradeIsPending) exitTrade(ThisTrade);
		}

		if(!isNewsMinute()) continue;
		if(NumOpenTotal > 0) continue;

		Spread = NEWS_SPREAD * PIP;
		Lots = LOTS;
		EntryTime = PENDING_MIN + 2;
		LifeTime = LIFE_MIN;
		Stop = (SL_PIPS - NEWS_SLIP_PIPS) * PIP;      // a csúszás a stopból "eszik"
		TakeProfit = (TP_PIPS - NEWS_SLIP_PIPS) * PIP;
		Entry = (ENTRY_PIPS + NEWS_SLIP_PIPS) * PIP;  // pozitív = stop megbízás az ártól ennyire
		enterLong();
		enterShort();
		printf("\n[NFP] %s %04d-%02d-%02d: buy stop %.5f / sell stop %.5f",
			Asset, year(), month(), day(), priceClose() + Entry, priceClose() - Entry);
	}
}
