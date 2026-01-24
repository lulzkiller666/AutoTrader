#!/usr/bin/env python3
"""
Dynamic Market Intelligence Engine
Automatically discovers most traded stocks and monitors real market signals
"""

import sys
from pathlib import Path
# Ensure core_v2 modules can be imported
current_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(current_dir))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple
import logging
import requests
import json
import asyncio
import time

# Import advanced logging for opportunity tracking
try:
    from advanced_logging import log_system
    ADVANCED_LOGGING = True
except ImportError:
    ADVANCED_LOGGING = False
    def log_system(*args, **kwargs): pass

try:
    from .dynamic_universe_manager import DynamicUniverseManager
except ImportError:
    from dynamic_universe_manager import DynamicUniverseManager

class DynamicMarketIntelligence:
    """Real-time market intelligence with dynamic stock discovery"""
    
    def __init__(self, enhanced_data_fetcher):
        self.data_fetcher = enhanced_data_fetcher
        self.logger = logging.getLogger(__name__)

        # Initialize Dynamic Universe Manager
        cache_dir = Path("./automated_trading/universe_cache")
        cache_dir.mkdir(parents=True, exist_ok=True)

        self.universe_manager = DynamicUniverseManager(
            data_fetcher=enhanced_data_fetcher,
            cache_dir=cache_dir
        )

        # Load cached universe (fast startup)
        self.universe_manager.load_universe()

        # Legacy attributes (maintained for backward compatibility)
        self.most_traded_stocks = []
        self.last_universe_update = None
        self.universe_size = 505  # Full S&P 500 + key ETFs

        # Market state tracking
        self.market_state = self._check_market_state()

        self.logger.info(f"Dynamic Market Intelligence initialized with {len(self.universe_manager.universe)} stocks")
    
    def _check_market_state(self) -> Dict[str, Any]:
        """Check current market state and trading hours"""
        import pytz
        
        et = pytz.timezone('US/Eastern')
        now = datetime.now(et)
        
        # Market hours: 9:30 AM - 4:00 PM ET, Monday-Friday
        is_weekday = now.weekday() < 5  # 0-4 = Monday-Friday
        is_trading_hours = 9.5 <= now.hour + now.minute/60 <= 16.0
        is_market_open = is_weekday and is_trading_hours
        
        # Extended hours
        is_premarket = is_weekday and 4.0 <= now.hour + now.minute/60 < 9.5
        is_aftermarket = is_weekday and 16.0 < now.hour + now.minute/60 <= 20.0
        
        state = {
            'current_time': now,
            'is_market_open': is_market_open,
            'is_premarket': is_premarket, 
            'is_aftermarket': is_aftermarket,
            'is_weekend': not is_weekday,
            'market_phase': self._get_market_phase(now, is_market_open, is_premarket, is_aftermarket),
            'next_open': self._get_next_market_open(now),
            'time_to_open': self._get_time_to_open(now)
        }
        
        return state
    
    def _get_market_phase(self, now, is_open, is_pre, is_after) -> str:
        """Get descriptive market phase"""
        if is_open:
            hour = now.hour + now.minute/60
            if hour < 10.5:
                return "MARKET_OPENING"
            elif hour < 12:
                return "MARKET_MID_MORNING"  
            elif hour < 14:
                return "MARKET_LUNCH"
            elif hour < 16:
                return "MARKET_AFTERNOON"
        elif is_pre:
            return "PRE_MARKET"
        elif is_after:
            return "AFTER_MARKET"
        elif now.weekday() >= 5:
            return "WEEKEND"
        else:
            return "OVERNIGHT"
            
        return "CLOSED"
    
    def _get_next_market_open(self, now) -> datetime:
        """Get next market open time"""
        import pytz
        et = pytz.timezone('US/Eastern')
        
        # Next 9:30 AM ET
        next_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        
        # If it's past 9:30 AM today, go to next trading day
        if now.hour >= 9 and now.minute >= 30:
            next_open += timedelta(days=1)
        
        # Skip weekends
        while next_open.weekday() >= 5:  # Saturday or Sunday
            next_open += timedelta(days=1)
            
        return next_open
    
    def _get_time_to_open(self, now) -> str:
        """Get human-readable time until market open"""
        next_open = self._get_next_market_open(now)
        delta = next_open - now
        
        if delta.days > 0:
            return f"{delta.days} days, {delta.seconds//3600} hours"
        elif delta.seconds > 3600:
            return f"{delta.seconds//3600} hours, {(delta.seconds%3600)//60} minutes"
        else:
            return f"{delta.seconds//60} minutes"
    
    def get_most_traded_stocks(self, refresh_hours: int = 1) -> List[str]:
        """
        Get dynamic stock universe (refreshes hourly during market hours)

        Changed from 24h to 1h refresh to catch trending stocks quickly.
        Uses DynamicUniverseManager for:
        - S&P 500 (503 stocks, fetched from Wikipedia)
        - yfinance screeners (gainers, losers, actives - 700+ stocks)
        - Polygon top movers (40 stocks)
        - Automatic expiration of stale stocks (24h)

        Returns:
            List of ticker symbols (900-1200 stocks)
        """
        # Check if hourly refresh is needed
        if self.universe_manager.should_refresh():
            self.logger.info("Triggering hourly universe refresh...")
            asyncio.run(self.universe_manager.refresh_universe())

        # Return full dynamic universe
        return list(self.universe_manager.universe.keys())

    def get_universe_stats_for_dashboard(self) -> Dict[str, Any]:
        """
        Get universe statistics formatted for dashboard display

        Returns:
            Dict with total_stocks, tier1_count, tier2_count, tier3_count, last_refresh
        """
        return self.universe_manager.get_universe_stats()
    
    def screen_for_opportunities(self, max_opportunities: int = 25) -> List[Dict[str, Any]]:
        """
        Intelligent opportunity screening using DynamicUniverseManager tiering

        - 50% Tier 1 (hot stocks with |change| >= 5%)
        - 25% Tier 2 (active stocks with volume >= 0.5 or |change| >= 2%)
        - 25% Tier 3 (core S&P 500 stocks)

        Hot stocks screened every 20 min, core stocks every 80-100 min.

        Args:
            max_opportunities: Maximum stocks to screen (default: 25)

        Returns:
            List of opportunity dicts with scoring
        """
        try:
            self.logger.info(f"Screening {len(self.get_most_traded_stocks())} stock universe...")

            opportunities = []

            # Use intelligent rotation (replaces random.sample)
            sample_stocks = self.universe_manager.get_next_batch_for_screening(max_opportunities)

            self.logger.info(f"Selected {len(sample_stocks)} stocks using intelligent rotation")

            for ticker in sample_stocks:
                try:
                    data = self.data_fetcher.get_comprehensive_market_data(ticker)

                    if data and data.get('current_price'):
                        # Update universe metadata with fresh market data
                        self.universe_manager.update_stock_metadata(ticker, {
                            'change_1d': data.get('price_change_1d', 0.0),
                            'change_5d': data.get('price_change_5d', 0.0),
                            'volume': data.get('volume', 0),
                            'avg_volume': data.get('volume', 1)  # Use volume as fallback since avg_volume not in data
                        })
                        # Calculate opportunity score based on multiple factors
                        change_1d = data.get('price_change_1d') or 0.0
                        change_5d = data.get('price_change_5d') or 0.0
                        volatility = data.get('volatility_5d') or 0.0
                        volume = data.get('volume_average') or data.get('volume') or 0
                        indicators = data.get('indicators')

                        # Multi-factor opportunity scoring (enhanced with technical indicators)
                        # CHANGED: Favor UP momentum for BUY opportunities, but also consider oversold bounces
                        if change_1d > 0:
                            # UP stocks get full positive score (BUY opportunities)
                            momentum_score = change_1d * 15  # 1% up = 0.15 score (boosted)
                        elif change_1d < -0.02:
                            # DOWN >2% could be oversold bounce opportunity (if RSI confirms)
                            momentum_score = abs(change_1d) * 3  # Reduced score, needs technical confirmation
                        else:
                            # Small down moves or flat = low priority
                            momentum_score = abs(change_1d) * 2

                        trend_score = change_5d * 5 if change_1d * change_5d > 0 else 0  # Trending in same direction
                        volume_score = 1 if volume > 1000000 else 0.5  # High volume bonus
                        volatility_penalty = volatility * 2 if volatility > 0.05 else 0  # Penalize excessive volatility

                        # Technical indicator scoring (NEW)
                        technical_score = 0.0
                        technical_signals = []

                        if indicators:
                            # RSI signals
                            rsi = indicators.get('rsi')
                            if rsi is not None:
                                if rsi < 30:
                                    technical_score += 1.5  # BOOSTED: Oversold = strong buying opportunity
                                    technical_signals.append('RSI_OVERSOLD')
                                elif rsi < 40:
                                    technical_score += 0.5  # Approaching oversold
                                    technical_signals.append('RSI_LOW')
                                elif rsi > 70:
                                    technical_score += 0.3  # Overbought = selling opportunity
                                    technical_signals.append('RSI_OVERBOUGHT')

                            # MACD signals
                            macd = indicators.get('macd')
                            if macd:
                                if macd.get('crossover') == 'bullish':
                                    technical_score += 1.5  # BOOSTED: Bullish crossover = strong BUY signal
                                    technical_signals.append('MACD_BULLISH_CROSS')
                                elif macd.get('crossover') == 'bearish':
                                    technical_score += 0.4
                                    technical_signals.append('MACD_BEARISH_CROSS')

                            # Bollinger Band signals
                            bb = indicators.get('bollinger')
                            if bb:
                                if bb.get('signal') == 'oversold':
                                    technical_score += 1.0  # BOOSTED: Oversold bounce opportunity
                                    technical_signals.append('BB_OVERSOLD')
                                elif bb.get('signal') == 'overbought':
                                    technical_score += 0.2
                                    technical_signals.append('BB_OVERBOUGHT')

                            # Moving average trend
                            ma = indicators.get('moving_averages')
                            if ma:
                                if ma.get('golden_cross'):
                                    technical_score += 2.0  # BOOSTED: Golden cross = very strong BUY signal
                                    technical_signals.append('GOLDEN_CROSS')
                                elif ma.get('death_cross'):
                                    technical_score += 0.3
                                    technical_signals.append('DEATH_CROSS')

                        opportunity_score = (
                            momentum_score * 0.20 +  # Reduced slightly
                            trend_score * 0.15 +      # Reduced slightly
                            volume_score * 0.10 +
                            technical_score * 0.40 -  # BOOSTED: Technical BUY signals now heavily weighted
                            volatility_penalty * 0.15
                        )

                        # Only include if there's some movement or opportunity
                        if opportunity_score > 0.05 or abs(change_1d) > 0.01 or technical_signals:
                            opportunities.append({
                                'ticker': ticker,
                                'opportunity_score': opportunity_score,
                                'price': data['current_price'],
                                'change_1d': change_1d,
                                'change_5d': change_5d,
                                'volatility': volatility,
                                'volume': volume,
                                'trend_direction': 'UP' if change_1d > 0 else 'DOWN',
                                'technical_signals': technical_signals,
                                'indicators': indicators
                            })

                except Exception as e:
                    continue
                    
                # Small delay to not overwhelm Stooq
                import time
                time.sleep(0.05)
            
            # Sort by opportunity score
            opportunities.sort(key=lambda x: x['opportunity_score'], reverse=True)
            
            # Return top opportunities
            top_opportunities = opportunities[:max_opportunities]

            self.logger.info(f"Found {len(top_opportunities)} opportunities from screening")

            # Log top 5 for visibility
            for i, opp in enumerate(top_opportunities[:5]):
                direction = "[UP]" if opp['trend_direction'] == 'UP' else "[DOWN]"
                self.logger.info(f"{direction} #{i+1}: {opp['ticker']} - ${opp['price']:.2f} ({opp['change_1d']:+.2%}) Score: {opp['opportunity_score']:.3f}")

            # Log detailed screening results for optimization
            if ADVANCED_LOGGING:
                # Collect all technical signals found
                all_signals = []
                for opp in top_opportunities:
                    all_signals.extend(opp.get('technical_signals', []))

                signal_counts = {}
                for signal in all_signals:
                    signal_counts[signal] = signal_counts.get(signal, 0) + 1

                log_system("info", "Opportunity screening complete", {
                    "event": "opportunity_screening",
                    "stocks_screened": len(sample_stocks),
                    "opportunities_found": len(top_opportunities),
                    "top_5_tickers": [opp['ticker'] for opp in top_opportunities[:5]],
                    "top_5_scores": [round(opp['opportunity_score'], 3) for opp in top_opportunities[:5]],
                    "technical_signals": signal_counts,
                    "up_count": sum(1 for opp in top_opportunities if opp['change_1d'] > 0),
                    "down_count": sum(1 for opp in top_opportunities if opp['change_1d'] < 0),
                    "avg_score": round(sum(opp['opportunity_score'] for opp in top_opportunities) / len(top_opportunities), 3) if top_opportunities else 0,
                    "timestamp": datetime.now().isoformat()
                })

            return top_opportunities
            
        except Exception as e:
            self.logger.error(f"Error screening opportunities: {e}")
            return []
    
    def get_market_context_with_intelligence(self) -> Dict[str, Any]:
        """Get comprehensive market context with intelligence"""
        
        # Update market state
        self.market_state = self._check_market_state()
        
        # Screen for opportunities 
        opportunities = self.screen_for_opportunities(25)
        
        context = {
            'market_state': self.market_state,
            'opportunities': opportunities,
            'universe_size': len(self.get_most_traded_stocks()),
            'screening_timestamp': datetime.now().isoformat()
        }
        
        return context
    
    def format_for_gpt5(self) -> str:
        """Format market intelligence for GPT-5"""
        
        intel = self.get_market_context_with_intelligence()
        market_state = intel['market_state']
        opportunities = intel['opportunities']
        
        # Market status header
        phase = market_state['market_phase']
        time_info = market_state['current_time'].strftime('%Y-%m-%d %H:%M:%S %Z')
        
        if market_state['is_market_open']:
            status_header = f" MARKET OPEN - {phase} at {time_info}"
        else:
            next_open = market_state['time_to_open']
            status_header = f" MARKET CLOSED - {phase} (Opens in {next_open})"
        
        # Format opportunities with technical signals
        if opportunities:
            opp_lines = []
            for i, opp in enumerate(opportunities[:15]):  # Top 15 opportunities
                direction = "[HOT]" if opp['change_1d'] > 0.03 else "[UP]" if opp['change_1d'] > 0 else "[DN]"
                signals = opp.get('technical_signals', [])
                signal_str = f" SIGNALS: {', '.join(signals)}" if signals else ""

                opp_lines.append(
                    f"{direction} {opp['ticker']}: ${opp['price']:.2f} "
                    f"(1D: {opp['change_1d']:+.2%}, 5D: {opp['change_5d']:+.2%}, "
                    f"Score: {opp['opportunity_score']:.3f}){signal_str}"
                )

            opportunities_text = "\n".join(opp_lines)
            
            # Market sentiment
            up_count = len([o for o in opportunities if o['change_1d'] > 0])
            down_count = len([o for o in opportunities if o['change_1d'] < 0])
            
            sentiment = f"\nSENTIMENT: {up_count} rising, {down_count} falling from top opportunities"
        else:
            opportunities_text = "No significant opportunities detected"
            sentiment = ""
        
        formatted = f"""
{status_header}
DYNAMIC MARKET INTELLIGENCE:
• Universe: {intel['universe_size']} most traded stocks
• Screened: {len(opportunities)} opportunities found
• Data Source: Stooq (reliable)

TOP MARKET OPPORTUNITIES:
{opportunities_text}{sentiment}

AUTONOMOUS TRADING MODE:
• Discover profitable trades from ANY of the top traded stocks
• Monitor volatility, momentum, and volume signals continuously  
• Adapt strategy based on market timing ({market_state['market_phase']})
• Execute immediately if market open, prepare if closed
"""
        
        return formatted

