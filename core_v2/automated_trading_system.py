#!/usr/bin/env python3
"""
Automated Trading System v2 - Improved AI Integration

Improvements over v1:
- Function calling for guaranteed structured JSON output
- Separate system/user messages for cleaner prompts
- External prompt templates
- Enhanced memory context with trade outcomes
- Configurable model via environment variable
- Better error handling and fallbacks
"""

import os
import sys
import json
import logging
import time
import pytz
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum
import pandas as pd

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Add paths
current_dir = Path(__file__).resolve().parent
sys.path.append(str(current_dir))
sys.path.append(str(current_dir.parent / "utils"))

# Import advanced logging
try:
    from advanced_logging import get_logger, log_decision, log_execution, log_api_call, log_error, log_system
    ADVANCED_LOGGING = True
except ImportError:
    ADVANCED_LOGGING = False


class TradingProfile(Enum):
    """Trading profile types"""
    CONSERVATIVE = "conservative"
    AGGRESSIVE = "aggressive"
    ULTRA_AGGRESSIVE = "ultra_aggressive"
    SMART_AGGRESSIVE = "smart_aggressive"  # NEW: Balanced aggressive with safety rails
    CUSTOM = "custom"


class MarketSession(Enum):
    """Market session types"""
    PRE_MARKET = "pre_market"
    OPENING = "opening"
    MID_MORNING = "mid_morning"
    LUNCH = "lunch"
    AFTERNOON = "afternoon"
    AFTER_HOURS = "after_hours"
    WEEKEND = "weekend"


@dataclass
class TradingLimits:
    """Trading limits configuration"""
    max_position_pct: float = 5.0
    max_daily_trades: int = 5
    max_daily_loss_pct: float = 5.0
    stop_loss_pct: float = 10.0
    min_trade_amount: float = 50.0
    max_trade_amount: float = 1000.0
    min_cash_reserve_pct: float = 10.0  # NEW: Minimum cash to maintain
    max_concentration_top3_pct: float = 60.0  # NEW: Max % in top 3 positions
    max_same_day_buys: int = 5  # NEW: Limit FOMO buying


@dataclass
class TradingAction:
    """Trading action decision"""
    action_type: str  # "BUY", "SELL", "HOLD", "CANCEL"
    ticker: str
    shares: int = 0
    order_id: str = ""  # For CANCEL actions - the order ID to cancel
    target_price: float = 0.0
    stop_loss: float = 0.0
    reasoning: str = ""
    confidence: float = 0.0
    execution_weight: float = 1.0
    timing_rationale: str = ""
    risk_assessment: str = ""
    prediction: str = ""


# =============================================================================
# FUNCTION CALLING SCHEMA - Guarantees structured output
# =============================================================================

TRADING_FUNCTION = {
    "type": "function",
    "function": {
        "name": "execute_trade",
        "description": "Execute a trading decision based on market analysis. Call this function with your trading decision.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["BUY", "SELL", "HOLD", "CANCEL"],
                    "description": "The trading action to take. Use CANCEL to cancel a pending order if market conditions changed."
                },
                "ticker": {
                    "type": "string",
                    "description": "Stock symbol (e.g., AAPL, GOOGL). Required for BUY/SELL/CANCEL, can be empty for HOLD."
                },
                "shares": {
                    "type": "integer",
                    "description": "Number of shares to trade. Required for BUY/SELL, 0 for HOLD/CANCEL.",
                    "minimum": 0
                },
                "order_id": {
                    "type": "string",
                    "description": "Order ID to cancel. Required for CANCEL action. Check PENDING ORDERS section for order IDs."
                },
                "target_price": {
                    "type": "number",
                    "description": "Target/limit price for the trade (0 for market order)",
                    "minimum": 0
                },
                "stop_loss": {
                    "type": "number",
                    "description": "Stop loss price for risk management",
                    "minimum": 0
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence level in this decision (0.0 to 1.0)",
                    "minimum": 0,
                    "maximum": 1
                },
                "reasoning": {
                    "type": "string",
                    "description": "Detailed reasoning for this trading decision including market analysis"
                },
                "risk_assessment": {
                    "type": "string",
                    "description": "Assessment of risks involved in this trade"
                },
                "timing_rationale": {
                    "type": "string",
                    "description": "Why this is the optimal time for this trade"
                },
                "prediction": {
                    "type": "string",
                    "description": "Expected price movement (e.g., '+2-3% in 1 week')"
                }
            },
            "required": ["action", "confidence", "reasoning"]
        }
    }
}


# =============================================================================
# SYSTEM PROMPT - Defines AI behavior (separate from user context)
# =============================================================================

SYSTEM_PROMPT = """You are an expert AI trading analyst with deep knowledge of:
- Technical analysis (price patterns, momentum, volume, RSI, MACD, Bollinger Bands)
- Fundamental analysis (valuations, earnings, sector trends)
- Portfolio management (diversification, position sizing, risk)
- Market microstructure (timing, liquidity, volatility)
- News sentiment analysis and market psychology

DECISION FRAMEWORK:
1. Analyze current portfolio positions and their P&L
2. Review market opportunities from screened stocks (500+ S&P 500 stocks)
3. Consider TECHNICAL INDICATORS:
   - RSI < 30 = OVERSOLD (buying opportunity)
   - RSI > 70 = OVERBOUGHT (selling opportunity)
   - MACD bullish crossover = strong buy signal
   - MACD bearish crossover = sell signal
   - Bollinger Band position indicates momentum
   - Golden Cross (SMA50 > SMA200) = strong bullish
   - Death Cross (SMA50 < SMA200) = strong bearish
4. Consider NEWS SENTIMENT:
   - Bullish sentiment + technical confirmation = high conviction BUY
   - Bearish sentiment + technical confirmation = high conviction SELL
   - Mixed signals = HOLD or lower confidence
5. Make ONE clear trading decision: BUY, SELL, or HOLD

RISK MANAGEMENT RULES:
- Never exceed position size limits
- Always set stop-losses on new positions
- Take profits when targets are reached (5%+ gains)
- Cut losses when stop-loss is hit (8%+ losses)
- Maintain cash buffer for new opportunities
- Avoid overconcentration in any single sector

SIGNAL PRIORITY:
1. Technical crossovers (MACD, Golden/Death Cross) = strongest signals
2. Extreme RSI levels (< 30 or > 70) = actionable signals
3. Bollinger Band extremes = timing signals
4. News sentiment confirms or challenges technicals

DECISION QUALITY:
- High confidence (>0.8): Multiple indicators aligned + sentiment confirmation
- Medium confidence (0.6-0.8): Good setup but some conflicting signals
- Low confidence (<0.6): Mixed signals or unclear situation

You MUST call the execute_trade function with your decision. Do not include JSON in your text response."""


