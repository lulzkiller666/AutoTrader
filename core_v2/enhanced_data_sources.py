#!/usr/bin/env python3
"""
Enhanced Data Sources with Professional APIs + Robust Fallbacks
Now powered by Polygon.io → Alpha Vantage → Stooq with intelligent rate limiting
"""

import asyncio
import aiohttp
import time
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass
import pandas as pd
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import advanced logging for market data tracking
try:
    from advanced_logging import log_market_data, log_system
    ADVANCED_LOGGING = True
except ImportError:
    ADVANCED_LOGGING = False
    def log_market_data(*args, **kwargs): pass
    def log_system(*args, **kwargs): pass

@dataclass
class MarketDataPoint:
    """Standardized market data structure"""
    symbol: str
    current_price: float
    price_change_1d: float
    price_change_5d: float
    volume: int
    volatility: float
    timestamp: datetime
    data_source: str
    quality_score: float = 1.0


# =============================================================================
# Technical Indicator Calculations
# =============================================================================

def calculate_rsi(prices: pd.Series, period: int = 14) -> Optional[float]:
    """
    Calculate Relative Strength Index (RSI)
    RSI > 70 = overbought, RSI < 30 = oversold
    """
    if len(prices) < period + 1:
        return None

    delta = prices.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()

    # Avoid division by zero
    rs = avg_gain / avg_loss.replace(0, float('inf'))
    rsi = 100 - (100 / (1 + rs))

    return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else None


def calculate_macd(prices: pd.Series) -> Optional[Dict[str, Any]]:
    """
    Calculate MACD (Moving Average Convergence Divergence)
    Bullish when MACD line crosses above signal line
    """
    if len(prices) < 26:
        return None

    ema12 = prices.ewm(span=12, adjust=False).mean()
    ema26 = prices.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line

    macd_val = float(macd_line.iloc[-1])
    signal_val = float(signal_line.iloc[-1])
    hist_val = float(histogram.iloc[-1])

    # Determine trend
    if pd.isna(macd_val) or pd.isna(signal_val):
        return None

    return {
        'macd': macd_val,
        'signal': signal_val,
        'histogram': hist_val,
        'bullish': macd_val > signal_val,
        'crossover': 'bullish' if (macd_line.iloc[-2] <= signal_line.iloc[-2] and macd_val > signal_val) else
                     'bearish' if (macd_line.iloc[-2] >= signal_line.iloc[-2] and macd_val < signal_val) else 'none'
    }


def calculate_bollinger_bands(prices: pd.Series, period: int = 20, num_std: float = 2.0) -> Optional[Dict[str, Any]]:
    """
    Calculate Bollinger Bands
    Price near upper band = potentially overbought
    Price near lower band = potentially oversold
    """
    if len(prices) < period:
        return None

    sma = prices.rolling(window=period).mean()
    std = prices.rolling(window=period).std()

    upper = sma + (std * num_std)
    lower = sma - (std * num_std)

    current_price = float(prices.iloc[-1])
    upper_val = float(upper.iloc[-1])
    lower_val = float(lower.iloc[-1])
    middle_val = float(sma.iloc[-1])

    if pd.isna(upper_val) or pd.isna(lower_val) or upper_val == lower_val:
        return None

    # Position within bands (0 = at lower, 1 = at upper)
    position = (current_price - lower_val) / (upper_val - lower_val)

    return {
        'upper': upper_val,
        'middle': middle_val,
        'lower': lower_val,
        'position': position,
        'bandwidth': (upper_val - lower_val) / middle_val,  # Band width as % of price
        'signal': 'overbought' if position > 0.95 else 'oversold' if position < 0.05 else 'neutral'
    }


