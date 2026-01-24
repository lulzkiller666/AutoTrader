"""
Dynamic Universe Manager for AutoTrader v2

Manages a dynamic stock universe with smart prioritization, hourly refresh from multiple APIs,
and intelligent rotation that ensures hot stocks are screened every session.

Key Features:
- 3-tier priority system (Hot/Active/Core)
- Multi-source data fetching (yfinance, Twelve Data, Polygon, Alpha Vantage)
- Intelligent rotation algorithm (25 stocks per session)
- Hourly refresh during market hours
- Persistent caching for fast startup
"""

import json
import logging
import heapq
import asyncio
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Set
from pathlib import Path

logger = logging.getLogger(__name__)

# Import advanced logging for detailed metrics
try:
    from advanced_logging import get_logger, log_system
    ADVANCED_LOGGING = True
except ImportError:
    ADVANCED_LOGGING = False
    def log_system(*args, **kwargs): pass


@dataclass
class StockMetadata:
    """Metadata for a single stock in the universe"""
    ticker: str
    priority_tier: int  # 1=hot, 2=active, 3=core
    last_screened: Optional[datetime]
    discovery_source: str  # 'yfinance_gainers', 'polygon_gainers', etc.
    momentum_score: float  # 0-1 scale
    volume_score: float  # 0-1 scale
    last_change_1d: float  # Daily change percentage
    last_change_5d: float  # 5-day change percentage
    screen_count: int  # Number of times screened
    expires_at: Optional[datetime]  # Dynamic stocks expire after 24h
    added_at: datetime  # When stock was added to universe
    last_updated: datetime  # When metadata was last updated

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            'ticker': self.ticker,
            'priority_tier': self.priority_tier,
            'last_screened': self.last_screened.isoformat() if self.last_screened else None,
            'discovery_source': self.discovery_source,
            'momentum_score': self.momentum_score,
            'volume_score': self.volume_score,
            'last_change_1d': self.last_change_1d,
            'last_change_5d': self.last_change_5d,
            'screen_count': self.screen_count,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'added_at': self.added_at.isoformat(),
            'last_updated': self.last_updated.isoformat()
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'StockMetadata':
        """Create from dictionary"""
        return cls(
            ticker=data['ticker'],
            priority_tier=data['priority_tier'],
            last_screened=datetime.fromisoformat(data['last_screened']) if data.get('last_screened') else None,
            discovery_source=data['discovery_source'],
            momentum_score=data['momentum_score'],
            volume_score=data['volume_score'],
            last_change_1d=data['last_change_1d'],
            last_change_5d=data.get('last_change_5d', 0.0),
            screen_count=data['screen_count'],
            expires_at=datetime.fromisoformat(data['expires_at']) if data.get('expires_at') else None,
            added_at=datetime.fromisoformat(data['added_at']),
            last_updated=datetime.fromisoformat(data['last_updated'])
        )


@dataclass
class UniverseSnapshot:
    """Snapshot of the universe state for persistence"""
    version: str = "1.0"
    last_refresh: Optional[datetime] = None
    total_stocks: int = 0
    tier1_count: int = 0
    tier2_count: int = 0
    tier3_count: int = 0
    stocks: Dict[str, dict] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            'version': self.version,
            'last_refresh': self.last_refresh.isoformat() if self.last_refresh else None,
            'total_stocks': self.total_stocks,
            'tier1_count': self.tier1_count,
            'tier2_count': self.tier2_count,
            'tier3_count': self.tier3_count,
            'stocks': self.stocks or {}
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'UniverseSnapshot':
        """Create from dictionary"""
        return cls(
            version=data.get('version', '1.0'),
            last_refresh=datetime.fromisoformat(data['last_refresh']) if data.get('last_refresh') else None,
            total_stocks=data.get('total_stocks', 0),
            tier1_count=data.get('tier1_count', 0),
            tier2_count=data.get('tier2_count', 0),
            tier3_count=data.get('tier3_count', 0),
            stocks=data.get('stocks', {})
        )


