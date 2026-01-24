# AutoTrader

An automated trading system that uses OpenAI's GPT models to analyze markets and execute trades via Alpaca's paper trading API. Features a live terminal dashboard, dynamic stock universe management, and comprehensive risk controls.

**Note: This system uses paper trading only. No real money is involved.**

## Features

- **AI-Powered Decisions**: Uses GPT-4o to analyze market data, technical indicators, and news sentiment
- **Live Dashboard**: Real-time terminal UI showing positions, P&L, pending orders, and AI activity
- **Dynamic Stock Universe**: Automatically discovers and prioritizes 900-1200 stocks based on momentum and volume
- **Multi-Source Data**: Fetches data from Polygon, Alpha Vantage, Twelve Data, yfinance, and Stooq with automatic fallbacks
- **Risk Management**: Configurable position limits, stop-losses, daily loss limits, and cash reserve requirements
- **Position Tracking**: SQLite-based P&L tracking with FIFO matching for accurate performance metrics

## Requirements

- Python 3.10+
- Alpaca paper trading account (free)
- OpenAI API key
- Optional: Polygon, Alpha Vantage, Twelve Data, Finnhub API keys for enhanced data

## Installation

```bash
git clone https://github.com/lulzkiller666/AutoTrader.git
cd AutoTrader
pip install -r requirements.txt
```

## Configuration

Create a `.env` file in the project root:

```bash
# Required
OPENAI_API_KEY=your_openai_key
ALPACA_API_KEY=your_alpaca_key
ALPACA_SECRET_KEY=your_alpaca_secret

# Optional - for better market data
POLYGON_API_KEY=your_polygon_key
ALPHA_VANTAGE_KEY=your_alphavantage_key
TWELVE_DATA_API_KEY=your_twelvedata_key
FINNHUB_API_KEY=your_finnhub_key

# Optional - model selection (defaults to gpt-4o)
TRADING_MODEL=gpt-4o
```

Get your API keys:
- Alpaca: https://alpaca.markets (free paper trading account)
- OpenAI: https://platform.openai.com
- Polygon: https://polygon.io (free tier available)
- Alpha Vantage: https://www.alphavantage.co (free tier available)
- Twelve Data: https://twelvedata.com (free tier available)

## Usage

### Running the Dashboard

The recommended way to run the system:

```bash
python run_dashboard.py --profile smart_aggressive
```

Available profiles:
- `conservative`: 8% max position, 3 trades/day, 8% stop-loss
- `aggressive`: 15% max position, 8 trades/day, 12% stop-loss
- `smart_aggressive`: 20% max position, 20 trades/day, 12% stop-loss (with dynamic universe)

### Running in Background (Linux/Mac)

For persistent operation, use screen:

```bash
# Start in background
screen -dmS trader python3 run_dashboard.py --profile smart_aggressive

# Attach to view dashboard
screen -r trader

# Detach (keep running): Ctrl+A then D
# Stop: Ctrl+C while attached
```

### Dashboard Layout

```
+---------------------------+---------------------------+
|     Trades & Decisions    |        Portfolio          |
|                           |                           |
| Recent AI decisions and   | Current positions, P&L,   |
| pending orders            | equity, cash balance      |
+---------------------------+---------------------------+
|  AI Activity  |  Market   |     System & API Log      |
|               |  Analysis |                           |
| Status and    | Universe  | Real-time system events   |
| AI messages   | stats     | and API calls             |
+---------------------------+---------------------------+
```

## Project Structure

```
AutoTrader/
├── run_dashboard.py          # Main entry point - dashboard + trading
├── core_v2/
│   ├── automated_trading_system.py  # AI decision engine
│   ├── trading_engine.py            # Memory and P&L tracking
│   ├── trading_scheduler.py         # Market-hours aware scheduling
│   ├── alpaca_broker.py             # Alpaca API integration
│   ├── dashboard.py                 # Terminal UI (Rich)
│   ├── dynamic_universe_manager.py  # Stock discovery and prioritization
│   ├── market_intelligence.py       # Opportunity screening
│   ├── enhanced_data_sources.py     # Multi-source data fetching
│   ├── news_sentiment.py            # News analysis
│   └── advanced_logging.py          # Structured JSONL logging
├── automated_trading/        # Runtime data (gitignored)
│   ├── trading_memory_v2.db  # SQLite database
│   └── universe_cache/       # Stock universe state
├── logs/                     # Daily JSONL logs (gitignored)
└── requirements.txt
```

## How It Works

1. **Stock Discovery**: Every hour, the system fetches top gainers, losers, and most active stocks from multiple APIs, combining them with the S&P 500 base to create a universe of 900-1200 stocks.

2. **Prioritization**: Stocks are assigned to tiers based on momentum:
   - Tier 1 (Hot): >5% daily change - screened every session
   - Tier 2 (Active): >2% daily change - screened every 2-3 sessions
   - Tier 3 (Core): S&P 500 base - screened every 4-5 sessions

3. **Trading Sessions**: Every 20 minutes during market hours (9:30 AM - 4:00 PM ET), the system:
   - Selects 25 stocks weighted by tier priority
   - Fetches comprehensive market data
   - Sends context to GPT-4o for analysis
   - Executes trades based on AI recommendations

4. **Risk Controls**: Before executing any trade, the system validates:
   - Position size limits
   - Cash reserve requirements
   - Daily trade limits
   - Concentration limits
   - Stop-loss placement

## Logs

The system writes structured JSONL logs to `logs/YYYY-MM-DD/`:

- `system.jsonl`: Universe refresh, tier changes
- `decisions.jsonl`: AI trading decisions with reasoning
- `executions.jsonl`: Order executions and fills
- `market_data.jsonl`: Data fetches from all sources
- `api_calls.jsonl`: External API calls with timing

View logs in real-time:
```bash
tail -f logs/$(date +%Y-%m-%d)/decisions.jsonl | jq
```

## Troubleshooting

**Dashboard shows raw text instead of UI**
```bash
rm -rf __pycache__ core_v2/__pycache__
# Restart the application
```

**"Max retries exceeded" errors**
Usually temporary network issues. The system will retry automatically.

**Position tracking discrepancies**
Check reconciliation on startup. The system logs any mismatches between the database and Alpaca.

## Disclaimer

This software is for educational purposes only. It uses paper trading (simulated money) and should not be used with real funds. Trading involves substantial risk of loss. The AI's decisions are not guaranteed to be profitable. Always do your own research before making investment decisions.

## License

This software is provided for educational and personal use only. Commercial use and redistribution are prohibited. See LICENSE file for details.