def calculate_moving_averages(prices: pd.Series) -> Optional[Dict[str, Any]]:
    """
    Calculate various moving averages
    Price above MAs = bullish, below = bearish
    """
    current_price = float(prices.iloc[-1])

    result = {
        'current_price': current_price,
        'sma_20': None,
        'sma_50': None,
        'sma_200': None,
        'ema_12': None,
        'ema_26': None,
        'trend': 'neutral',
        'golden_cross': False,
        'death_cross': False
    }

    if len(prices) >= 20:
        result['sma_20'] = float(prices.rolling(20).mean().iloc[-1])
    if len(prices) >= 50:
        result['sma_50'] = float(prices.rolling(50).mean().iloc[-1])
    if len(prices) >= 200:
        result['sma_200'] = float(prices.rolling(200).mean().iloc[-1])
    if len(prices) >= 12:
        result['ema_12'] = float(prices.ewm(span=12, adjust=False).mean().iloc[-1])
    if len(prices) >= 26:
        result['ema_26'] = float(prices.ewm(span=26, adjust=False).mean().iloc[-1])

    # Determine trend based on price vs SMA50
    if result['sma_50']:
        if current_price > result['sma_50'] * 1.02:
            result['trend'] = 'bullish'
        elif current_price < result['sma_50'] * 0.98:
            result['trend'] = 'bearish'

    # Check for golden cross (SMA50 crosses above SMA200) / death cross
    if result['sma_50'] and result['sma_200'] and len(prices) >= 201:
        sma50_prev = float(prices.iloc[:-1].rolling(50).mean().iloc[-1])
        sma200_prev = float(prices.iloc[:-1].rolling(200).mean().iloc[-1])

        if sma50_prev <= sma200_prev and result['sma_50'] > result['sma_200']:
            result['golden_cross'] = True
        elif sma50_prev >= sma200_prev and result['sma_50'] < result['sma_200']:
            result['death_cross'] = True

    return result


def calculate_all_indicators(prices: pd.Series) -> Dict[str, Any]:
    """
    Calculate all technical indicators from a price series
    Returns a comprehensive dictionary of all indicators
    """
    indicators = {
        'rsi': None,
        'macd': None,
        'bollinger': None,
        'moving_averages': None,
        'has_sufficient_data': len(prices) >= 26
    }

    if len(prices) < 14:
        return indicators

    indicators['rsi'] = calculate_rsi(prices)
    indicators['macd'] = calculate_macd(prices)
    indicators['bollinger'] = calculate_bollinger_bands(prices)
    indicators['moving_averages'] = calculate_moving_averages(prices)

    return indicators


def format_indicators_for_ai(indicators: Dict[str, Any]) -> str:
    """
    Format technical indicators into a human-readable string for AI context
    """
    lines = []

    # RSI
    rsi = indicators.get('rsi')
    if rsi is not None:
        if rsi > 70:
            rsi_signal = "OVERBOUGHT - consider selling"
        elif rsi < 30:
            rsi_signal = "OVERSOLD - consider buying"
        elif rsi > 60:
            rsi_signal = "approaching overbought"
        elif rsi < 40:
            rsi_signal = "approaching oversold"
        else:
            rsi_signal = "neutral"
        lines.append(f"RSI(14): {rsi:.1f} ({rsi_signal})")

    # MACD
    macd = indicators.get('macd')
    if macd:
        trend = "bullish" if macd['bullish'] else "bearish"
        crossover = f", {macd['crossover']} crossover" if macd['crossover'] != 'none' else ""
        hist_direction = "expanding" if abs(macd['histogram']) > 0.5 else "contracting"
        lines.append(f"MACD: {trend}, histogram {hist_direction}{crossover}")

    # Bollinger Bands
    bb = indicators.get('bollinger')
    if bb:
        pos_pct = bb['position'] * 100
        lines.append(f"Bollinger: at {pos_pct:.0f}% of band ({bb['signal']})")

    # Moving Averages
    ma = indicators.get('moving_averages')
    if ma:
        trend_str = f"Trend: {ma['trend'].upper()}"
        if ma['golden_cross']:
            trend_str += " + GOLDEN CROSS (bullish signal)"
        elif ma['death_cross']:
            trend_str += " + DEATH CROSS (bearish signal)"
        lines.append(trend_str)

        if ma['sma_50']:
            above_below = "above" if ma['current_price'] > ma['sma_50'] else "below"
            pct_from_sma50 = ((ma['current_price'] / ma['sma_50']) - 1) * 100
            lines.append(f"Price {above_below} SMA50 by {abs(pct_from_sma50):.1f}%")

    return "\n".join(lines) if lines else "Insufficient data for technical analysis"

@dataclass 
class DataFetchResult:
    """Data fetch result structure for backward compatibility"""
    df: pd.DataFrame
    source: str
    success: bool
    ticker: str
    timestamp: Optional[datetime] = None
    quality_score: float = 1.0

