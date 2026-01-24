#!/usr/bin/env python3
"""
News & Sentiment Analysis Provider
Integrates Finnhub and Alpha Vantage for real-time news sentiment
"""

import os
import time
import logging
import asyncio
import aiohttp
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class NewsSentiment:
    """Sentiment data for a ticker"""
    ticker: str
    overall_sentiment: float  # -1 (bearish) to +1 (bullish)
    sentiment_label: str  # 'bearish', 'neutral', 'bullish'
    news_count: int
    buzz_score: float  # 0-1, how much attention
    headlines: List[str]
    sources: List[str]
    timestamp: datetime


class NewsSentimentProvider:
    """
    Fetches and analyzes news sentiment from multiple sources:
    - Finnhub (primary): Company news and sentiment scores
    - Alpha Vantage (fallback): News sentiment API
    """

    def __init__(self):
        # API Keys from environment
        self.finnhub_key = os.getenv('FINNHUB_API_KEY', 'd5e3ns1r01qjckl1ls60')
        self.alpha_vantage_key = os.getenv('ALPHA_VANTAGE_KEY', 'OENJH9RCUTK74IMD')

        # Rate limiting (requests per minute)
        self.finnhub_requests = deque(maxlen=60)  # 60 calls/min free tier
        self.alpha_requests = deque(maxlen=5)  # 5 calls/min free tier

        # Cache
        self.cache: Dict[str, NewsSentiment] = {}
        self.cache_ttl = 300  # 5 minutes

        # Session
        self.session: Optional[aiohttp.ClientSession] = None

        logger.info("NewsSentimentProvider initialized")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=15, connect=5)
            self.session = aiohttp.ClientSession(timeout=timeout)
        return self.session

    def _rate_limit_check(self, api: str) -> bool:
        """Check if we're within rate limits"""
        current_time = time.time()

        if api == 'finnhub':
            # Clean old requests
            while self.finnhub_requests and current_time - self.finnhub_requests[0] > 60:
                self.finnhub_requests.popleft()
            if len(self.finnhub_requests) >= 55:  # Buffer of 5
                return False
            self.finnhub_requests.append(current_time)

        elif api == 'alpha_vantage':
            while self.alpha_requests and current_time - self.alpha_requests[0] > 60:
                self.alpha_requests.popleft()
            if len(self.alpha_requests) >= 4:  # Buffer of 1
                return False
            self.alpha_requests.append(current_time)

        return True

    async def _fetch_finnhub_news(self, ticker: str) -> Optional[List[Dict]]:
        """Fetch company news from Finnhub"""
        if not self._rate_limit_check('finnhub'):
            logger.debug(f"Finnhub rate limit hit for {ticker}")
            return None

        try:
            session = await self._get_session()
            # Get news from last 7 days
            today = datetime.now()
            week_ago = today - timedelta(days=7)

            url = "https://finnhub.io/api/v1/company-news"
            params = {
                'symbol': ticker.upper(),
                'from': week_ago.strftime('%Y-%m-%d'),
                'to': today.strftime('%Y-%m-%d'),
                'token': self.finnhub_key
            }

            async with session.get(url, params=params) as response:
                if response.status == 200:
                    news = await response.json()
                    if news:
                        logger.debug(f"Finnhub: Got {len(news)} articles for {ticker}")
                        return news[:20]  # Limit to 20 most recent
                elif response.status == 429:
                    logger.warning(f"Finnhub rate limited for {ticker}")
                else:
                    logger.debug(f"Finnhub error {response.status} for {ticker}")

        except Exception as e:
            logger.error(f"Finnhub fetch error for {ticker}: {e}")

        return None

    async def _fetch_finnhub_sentiment(self, ticker: str) -> Optional[Dict]:
        """Fetch sentiment indicators from Finnhub"""
        if not self._rate_limit_check('finnhub'):
            return None

        try:
            session = await self._get_session()
            url = "https://finnhub.io/api/v1/news-sentiment"
            params = {
                'symbol': ticker.upper(),
                'token': self.finnhub_key
            }

            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data and 'sentiment' in data:
                        return data
                elif response.status == 429:
                    logger.warning(f"Finnhub sentiment rate limited for {ticker}")

        except Exception as e:
            logger.debug(f"Finnhub sentiment error for {ticker}: {e}")

        return None

    async def _fetch_alpha_vantage_sentiment(self, ticker: str) -> Optional[Dict]:
        """Fetch news sentiment from Alpha Vantage (fallback)"""
        if not self._rate_limit_check('alpha_vantage'):
            logger.debug(f"Alpha Vantage rate limit hit for {ticker}")
            return None

        try:
            session = await self._get_session()
            url = "https://www.alphavantage.co/query"
            params = {
                'function': 'NEWS_SENTIMENT',
                'tickers': ticker.upper(),
                'apikey': self.alpha_vantage_key,
                'limit': 20
            }

            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'feed' in data:
                        logger.debug(f"Alpha Vantage: Got {len(data['feed'])} articles for {ticker}")
                        return data
                    elif 'Note' in data or 'Information' in data:
                        logger.warning(f"Alpha Vantage rate limited for {ticker}")

        except Exception as e:
            logger.debug(f"Alpha Vantage error for {ticker}: {e}")

        return None

    def _analyze_finnhub_sentiment(self, news: List[Dict], sentiment_data: Optional[Dict]) -> NewsSentiment:
        """Analyze Finnhub news and sentiment data"""
        if not news:
            return NewsSentiment(
                ticker='',
                overall_sentiment=0.0,
                sentiment_label='neutral',
                news_count=0,
                buzz_score=0.0,
                headlines=[],
                sources=[],
                timestamp=datetime.now()
            )

        # Extract headlines and sources
        headlines = [article.get('headline', '')[:100] for article in news[:5]]
        sources = list(set(article.get('source', '') for article in news))[:5]

        # Calculate sentiment from Finnhub sentiment API if available
        if sentiment_data and 'sentiment' in sentiment_data:
            sent = sentiment_data['sentiment']
            bullish = sent.get('bullishPercent', 0.5)
            bearish = sent.get('bearishPercent', 0.5)
            overall = (bullish - bearish)  # -1 to +1 range

            buzz = sentiment_data.get('buzz', {})
            buzz_score = min(1.0, buzz.get('articlesInLastWeek', 0) / 50)
        else:
            # Simple heuristic based on news volume and recency
            recent_count = len([n for n in news if
                                datetime.fromtimestamp(n.get('datetime', 0)) >
                                datetime.now() - timedelta(days=2)])
            overall = 0.0  # Neutral without explicit sentiment
            buzz_score = min(1.0, recent_count / 10)

        # Determine sentiment label
        if overall > 0.15:
            label = 'bullish'
        elif overall < -0.15:
            label = 'bearish'
        else:
            label = 'neutral'

        return NewsSentiment(
            ticker=news[0].get('symbol', '') if news else '',
            overall_sentiment=overall,
            sentiment_label=label,
            news_count=len(news),
            buzz_score=buzz_score,
            headlines=headlines,
            sources=sources,
            timestamp=datetime.now()
        )

    def _analyze_alpha_vantage_sentiment(self, data: Dict) -> NewsSentiment:
        """Analyze Alpha Vantage sentiment data"""
        feed = data.get('feed', [])

        if not feed:
            return NewsSentiment(
                ticker='',
                overall_sentiment=0.0,
                sentiment_label='neutral',
                news_count=0,
                buzz_score=0.0,
                headlines=[],
                sources=[],
                timestamp=datetime.now()
            )

        # Calculate average sentiment from articles
        sentiments = []
        for article in feed:
            ticker_sentiment = article.get('ticker_sentiment', [])
            if ticker_sentiment:
                for ts in ticker_sentiment:
                    score = float(ts.get('ticker_sentiment_score', 0))
                    sentiments.append(score)

        overall = sum(sentiments) / len(sentiments) if sentiments else 0.0

        # Extract headlines and sources
        headlines = [article.get('title', '')[:100] for article in feed[:5]]
        sources = list(set(article.get('source', '') for article in feed))[:5]

        # Buzz score based on article count
        buzz_score = min(1.0, len(feed) / 20)

        # Determine label
        if overall > 0.15:
            label = 'bullish'
        elif overall < -0.15:
            label = 'bearish'
        else:
            label = 'neutral'

        return NewsSentiment(
            ticker=feed[0].get('tickers', [''])[0] if feed else '',
            overall_sentiment=overall,
            sentiment_label=label,
            news_count=len(feed),
            buzz_score=buzz_score,
            headlines=headlines,
            sources=sources,
            timestamp=datetime.now()
        )

    async def get_sentiment(self, ticker: str) -> Optional[NewsSentiment]:
        """Get news sentiment for a ticker (with caching and fallback)"""
        # Check cache
        cache_key = ticker.upper()
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if (datetime.now() - cached.timestamp).seconds < self.cache_ttl:
                return cached

        try:
            # Try Finnhub first (higher quality)
            news = await self._fetch_finnhub_news(ticker)
            if news:
                sentiment_data = await self._fetch_finnhub_sentiment(ticker)
                result = self._analyze_finnhub_sentiment(news, sentiment_data)
                result.ticker = ticker.upper()
                self.cache[cache_key] = result
                return result

            # Fallback to Alpha Vantage
            av_data = await self._fetch_alpha_vantage_sentiment(ticker)
            if av_data:
                result = self._analyze_alpha_vantage_sentiment(av_data)
                result.ticker = ticker.upper()
                self.cache[cache_key] = result
                return result

        except Exception as e:
            logger.error(f"Error getting sentiment for {ticker}: {e}")

        return None

    def get_sentiment_sync(self, ticker: str) -> Optional[NewsSentiment]:
        """Synchronous wrapper for get_sentiment"""
        try:
            try:
                loop = asyncio.get_running_loop()
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, self.get_sentiment(ticker))
                    return future.result(timeout=20)
            except RuntimeError:
                return asyncio.run(self.get_sentiment(ticker))
        except Exception as e:
            logger.error(f"Sync sentiment error for {ticker}: {e}")
            return None

    async def get_batch_sentiment(self, tickers: List[str], max_concurrent: int = 5) -> Dict[str, NewsSentiment]:
        """Get sentiment for multiple tickers efficiently"""
        results = {}

        # Process in batches
        for i in range(0, len(tickers), max_concurrent):
            batch = tickers[i:i + max_concurrent]
            tasks = [self.get_sentiment(ticker) for ticker in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for ticker, result in zip(batch, batch_results):
                if isinstance(result, NewsSentiment):
                    results[ticker] = result

            # Small delay between batches to respect rate limits
            if i + max_concurrent < len(tickers):
                await asyncio.sleep(1)

        return results

    def format_for_ai(self, sentiment: NewsSentiment) -> str:
        """Format sentiment data for AI context"""
        if not sentiment:
            return "No sentiment data available"

        lines = [
            f"NEWS SENTIMENT for {sentiment.ticker}:",
            f"  Overall: {sentiment.sentiment_label.upper()} ({sentiment.overall_sentiment:+.2f})",
            f"  Buzz Score: {sentiment.buzz_score:.2f} (news activity level)",
            f"  Articles: {sentiment.news_count} in last 7 days"
        ]

        if sentiment.headlines:
            lines.append("  Recent Headlines:")
            for headline in sentiment.headlines[:3]:
                lines.append(f"    - {headline[:80]}...")

        return "\n".join(lines)

    async def close(self):
        """Close the session"""
        if self.session and not self.session.closed:
            await self.session.close()


# Global instance
_sentiment_provider: Optional[NewsSentimentProvider] = None


def get_sentiment_provider() -> NewsSentimentProvider:
    """Get global sentiment provider instance"""
    global _sentiment_provider
    if _sentiment_provider is None:
        _sentiment_provider = NewsSentimentProvider()
    return _sentiment_provider


# Test function
if __name__ == "__main__":
    print("Testing News Sentiment Provider")
    print("=" * 60)

    provider = NewsSentimentProvider()

    async def test():
        test_tickers = ['AAPL', 'TSLA', 'NVDA']

        for ticker in test_tickers:
            print(f"\nFetching sentiment for {ticker}...")
            sentiment = await provider.get_sentiment(ticker)

            if sentiment:
                print(f"  Sentiment: {sentiment.sentiment_label} ({sentiment.overall_sentiment:+.2f})")
                print(f"  Buzz: {sentiment.buzz_score:.2f}")
                print(f"  Articles: {sentiment.news_count}")
                if sentiment.headlines:
                    print(f"  Top headline: {sentiment.headlines[0][:60]}...")
            else:
                print(f"  No sentiment data available")

        await provider.close()

    asyncio.run(test())
