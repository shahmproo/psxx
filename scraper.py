"""
PSX Market Scraper & Data Normalizer.
Asynchronously scrapes live market data from PSX Data Portal (https://dps.psx.com.pk)
Handles session token management, table parsing, index data extraction, and fallback simulation.
"""
import re
import asyncio
import logging
from datetime import datetime, time as dtime, timezone, timedelta
from typing import Dict, List, Optional, Tuple, Any

import httpx
from bs4 import BeautifulSoup

from models import StockQuote, IndexQuote, MarketSummary, MarketSessionStatus

logger = logging.getLogger("psx_scraper")
logging.basicConfig(level=logging.INFO)

# PKT Timezone is UTC+5
PKT = timezone(timedelta(hours=5))


class PSXScraper:
    BASE_URL = "https://dps.psx.com.pk"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

    def __init__(self):
        self.req_id: Optional[str] = None
        self.cookies: Dict[str, str] = {}
        self.latest_stocks: Dict[str, StockQuote] = {}
        self.latest_indices: Dict[str, IndexQuote] = {}
        self.last_sync_time: Optional[datetime] = None
        self.lock = asyncio.Lock()
        self._client: Optional[httpx.AsyncClient] = None
        self.is_connected = False
        self.consecutive_errors = 0

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "User-Agent": self.USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                follow_redirects=True,
                timeout=httpx.Timeout(15.0, connect=8.0),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @staticmethod
    def get_pkt_now() -> datetime:
        return datetime.now(PKT)

    @classmethod
    def get_market_status(cls) -> Tuple[MarketSessionStatus, str, str]:
        """
        Determines PSX market session status according to official schedule:
        Monday - Thursday: 09:15 AM - 03:30 PM PKT
        Friday:
          Session 1: 09:15 AM - 12:00 PM PKT
          Recess (Friday Prayer): 12:00 PM - 02:30 PM PKT
          Session 2: 02:30 PM - 04:30 PM PKT
        Saturday & Sunday: Closed
        """
        now = cls.get_pkt_now()
        weekday = now.weekday()  # Monday is 0, Sunday is 6
        current_time = now.time()

        t_0915 = dtime(9, 15)
        t_1200 = dtime(12, 0)
        t_1430 = dtime(14, 30)
        t_1530 = dtime(15, 30)
        t_1630 = dtime(16, 30)

        # Weekend Check
        if weekday in (5, 6):
            return (
                MarketSessionStatus.CLOSED,
                "Market Closed for Weekend",
                "Opens Monday at 09:15 AM PKT"
            )

        # Friday Schedule
        if weekday == 4:
            if t_0915 <= current_time < t_1200:
                return (
                    MarketSessionStatus.OPEN,
                    "Friday Morning Session Active",
                    "Recess starts at 12:00 PM PKT"
                )
            elif t_1200 <= current_time < t_1430:
                return (
                    MarketSessionStatus.RECESS,
                    "Friday Prayer Recess",
                    "Session 2 resumes at 02:30 PM PKT"
                )
            elif t_1430 <= current_time < t_1630:
                return (
                    MarketSessionStatus.OPEN,
                    "Friday Afternoon Session Active",
                    "Closes at 04:30 PM PKT"
                )
            elif current_time < t_0915:
                return (
                    MarketSessionStatus.CLOSED,
                    "Market Pre-Open",
                    "Opens today at 09:15 AM PKT"
                )
            else:
                return (
                    MarketSessionStatus.CLOSED,
                    "Market Closed for Weekend",
                    "Opens Monday at 09:15 AM PKT"
                )

        # Monday - Thursday Schedule
        if t_0915 <= current_time < t_1530:
            return (
                MarketSessionStatus.OPEN,
                "Regular Trading Session Active",
                "Closes today at 03:30 PM PKT"
            )
        elif current_time < t_0915:
            return (
                MarketSessionStatus.CLOSED,
                "Market Pre-Open",
                "Opens today at 09:15 AM PKT"
            )
        else:
            return (
                MarketSessionStatus.CLOSED,
                "Market Closed",
                "Opens tomorrow at 09:15 AM PKT"
            )

    async def initialize_session(self) -> bool:
        """
        Fetches the DPS homepage to extract the dynamic _k token (X-Req-Id)
        and establish session cookies required by the portal.
        """
        try:
            client = await self.get_client()
            resp = await client.get(self.BASE_URL)
            if resp.status_code == 200:
                html = resp.text
                ps_match = re.search(r'["\']?_k["\']?\s*:\s*["\']([^"\']+)["\']', html)
                if ps_match:
                    self.req_id = ps_match.group(1)
                    logger.info("Acquired PSX DPS session token _k: %s", self.req_id[:10] + "...")
                    self.is_connected = True
                    return True
                else:
                    logger.warning("Could not extract _k token from DPS homepage")
            else:
                logger.warning("DPS homepage returned status %s", resp.status_code)
        except Exception as e:
            logger.error("Failed to initialize DPS session: %s", e)
        return False

    async def fetch_indices(self) -> Dict[str, IndexQuote]:
        """
        Fetches official indices data (KSE100, KSE30, ALLSHR, etc.) from /indices.
        """
        indices: Dict[str, IndexQuote] = {}
        client = await self.get_client()
        headers = {
            "Referer": f"{self.BASE_URL}/",
            "User-Agent": self.USER_AGENT,
        }
        try:
            resp = await client.get(f"{self.BASE_URL}/indices", headers=headers)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                tables = soup.find_all("table")
                if tables:
                    rows = tables[0].find_all("tr")
                    for row in rows:
                        tds = [td.get_text(strip=True) for td in row.find_all("td")]
                        # Expected: ['KSE100', '170,944.34', '169,538.22', '169,600.40', '-825.22', '-0.48%']
                        if len(tds) >= 6:
                            code = tds[0].strip().upper()
                            try:
                                high = float(tds[1].replace(",", ""))
                                low = float(tds[2].replace(",", ""))
                                current = float(tds[3].replace(",", ""))
                                change = float(tds[4].replace(",", ""))
                                change_pct = float(tds[5].replace("%", "").replace(",", ""))
                                indices[code] = IndexQuote(
                                    code=code,
                                    name=f"PSX {code} Index",
                                    high=high,
                                    low=low,
                                    current=current,
                                    change=change,
                                    change_pct=change_pct,
                                    last_updated=datetime.utcnow()
                                )
                            except (ValueError, IndexError):
                                continue
        except Exception as e:
            logger.warning("Error fetching indices: %s", e)
        return indices

    async def fetch_sectorwise_stocks(self) -> Dict[str, StockQuote]:
        """
        Scrapes all stocks across all sectors from /sector-summary/sectorwise.
        Provides comprehensive real-time market data for all ~550+ listed equities.
        """
        if not self.req_id:
            await self.initialize_session()

        stocks: Dict[str, StockQuote] = {}
        client = await self.get_client()
        headers = {
            "User-Agent": self.USER_AGENT,
            "Referer": f"{self.BASE_URL}/",
            "X-Requested-With": "XMLHttpRequest",
        }
        if self.req_id:
            headers["X-Req-Id"] = self.req_id

        try:
            resp = await client.get(f"{self.BASE_URL}/sector-summary/sectorwise", headers=headers)
            if resp.status_code in (401, 403):
                # Token expired, renew and retry once
                logger.info("Renewing session token after %s response...", resp.status_code)
                if await self.initialize_session():
                    headers["X-Req-Id"] = self.req_id
                    resp = await client.get(f"{self.BASE_URL}/sector-summary/sectorwise", headers=headers)

            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                tables = soup.find_all("table")

                # Table 0 is sector overview summary; tables 1..N are sector stock tables
                for tbl in tables[1:]:
                    # Extract sector name from previous heading if available
                    sector_name = "EQUITY"
                    heading = tbl.find_previous(["h3", "h4", "h5", "div"], class_=re.compile(r"head|title|name", re.I))
                    if heading:
                        sector_name = heading.get_text(strip=True)[:40]

                    rows = tbl.find_all("tr")
                    for row in rows:
                        tds = [td.get_text(strip=True) for td in row.find_all("td")]
                        # Format: ['SYMBOL', 'NAME', 'LDCP', 'OPEN', 'HIGH', 'LOW', 'CURRENT', 'CHANGE', 'CHANGE (%)', 'VOLUME']
                        if len(tds) >= 9:
                            symbol = tds[0].strip().upper()
                            if not symbol or symbol in ("SYMBOL", "TOTAL"):
                                continue
                            name = tds[1].strip()
                            ldcp = tds[2]
                            open_p = tds[3]
                            high = tds[4]
                            low = tds[5]
                            current = tds[6]
                            change = tds[7]
                            change_pct = tds[8]
                            vol = tds[9] if len(tds) > 9 else "0"

                            try:
                                quote = StockQuote(
                                    symbol=symbol,
                                    name=name,
                                    sector=sector_name,
                                    ldcp=ldcp,
                                    open=open_p,
                                    high=high,
                                    low=low,
                                    current=current,
                                    change=change,
                                    change_pct=change_pct,
                                    volume=vol,
                                    last_updated=datetime.utcnow(),
                                    is_up=float(str(change).replace(",", "") or 0) > 0,
                                    is_down=float(str(change).replace(",", "") or 0) < 0
                                )
                                stocks[symbol] = quote
                            except Exception as ex:
                                continue

                self.consecutive_errors = 0
                return stocks
            else:
                logger.warning("sectorwise endpoint returned %s", resp.status_code)
                self.consecutive_errors += 1
        except Exception as e:
            logger.error("Error scraping sectorwise stocks: %s", e)
            self.consecutive_errors += 1

        return stocks

    async def update(self) -> Dict[str, Any]:
        """
        Executes one full sync pass: indices + all equities.
        Falls back to realistic baseline simulation if DPS is temporarily offline.
        """
        async with self.lock:
            indices_task = asyncio.create_task(self.fetch_indices())
            stocks_task = asyncio.create_task(self.fetch_sectorwise_stocks())

            fetched_indices, fetched_stocks = await asyncio.gather(indices_task, stocks_task)

            if fetched_stocks:
                self.latest_stocks.update(fetched_stocks)
                self.last_sync_time = datetime.utcnow()
                self.is_connected = True
            elif not self.latest_stocks:
                # Seed fallback data if initial fetch had zero items (offline or startup test)
                logger.info("Using baseline PSX stocks cache...")
                self._seed_fallback_stocks()

            if fetched_indices:
                self.latest_indices.update(fetched_indices)
            elif not self.latest_indices:
                self._seed_fallback_indices()

            # Update previous change flags
            for quote in self.latest_stocks.values():
                quote.is_up = quote.change > 0
                quote.is_down = quote.change < 0

            return {
                "stocks_count": len(self.latest_stocks),
                "indices_count": len(self.latest_indices),
                "last_sync": self.last_sync_time.isoformat() if self.last_sync_time else None
            }

    def get_summary(self) -> MarketSummary:
        """
        Generates aggregate market summary metrics.
        """
        status, reason, next_info = self.get_market_status()
        pkt_time_str = self.get_pkt_now().strftime("%Y-%m-%d %I:%M:%S %p PKT")

        advancers = sum(1 for s in self.latest_stocks.values() if s.change > 0)
        decliners = sum(1 for s in self.latest_stocks.values() if s.change < 0)
        unchanged = sum(1 for s in self.latest_stocks.values() if s.change == 0)
        total_vol = sum(s.volume for s in self.latest_stocks.values())

        kse100 = self.latest_indices.get("KSE100")
        allshr = self.latest_indices.get("ALLSHR")
        kse30 = self.latest_indices.get("KSE30")

        sync_str = self.last_sync_time.strftime("%I:%M:%S %p UTC") if self.last_sync_time else "Never"

        return MarketSummary(
            status=status,
            status_reason=reason,
            pkt_time=pkt_time_str,
            next_session_info=next_info,
            kse100=kse100,
            allshr=allshr,
            kse30=kse30,
            total_volume=total_vol,
            advancers=advancers,
            decliners=decliners,
            unchanged=unchanged,
            total_symbols=len(self.latest_stocks),
            last_sync=sync_str
        )

    def get_top_movers(self, min_volume: int = 50000, limit: int = 5) -> Dict[str, List[StockQuote]]:
        """
        Calculates Top Gainers, Top Losers, and Volume Leaders.
        Top Gainers are filtered by min_volume threshold to eliminate illiquid stocks.
        """
        stocks = list(self.latest_stocks.values())

        # Top Gainers: change_pct > 0 and volume >= min_volume
        liquid_gainers = [s for s in stocks if s.change_pct > 0 and s.volume >= min_volume]
        liquid_gainers.sort(key=lambda s: s.change_pct, reverse=True)
        # If not enough with strict volume, include any positive mover
        if len(liquid_gainers) < limit:
            fallback_gainers = [s for s in stocks if s.change_pct > 0 and s not in liquid_gainers]
            fallback_gainers.sort(key=lambda s: s.change_pct, reverse=True)
            gainers = (liquid_gainers + fallback_gainers)[:limit]
        else:
            gainers = liquid_gainers[:limit]

        # Top Losers: change_pct < 0, sorted by most negative
        losers = [s for s in stocks if s.change_pct < 0]
        losers.sort(key=lambda s: s.change_pct)
        top_losers = losers[:limit]

        # Volume Leaders: Highest volume
        vol_leaders = sorted(stocks, key=lambda s: s.volume, reverse=True)[:limit]

        return {
            "gainers": gainers,
            "losers": top_losers,
            "volume_leaders": vol_leaders
        }

    def _seed_fallback_stocks(self):
        """
        Seeds standard prominent PSX stocks to guarantee an instant, fully populated
        dashboard during initial connection or offline development.
        """
        prominent = [
            ("CNERGY", "Cnergyico PK Limited", "REFINERY", 4.25, 4.20, 4.35, 4.15, 4.30, 0.05, 1.18, 56532000),
            ("KEL", "K-Electric Limited", "POWER GENERATION & DISTRIBUTION", 4.85, 4.80, 5.05, 4.75, 4.95, 0.10, 2.06, 34673000),
            ("OGDC", "Oil & Gas Development Company", "OIL & GAS EXPLORATION", 142.50, 142.00, 144.00, 141.50, 143.20, 0.70, 0.49, 12540000),
            ("PPL", "Pakistan Petroleum Limited", "OIL & GAS EXPLORATION", 124.00, 124.50, 126.00, 123.50, 125.10, 1.10, 0.89, 9870000),
            ("HUBC", "Hub Power Company Limited", "POWER GENERATION & DISTRIBUTION", 138.20, 138.50, 140.00, 137.50, 139.00, 0.80, 0.58, 8650000),
            ("LUCK", "Lucky Cement Limited", "CEMENT", 985.00, 982.00, 995.00, 978.00, 991.50, 6.50, 0.66, 2140000),
            ("ENGRO", "Engro Corporation Limited", "FERTILIZER", 345.50, 346.00, 348.50, 343.00, 344.20, -1.30, -0.38, 3420000),
            ("FFC", "Fauji Fertilizer Company", "FERTILIZER", 215.00, 214.50, 218.00, 214.00, 217.40, 2.40, 1.12, 4580000),
            ("SYS", "Systems Limited", "TECHNOLOGY & COMMUNICATION", 412.00, 413.00, 418.00, 410.00, 416.80, 4.80, 1.17, 3120000),
            ("PSO", "Pakistan State Oil", "OIL & GAS MARKETING", 188.50, 189.00, 191.00, 186.50, 187.10, -1.40, -0.74, 5230000),
            ("HBL", "Habib Bank Limited", "COMMERCIAL BANKS", 132.00, 131.50, 133.50, 130.50, 131.80, -0.20, -0.15, 4120000),
            ("MCB", "MCB Bank Limited", "COMMERCIAL BANKS", 225.00, 226.00, 228.00, 224.50, 227.10, 2.10, 0.93, 1950000),
            ("UBL", "United Bank Limited", "COMMERCIAL BANKS", 278.00, 277.50, 281.00, 276.00, 280.40, 2.40, 0.86, 2870000),
            ("MEBL", "Meezan Bank Limited", "COMMERCIAL BANKS", 242.00, 243.00, 246.00, 241.00, 245.50, 3.50, 1.45, 2340000),
            ("PRL", "Pakistan Refinery Limited", "REFINERY", 28.50, 28.20, 29.80, 28.00, 29.40, 0.90, 3.16, 24560000),
            ("WTL", "WorldCall Telecom Limited", "TECHNOLOGY & COMMUNICATION", 1.25, 1.24, 1.30, 1.22, 1.27, 0.02, 1.60, 45670000),
            ("TRG", "TRG Pakistan Limited", "TECHNOLOGY & COMMUNICATION", 58.20, 58.00, 60.50, 57.50, 59.80, 1.60, 2.75, 18900000),
            ("BIPL", "BankIslami Pakistan Limited", "COMMERCIAL BANKS", 36.50, 36.80, 37.50, 36.20, 37.10, 0.60, 1.64, 7650000),
            ("FCSC", "First Capital Securities Corp", "INVESTMENT BANKS", 2.10, 2.15, 2.20, 2.05, 2.08, -0.02, -0.95, 3420000),
            ("TPLP", "TPL Properties Limited", "PROPERTY", 9.40, 9.45, 9.70, 9.25, 9.35, -0.05, -0.53, 6210000),
            ("AIRLINK", "Air Link Communication", "TECHNOLOGY & COMMUNICATION", 112.00, 111.00, 114.50, 110.00, 113.80, 1.80, 1.61, 4890000),
            ("EFERT", "Engro Fertilizers Limited", "FERTILIZER", 168.00, 168.50, 171.00, 167.00, 170.20, 2.20, 1.31, 3890000),
            ("FCCL", "Fauji Cement Company", "CEMENT", 21.40, 21.50, 22.10, 21.20, 21.85, 0.45, 2.10, 14200000),
            ("DGKC", "D.G. Khan Cement", "CEMENT", 82.50, 82.00, 84.00, 81.50, 83.40, 0.90, 1.09, 6540000),
            ("MLCF", "Maple Leaf Cement Factory", "CEMENT", 38.60, 38.80, 39.50, 38.20, 39.10, 0.50, 1.30, 8910000),
            ("PIOC", "Pioneer Cement Limited", "CEMENT", 145.00, 144.50, 148.00, 143.50, 146.50, 1.50, 1.03, 1980000),
            ("CHCC", "Cherat Cement Co.", "CEMENT", 178.00, 179.00, 182.00, 177.00, 180.50, 2.50, 1.40, 1250000),
            ("SEARL", "The Searle Company Limited", "PHARMACEUTICALS", 64.50, 64.00, 66.20, 63.80, 65.40, 0.90, 1.40, 3450000),
            ("ILP", "Interloop Limited", "TEXTILE COMPOSITE", 76.50, 77.00, 78.50, 76.00, 77.80, 1.30, 1.70, 2780000),
            ("NML", "Nishat Mills Limited", "TEXTILE COMPOSITE", 84.00, 83.50, 85.50, 83.00, 84.60, 0.60, 0.71, 1540000),
            ("GGL", "Ghani Global Glass Limited", "GLASS & CERAMICS", 8.90, 8.85, 9.20, 8.75, 9.05, 0.15, 1.69, 5670000),
            ("UNITY", "Unity Foods Limited", "FOOD & PERSONAL CARE", 24.50, 24.80, 25.20, 24.30, 24.90, 0.40, 1.63, 11450000),
            ("SNGP", "Sui Northern Gas Pipelines", "GAS UTILITIES", 78.00, 78.50, 80.00, 77.50, 79.20, 1.20, 1.54, 7650000),
            ("SSGC", "Sui Southern Gas Company", "GAS UTILITIES", 12.80, 12.75, 13.20, 12.60, 13.05, 0.25, 1.95, 8760000),
            ("ATRL", "Attock Refinery Limited", "REFINERY", 380.00, 379.00, 386.00, 376.00, 383.50, 3.50, 0.92, 1650000),
            ("NRL", "National Refinery Limited", "REFINERY", 248.00, 249.00, 254.00, 246.00, 251.80, 3.80, 1.53, 1430000),
            ("GHNI", "Ghandhara Industries", "AUTOMOBILE ASSEMBLER", 195.00, 196.00, 199.00, 193.00, 197.50, 2.50, 1.28, 890000),
            ("PAEL", "Pak Elektron Limited", "CABLE & ELECTRICAL GOODS", 24.10, 24.30, 25.00, 23.90, 24.75, 0.65, 2.70, 16780000),
            ("HASCOL", "Hascol Petroleum Limited", "OIL & GAS MARKETING", 6.80, 6.75, 7.15, 6.65, 7.02, 0.22, 3.24, 28900000),
            ("TELE", "Telecard Limited", "TECHNOLOGY & COMMUNICATION", 7.40, 7.35, 7.75, 7.20, 7.58, 0.18, 2.43, 9870000),
        ]
        for (sym, name, sec, ldcp, op, hi, lo, cur, chg, chg_p, vol) in prominent:
            self.latest_stocks[sym] = StockQuote(
                symbol=sym,
                name=name,
                sector=sec,
                ldcp=ldcp,
                open=op,
                high=hi,
                low=lo,
                current=cur,
                change=chg,
                change_pct=chg_p,
                volume=vol,
                last_updated=datetime.utcnow(),
                is_up=chg > 0,
                is_down=chg < 0
            )
        self.last_sync_time = datetime.utcnow()

    def _seed_fallback_indices(self):
        self.latest_indices["KSE100"] = IndexQuote(
            code="KSE100",
            name="KSE 100 Index",
            high=170944.34,
            low=169538.22,
            current=169600.40,
            change=-825.22,
            change_pct=-0.48,
            volume=385620000,
            last_updated=datetime.utcnow()
        )
        self.latest_indices["ALLSHR"] = IndexQuote(
            code="ALLSHR",
            name="All Share Index",
            high=103404.87,
            low=102645.41,
            current=102686.25,
            change=-370.77,
            change_pct=-0.36,
            volume=612450000,
            last_updated=datetime.utcnow()
        )
        self.latest_indices["KSE30"] = IndexQuote(
            code="KSE30",
            name="KSE 30 Index",
            high=50874.04,
            low=50431.84,
            current=50456.98,
            change=-260.85,
            change_pct=-0.51,
            volume=145890000,
            last_updated=datetime.utcnow()
        )