class ProfessionalDataFetcher:
    """Professional market data fetcher with robust fallback chain"""
    
    def __init__(self):
        # API Keys from environment
        self.polygon_api_key = os.getenv('POLYGON_API_KEY', 'UrkZlyv7XtbEbrTXVsnLDsCMFxxavdL_')
        self.alpha_vantage_key = os.getenv('ALPHA_VANTAGE_KEY', 'OENJH9RCUTK74IMD')
        self.twelve_data_key = os.getenv('TWELVE_DATA_API_KEY', '')

        # Verify API keys
        if not self.polygon_api_key or self.polygon_api_key.startswith('your_'):
            logger.warning("No valid Polygon.io API key found")
        else:
            logger.info("Polygon.io API key loaded")

        if not self.alpha_vantage_key or self.alpha_vantage_key.startswith('your_'):
            logger.warning("No valid Alpha Vantage API key found")
        else:
            logger.info("Alpha Vantage API key loaded")

        if not self.twelve_data_key or self.twelve_data_key.startswith('your_'):
            logger.info("No Twelve Data API key - skipping this source")
        else:
            logger.info("Twelve Data API key loaded")

        # Request tracking for rate limiting
        self.polygon_requests = []
        self.alpha_requests = []
        self.twelve_data_requests = []
        self.twelve_data_daily_count = 0
        self.twelve_data_daily_reset = time.time()
        self.stooq_requests = []
        self.cache = {}
        self.cache_timeout = 300  # 5 minutes

        # Session for connection pooling
        self.session = None

        # Fallback statistics
        self.fallback_stats = {
            'polygon_success': 0,
            'polygon_fail': 0,
            'alpha_success': 0,
            'alpha_fail': 0,
            'twelve_data_success': 0,
            'twelve_data_fail': 0,
            'stooq_success': 0,
            'stooq_fail': 0,
            'total_requests': 0
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            connector = aiohttp.TCPConnector(limit=100, keepalive_timeout=60)
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={'User-Agent': 'TradingSystem/2.0'}
            )
        return self.session

    def _rate_limit_check(self, api_name: str) -> bool:
        """Enhanced rate limiting with intelligent backoff"""
        current_time = time.time()
        
        if api_name == 'polygon':
            # Remove old requests (older than 1 minute)
            self.polygon_requests = [t for t in self.polygon_requests if current_time - t < 60]
            # Free tier: 5 requests per minute - leave buffer
            if len(self.polygon_requests) >= 4:
                wait_time = 60 - (current_time - self.polygon_requests[0])
                if wait_time > 0:
                    logger.info(f"⏱️ Polygon rate limit: waiting {wait_time:.1f}s")
                    return False
            self.polygon_requests.append(current_time)
            
        elif api_name == 'alpha_vantage':
            # Remove old requests (older than 1 minute) 
            self.alpha_requests = [t for t in self.alpha_requests if current_time - t < 60]
            # Free tier: 5 requests per minute - leave buffer
            if len(self.alpha_requests) >= 4:
                wait_time = 60 - (current_time - self.alpha_requests[0])
                if wait_time > 0:
                    logger.info(f"⏱️ Alpha Vantage rate limit: waiting {wait_time:.1f}s")
                    return False
            self.alpha_requests.append(current_time)
            
        elif api_name == 'twelve_data':
            # Twelve Data: 800 calls per day limit
            # Reset daily counter if 24 hours passed
            if current_time - self.twelve_data_daily_reset > 86400:  # 24 hours
                self.twelve_data_daily_count = 0
                self.twelve_data_daily_reset = current_time

            if self.twelve_data_daily_count >= 800:
                logger.warning(f"⏱️ Twelve Data daily limit reached (800/800)")
                return False

            self.twelve_data_daily_count += 1
            self.twelve_data_requests.append(current_time)

        elif api_name == 'stooq':
            # Stooq is more lenient but still respect limits
            self.stooq_requests = [t for t in self.stooq_requests if current_time - t < 60]
            if len(self.stooq_requests) >= 30:  # More generous limit
                return False
            self.stooq_requests.append(current_time)

        return True

    async def _fetch_polygon_quote(self, symbol: str) -> Optional[Dict]:
        """Fetch real-time quote from Polygon.io"""
        try:
            if not self._rate_limit_check('polygon'):
                logger.debug(f"⏱️ Rate limit hit for Polygon.io - {symbol}")
                self.fallback_stats['polygon_fail'] += 1
                return None
                
            session = await self._get_session()
            url = f"https://api.polygon.io/v2/last/trade/{symbol.upper()}"
            params = {'apikey': self.polygon_api_key}
            
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('status') == 'OK' and 'results' in data:
                        result = data['results']
                        self.fallback_stats['polygon_success'] += 1
                        return {
                            'symbol': symbol.upper(),
                            'price': result.get('p', 0),
                            'volume': result.get('s', 0),
                            'timestamp': result.get('t', int(time.time() * 1000)),
                            'source': 'polygon'
                        }
                elif response.status == 429:
                    logger.debug(f"⏱️ Polygon.io rate limited for {symbol}")
                    self.fallback_stats['polygon_fail'] += 1
                else:
                    logger.debug(f"Polygon.io API error {response.status} for {symbol}")
                    self.fallback_stats['polygon_fail'] += 1
                    
        except Exception as e:
            logger.debug(f"Polygon.io fetch error for {symbol}: {e}")
            self.fallback_stats['polygon_fail'] += 1
            
        return None

    async def _fetch_alpha_vantage_quote(self, symbol: str) -> Optional[Dict]:
        """Fetch quote from Alpha Vantage"""
        try:
            if not self._rate_limit_check('alpha_vantage'):
                logger.debug(f"⏱️ Rate limit hit for Alpha Vantage - {symbol}")
                self.fallback_stats['alpha_fail'] += 1
                return None
                
            session = await self._get_session()
            url = "https://www.alphavantage.co/query"
            params = {
                'function': 'GLOBAL_QUOTE',
                'symbol': symbol.upper(),
                'apikey': self.alpha_vantage_key
            }
            
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'Global Quote' in data:
                        quote = data['Global Quote']
                        self.fallback_stats['alpha_success'] += 1
                        return {
                            'symbol': symbol.upper(),
                            'price': float(quote.get('05. price', 0)),
                            'change': float(quote.get('09. change', 0)),
                            'change_percent': quote.get('10. change percent', '0%'),
                            'volume': int(quote.get('06. volume', 0)),
                            'source': 'alpha_vantage'
                        }
                    elif 'Error Message' in data:
                        logger.debug(f"Alpha Vantage error for {symbol}: {data['Error Message']}")
                        self.fallback_stats['alpha_fail'] += 1
                    elif 'Information' in data:
                        logger.debug(f"⏱️ Alpha Vantage rate limited for {symbol}: {data['Information']}")
                        self.fallback_stats['alpha_fail'] += 1
                else:
                    logger.debug(f"Alpha Vantage API error {response.status} for {symbol}")
                    self.fallback_stats['alpha_fail'] += 1
                    
        except Exception as e:
            logger.debug(f"Alpha Vantage fetch error for {symbol}: {e}")
            self.fallback_stats['alpha_fail'] += 1

        return None

    async def _fetch_twelve_data_quote(self, symbol: str) -> Optional[Dict]:
        """Fetch quote from Twelve Data (800 calls/day limit)"""
        try:
            if not self.twelve_data_key or self.twelve_data_key.startswith('your_'):
                return None

            if not self._rate_limit_check('twelve_data'):
                logger.debug(f"⏱️ Rate limit hit for Twelve Data - {symbol}")
                self.fallback_stats['twelve_data_fail'] += 1
                return None

            session = await self._get_session()
            url = "https://api.twelvedata.com/quote"
            params = {
                'symbol': symbol.upper(),
                'apikey': self.twelve_data_key
            }

            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    # Valid responses have 'close' field, error responses have 'status': 'error'
                    if 'close' in data and data.get('status') != 'error':
                        self.fallback_stats['twelve_data_success'] += 1
                        return {
                            'symbol': symbol.upper(),
                            'price': float(data.get('close', 0)),
                            'change': float(data.get('change', 0)),
                            'change_percent': data.get('percent_change', '0'),
                            'volume': int(float(data.get('volume', 0))),
                            'source': 'twelve_data'
                        }
                    elif data.get('status') == 'error' or 'message' in data:
                        logger.debug(f"Twelve Data error for {symbol}: {data.get('message', 'Unknown error')}")
                        self.fallback_stats['twelve_data_fail'] += 1
                else:
                    logger.debug(f"Twelve Data API error {response.status} for {symbol}")
                    self.fallback_stats['twelve_data_fail'] += 1

        except Exception as e:
            logger.debug(f"Twelve Data fetch error for {symbol}: {e}")
            self.fallback_stats['twelve_data_fail'] += 1

        return None

    async def _fetch_stooq_quote(self, symbol: str) -> Optional[Dict]:
        """Fetch quote from Stooq (final fallback)"""
        try:
            if not self._rate_limit_check('stooq'):
                logger.debug(f"⏱️ Rate limit hit for Stooq - {symbol}")
                self.fallback_stats['stooq_fail'] += 1
                return None
                
            session = await self._get_session()
            url = f"https://stooq.com/q/l/"
            params = {
                's': f"{symbol.lower()}.us",
                'f': 'sd2t2ohlcv',
                'h': '',
                'e': 'csv'
            }
            
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    text = await response.text()
                    lines = text.strip().split('\n')
                    
                    if len(lines) >= 2:
                        headers = lines[0].split(',')
                        data = lines[1].split(',')
                        
                        if len(data) >= 6 and data[4] != 'N/D':  # Check if close price exists
                            price = float(data[4])  # Close price
                            volume = int(float(data[6])) if data[6] != 'N/D' else 0
                            
                            self.fallback_stats['stooq_success'] += 1
                            return {
                                'symbol': symbol.upper(),
                                'price': price,
                                'volume': volume,
                                'source': 'stooq'
                            }
                
                logger.debug(f"Stooq returned invalid data for {symbol}")
                self.fallback_stats['stooq_fail'] += 1
                    
        except Exception as e:
            logger.debug(f"Stooq fetch error for {symbol}: {e}")
            self.fallback_stats['stooq_fail'] += 1
            
        return None

    async def _fetch_historical_polygon(self, symbol: str, days: int = 60) -> Optional[pd.DataFrame]:
        """Fetch historical data from Polygon.io"""
        try:
            if not self._rate_limit_check('polygon'):
                return None
                
            session = await self._get_session()
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            
            url = f"https://api.polygon.io/v2/aggs/ticker/{symbol.upper()}/range/1/day/{start_date}/{end_date}"
            params = {'apikey': self.polygon_api_key, 'adjusted': 'true', 'sort': 'asc'}
            
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('status') == 'OK' and 'results' in data:
                        df_data = []
                        for result in data['results']:
                            df_data.append({
                                'date': pd.to_datetime(result['t'], unit='ms'),
                                'open': result['o'],
                                'high': result['h'],
                                'low': result['l'],
                                'close': result['c'],
                                'volume': result['v']
                            })
                        
                        if df_data:
                            return pd.DataFrame(df_data).set_index('date')
                            
        except Exception as e:
            logger.debug(f"Polygon.io historical data error for {symbol}: {e}")
            
        return None

    def get_comprehensive_market_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get comprehensive market data with proper async handling"""
        try:
            self.fallback_stats['total_requests'] += 1
            
            # Simple approach: always create new event loop for this operation
            try:
                # Check if there's already a running loop
                loop = asyncio.get_running_loop()
                # If we get here, there's a running loop, so we can't use asyncio.run()
                # Use run_in_executor to run in a separate thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, self._get_comprehensive_async(symbol))
                    return future.result(timeout=30)
            except RuntimeError:
                # No running loop, safe to use asyncio.run()
                return asyncio.run(self._get_comprehensive_async(symbol))
                
        except Exception as e:
            logger.error(f"Error getting comprehensive data for {symbol}: {e}")
            return None

    async def _get_comprehensive_async(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Async method with complete fallback chain: Polygon → Alpha Vantage → Twelve Data → Stooq → yfinance"""
        cache_key = f"{symbol}_{int(time.time() // self.cache_timeout)}"
        
        # Check cache
        if cache_key in self.cache:
            logger.debug(f" Cache hit for {symbol}")
            return self.cache[cache_key]

        try:
            # FALLBACK CHAIN: Try sources in order
            
            # 1. Try Polygon.io first (highest quality)
            polygon_data = await self._fetch_polygon_quote(symbol)
            if polygon_data:
                historical_df = await self._fetch_historical_polygon(symbol, 60)

                result = {
                    'symbol': symbol.upper(),
                    'current_price': polygon_data['price'],
                    'volume': polygon_data['volume'],
                    'data_source': 'Polygon.io',
                    'timestamp': time.time(),
                    'quality_score': 0.95,
                    'indicators': None
                }

                # Calculate price changes and technical indicators if we have historical data
                if historical_df is not None and len(historical_df) > 0:
                    prices = historical_df['close']
                    if len(prices) >= 2:
                        result['price_change_1d'] = (prices.iloc[-1] - prices.iloc[-2]) / prices.iloc[-2] if len(prices) >= 2 else 0.0
                        result['price_change_5d'] = (prices.iloc[-1] - prices.iloc[-6]) / prices.iloc[-6] if len(prices) >= 6 else 0.0
                        result['volatility_5d'] = prices.pct_change().std() * (252 ** 0.5) if len(prices) > 1 else 0.02

                        # Calculate technical indicators
                        result['indicators'] = calculate_all_indicators(prices)
                    else:
                        result['price_change_1d'] = 0.0
                        result['price_change_5d'] = 0.0
                        result['volatility_5d'] = 0.02
                else:
                    result['price_change_1d'] = 0.0
                    result['price_change_5d'] = 0.0
                    result['volatility_5d'] = 0.02

                self.cache[cache_key] = result
                logger.info(f"{symbol}: ${result['current_price']:.2f} via Polygon.io")

                # Log market data fetch
                if ADVANCED_LOGGING:
                    log_market_data(symbol, result, 'Polygon.io', fetch_time_ms=None)

                return result

            # 2. Fallback to Alpha Vantage
            alpha_data = await self._fetch_alpha_vantage_quote(symbol)
            if alpha_data:
                change_percent_str = alpha_data.get('change_percent', '0%')
                change_percent = float(change_percent_str.replace('%', '')) / 100 if change_percent_str != '0%' else 0.0

                result = {
                    'symbol': symbol.upper(),
                    'current_price': alpha_data['price'],
                    'price_change_1d': change_percent,
                    'price_change_5d': change_percent * 3,  # Rough estimate
                    'volume': alpha_data['volume'],
                    'volatility_5d': abs(change_percent) * 5,  # Rough estimate
                    'data_source': 'Alpha Vantage',
                    'timestamp': time.time(),
                    'quality_score': 0.85,
                    'indicators': None  # Alpha Vantage quote doesn't have historical data for indicators
                }

                self.cache[cache_key] = result
                logger.info(f"{symbol}: ${result['current_price']:.2f} via Alpha Vantage")

                # Log market data fetch
                if ADVANCED_LOGGING:
                    log_market_data(symbol, result, 'Alpha Vantage', fetch_time_ms=None)
                return result

            # 3. Fallback to Twelve Data
            twelve_data = await self._fetch_twelve_data_quote(symbol)
            if twelve_data:
                change_percent_str = twelve_data.get('change_percent', '0')
                change_percent = float(str(change_percent_str).replace('%', '')) / 100 if change_percent_str else 0.0

                result = {
                    'symbol': symbol.upper(),
                    'current_price': twelve_data['price'],
                    'price_change_1d': change_percent,
                    'price_change_5d': change_percent * 2.5,  # Rough estimate
                    'volume': twelve_data['volume'],
                    'volatility_5d': abs(change_percent) * 4,  # Rough estimate
                    'data_source': 'Twelve Data',
                    'timestamp': time.time(),
                    'quality_score': 0.80,
                    'indicators': None  # Twelve Data quote doesn't have historical data for indicators
                }

                self.cache[cache_key] = result
                logger.info(f"{symbol}: ${result['current_price']:.2f} via Twelve Data")

                # Log market data fetch
                if ADVANCED_LOGGING:
                    log_market_data(symbol, result, 'Twelve Data', fetch_time_ms=None)
                return result

            # 4. Fallback to Stooq
            stooq_data = await self._fetch_stooq_quote(symbol)
            if stooq_data:
                result = {
                    'symbol': symbol.upper(),
                    'current_price': stooq_data['price'],
                    'price_change_1d': 0.0,  # Stooq doesn't provide change directly
                    'price_change_5d': 0.0,  # Estimate only
                    'volume': stooq_data['volume'],
                    'volatility_5d': 0.02,  # Default estimate
                    'data_source': 'Stooq',
                    'timestamp': time.time(),
                    'quality_score': 0.70,
                    'indicators': None  # Stooq quote doesn't have historical data for indicators
                }

                self.cache[cache_key] = result
                logger.info(f"{symbol}: ${result['current_price']:.2f} via Stooq")

                # Log market data fetch
                if ADVANCED_LOGGING:
                    log_market_data(symbol, result, 'Stooq', fetch_time_ms=None)
                return result

            # 5. Final fallback to yfinance (no rate limits, but slower)
            try:
                import yfinance as yf
                ticker = yf.Ticker(symbol)
                info = ticker.info

                if info and 'currentPrice' in info:
                    current_price = info.get('currentPrice') or info.get('regularMarketPrice', 0)
                    if current_price > 0:
                        result = {
                            'symbol': symbol.upper(),
                            'current_price': current_price,
                            'price_change_1d': info.get('regularMarketChangePercent', 0) / 100 if info.get('regularMarketChangePercent') else 0,
                            'price_change_5d': 0.0,
                            'volume': info.get('volume', 0),
                            'volatility_5d': 0.02,
                            'data_source': 'yfinance',
                            'timestamp': time.time(),
                            'quality_score': 0.60,
                            'indicators': None
                        }
                        self.cache[cache_key] = result
                        logger.info(f"{symbol}: ${result['current_price']:.2f} via yfinance")

                        # Log market data fetch
                        if ADVANCED_LOGGING:
                            log_market_data(symbol, result, 'yfinance', fetch_time_ms=None)
                        return result
            except Exception as e:
                logger.debug(f"yfinance fetch error for {symbol}: {e}")

            # If all sources fail
            logger.error(f"ALL DATA SOURCES FAILED for {symbol}")
            return None
            
        finally:
            # Always clean up session after each request to prevent unclosed session warnings
            if self.session and not self.session.closed:
                await self.session.close()
                self.session = None

    def get_fallback_statistics(self) -> Dict[str, Any]:
        """Get detailed statistics about fallback usage"""
        total = self.fallback_stats['total_requests']
        if total == 0:
            return {'message': 'No requests made yet'}
            
        return {
            'total_requests': total,
            'polygon': {
                'success': self.fallback_stats['polygon_success'],
                'fail': self.fallback_stats['polygon_fail'],
                'success_rate': self.fallback_stats['polygon_success'] / max(1, self.fallback_stats['polygon_success'] + self.fallback_stats['polygon_fail']) * 100
            },
            'alpha_vantage': {
                'success': self.fallback_stats['alpha_success'],
                'fail': self.fallback_stats['alpha_fail'],
                'success_rate': self.fallback_stats['alpha_success'] / max(1, self.fallback_stats['alpha_success'] + self.fallback_stats['alpha_fail']) * 100
            },
            'stooq': {
                'success': self.fallback_stats['stooq_success'],
                'fail': self.fallback_stats['stooq_fail'],
                'success_rate': self.fallback_stats['stooq_success'] / max(1, self.fallback_stats['stooq_success'] + self.fallback_stats['stooq_fail']) * 100
            }
        }

    async def close(self):
        """Close the session"""
        if self.session and not self.session.closed:
            await self.session.close()

    def __del__(self):
        """Cleanup on deletion"""
        if hasattr(self, 'session') and self.session and not self.session.closed:
            try:
                # Try to close gracefully
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.session.close())
                loop.close()
            except Exception as e:
                logger.warning(f"Failed to close async session cleanly: {e}")
                # Continue - cleanup failure is not critical