def get_profile_limits(profile: TradingProfile) -> TradingLimits:
    """Get trading limits for a profile"""
    if profile == TradingProfile.CONSERVATIVE:
        return TradingLimits(
            max_position_pct=8.0,
            max_daily_trades=3,
            max_daily_loss_pct=5.0,
            stop_loss_pct=8.0,
            min_trade_amount=50.0,
            max_trade_amount=500.0,
            min_cash_reserve_pct=30.0,  # Conservative keeps high cash
            max_concentration_top3_pct=40.0,  # Low concentration
            max_same_day_buys=1  # Very selective
        )
    elif profile == TradingProfile.AGGRESSIVE:
        return TradingLimits(
            max_position_pct=15.0,
            max_daily_trades=8,
            max_daily_loss_pct=10.0,
            stop_loss_pct=12.0,
            min_trade_amount=100.0,
            max_trade_amount=2000.0,
            min_cash_reserve_pct=20.0,  # Moderate cash reserve
            max_concentration_top3_pct=55.0,  # Moderate concentration
            max_same_day_buys=2  # Moderate buying
        )
    elif profile == TradingProfile.ULTRA_AGGRESSIVE:
        # High-confidence, high-frequency trading
        # Minimal limits - let confidence drive decisions
        return TradingLimits(
            max_position_pct=25.0,  # Up to 25% per position for high confidence
            max_daily_trades=50,  # No practical limit on trade count
            max_daily_loss_pct=15.0,  # Higher loss tolerance
            stop_loss_pct=15.0,  # Wider stops
            min_trade_amount=100.0,
            max_trade_amount=5000.0,  # Can take large positions on conviction
            min_cash_reserve_pct=5.0,  # Very low cash requirement
            max_concentration_top3_pct=75.0,  # Allow high concentration
            max_same_day_buys=10  # Many buys allowed
        )
    elif profile == TradingProfile.SMART_AGGRESSIVE:
        # NEW: Aggressive but with smart risk controls
        # Balance between opportunity and safety
        return TradingLimits(
            max_position_pct=20.0,  # Reduced from 25% for better diversification
            max_daily_trades=20,  # Reduced from 50 to prevent overtrading
            max_daily_loss_pct=12.0,  # Moderate loss tolerance
            stop_loss_pct=12.0,  # Tighter stops than ultra
            min_trade_amount=100.0,
            max_trade_amount=4000.0,  # Slightly reduced from 5000
            min_cash_reserve_pct=15.0,  # Always keep 15% cash for opportunities
            max_concentration_top3_pct=60.0,  # Increased from 50% to allow portfolio growth
            max_same_day_buys=3  # Limit FOMO buying
        )
    else:  # CUSTOM
        return TradingLimits(
            max_position_pct=10.0,
            max_daily_trades=5,
            max_daily_loss_pct=7.5,
            stop_loss_pct=10.0,
            min_trade_amount=75.0,
            max_trade_amount=1000.0
        )


def load_portfolio(data_dir: Path) -> Dict[str, Any]:
    """Load portfolio from Alpaca with CSV backup"""
    try:
        from alpaca_broker import AlpacaBroker
        broker = AlpacaBroker()
        account = broker.get_account_info()
        positions = broker.get_positions()

        portfolio_positions = {}
        for pos in positions:
            if isinstance(pos, dict):
                ticker = pos.get('ticker', 'Unknown')
                shares = pos.get('shares', 0)
                market_value = pos.get('market_value', 0)
                avg_price = pos.get('avg_entry_price', 0)

                if shares > 0 and ticker != 'Unknown':
                    portfolio_positions[ticker] = {
                        'shares': int(shares),
                        'avg_price': float(avg_price),
                        'value': float(market_value)
                    }

        cash_available = account.get('cash', 100000)
        real_portfolio = {
            'cash': cash_available,
            'positions': portfolio_positions
        }

        save_portfolio(data_dir, real_portfolio)
        return real_portfolio

    except Exception as e:
        logging.warning(f"Alpaca portfolio load failed: {e}, using default")
        return {'cash': 100000.0, 'positions': {}}


def save_portfolio(data_dir: Path, portfolio: Dict[str, Any]):
    """Save portfolio to CSV"""
    try:
        portfolio_file = data_dir / "chatgpt_portfolio_update.csv"
        rows = []
        for ticker, pos in portfolio['positions'].items():
            rows.append({
                'ticker': ticker,
                'shares': pos['shares'],
                'avg_price': pos['avg_price'],
                'value': pos['value']
            })

        df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=['ticker', 'shares', 'avg_price', 'value'])
        df.to_csv(portfolio_file, index=False)
    except Exception as e:
        logging.error(f"Error saving portfolio: {e}")


def save_watchlist(data_dir: Path, items: List[Dict[str, Any]]):
    """Save overnight watchlist items for validation at market open"""
    try:
        watchlist_file = data_dir / "overnight_watchlist.json"
        watchlist = {
            'created': datetime.now().isoformat(),
            'items': items,
            'validated': False
        }
        with open(watchlist_file, 'w') as f:
            json.dump(watchlist, f, indent=2)
        logging.info(f"Saved {len(items)} watchlist items for market open validation")
    except Exception as e:
        logging.error(f"Error saving watchlist: {e}")


def load_watchlist(data_dir: Path) -> List[Dict[str, Any]]:
    """Load overnight watchlist items for validation"""
    try:
        watchlist_file = data_dir / "overnight_watchlist.json"
        if not watchlist_file.exists():
            return []

        with open(watchlist_file, 'r') as f:
            watchlist = json.load(f)

        # Check if watchlist is from today and not yet validated
        created = datetime.fromisoformat(watchlist['created'])
        if created.date() == datetime.now().date() and not watchlist.get('validated', False):
            return watchlist.get('items', [])
        return []
    except Exception as e:
        logging.error(f"Error loading watchlist: {e}")
        return []


def clear_watchlist(data_dir: Path):
    """Clear watchlist after validation"""
    try:
        watchlist_file = data_dir / "overnight_watchlist.json"
        if watchlist_file.exists():
            with open(watchlist_file, 'r') as f:
                watchlist = json.load(f)
            watchlist['validated'] = True
            with open(watchlist_file, 'w') as f:
                json.dump(watchlist, f, indent=2)
    except Exception as e:
        logging.error(f"Error clearing watchlist: {e}")