class DynamicUniverseManager:
    """
    Manages the dynamic stock universe with smart prioritization and rotation.

    Features:
    - Multi-source data fetching (yfinance, Twelve Data, Polygon, Alpha Vantage)
    - 3-tier priority system (Hot/Active/Core)
    - Intelligent rotation ensuring hot stocks screened every session
    - Hourly refresh during market hours
    - Persistent caching
    """

    def _fetch_sp500_list(self) -> List[str]:
        """
        Fetch S&P 500 list dynamically from Wikipedia

        Returns:
            List of S&P 500 ticker symbols (~503 stocks)
        """
        try:
            import pandas as pd
            import requests
            from io import StringIO

            logger.info(" Fetching S&P 500 list from Wikipedia...")

            # Wikipedia scraping (free, updated regularly)
            url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'

            # Add User-Agent header to avoid 403 Forbidden
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            # Fetch HTML with requests (storage_options doesn't work for HTTP)
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            tables = pd.read_html(StringIO(response.text))
            sp500_df = tables[0]

            # Extract ticker symbols
            tickers = sp500_df['Symbol'].tolist()

            # Clean tickers (replace periods with hyphens for Alpaca compatibility)
            tickers = [ticker.replace('.', '-') for ticker in tickers]

            logger.info(f"Fetched {len(tickers)} S&P 500 stocks from Wikipedia")

            return tickers

        except Exception as e:
            logger.warning(f" Failed to fetch S&P 500 list from Wikipedia: {e}")
            logger.info("Using hardcoded S&P 500 fallback list (top 100 stocks)")

            # Hardcoded fallback: Top 100 S&P 500 stocks by market cap
            # This ensures we still have quality core stocks even if Wikipedia fails
            return [
                'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK-B', 'V', 'UNH',
                'JNJ', 'XOM', 'WMT', 'JPM', 'LLY', 'MA', 'PG', 'CVX', 'HD', 'ABBV',
                'MRK', 'AVGO', 'COST', 'PEP', 'KO', 'ADBE', 'TMO', 'MCD', 'CSCO', 'ACN',
                'ABT', 'CRM', 'NFLX', 'NKE', 'DHR', 'TXN', 'PM', 'VZ', 'DIS', 'UPS',
                'INTC', 'NEE', 'BMY', 'CMCSA', 'WFC', 'ORCL', 'AMD', 'QCOM', 'HON', 'COP',
                'UNP', 'LOW', 'BA', 'T', 'PFE', 'INTU', 'RTX', 'MS', 'SPGI', 'GS',
                'CAT', 'DE', 'AMGN', 'AXP', 'GE', 'BLK', 'ELV', 'MDLZ', 'ISRG', 'SYK',
                'BKNG', 'NOW', 'ADP', 'GILD', 'MMC', 'TJX', 'VRTX', 'C', 'REGN', 'ADI',
                'TMUS', 'MO', 'CB', 'ZTS', 'PLD', 'AMT', 'CI', 'SCHW', 'BDX', 'SO',
                'DUK', 'LRCX', 'CVS', 'BSX', 'SLB', 'ETN', 'PNC', 'EOG', 'ITW', 'USB'
            ]

    def __init__(self, data_fetcher, cache_dir: Path):
        """
        Initialize the Dynamic Universe Manager

        Args:
            data_fetcher: EnhancedDataSources instance for API calls
            cache_dir: Directory for caching universe state
        """
        self.data_fetcher = data_fetcher
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.cache_file = self.cache_dir / "universe_state.json"

        # Main universe storage
        self.universe: Dict[str, StockMetadata] = {}

        # Priority queues (max-heap using negative scores)
        self.tier1_queue: List[Tuple[float, str]] = []  # Hot stocks
        self.tier2_queue: List[Tuple[float, str]] = []  # Active stocks
        self.tier3_queue: List[Tuple[float, str]] = []  # Core stocks

        # Tracking
        self.last_refresh: Optional[datetime] = None
        self.refresh_interval_hours = 1

        # Fetch S&P 500 core stocks dynamically
        self.core_stocks: List[str] = self._fetch_sp500_list()

        logger.info(f"DynamicUniverseManager initialized (cache: {self.cache_file}, core: {len(self.core_stocks)} stocks)")

    def should_refresh(self) -> bool:
        """Check if universe should be refreshed (hourly)"""
        if self.last_refresh is None:
            return True

        elapsed = datetime.now() - self.last_refresh
        should = elapsed.total_seconds() >= (self.refresh_interval_hours * 3600)

        if should:
            logger.info(f"Universe refresh needed (last: {elapsed.total_seconds()/60:.1f} min ago)")

        return should

    def load_universe(self) -> bool:
        """
        Load universe from cache file

        Returns:
            True if loaded successfully, False otherwise
        """
        try:
            if not self.cache_file.exists():
                logger.info(f" No cache file found, will initialize S&P 500 core")
                self._initialize_core_stocks()
                return False

            with open(self.cache_file, 'r') as f:
                data = json.load(f)

            snapshot = UniverseSnapshot.from_dict(data)

            # Reconstruct universe from snapshot
            self.universe = {}
            for ticker, stock_data in snapshot.stocks.items():
                self.universe[ticker] = StockMetadata.from_dict(stock_data)

            self.last_refresh = snapshot.last_refresh

            # Rebuild priority queues
            self._rebuild_priority_queues()

            logger.info(f"Loaded universe from cache: {snapshot.total_stocks} stocks "
                       f"(T1: {snapshot.tier1_count}, T2: {snapshot.tier2_count}, T3: {snapshot.tier3_count})")

            return True

        except Exception as e:
            logger.error(f"Failed to load universe cache: {e}")
            self._initialize_core_stocks()
            return False

    def persist_universe(self) -> bool:
        """
        Save universe to cache file

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            # Count tiers
            tier_counts = {1: 0, 2: 0, 3: 0}
            for stock in self.universe.values():
                tier_counts[stock.priority_tier] = tier_counts.get(stock.priority_tier, 0) + 1

            # Create snapshot
            snapshot = UniverseSnapshot(
                last_refresh=self.last_refresh,
                total_stocks=len(self.universe),
                tier1_count=tier_counts[1],
                tier2_count=tier_counts[2],
                tier3_count=tier_counts[3],
                stocks={ticker: stock.to_dict() for ticker, stock in self.universe.items()}
            )

            # Write to file
            with open(self.cache_file, 'w') as f:
                json.dump(snapshot.to_dict(), f, indent=2)

            logger.info(f" Persisted universe: {snapshot.total_stocks} stocks "
                       f"(T1: {snapshot.tier1_count}, T2: {snapshot.tier2_count}, T3: {snapshot.tier3_count})")

            return True

        except Exception as e:
            logger.error(f"Failed to persist universe: {e}")
            return False

    def _initialize_core_stocks(self):
        """Initialize universe with S&P 500 core stocks (dynamically fetched)"""
        now = datetime.now()

        for ticker in self.core_stocks:
            self.universe[ticker] = StockMetadata(
                ticker=ticker,
                priority_tier=3,  # Core tier
                last_screened=None,
                discovery_source='sp500_core',
                momentum_score=0.0,
                volume_score=0.0,
                last_change_1d=0.0,
                last_change_5d=0.0,
                screen_count=0,
                expires_at=None,  # Never expires
                added_at=now,
                last_updated=now
            )

        self._rebuild_priority_queues()
        self.persist_universe()

        logger.info(f"Initialized {len(self.core_stocks)} S&P 500 core stocks (dynamically fetched)")

    def _rebuild_priority_queues(self):
        """Rebuild all priority queues from current universe state"""
        # Clear existing queues
        self.tier1_queue = []
        self.tier2_queue = []
        self.tier3_queue = []

        # Add all stocks to appropriate queues
        for ticker, metadata in self.universe.items():
            score = self._calculate_priority_score(metadata)

            # Use negative score for max-heap behavior
            if metadata.priority_tier == 1:
                heapq.heappush(self.tier1_queue, (-score, ticker))
            elif metadata.priority_tier == 2:
                heapq.heappush(self.tier2_queue, (-score, ticker))
            else:
                heapq.heappush(self.tier3_queue, (-score, ticker))

        logger.debug(f"Priority queues rebuilt: T1={len(self.tier1_queue)}, "
                    f"T2={len(self.tier2_queue)}, T3={len(self.tier3_queue)}")

    def _calculate_priority_score(self, metadata: StockMetadata) -> float:
        """
        Calculate priority score for a stock

        Formula: tier_urgency + staleness + momentum + volume

        Returns:
            Priority score (higher = more urgent to screen)
        """
        # Base urgency by tier
        tier_urgency = {1: 100.0, 2: 50.0, 3: 10.0}[metadata.priority_tier]

        # Staleness factor (10 points per hour since last screen)
        staleness = 0.0
        if metadata.last_screened:
            hours_since = (datetime.now() - metadata.last_screened).total_seconds() / 3600
            staleness = hours_since * 10
        else:
            staleness = 100.0  # Never screened = high priority

        # Momentum factor (1% move = 1 point)
        momentum = abs(metadata.last_change_1d) * 100

        # Volume factor (0-20 points)
        volume = metadata.volume_score * 20

        score = tier_urgency + staleness + momentum + volume

        return score

    def get_universe_stats(self) -> dict:
        """Get statistics about current universe state"""
        tier_counts = {1: 0, 2: 0, 3: 0}
        for stock in self.universe.values():
            tier_counts[stock.priority_tier] = tier_counts.get(stock.priority_tier, 0) + 1

        return {
            'total_stocks': len(self.universe),
            'tier1_count': tier_counts[1],
            'tier2_count': tier_counts[2],
            'tier3_count': tier_counts[3],
            'last_refresh': self.last_refresh.isoformat() if self.last_refresh else None,
            'cache_file': str(self.cache_file)
        }

    # ==================== API Data Fetching Methods ====================

    async def _fetch_yfinance_screeners(self) -> Dict[str, str]:
        """
        Fetch top stocks from yfinance screeners (unlimited)

        Returns:
            Dict mapping ticker -> discovery_source
        """
        import yfinance as yf

        results = {}

        try:
            logger.info(" Fetching from yfinance screeners...")

            # Fetch day gainers (250 stocks)
            try:
                gainers = yf.screen('day_gainers', count=250)
                if gainers and isinstance(gainers, dict) and 'quotes' in gainers:
                    for quote in gainers['quotes']:
                        ticker = quote.get('symbol')
                        if ticker and isinstance(ticker, str):
                            results[ticker] = 'yfinance_gainers'
                    logger.info(f"yfinance gainers: {len([t for t in results.values() if t == 'yfinance_gainers'])} stocks")
                elif gainers and hasattr(gainers, 'symbol'):  # Fallback for DataFrame format
                    for ticker in gainers.symbol.tolist():
                        if ticker and isinstance(ticker, str):
                            results[ticker] = 'yfinance_gainers'
                    logger.info(f"yfinance gainers: {len([t for t in results.values() if t == 'yfinance_gainers'])} stocks")
            except Exception as e:
                logger.warning(f" yfinance gainers failed: {e}")

            # Fetch day losers (250 stocks)
            try:
                losers = yf.screen('day_losers', count=250)
                if losers and isinstance(losers, dict) and 'quotes' in losers:
                    for quote in losers['quotes']:
                        ticker = quote.get('symbol')
                        if ticker and isinstance(ticker, str) and ticker not in results:
                            results[ticker] = 'yfinance_losers'
                    logger.info(f"yfinance losers: {len([t for t in results.values() if t == 'yfinance_losers'])} stocks")
                elif losers and hasattr(losers, 'symbol'):  # Fallback for DataFrame format
                    for ticker in losers.symbol.tolist():
                        if ticker and isinstance(ticker, str) and ticker not in results:
                            results[ticker] = 'yfinance_losers'
                    logger.info(f"yfinance losers: {len([t for t in results.values() if t == 'yfinance_losers'])} stocks")
            except Exception as e:
                logger.warning(f" yfinance losers failed: {e}")

            # Fetch most actives (250 stocks)
            try:
                actives = yf.screen('most_actives', count=250)
                if actives and isinstance(actives, dict) and 'quotes' in actives:
                    for quote in actives['quotes']:
                        ticker = quote.get('symbol')
                        if ticker and isinstance(ticker, str) and ticker not in results:
                            results[ticker] = 'yfinance_actives'
                    logger.info(f"yfinance actives: {len([t for t in results.values() if t == 'yfinance_actives'])} stocks")
                elif actives and hasattr(actives, 'symbol'):  # Fallback for DataFrame format
                    for ticker in actives.symbol.tolist():
                        if ticker and isinstance(ticker, str) and ticker not in results:
                            results[ticker] = 'yfinance_actives'
                    logger.info(f"yfinance actives: {len([t for t in results.values() if t == 'yfinance_actives'])} stocks")
            except Exception as e:
                logger.warning(f" yfinance actives failed: {e}")

            logger.info(f"yfinance total: {len(results)} unique stocks")

        except Exception as e:
            logger.error(f"yfinance screeners failed: {e}")

        return results

    async def _fetch_twelve_data_movers(self) -> Dict[str, str]:
        """
        Fetch top movers from Twelve Data Market Movers API (800 req/day, 100 credits/call)

        Uses /market_movers/stocks endpoint with direction=gainers/losers
        Costs: 100 credits per call, so 2 calls = 200 credits
        With 800 req/day limit, ~4 calls/day max = 2 gainers + 2 losers calls

        Returns:
            Dict mapping ticker -> discovery_source
        """
        results = {}

        try:
            if not self.data_fetcher.twelve_data_key:
                logger.debug(" No Twelve Data API key configured")
                return results

            logger.info(" Fetching from Twelve Data Market Movers API...")

            # Check rate limit (conservative: only fetch during hourly refresh)
            if not self.data_fetcher._rate_limit_check('twelve_data'):
                logger.warning("⏱️  Twelve Data rate limit reached, skipping")
                return results

            session = await self.data_fetcher._get_session()
            base_url = "https://api.twelvedata.com/market_movers/stocks"

            # Fetch gainers (top 50)
            try:
                params = {
                    'apikey': self.data_fetcher.twelve_data_key,
                    'direction': 'gainers',
                    'outputsize': 50,
                    'country': 'US'
                }

                async with session.get(base_url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get('status') == 'ok' and 'values' in data:
                            for item in data['values']:
                                ticker = item.get('symbol')
                                if ticker:
                                    results[ticker] = 'twelve_data_gainers'
                            logger.info(f"Twelve Data gainers: {len([t for t in results.values() if t == 'twelve_data_gainers'])} stocks")
                        else:
                            logger.warning(f" Twelve Data gainers response: {data.get('message', 'Unknown error')}")
                    else:
                        logger.warning(f" Twelve Data gainers HTTP {response.status}")
            except Exception as e:
                logger.warning(f" Twelve Data gainers failed: {e}")

            # Rate limit: wait before next call
            await asyncio.sleep(1)

            # Check rate limit again
            if not self.data_fetcher._rate_limit_check('twelve_data'):
                logger.warning("⏱️  Twelve Data rate limit reached after gainers")
                return results

            # Fetch losers (top 50)
            try:
                params = {
                    'apikey': self.data_fetcher.twelve_data_key,
                    'direction': 'losers',
                    'outputsize': 50,
                    'country': 'US'
                }

                async with session.get(base_url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get('status') == 'ok' and 'values' in data:
                            for item in data['values']:
                                ticker = item.get('symbol')
                                if ticker:
                                    results[ticker] = 'twelve_data_losers'
                            logger.info(f"Twelve Data losers: {len([t for t in results.values() if t == 'twelve_data_losers'])} stocks")
                        else:
                            logger.warning(f" Twelve Data losers response: {data.get('message', 'Unknown error')}")
                    else:
                        logger.warning(f" Twelve Data losers HTTP {response.status}")
            except Exception as e:
                logger.warning(f" Twelve Data losers failed: {e}")

            logger.info(f"Twelve Data total: {len(results)} unique stocks")

        except Exception as e:
            logger.error(f"Twelve Data fetch failed: {e}")

        return results

    async def _fetch_polygon_movers(self) -> Dict[str, str]:
        """
        Fetch top movers from Polygon.io (5 req/min, 2 calls)

        Returns:
            Dict mapping ticker -> discovery_source
        """
        results = {}

        try:
            if not self.data_fetcher.polygon_api_key:
                logger.debug(" No Polygon API key configured")
                return results

            logger.info(" Fetching from Polygon.io...")

            # Check rate limit
            if not self.data_fetcher._rate_limit_check('polygon'):
                logger.warning("⏱️  Polygon rate limit reached, skipping")
                return results

            session = await self.data_fetcher._get_session()
            base_url = "https://api.polygon.io/v2"

            # Fetch gainers (top 20)
            try:
                url = f"{base_url}/snapshot/locale/us/markets/stocks/gainers"
                params = {'apiKey': self.data_fetcher.polygon_api_key}

                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get('status') == 'OK' and 'tickers' in data:
                            for ticker_data in data['tickers'][:20]:
                                ticker = ticker_data.get('ticker')
                                if ticker:
                                    results[ticker] = 'polygon_gainers'
                            logger.info(f"Polygon gainers: {len([t for t in results.values() if t == 'polygon_gainers'])} stocks")
            except Exception as e:
                logger.warning(f" Polygon gainers failed: {e}")

            # Rate limit: wait a bit before next call
            await asyncio.sleep(1)

            # Check rate limit again
            if not self.data_fetcher._rate_limit_check('polygon'):
                logger.warning("⏱️  Polygon rate limit reached after gainers")
                return results

            # Fetch losers (top 20)
            try:
                url = f"{base_url}/snapshot/locale/us/markets/stocks/losers"
                params = {'apiKey': self.data_fetcher.polygon_api_key}

                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get('status') == 'OK' and 'tickers' in data:
                            for ticker_data in data['tickers'][:20]:
                                ticker = ticker_data.get('ticker')
                                if ticker and ticker not in results:
                                    results[ticker] = 'polygon_losers'
                            logger.info(f"Polygon losers: {len([t for t in results.values() if t == 'polygon_losers'])} stocks")
            except Exception as e:
                logger.warning(f" Polygon losers failed: {e}")

            logger.info(f"Polygon total: {len(results)} unique stocks")

        except Exception as e:
            logger.error(f"Polygon fetch failed: {e}")

        return results

    def _merge_and_deduplicate(self, api_results: List[Dict[str, str]]) -> Dict[str, str]:
        """
        Merge results from all APIs and deduplicate

        Args:
            api_results: List of dicts from each API (yfinance, Twelve Data, Polygon)

        Returns:
            Dict mapping ticker -> discovery_source (first source wins)
        """
        merged = {}

        # Merge in order: yfinance (primary), Twelve Data, Polygon
        # First source wins for priority
        for api_dict in api_results:
            if isinstance(api_dict, dict):
                for ticker, source in api_dict.items():
                    if ticker not in merged:
                        merged[ticker] = source

        # Add S&P 500 core stocks if not already present
        for ticker in self.core_stocks:
            if ticker not in merged:
                merged[ticker] = 'sp500_core'

        logger.info(f"Merged universe: {len(merged)} unique stocks")

        return merged

    # ==================== Priority System Methods ====================

    def _assign_priority_tier(self, metadata: StockMetadata) -> int:
        """
        Assign priority tier based on CURRENT stock characteristics

        Tier 1 (Hot): abs(change) >= 5%
        Tier 2 (Active): volume >= 0.5 OR abs(change) >= 2%
        Tier 3 (Core): Everything else (including S&P 500 base stocks)

        Note: Discovery source is NOT used for tier assignment - only current metrics.
        This allows stocks to be promoted/demoted based on real-time data.

        Args:
            metadata: StockMetadata object

        Returns:
            Priority tier (1=hot, 2=active, 3=core)
        """
        # Check if hot (Tier 1) - based on current metrics only
        if abs(metadata.last_change_1d) >= 5.0:
            return 1

        # Check if active (Tier 2)
        if metadata.volume_score >= 0.5:
            return 2

        if abs(metadata.last_change_1d) >= 2.0:
            return 2

        # Default to core (Tier 3)
        return 3

    def _recalculate_priority_tiers(self):
        """
        Recalculate priority tiers for all stocks in universe

        Updates tier assignments based on current stock characteristics
        and rebuilds priority queues
        """
        tier_changes = 0
        tier_movements = {
            'promoted_to_t1': [],  # T2/T3 → T1
            'promoted_to_t2': [],  # T3 → T2
            'demoted_to_t2': [],   # T1 → T2
            'demoted_to_t3': []    # T1/T2 → T3
        }

        for ticker, metadata in self.universe.items():
            old_tier = metadata.priority_tier
            new_tier = self._assign_priority_tier(metadata)

            if old_tier != new_tier:
                metadata.priority_tier = new_tier
                tier_changes += 1
                logger.debug(f"{ticker} tier changed: {old_tier} → {new_tier}")

                # Track movement direction
                if new_tier == 1 and old_tier > 1:
                    tier_movements['promoted_to_t1'].append(ticker)
                elif new_tier == 2 and old_tier == 3:
                    tier_movements['promoted_to_t2'].append(ticker)
                elif new_tier == 2 and old_tier == 1:
                    tier_movements['demoted_to_t2'].append(ticker)
                elif new_tier == 3 and old_tier < 3:
                    tier_movements['demoted_to_t3'].append(ticker)

        if tier_changes > 0:
            logger.info(f"Recalculated tiers: {tier_changes} stocks changed tiers")

            # Log tier movements for optimization analysis
            if ADVANCED_LOGGING and tier_changes > 0:
                log_system("info", "Tier movements detected", {
                    "event": "tier_recalculation",
                    "total_changes": tier_changes,
                    "promoted_to_t1": tier_movements['promoted_to_t1'],
                    "promoted_to_t2": tier_movements['promoted_to_t2'],
                    "demoted_to_t2": tier_movements['demoted_to_t2'],
                    "demoted_to_t3": tier_movements['demoted_to_t3'],
                    "timestamp": datetime.now().isoformat()
                })

        # Rebuild queues with new tiers
        self._rebuild_priority_queues()

    # ==================== Expiration & Refresh Methods ====================

    def _remove_expired_stocks(self):
        """
        Remove expired stocks from universe (>24h old, not S&P 500)

        S&P 500 core stocks never expire (expires_at = None)
        Dynamic stocks expire after 24 hours
        """
        now = datetime.now()
        expired_tickers = []

        for ticker, metadata in self.universe.items():
            # Skip S&P 500 core stocks (never expire)
            if metadata.expires_at is None:
                continue

            # Check if expired
            if metadata.expires_at <= now:
                expired_tickers.append(ticker)

        # Remove expired stocks
        for ticker in expired_tickers:
            del self.universe[ticker]
            logger.debug(f"  Removed expired stock: {ticker}")

        if expired_tickers:
            logger.info(f"  Removed {len(expired_tickers)} expired stocks")

    async def refresh_universe(self):
        """
        Refresh universe from all APIs (hourly during market hours)

        Steps:
        1. Fetch from all APIs in parallel (yfinance, Polygon)
        2. Merge and deduplicate results
        3. Update/add stocks to universe
        4. Remove expired stocks (>24h)
        5. Recalculate tiers
        6. Rebuild priority queues
        7. Persist to disk

        Returns:
            True if refresh succeeded, False otherwise
        """
        try:
            logger.info("Starting universe refresh...")
            refresh_start = datetime.now()
            refresh_start_time = time.time()

            # Fetch from all APIs in parallel
            tasks = [
                self._fetch_yfinance_screeners(),
                self._fetch_twelve_data_movers(),
                self._fetch_polygon_movers()
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Filter out exceptions
            valid_results = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning(f" API fetch failed: {result}")
                else:
                    valid_results.append(result)

            # Merge results
            if not valid_results:
                logger.error("All API fetches failed, keeping cached universe")
                return False

            merged_stocks = self._merge_and_deduplicate(valid_results)

            # Update universe with new/updated stocks
            now = datetime.now()
            new_stocks = 0
            updated_stocks = 0

            for ticker, source in merged_stocks.items():
                if ticker in self.universe:
                    # Existing stock - refresh expiry
                    if self.universe[ticker].expires_at is not None:
                        self.universe[ticker].expires_at = now + timedelta(hours=24)
                        self.universe[ticker].last_updated = now
                        updated_stocks += 1
                else:
                    # New stock - create metadata
                    self.universe[ticker] = StockMetadata(
                        ticker=ticker,
                        priority_tier=3,  # Default tier, will be recalculated
                        last_screened=None,
                        discovery_source=source,
                        momentum_score=0.0,
                        volume_score=0.0,
                        last_change_1d=0.0,
                        last_change_5d=0.0,
                        screen_count=0,
                        expires_at=now + timedelta(hours=24) if source != 'sp500_core' else None,
                        added_at=now,
                        last_updated=now
                    )
                    new_stocks += 1

            logger.info(f"Universe updated: {new_stocks} new, {updated_stocks} refreshed")

            # Remove expired stocks
            self._remove_expired_stocks()

            # Recalculate tiers based on latest data
            self._recalculate_priority_tiers()

            # Persist to disk
            self.persist_universe()

            # Update last refresh time
            self.last_refresh = datetime.now()

            refresh_duration_ms = (time.time() - refresh_start_time) * 1000

            # Get tier statistics
            tier_stats = self.get_universe_stats()

            logger.info(f"Universe refresh complete in {refresh_duration_ms:.0f}ms ({len(self.universe)} stocks)")

            # Log detailed refresh metrics
            if ADVANCED_LOGGING:
                log_system("info", "Universe refresh complete", {
                    "event": "universe_refresh",
                    "duration_ms": refresh_duration_ms,
                    "total_stocks": len(self.universe),
                    "new_stocks": new_stocks,
                    "updated_stocks": updated_stocks,
                    "tier1_count": tier_stats['tier1_count'],
                    "tier2_count": tier_stats['tier2_count'],
                    "tier3_count": tier_stats['tier3_count'],
                    "sources": list(set(m.discovery_source for m in self.universe.values())),
                    "timestamp": datetime.now().isoformat()
                })

            return True

        except Exception as e:
            logger.error(f"Universe refresh failed: {e}")
            logger.exception(e)

            # Log refresh failure
            if ADVANCED_LOGGING:
                log_system("error", "Universe refresh failed", {
                    "event": "universe_refresh_failed",
                    "error": str(e),
                    "timestamp": datetime.now().isoformat()
                })

            return False

    # ================================================================================================
    # PHASE 5: ROTATION ALGORITHM
    # ================================================================================================

    def get_next_batch_for_screening(self, max_stocks: int = 25) -> List[str]:
        """
        Select next batch of stocks for screening using intelligent rotation

        Algorithm:
        1. Calculate tier targets: 50% Tier 1, 25% Tier 2, 25% Tier 3
        2. Select from each tier using priority scores
        3. Update last_screened timestamp
        4. Ensure no duplicates within session

        Args:
            max_stocks: Maximum number of stocks to select (default: 25)

        Returns:
            List of ticker symbols to screen
        """
        try:
            # Rebuild priority queues to ensure latest scores
            self._rebuild_priority_queues()

            # Calculate tier targets (50% T1, 25% T2, 25% T3)
            tier1_target = max(1, int(max_stocks * 0.5))  # At least 1 stock
            tier2_target = max(1, int(max_stocks * 0.25))
            tier3_target = max(1, max_stocks - tier1_target - tier2_target)

            logger.debug(f"Selection targets: T1={tier1_target}, T2={tier2_target}, T3={tier3_target}")

            # Select from each tier
            selected = []
            selected.extend(self._select_from_tier(self.tier1_queue, tier1_target, "Tier 1"))
            selected.extend(self._select_from_tier(self.tier2_queue, tier2_target, "Tier 2"))
            selected.extend(self._select_from_tier(self.tier3_queue, tier3_target, "Tier 3"))

            # Update last_screened timestamps
            now = datetime.now()
            for ticker in selected:
                if ticker in self.universe:
                    self.universe[ticker].last_screened = now
                    self.universe[ticker].screen_count += 1

            logger.info(f"Selected {len(selected)} stocks for screening (T1: {tier1_target}, T2: {tier2_target}, T3: {tier3_target})")

            return selected

        except Exception as e:
            logger.error(f"Failed to select batch: {e}")
            logger.exception(e)
            # Fallback: return random sample from universe
            available = list(self.universe.keys())
            if available:
                import random
                return random.sample(available, min(max_stocks, len(available)))
            return []

    def _select_from_tier(self, queue: List, count: int, tier_name: str) -> List[str]:
        """
        Select stocks from a tier queue (max-heap)

        Args:
            queue: Priority queue (heapq max-heap with negative scores)
            count: Number of stocks to select
            tier_name: Name for logging

        Returns:
            List of selected ticker symbols
        """
        selected = []
        seen = set()

        # Pop from heap (highest priority first)
        while len(selected) < count and queue:
            neg_score, ticker = heapq.heappop(queue)

            # Skip duplicates
            if ticker in seen:
                continue

            # Skip if not in universe (removed during refresh)
            if ticker not in self.universe:
                continue

            selected.append(ticker)
            seen.add(ticker)

        if len(selected) < count:
            logger.warning(f" {tier_name}: Only {len(selected)}/{count} stocks available")

        return selected

    def update_stock_metadata(self, ticker: str, market_data: dict):
        """
        Update stock metadata after screening

        Args:
            ticker: Stock ticker symbol
            market_data: Dict with keys: change_1d, change_5d, volume, avg_volume

        Updates:
        - momentum_score, volume_score, last_change_1d, last_change_5d
        - Recalculates tier if scores changed significantly
        - Rebuilds priority queues if tier changed
        """
        if ticker not in self.universe:
            logger.warning(f" Cannot update metadata: {ticker} not in universe")
            return

        try:
            metadata = self.universe[ticker]
            old_tier = metadata.priority_tier

            # Update momentum and volume scores
            if 'change_1d' in market_data:
                metadata.last_change_1d = market_data['change_1d']
                # Momentum score: 0-1 scale based on abs(change)
                # 10% change = 1.0, 5% = 0.5, 0% = 0.0
                metadata.momentum_score = min(1.0, abs(market_data['change_1d']) / 10.0)

            if 'change_5d' in market_data:
                metadata.last_change_5d = market_data['change_5d']

            if 'volume' in market_data and 'avg_volume' in market_data:
                avg_vol = market_data['avg_volume']
                if avg_vol and avg_vol > 0:
                    volume_ratio = market_data['volume'] / avg_vol
                    # Volume score: 0-1 scale
                    # 2x avg = 1.0, 1x avg = 0.5, 0x avg = 0.0
                    metadata.volume_score = min(1.0, volume_ratio / 2.0)

            # Recalculate tier
            new_tier = self._assign_priority_tier(metadata)

            if new_tier != old_tier:
                metadata.priority_tier = new_tier
                logger.info(f"{ticker} moved: Tier {old_tier} → Tier {new_tier}")

                # Log tier change for optimization
                if ADVANCED_LOGGING:
                    log_system("info", f"Stock tier changed: {ticker}", {
                        "event": "stock_tier_change",
                        "ticker": ticker,
                        "old_tier": old_tier,
                        "new_tier": new_tier,
                        "momentum_score": metadata.momentum_score,
                        "volume_score": metadata.volume_score,
                        "change_1d": metadata.last_change_1d,
                        "change_5d": metadata.last_change_5d,
                        "timestamp": datetime.now().isoformat()
                    })

                # Rebuild queues when tier changes
                self._rebuild_priority_queues()

            metadata.last_updated = datetime.now()

        except Exception as e:
            logger.error(f"Failed to update metadata for {ticker}: {e}")
            logger.exception(e)