# Legacy compatibility - keep the old interface working
class EnhancedDataFetcher:
    """Legacy wrapper for backward compatibility"""

    def __init__(self):
        self.professional_fetcher = ProfessionalDataFetcher()
        # Add caching structure for backward compatibility
        self.cache = self.professional_fetcher.cache
        self._cache = self.professional_fetcher.cache  # For test compatibility
        self.last_fetched = {}

        # Expose API keys for universe manager
        self.polygon_api_key = self.professional_fetcher.polygon_api_key
        self.alpha_vantage_key = self.professional_fetcher.alpha_vantage_key
        self.twelve_data_key = self.professional_fetcher.twelve_data_key

        logger.info("Enhanced data sources initialized with professional APIs + robust fallbacks")

    def get_comprehensive_market_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get market data - legacy interface"""
        return self.professional_fetcher.get_comprehensive_market_data(symbol)

    def _rate_limit_check(self, api_name: str) -> bool:
        """Check rate limit for given API"""
        return self.professional_fetcher._rate_limit_check(api_name)

    async def _get_session(self):
        """Get aiohttp session"""
        return await self.professional_fetcher._get_session()
    
    def get_fallback_statistics(self) -> Dict[str, Any]:
        """Get fallback usage statistics"""
        return self.professional_fetcher.get_fallback_statistics()
    
    def fetch_yfinance_enhanced(self, symbol: str, period: str = "1y") -> DataFetchResult:
        """Legacy method for backward compatibility"""
        try:
            data = self.get_comprehensive_market_data(symbol)
            if data:
                # Create a simple DataFrame from the data
                df = pd.DataFrame([{
                    'Close': data['current_price'],
                    'Volume': data.get('volume', 0),
                    'Date': datetime.now()
                }])
                return DataFetchResult(
                    df=df,
                    source=data['data_source'],
                    success=True,
                    ticker=symbol,
                    timestamp=datetime.now(),
                    quality_score=data.get('quality_score', 1.0)
                )
            else:
                return DataFetchResult(
                    df=pd.DataFrame(),
                    source="failed",
                    success=False,
                    ticker=symbol,
                    timestamp=datetime.now(),
                    quality_score=0.0
                )
        except Exception as e:
            logger.error(f"fetch_yfinance_enhanced error for {symbol}: {e}")
            return DataFetchResult(
                df=pd.DataFrame(),
                source="error",
                success=False,
                ticker=symbol,
                timestamp=datetime.now(),
                quality_score=0.0
            )
    
    def fetch_stooq(self, symbol: str) -> DataFetchResult:
        """Legacy Stooq fetch method for backward compatibility"""
        try:
            # Use the professional fetcher's Stooq method
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            stooq_data = loop.run_until_complete(self.professional_fetcher._fetch_stooq_quote(symbol))
            loop.close()
            
            if stooq_data:
                df = pd.DataFrame([{
                    'Close': stooq_data['price'],
                    'Volume': stooq_data.get('volume', 0),
                    'Date': datetime.now()
                }])
                return DataFetchResult(
                    df=df,
                    source="stooq", 
                    success=True,
                    ticker=symbol,
                    timestamp=datetime.now(),
                    quality_score=0.70
                )
            else:
                return DataFetchResult(
                    df=pd.DataFrame(),
                    source="stooq",
                    success=False,
                    ticker=symbol,
                    timestamp=datetime.now(),
                    quality_score=0.0
                )
        except Exception as e:
            logger.error(f"fetch_stooq error for {symbol}: {e}")
            return DataFetchResult(
                df=pd.DataFrame(),
                source="stooq_error",
                success=False,
                ticker=symbol,
                timestamp=datetime.now(),
                quality_score=0.0
            )

# Global instance
_enhanced_fetcher = None

def get_data_fetcher() -> EnhancedDataFetcher:
    """Get global enhanced data fetcher instance"""
    global _enhanced_fetcher
    if _enhanced_fetcher is None:
        _enhanced_fetcher = EnhancedDataFetcher()
    return _enhanced_fetcher

# Test function
if __name__ == "__main__":
    print(" Testing Professional Data Sources with Full Fallback Chain")
    print("=" * 70)
    
    fetcher = ProfessionalDataFetcher()
    
    # Test symbols
    test_symbols = ['AAPL', 'MSFT', 'GOOGL', 'INVALID']
    
    async def test_all():
        for symbol in test_symbols:
            print(f"\nTesting {symbol}...")
            try:
                data = await fetcher._get_comprehensive_async(symbol)
                if data:
                    print(f"{symbol}: ${data['current_price']:.2f} from {data['data_source']} (Quality: {data['quality_score']:.2f})")
                    print(f"   Change 1D: {data.get('price_change_1d', 0):+.2%}, Volume: {data.get('volume', 0):,}")
                else:
                    print(f"No data for {symbol}")
            except Exception as e:
                print(f"Error testing {symbol}: {e}")
        
        # Show fallback statistics
        print(f"\nFallback Statistics:")
        stats = fetcher.get_fallback_statistics()
        for source, data in stats.items():
            if isinstance(data, dict) and 'success_rate' in data:
                print(f"   {source.title()}: {data['success_rate']:.1f}% success rate ({data['success']}/{data['success'] + data['fail']})")
        
        await fetcher.close()
    
    asyncio.run(test_all())