class AutomatedTradingSystem:
    """Enhanced automated trading system with improved AI integration"""

    def __init__(self, profile: TradingProfile):
        """Initialize the trading system"""
        self.profile = profile
        self.limits = get_profile_limits(profile)

        # Set up directories
        self.data_dir = Path(__file__).resolve().parent.parent / "automated_trading"
        self.data_dir.mkdir(exist_ok=True)

        self.logger = logging.getLogger(__name__)

        # Model configuration from environment (defaults to gpt-4o)
        self.model = os.environ.get('TRADING_MODEL', 'gpt-4o')
        self.logger.info(f"Using model: {self.model}")

        # Initialize OpenAI client
        try:
            from openai import OpenAI
            api_key = os.environ.get('OPENAI_API_KEY')
            if not api_key:
                raise ValueError("OPENAI_API_KEY environment variable not set")

            self.openai_client = OpenAI(api_key=api_key)

            # Connection test
            test_response = self.openai_client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "Reply with just 'ok'"}],
                max_tokens=5
            )

            if test_response.choices[0].message.content:
                self.logger.info(f"OpenAI connected successfully")

        except Exception as e:
            self.logger.error(f"OpenAI initialization failed: {e}")
            raise

        # Initialize components
        from trading_engine import TradingMemoryEngine
        from enhanced_data_sources import EnhancedDataFetcher, format_indicators_for_ai
        from market_intelligence import DynamicMarketIntelligence

        # Use separate v2 database to not interfere with v1
        self.memory_engine = TradingMemoryEngine(str(self.data_dir / "trading_memory_v2.db"))

        # Reconcile positions with broker on startup
        try:
            reconciliation = self.memory_engine.reconcile_positions_with_broker()
            if reconciliation.get('status') == 'success':
                if reconciliation.get('match'):
                    self.logger.info("Position tracking is in sync with broker")
                else:
                    self.logger.warning(f"Found {len(reconciliation['discrepancies'])} position discrepancies")
        except Exception as e:
            self.logger.warning(f"Could not reconcile positions on startup: {e}")

        self.enhanced_data_sources = EnhancedDataFetcher()
        self.market_intelligence = DynamicMarketIntelligence(self.enhanced_data_sources)

        # Store format function for later use
        self.format_indicators = format_indicators_for_ai

        # Try to initialize news sentiment provider (optional)
        try:
            from news_sentiment import get_sentiment_provider
            self.sentiment_provider = get_sentiment_provider()
            self.logger.info("News sentiment provider initialized")
        except Exception as e:
            self.sentiment_provider = None
            self.logger.warning(f"News sentiment not available: {e}")

        # Initialize advanced logging
        if ADVANCED_LOGGING:
            self.trading_logger = get_logger(str(self.data_dir.parent / "logs"))
            self.trading_logger.log_system("info", f"Trading System v2 initialized", {
                "profile": profile.value,
                "model": self.model
            })

        self.logger.info(f"Trading System v2 initialized [{profile.value}]")

    def _format_holdings_with_pnl(self, portfolio: Dict[str, Any]) -> str:
        """Format current holdings with P&L information"""
        if not portfolio['positions']:
            return "No current positions - portfolio is 100% cash, ready for new trades"

        lines = []
        total_pl = 0
        total_value = 0

        try:
            from alpaca_broker import AlpacaBroker
            broker = AlpacaBroker()
            alpaca_positions = broker.get_positions()
            alpaca_lookup = {
                pos['ticker']: pos for pos in alpaca_positions
                if isinstance(pos, dict) and pos.get('ticker')
            }
        except Exception:
            alpaca_lookup = {}

        for ticker, position in portfolio['positions'].items():
            shares = position['shares']
            avg_price = position['avg_price']
            value = position['value']
            total_value += value

            if ticker in alpaca_lookup:
                pos_data = alpaca_lookup[ticker]
                current_price = pos_data.get('current_price', avg_price)
                unrealized_pl = pos_data.get('unrealized_pl', 0)
                unrealized_plpc = pos_data.get('unrealized_plpc', 0)
                total_pl += unrealized_pl

                status = "+" if unrealized_pl >= 0 else ""
                action_hint = ""
                if unrealized_plpc > 0.05:
                    action_hint = " [PROFIT-TAKING CANDIDATE]"
                elif unrealized_plpc < -0.08:
                    action_hint = " [STOP-LOSS CANDIDATE]"

                lines.append(
                    f"  {ticker}: {shares} shares @ ${avg_price:.2f} -> ${current_price:.2f} "
                    f"({status}{unrealized_plpc:.1%}, ${unrealized_pl:+.2f}){action_hint}"
                )
            else:
                lines.append(f"  {ticker}: {shares} shares @ ${avg_price:.2f} (${value:.2f})")

        # Summary
        summary = f"\nTotal Position Value: ${total_value:,.2f}"
        if total_pl != 0:
            summary += f" | Unrealized P&L: ${total_pl:+,.2f}"

        return "\n".join(lines) + summary

    def _get_performance_summary(self) -> str:
        """Get summary of past trading performance with P/L tracking"""
        try:
            # Get performance from closed positions (actual P/L tracking)
            pos_perf = self.memory_engine.get_position_performance_summary()

            if pos_perf['total_trades'] == 0:
                return "No completed trades yet - new trading session"

            # Build comprehensive performance summary
            summary = (
                f"Closed Positions: {pos_perf['total_trades']} trades | "
                f"Wins: {pos_perf['wins']} | Losses: {pos_perf['losses']} | "
                f"Win Rate: {pos_perf['win_rate']:.1%}\n"
                f"Total P/L: ${pos_perf['total_pl']:+,.2f} | "
                f"Avg P/L: ${pos_perf['avg_pl']:+.2f} ({pos_perf['avg_pl_pct']:+.2%})\n"
                f"Best Trade: ${pos_perf['best_trade']:+.2f} | "
                f"Worst Trade: ${pos_perf['worst_trade']:+.2f} | "
                f"Avg Hold: {pos_perf['avg_hold_hours']:.1f}h"
            )

            return summary
        except Exception as e:
            self.logger.warning(f"Performance summary error: {e}")
            return "Performance history unavailable"

    def _get_memory_context(self, limit: int = 7) -> str:
        """Get recent trading decisions WITH outcomes for learning"""
        try:
            decisions = self.memory_engine.get_recent_decisions(limit)
            if not decisions:
                return "No previous trading decisions - starting fresh"

            lines = ["Recent Trading History:"]
            for d in decisions:
                decision_text = d.get('decision', 'Unknown')
                timestamp = d.get('timestamp', '')[:10]  # Just date
                confidence = d.get('confidence', 0)
                outcome = d.get('outcome')
                profit_loss = d.get('profit_loss')

                line = f"  [{timestamp}] {decision_text} (conf: {confidence:.0%})"

                # Include outcome if available - this is key for learning!
                if outcome and profit_loss is not None:
                    outcome_emoji = "WIN" if outcome == 'win' else "LOSS"
                    line += f" -> {outcome_emoji}: ${profit_loss:+.2f}"
                elif d.get('execution_result'):
                    line += " -> Executed"

                lines.append(line)

            # Add pattern insights if available
            try:
                patterns = self.memory_engine.analyze_decision_patterns()
                if patterns and 'confidence_performance' in patterns:
                    high_conf = patterns['confidence_performance'].get('high', {})
                    if high_conf.get('count', 0) > 3:
                        avg_outcome = high_conf.get('avg_outcome', 0)
                        lines.append(f"\nPattern: High-confidence trades avg outcome: {avg_outcome:.2f}")
            except Exception as e:
                self.logger.warning(f"Failed to fetch decision patterns: {e}")
                # Continue without pattern info - not critical for decision making

            return "\n".join(lines)
        except Exception as e:
            self.logger.error(f"Memory context failed: {e}")
            return "Memory context unavailable"

    def _get_technical_analysis_context(self) -> str:
        """Get detailed technical analysis for top opportunities"""
        try:
            # Get recent opportunities from market intelligence
            intel = self.market_intelligence.get_market_context_with_intelligence()
            opportunities = intel.get('opportunities', [])

            if not opportunities:
                return "No opportunities with technical data available"

            lines = []
            sentiment_data = {}

            # Get sentiment for top 5 tickers if sentiment provider available
            if self.sentiment_provider:
                try:
                    top_tickers = [opp['ticker'] for opp in opportunities[:5]]
                    for ticker in top_tickers:
                        sent = self.sentiment_provider.get_sentiment_sync(ticker)
                        if sent:
                            sentiment_data[ticker] = sent
                except Exception as e:
                    self.logger.debug(f"Sentiment fetch error: {e}")

            # Format top 5 opportunities with full technical analysis
            for i, opp in enumerate(opportunities[:5]):
                ticker = opp['ticker']
                lines.append(f"\n[{i+1}] {ticker} - ${opp['price']:.2f} ({opp['change_1d']:+.2%} today)")

                # Technical indicators
                indicators = opp.get('indicators')
                if indicators:
                    lines.append(self.format_indicators(indicators))

                    # Technical signals summary
                    signals = opp.get('technical_signals', [])
                    if signals:
                        lines.append(f"SIGNALS: {', '.join(signals)}")
                else:
                    lines.append("  Technical data unavailable (fallback data source)")

                # News sentiment
                if ticker in sentiment_data:
                    sent = sentiment_data[ticker]
                    lines.append(f"NEWS: {sent.sentiment_label.upper()} ({sent.overall_sentiment:+.2f}), {sent.news_count} articles")
                    if sent.headlines:
                        lines.append(f"  Latest: {sent.headlines[0][:60]}...")

            # Summary of technical signals across all opportunities
            all_signals = []
            for opp in opportunities[:15]:
                all_signals.extend(opp.get('technical_signals', []))

            if all_signals:
                from collections import Counter
                signal_counts = Counter(all_signals)
                top_signals = signal_counts.most_common(5)
                lines.append(f"\nMARKET SIGNALS SUMMARY:")
                for signal, count in top_signals:
                    lines.append(f"  {signal}: {count} stocks")

            return "\n".join(lines) if lines else "Technical analysis unavailable"

        except Exception as e:
            self.logger.error(f"Technical analysis context error: {e}")
            return "Technical analysis error - using basic data only"

    def _get_session_context(self, session: MarketSession) -> tuple:
        """Get session description and trading guidance"""
        contexts = {
            MarketSession.PRE_MARKET: (
                "Pre-Market Preparation",
                "Analyze overnight news, gaps, and prepare watchlist. IMPORTANT: Do NOT execute trades now - only generate ideas to validate at market open. Save promising setups as watchlist items with entry targets."
            ),
            MarketSession.OPENING: (
                "Market Opening (9:30-10:00 ET)",
                "VALIDATION PHASE: Re-check any overnight watchlist ideas against LIVE prices and news. Only execute if thesis still valid. High volatility - wait for direction to establish. Avoid chasing gaps."
            ),
            MarketSession.MID_MORNING: (
                "Mid-Morning Session (10:00-12:00 ET)",
                "Trends establishing. Good window for entries. Look for pullbacks."
            ),
            MarketSession.LUNCH: (
                "Lunch Hours (12:00-14:00 ET)",
                "Lower volume, choppy action. Avoid new positions. Review existing."
            ),
            MarketSession.AFTERNOON: (
                "Afternoon Session (14:00-16:00 ET)",
                "Final positioning before close. Take profits or prepare for overnight."
            ),
            MarketSession.AFTER_HOURS: (
                "After-Hours Review",
                "No execution. Analyze day's performance. Plan for tomorrow."
            ),
            MarketSession.WEEKEND: (
                "Weekend Analysis",
                "No execution. Research, strategy review, prepare for Monday."
            )
        }
        return contexts.get(session, ("Trading Session", "Standard trading session"))

    def _get_pending_orders_context(self) -> str:
        """Get pending orders from broker and format for GPT context"""
        try:
            from alpaca_broker import AlpacaBroker
            broker = AlpacaBroker()
            pending_orders = broker.get_orders(status='open')

            if not pending_orders:
                return "No pending orders"

            orders_text = f"ACTIVE ORDERS ({len(pending_orders)}):\n"
            for order in pending_orders:
                side = str(order.get('side', '')).replace('OrderSide.', '')
                status = str(order.get('status', '')).replace('OrderStatus.', '')
                ticker = order.get('ticker') or order.get('symbol', 'N/A')
                qty = order.get('qty', 0)
                order_id = order.get('id', 'N/A')
                orders_text += f"  • {side} {int(qty)} {ticker} - Status: {status} - ID: {order_id}\n"

            orders_text += "\nIMPORTANT: These orders are already submitted.\n"
            orders_text += "- Do NOT create duplicate orders for these symbols.\n"
            orders_text += "- If conditions changed, you can CANCEL an order using its ID.\n"
            return orders_text
        except Exception as e:
            return f"Unable to fetch pending orders: {str(e)}"

    def _build_user_message(self, session: MarketSession) -> str:
        """Build the user message with all context (separate from system prompt)"""

        # Load portfolio
        portfolio = load_portfolio(self.data_dir)
        portfolio_value = sum(pos['value'] for pos in portfolio['positions'].values())
        cash = portfolio['cash']
        total_value = portfolio_value + cash

        # Get all contexts
        market_context = self.market_intelligence.format_for_gpt5()
        memory_context = self._get_memory_context()
        performance_summary = self._get_performance_summary()
        holdings_summary = self._format_holdings_with_pnl(portfolio)
        pending_orders_context = self._get_pending_orders_context()

        # Session info
        session_name, session_guidance = self._get_session_context(session)
        et = pytz.timezone('US/Eastern')
        current_time = datetime.now(et)
        is_market_open = 9 <= current_time.hour < 16 and current_time.weekday() < 5

        # Owned tickers warning + pending orders
        owned_tickers = list(portfolio['positions'].keys())

        # Get tickers with pending orders
        pending_order_tickers = []
        try:
            from alpaca_broker import AlpacaBroker
            broker = AlpacaBroker()
            pending_orders = broker.get_orders(status='open')
            pending_order_tickers = [order.get('ticker') for order in pending_orders if order.get('ticker')]
        except Exception as e:
            self.logger.warning(f"Failed to fetch pending orders for context: {e}")
            # Continue with empty list - AI will still function without this info

        owned_warning = f"ALREADY OWNED: {', '.join(owned_tickers)}" if owned_tickers else "No current positions"
        if pending_order_tickers:
            owned_warning += f"\nPENDING ORDERS FOR: {', '.join(pending_order_tickers)} - DO NOT DUPLICATE"

        # Load overnight watchlist for market open validation
        watchlist_section = ""
        if session == MarketSession.OPENING:
            watchlist = load_watchlist(self.data_dir)
            if watchlist:
                watchlist_section = "\n=== OVERNIGHT WATCHLIST (REQUIRES VALIDATION) ===\n"
                watchlist_section += "These ideas were generated overnight. RE-VALIDATE against current prices before executing!\n"
                for i, item in enumerate(watchlist, 1):
                    watchlist_section += f"\n#{i}: {item.get('ticker', 'N/A')} - {item.get('action', 'WATCH')}\n"
                    watchlist_section += f"   Entry Target: ${item.get('target_price', 0):.2f}\n"
                    watchlist_section += f"   Overnight Reasoning: {item.get('reasoning', 'N/A')[:100]}...\n"
                    watchlist_section += f"   ** CHECK IF STILL VALID WITH CURRENT PRICE **\n"
                watchlist_section += "\n"

        user_message = f"""
=== TRADING SESSION ===
Session: {session_name}
Guidance: {session_guidance}
Profile: {self.profile.value.upper()}
Time: {current_time.strftime('%Y-%m-%d %H:%M:%S %Z')}
Market: {'OPEN - Can execute trades' if is_market_open else 'CLOSED - Analysis only'}

=== ACCOUNT STATUS ===
Cash Available: ${cash:,.2f}
Portfolio Value: ${portfolio_value:,.2f}
Total Account: ${total_value:,.2f}
Position Count: {len(portfolio['positions'])}
Cash Allocation: {(cash / total_value * 100):.1f}%

{'HIGH CASH WARNING: ' + str(int(cash / total_value * 100)) + '% cash is too high! Look for BUY opportunities to deploy capital.' if cash / total_value > 0.5 else ''}
{'BALANCED: Good mix of cash and positions.' if 0.2 <= cash / total_value <= 0.5 else ''}
{'FULLY INVESTED: Low cash reserves, focus on managing existing positions.' if cash / total_value < 0.2 else ''}

=== CURRENT HOLDINGS ===
{holdings_summary}

=== PENDING ORDERS ===
{pending_orders_context}

=== TRADING CONSTRAINTS ===
{owned_warning}
- Do NOT buy stocks you already own (no pyramiding unless explicit reason)
- Max Position Size: {self.limits.max_position_pct}% = ${total_value * self.limits.max_position_pct / 100:,.2f}
- Max Daily Trades: {self.limits.max_daily_trades}
- Stop Loss Default: {self.limits.stop_loss_pct}%
- Daily Loss Limit: {self.limits.max_daily_loss_pct}%
{watchlist_section}
=== MARKET INTELLIGENCE ===
{market_context}

=== TECHNICAL ANALYSIS (Top Opportunities) ===
{self._get_technical_analysis_context()}

=== TRADING HISTORY ===
{memory_context}

=== PERFORMANCE ===
{performance_summary}

=== YOUR TASK ===
Analyze all the above information and make ONE trading decision.
Consider: portfolio balance, P&L of existing positions, market opportunities, and risk limits.
Call the execute_trade function with your decision.
"""
        return user_message

    def get_trading_decision(self, session: MarketSession) -> Optional[TradingAction]:
        """Get trading decision using function calling (guaranteed structured output)"""
        try:
            # Build messages with proper separation
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._build_user_message(session)}
            ]

            # Save prompt for debugging
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            prompt_file = self.data_dir / f"v2_prompt_{timestamp}.txt"
            with open(prompt_file, 'w') as f:
                f.write("=== SYSTEM ===\n")
                f.write(SYSTEM_PROMPT)
                f.write("\n\n=== USER ===\n")
                f.write(messages[1]['content'])

            self.logger.info("Calling OpenAI with function calling...")

            # Call OpenAI with function calling - forces structured output
            api_start = time.time()
            response = self.openai_client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=[TRADING_FUNCTION],
                tool_choice={"type": "function", "function": {"name": "execute_trade"}},
                max_tokens=1500,
                timeout=45.0  # 45 second timeout to prevent indefinite hangs
            )
            api_duration = (time.time() - api_start) * 1000

            message = response.choices[0].message

            # Log API call
            if ADVANCED_LOGGING:
                log_api_call(
                    service="openai",
                    endpoint="chat.completions",
                    request={"model": self.model, "session": session.value},
                    response={"has_tool_calls": bool(message.tool_calls)},
                    duration_ms=api_duration
                )

            # Save response
            response_file = self.data_dir / f"v2_response_{timestamp}.txt"
            with open(response_file, 'w') as f:
                if message.tool_calls:
                    f.write("=== FUNCTION CALL ===\n")
                    args = json.loads(message.tool_calls[0].function.arguments)
                    f.write(json.dumps(args, indent=2))
                if message.content:
                    f.write(f"\n\n=== CONTENT ===\n{message.content}")

            # Extract function call result
            if not message.tool_calls:
                self.logger.warning("No function call in response, trying fallback")
                if message.content:
                    return self._parse_fallback_response(message.content)
                return None

            # Parse function arguments - this is guaranteed valid JSON!
            func_args = json.loads(message.tool_calls[0].function.arguments)

            action = TradingAction(
                action_type=func_args.get('action', 'HOLD'),
                ticker=func_args.get('ticker', '') or '',
                shares=int(func_args.get('shares', 0) or 0),
                order_id=func_args.get('order_id', '') or '',  # For CANCEL actions
                target_price=float(func_args.get('target_price', 0) or 0),
                stop_loss=float(func_args.get('stop_loss', 0) or 0),
                reasoning=func_args.get('reasoning', ''),
                confidence=float(func_args.get('confidence', 0.5) or 0.5),
                risk_assessment=func_args.get('risk_assessment', ''),
                timing_rationale=func_args.get('timing_rationale', ''),
                prediction=func_args.get('prediction', '')
            )

            # Calculate execution weight based on confidence
            action.execution_weight = min(action.confidence + 0.1, 1.0)

            # Log the decision with full context
            if ADVANCED_LOGGING:
                log_decision(
                    decision={
                        "action": action.action_type,
                        "ticker": action.ticker,
                        "shares": action.shares,
                        "confidence": action.confidence,
                        "reasoning": action.reasoning,
                        "risk_assessment": action.risk_assessment,
                        "timing_rationale": action.timing_rationale,
                        "prediction": action.prediction,
                        "target_price": action.target_price,
                        "stop_loss": action.stop_loss,
                        "execution_weight": action.execution_weight
                    },
                    context={
                        "session": session.value,
                        "model": self.model,
                        "profile": self.profile.value,
                        "api_duration_ms": api_duration
                    }
                )

            # Store decision in memory
            try:
                self.memory_engine.store_decision(
                    decision=f"{action.action_type} {action.shares} {action.ticker}".strip(),
                    reasoning=action.reasoning,
                    confidence=action.confidence,
                    market_data=f"Session: {session.value}",
                    execution_weight=action.execution_weight
                )
                self.logger.info("Decision stored in memory")
            except Exception as e:
                self.logger.error(f"Failed to store decision: {e}")

            self.logger.info(f"Decision: {action.action_type} {action.shares} {action.ticker} ({action.confidence:.0%})")
            return action

        except json.JSONDecodeError as e:
            self.logger.error(f"JSON parsing failed (shouldn't happen with function calling): {e}")
            if ADVANCED_LOGGING:
                log_error("json_parse_error", str(e), exception=e, context={"session": session.value})
            return None
        except Exception as e:
            self.logger.error(f"Trading decision error: {e}")
            if ADVANCED_LOGGING:
                log_error("decision_error", str(e), exception=e, context={"session": session.value})
            import traceback
            traceback.print_exc()
            return None

    def _parse_fallback_response(self, content: str) -> Optional[TradingAction]:
        """Fallback parser if function calling fails (shouldn't happen often)"""
        try:
            # Try to find JSON in content
            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return TradingAction(
                    action_type=data.get('action', 'HOLD'),
                    ticker=data.get('ticker', ''),
                    shares=int(data.get('shares', 0)),
                    reasoning=data.get('reasoning', 'Parsed from fallback'),
                    confidence=float(data.get('confidence', 0.5))
                )
        except Exception as e:
            self.logger.error(f"Fallback parsing failed: {e}")
        return None

    def validate_trade(self, action: TradingAction) -> Tuple[bool, str]:
        """
        Validate trade against profile limits and safety constraints.
        Returns (can_execute: bool, reason: str)
        """
        if action.action_type not in ["BUY", "SELL"]:
            return (True, "No validation needed for non-trade action")

        try:
            from alpaca_broker import AlpacaBroker
            broker = AlpacaBroker()
            account = broker.get_account_info()
            positions = broker.get_positions()

            total_equity = account['equity']
            cash = account['cash']
            cash_pct = (cash / total_equity * 100) if total_equity > 0 else 0

            # BUY-specific validations
            if action.action_type == "BUY":
                # 1. Check minimum cash reserve
                estimated_cost = action.shares * action.target_price if action.target_price > 0 else 0
                if estimated_cost == 0:
                    # Try to get current price
                    current_price = broker.get_latest_price(action.ticker)
                    if current_price:
                        estimated_cost = action.shares * current_price

                cash_after_buy = cash - estimated_cost
                cash_pct_after = (cash_after_buy / total_equity * 100) if total_equity > 0 else 0

                if cash_pct_after < self.limits.min_cash_reserve_pct:
                    return (False, f"Would violate minimum cash reserve: {cash_pct_after:.1f}% < {self.limits.min_cash_reserve_pct:.1f}%")

                # 2. Check concentration after buy
                # Only enforce concentration limits if portfolio has 5+ positions (portfolio is mature)
                # When building initial portfolio (< 5 positions), concentration is expected to be high
                position_values = {pos['ticker']: pos['market_value'] for pos in positions}
                position_values[action.ticker] = position_values.get(action.ticker, 0) + estimated_cost

                num_positions_after = len(position_values)

                if num_positions_after >= 5:
                    # Find top 3 positions
                    sorted_positions = sorted(position_values.items(), key=lambda x: x[1], reverse=True)
                    top3_value = sum(value for _, value in sorted_positions[:3])
                    top3_pct = (top3_value / total_equity * 100) if total_equity > 0 else 0

                    if top3_pct > self.limits.max_concentration_top3_pct:
                        return (False, f"Would violate concentration limit: top 3 = {top3_pct:.1f}% > {self.limits.max_concentration_top3_pct:.1f}%")
                else:
                    self.logger.info(f"ℹ️ Concentration check skipped: only {num_positions_after} positions (< 5)")

                # 3. Check same-day buy limit
                # Count BUYs from today's decisions
                from datetime import datetime
                today_decisions = self.memory_engine.get_recent_decisions(limit=50)
                today_buys = [d for d in today_decisions
                             if d.get('action') == 'BUY'
                             and d.get('timestamp', '').startswith(datetime.now().strftime('%Y-%m-%d'))]

                if len(today_buys) >= self.limits.max_same_day_buys:
                    return (False, f"Would exceed daily buy limit: {len(today_buys)} >= {self.limits.max_same_day_buys}")

            return (True, "Trade validated")

        except Exception as e:
            self.logger.error(f"Trade validation error: {e}")
            # On error, allow trade but log warning
            return (True, f"Validation error (allowing): {e}")

    def execute_trade_action(self, action: TradingAction) -> Dict[str, Any]:
        """Execute trading action via Alpaca broker"""
        try:
            from alpaca_broker import AlpacaBroker
            broker = AlpacaBroker()

            if action.action_type == "BUY":
                order_id = broker.place_buy_order(action.ticker, action.shares)
                if order_id:
                    self.logger.info(f"BUY order executed: {action.shares} {action.ticker} (ID: {order_id})")

                    # Place stop-loss order to protect the position
                    stop_loss_order_id = None
                    stop_loss_price = action.stop_loss

                    # If AI didn't set a stop-loss, calculate one using default percentage
                    if stop_loss_price <= 0:
                        try:
                            current_price = broker.get_current_price(action.ticker)
                            if current_price and current_price > 0:
                                stop_loss_price = current_price * (1 - self.limits.stop_loss_pct / 100)
                                self.logger.info(f"Calculated default stop-loss: ${stop_loss_price:.2f} ({self.limits.stop_loss_pct}% below ${current_price:.2f})")
                        except Exception as e:
                            self.logger.warning(f"Could not calculate stop-loss price: {e}")

                    # Place the stop-loss order if we have a valid price
                    if stop_loss_price > 0:
                        try:
                            stop_loss_order_id = broker.place_stop_loss_order(action.ticker, action.shares, stop_loss_price)
                            if stop_loss_order_id:
                                self.logger.info(f"STOP-LOSS order placed: {action.shares} {action.ticker} @ ${stop_loss_price:.2f} (ID: {stop_loss_order_id})")
                            else:
                                self.logger.warning(f"Failed to place stop-loss order for {action.ticker}")
                        except Exception as e:
                            self.logger.warning(f"Stop-loss order error for {action.ticker}: {e}")

                    return {
                        "status": "success",
                        "action": action.action_type,
                        "ticker": action.ticker,
                        "shares": action.shares,
                        "order_id": order_id,
                        "stop_loss_order_id": stop_loss_order_id,
                        "stop_loss_price": stop_loss_price
                    }
                else:
                    self.logger.error(f"BUY order failed: {action.ticker}")
                    return {"status": "failed", "action": action.action_type, "ticker": action.ticker}

            elif action.action_type == "SELL":
                order_id = broker.place_sell_order(action.ticker, action.shares)
                if order_id:
                    self.logger.info(f"SELL order executed: {action.shares} {action.ticker} (ID: {order_id})")
                    return {
                        "status": "success",
                        "action": action.action_type,
                        "ticker": action.ticker,
                        "shares": action.shares,
                        "order_id": order_id
                    }
                else:
                    self.logger.error(f"SELL order failed: {action.ticker}")
                    return {"status": "failed", "action": action.action_type, "ticker": action.ticker}

            elif action.action_type == "CANCEL":
                if not action.order_id:
                    self.logger.error(f"CANCEL failed: No order_id provided")
                    return {"status": "failed", "action": action.action_type, "error": "No order_id provided"}

                success = broker.cancel_order(action.order_id)
                if success:
                    self.logger.info(f"Order CANCELLED: {action.order_id} ({action.ticker})")
                    return {
                        "status": "success",
                        "action": action.action_type,
                        "ticker": action.ticker,
                        "order_id": action.order_id
                    }
                else:
                    self.logger.error(f"CANCEL order failed: {action.order_id}")
                    return {"status": "failed", "action": action.action_type, "order_id": action.order_id}

            elif action.action_type == "HOLD":
                return {"status": "hold", "action": action.action_type}
            else:
                return {"status": "invalid_action", "action": action.action_type}

        except Exception as e:
            self.logger.error(f"Trade execution error: {e}")
            return {"status": "error", "action": action.action_type, "ticker": action.ticker, "error": str(e)}

    def run_trading_session(self, session: MarketSession):
        """Run complete trading session"""
        session_start = time.time()
        try:
            session_name, _ = self._get_session_context(session)
            self.logger.info(f"Starting session: {session_name}")

            # Log session start
            if ADVANCED_LOGGING:
                self.trading_logger.log_session_start(
                    session_type=session.value,
                    profile=self.profile.value,
                    config={"model": self.model, "limits": self.limits.__dict__}
                )

            self.logger.info(f"=== Trading Session: {session_name} ({self.profile.value.upper()}) ===")

            # Log universe statistics
            try:
                universe_stats = self.market_intelligence.get_universe_stats_for_dashboard()
                self.logger.info(f"Universe: {universe_stats['total_stocks']} stocks "
                      f"(T1: {universe_stats['tier1_count']}, "
                      f"T2: {universe_stats['tier2_count']}, "
                      f"T3: {universe_stats['tier3_count']})")

                # Log to advanced logging
                if ADVANCED_LOGGING:
                    self.trading_logger.log_system_event(
                        event_type="universe_stats",
                        data={
                            "total_stocks": universe_stats['total_stocks'],
                            "tier1_count": universe_stats['tier1_count'],
                            "tier2_count": universe_stats['tier2_count'],
                            "tier3_count": universe_stats['tier3_count'],
                            "last_refresh": str(universe_stats.get('last_refresh', 'Never'))
                        }
                    )
            except Exception as e:
                self.logger.warning(f"Could not log universe stats: {e}")

            # Get decision
            action = self.get_trading_decision(session)

            if not action:
                self.logger.warning("No trading decision received - skipping")
                return

            # Display decision
            decision_msg = f"Decision: {action.action_type}"
            if action.ticker:
                decision_msg += f" {action.shares} {action.ticker}"
            decision_msg += f" (confidence: {action.confidence:.0%})"
            self.logger.info(decision_msg)

            if action.reasoning:
                # Truncate long reasoning for display
                reasoning = action.reasoning[:150] + "..." if len(action.reasoning) > 150 else action.reasoning
                self.logger.info(f"Reasoning: {reasoning}")

            self.logger.info(f"Decision: {action.action_type} {action.shares} {action.ticker}")


            # Check if this is an overnight session (no execution, save to watchlist)
            is_overnight_session = session in [MarketSession.PRE_MARKET, MarketSession.AFTER_HOURS, MarketSession.WEEKEND]

            # CANCEL can happen anytime (even during overnight)
            if action.action_type == "CANCEL":
                self.logger.info(f"Canceling order: {action.order_id} ({action.ticker})")
                exec_start = time.time()
                result = self.execute_trade_action(action)
                exec_duration = (time.time() - exec_start) * 1000
                self.logger.info(f"Result: {result}")
                self.logger.info(f"Order canceled: {result}")
                return

            if is_overnight_session and action.action_type in ["BUY", "SELL"]:
                # Save to watchlist for validation at market open
                watchlist_item = {
                    'ticker': action.ticker,
                    'action': action.action_type,
                    'shares': action.shares,
                    'target_price': action.target_price,
                    'stop_loss': action.stop_loss,
                    'confidence': action.confidence,
                    'reasoning': action.reasoning,
                    'created_session': session.value,
                    'created_time': datetime.now().isoformat()
                }
                # Load existing watchlist and append
                existing = load_watchlist(self.data_dir)
                existing.append(watchlist_item)
                save_watchlist(self.data_dir, existing)
                self.logger.info(f"OVERNIGHT SESSION - Saved to watchlist for market open validation")
                self.logger.info(f"Watchlist item: {action.action_type} {action.shares} {action.ticker} @ ${action.target_price:.2f}")
                self.logger.info(f"Saved watchlist item: {action.ticker} for validation at market open")

            # Clear watchlist after OPENING session validation
            elif session == MarketSession.OPENING:
                clear_watchlist(self.data_dir)

                # Execute if confidence is high enough
                if action.action_type in ["BUY", "SELL"]:
                    if action.execution_weight >= 0.7:
                        # Validate trade against profile limits
                        can_execute, reason = self.validate_trade(action)
                        if not can_execute:
                            self.logger.warning(f"Trade blocked by safety constraints: {reason}")
                            self.logger.warning(f"Trade blocked: {action.action_type} {action.ticker} - {reason}")

                            # Log blocked trade for analysis
                            if ADVANCED_LOGGING:
                                log_decision(
                                    decision={
                                        "action": "BLOCKED",
                                        "ticker": action.ticker,
                                        "shares": action.shares,
                                        "confidence": action.confidence,
                                        "reasoning": f"Trade blocked: {reason}"
                                    },
                                    context={
                                        "event": "trade_blocked",
                                        "original_action": action.action_type,
                                        "execution_weight": action.execution_weight,
                                        "reason": reason,
                                        "target_price": action.target_price,
                                        "session": session.value
                                    }
                                )

                            return  # Skip this trade

                        self.logger.info(f"Executing trade (validated)...")
                        exec_start = time.time()
                        result = self.execute_trade_action(action)
                        exec_duration = (time.time() - exec_start) * 1000
                        self.logger.info(f"Result: {result}")
                        self.logger.info(f"Trade executed: {result}")

                        # Log execution
                        if ADVANCED_LOGGING:
                            log_execution(
                                decision={
                                    "action": action.action_type,
                                    "ticker": action.ticker,
                                    "shares": action.shares,
                                    "confidence": action.confidence
                                },
                                result=result if isinstance(result, dict) else {"order_id": str(result)},
                                execution_time_ms=exec_duration
                            )

                        # Store execution
                        try:
                            decision_id = self.memory_engine.get_latest_decision_id()
                            self.memory_engine.store_execution(
                                decision_id=decision_id,
                                execution_result=result,
                                actual_price=action.target_price,
                                execution_weight=action.execution_weight
                            )

                            # Track position for P/L calculation
                            if result.get('status') == 'success' and result.get('order_id'):
                                try:
                                    from alpaca_broker import AlpacaBroker
                                    broker = AlpacaBroker()

                                    # Check order status before tracking
                                    time.sleep(0.5)  # Brief wait for order to process
                                    order_details = broker.get_order_details(result['order_id'])

                                    if order_details:
                                        filled_price = order_details.get('filled_avg_price')
                                        is_filled = order_details.get('is_filled', False)
                                        is_pending = order_details.get('is_pending', False)

                                        if action.action_type == "BUY":
                                            # For BUY orders, track with best available price
                                            if not filled_price:
                                                filled_price = action.target_price
                                                self.logger.warning(f"Using target_price ${filled_price:.2f} for order {result['order_id']}")
                                            if filled_price:
                                                self.memory_engine.track_position_open(
                                                    ticker=action.ticker,
                                                    entry_price=filled_price,
                                                    quantity=action.shares,
                                                    decision_id=decision_id
                                                )
                                                self.logger.info(f"Position opened: {action.shares} {action.ticker} @ ${filled_price:.2f}")

                                        elif action.action_type == "SELL":
                                            # For SELL orders, only track if actually filled
                                            if is_filled and filled_price:
                                                self.memory_engine.track_position_close(
                                                    ticker=action.ticker,
                                                    exit_price=filled_price,
                                                    quantity=action.shares,
                                                    decision_id=decision_id
                                                )
                                                self.logger.info(f"Position closed: {action.shares} {action.ticker} @ ${filled_price:.2f}")
                                            elif is_pending:
                                                self.logger.info(f"SELL order pending - position will close when order fills")
                                            else:
                                                self.logger.warning(f"SELL order status unknown: {order_details.get('status')}")
                                    else:
                                        self.logger.warning(f"Could not get order details for {result['order_id']}")
                                except Exception as e:
                                    self.logger.error(f"Failed to track position: {e}")
                        except Exception as e:
                            self.logger.error(f"Failed to store execution: {e}")
                    else:
                        self.logger.info(f"Low confidence ({action.execution_weight:.0%}) - recorded but not executed")
                else:
                    self.logger.info(f"Holding current positions")

            # Regular market hours sessions
            elif action.action_type in ["BUY", "SELL"]:
                if action.execution_weight >= 0.7:
                    # Validate trade against profile limits
                    can_execute, reason = self.validate_trade(action)
                    if not can_execute:
                        self.logger.warning(f"Trade blocked by safety constraints: {reason}")
                        self.logger.warning(f"Trade blocked: {action.action_type} {action.ticker} - {reason}")

                        # Log blocked trade for analysis
                        if ADVANCED_LOGGING:
                            log_decision(
                                decision={
                                    "action": "BLOCKED",
                                    "ticker": action.ticker,
                                    "shares": action.shares,
                                    "confidence": action.confidence,
                                    "reasoning": f"Trade blocked: {reason}"
                                },
                                context={
                                    "event": "trade_blocked",
                                    "original_action": action.action_type,
                                    "execution_weight": action.execution_weight,
                                    "reason": reason,
                                    "target_price": action.target_price,
                                    "session": session.value
                                }
                            )

                        return  # Skip this trade

                    self.logger.info(f"Executing trade...")
                    exec_start = time.time()
                    result = self.execute_trade_action(action)
                    exec_duration = (time.time() - exec_start) * 1000
                    self.logger.info(f"Result: {result}")
                    self.logger.info(f"Trade executed: {result}")

                    # Log execution
                    if ADVANCED_LOGGING:
                        log_execution(
                            decision={
                                "action": action.action_type,
                                "ticker": action.ticker,
                                "shares": action.shares,
                                "confidence": action.confidence
                            },
                            result=result if isinstance(result, dict) else {"order_id": str(result)},
                            execution_time_ms=exec_duration
                        )

                    # Store execution
                    try:
                        decision_id = self.memory_engine.get_latest_decision_id()
                        self.memory_engine.store_execution(
                            decision_id=decision_id,
                            execution_result=result,
                            actual_price=action.target_price,
                            execution_weight=action.execution_weight
                        )

                        # Track position for P/L calculation
                        if result.get('status') == 'success' and result.get('order_id'):
                            try:
                                from alpaca_broker import AlpacaBroker
                                broker = AlpacaBroker()

                                # Check order status before tracking
                                time.sleep(0.5)  # Brief wait for order to process
                                order_details = broker.get_order_details(result['order_id'])

                                if order_details:
                                    filled_price = order_details.get('filled_avg_price')
                                    is_filled = order_details.get('is_filled', False)
                                    is_pending = order_details.get('is_pending', False)

                                    if action.action_type == "BUY":
                                        # For BUY orders, we track even if pending (we need entry price)
                                        if not filled_price:
                                            filled_price = action.target_price
                                            self.logger.warning(f"Using target_price ${filled_price:.2f} for order {result['order_id']}")
                                        if filled_price:
                                            self.memory_engine.track_position_open(
                                                ticker=action.ticker,
                                                entry_price=filled_price,
                                                quantity=action.shares,
                                                decision_id=decision_id
                                            )
                                            self.logger.info(f"Position opened: {action.shares} {action.ticker} @ ${filled_price:.2f}")

                                    elif action.action_type == "SELL":
                                        # For SELL orders, only track if actually filled
                                        if is_filled and filled_price:
                                            self.memory_engine.track_position_close(
                                                ticker=action.ticker,
                                                exit_price=filled_price,
                                                quantity=action.shares,
                                                decision_id=decision_id
                                            )
                                            self.logger.info(f"Position closed: {action.shares} {action.ticker} @ ${filled_price:.2f}")
                                        elif is_pending:
                                            self.logger.info(f"SELL order pending - position will close when order fills")
                                        else:
                                            self.logger.warning(f"SELL order status unknown: {order_details.get('status')}")
                                else:
                                    self.logger.warning(f"Could not get order details for {result['order_id']}")
                            except Exception as e:
                                self.logger.error(f"Failed to track position: {e}")
                    except Exception as e:
                        self.logger.error(f"Failed to store execution: {e}")
                else:
                    self.logger.info(f"Low confidence ({action.execution_weight:.0%}) - recorded but not executed")
            else:
                self.logger.info(f"Holding current positions")

            self.logger.info("=== Session Complete ===")

            # Log session end
            session_duration = (time.time() - session_start) * 1000
            if ADVANCED_LOGGING:
                self.trading_logger.log_session_end(
                    session_type=session.value,
                    summary={
                        "action": action.action_type if action else "NONE",
                        "ticker": action.ticker if action else None,
                        "confidence": action.confidence if action else 0,
                        "executed": action.action_type in ["BUY", "SELL"] and action.execution_weight >= 0.7 if action else False,
                        "duration_ms": session_duration
                    }
                )

        except Exception as e:
            self.logger.error(f"Session error: {e}")
            if ADVANCED_LOGGING:
                log_error("session_error", str(e), exception=e, context={"session": session.value})
            import traceback
            traceback.print_exc()


# =============================================================================
# TEST FUNCTION
# =============================================================================

def test_v2_system():
    """Test the v2 trading system"""
    print("\n" + "="*60)
    print("  AutoTrader v2 - AI Integration Test")
    print("="*60 + "\n")

    try:
        system = AutomatedTradingSystem(TradingProfile.CONSERVATIVE)
        print(f"System initialized: {system.model}")

        print("\nRunning test session...")
        system.run_trading_session(MarketSession.MID_MORNING)

        print("\nTest completed successfully!")
        return True

    except Exception as e:
        print(f"\nTest failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    test_v2_system()