# Test the intelligence system
if __name__ == "__main__":
    from enhanced_data_sources import get_data_fetcher
    
    print("TESTING DYNAMIC MARKET INTELLIGENCE")
    print("=" * 60)
    
    fetcher = get_data_fetcher()
    intelligence = DynamicMarketIntelligence(fetcher)
    
    # Test market state detection
    state = intelligence.market_state
    print(f" Current Time: {state['current_time'].strftime('%H:%M ET')}")
    print(f" Market Phase: {state['market_phase']}")
    print(f"Market Open: {state['is_market_open']}")
    if not state['is_market_open']:
        print(f"Next Open: {state['time_to_open']}")
    
    # Test opportunity screening
    print(f"\nTesting opportunity screening...")
    opportunities = intelligence.screen_for_opportunities(10)
    
    if opportunities:
        print(f"Found {len(opportunities)} opportunities")
        for i, opp in enumerate(opportunities[:5]):
            print(f"   {i+1}. {opp['ticker']}: ${opp['price']:.2f} ({opp['change_1d']:+.2%}) Score: {opp['opportunity_score']:.3f}")
    
    # Test GPT-5 formatting
    print(f"\nTesting GPT-5 formatted output...")
    formatted = intelligence.format_for_gpt5()
    print("Formatted market intelligence ready for GPT-5")
    print(f"Length: {len(formatted)} characters")