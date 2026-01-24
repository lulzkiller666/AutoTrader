#!/usr/bin/env python3
"""
AutoTrader v2 - Dashboard Runner

Runs the trading system with a live split-screen dashboard UI.

IMPORTANT: Only ONE instance should run at a time. This script includes
safeguards to prevent multiple instances which cause rapid-fire trading.
"""

import os
import sys
import logging
import io
import signal
import subprocess
import atexit
import fcntl

# Don't completely disable logging - we want INFO to reach dashboard
# Just suppress specific noisy loggers later
os.environ['PYTHONWARNINGS'] = 'ignore'

import time
import threading
from datetime import datetime
from pathlib import Path

# Ensure we can import from core_v2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from rich.live import Live
from rich.console import Console

from core_v2.dashboard import TradingDashboard, DashboardIntegration

# =============================================================================
# INSTANCE LOCKING - Prevent multiple dashboards from running
# =============================================================================

PID_FILE = Path(__file__).parent / "automated_trading" / ".dashboard.pid"
LOCK_FILE = Path(__file__).parent / "automated_trading" / ".dashboard.lock"

def kill_existing_instances():
    """Kill any existing dashboard instances before starting"""
    killed = []
    ancestors = set()  # Will be populated in Method 2

    # Method 1: Kill by PID file
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            # Check if process is still running
            try:
                os.kill(old_pid, 0)  # Signal 0 just checks if process exists
                os.kill(old_pid, signal.SIGTERM)
                killed.append(f"PID {old_pid}")
                time.sleep(0.5)
            except ProcessLookupError:
                pass  # Process already dead
            except PermissionError:
                pass  # Not our process
            PID_FILE.unlink(missing_ok=True)
        except (ValueError, IOError):
            PID_FILE.unlink(missing_ok=True)

    # Method 2: Kill any python processes running this script (but not ourselves or ancestors)
    try:
        # Get all ancestor PIDs to avoid killing our own process tree
        ancestors = set()
        try:
            pid = os.getpid()
            while pid > 1:
                ancestors.add(str(pid))
                # Get parent PID by reading /proc/PID/stat
                with open(f'/proc/{pid}/stat', 'r') as f:
                    stat = f.read().split()
                    pid = int(stat[3])  # PPID is the 4th field
        except (FileNotFoundError, IndexError, ValueError):
            # Fallback to just current and direct parent
            ancestors = {str(os.getpid()), str(os.getppid())}

        result = subprocess.run(
            ["pgrep", "-f", "run_dashboard.py"],
            capture_output=True, text=True
        )
        if result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            for pid in pids:
                if pid and pid not in ancestors:
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                        killed.append(f"PID {pid}")
                    except (ProcessLookupError, PermissionError, ValueError):
                        pass
    except FileNotFoundError:
        pass  # pgrep not available

    # Method 3: Kill screen sessions named 'trader' (but not our ancestors)
    try:
        # Re-use ancestors set from above, or recreate if needed
        if not ancestors:
            ancestors = {str(os.getpid()), str(os.getppid())}

        result = subprocess.run(
            ["screen", "-ls"],
            capture_output=True, text=True
        )
        for line in result.stdout.split('\n'):
            if 'trader' in line.lower() and 'attached' not in line.lower():
                session_id = line.strip().split('.')[0].strip()
                if session_id and session_id.isdigit():
                    # Don't kill screen sessions that are our ancestors
                    if session_id not in ancestors:
                        subprocess.run(["screen", "-S", session_id, "-X", "quit"], capture_output=True)
                        killed.append(f"screen:{session_id}")
    except FileNotFoundError:
        pass  # screen not available

    if killed:
        print(f"[CLEANUP] Killed existing instances: {', '.join(killed)}")
        time.sleep(1)  # Give processes time to die

    return killed

def acquire_lock():
    """Acquire exclusive lock to prevent multiple instances"""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

    try:
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        # Write PID
        PID_FILE.write_text(str(os.getpid()))

        # Register cleanup - DON'T delete lock file, just release the lock
        # Deleting the file breaks locking because the lock is on the inode, not the path
        def cleanup():
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                lock_fd.close()
                # Only remove PID file, keep lock file for proper locking
                PID_FILE.unlink(missing_ok=True)
            except:
                pass

        atexit.register(cleanup)
        return lock_fd

    except (IOError, OSError) as e:
        print(f"\n{'='*60}")
        print("ERROR: Another dashboard instance is already running!")
        print(f"{'='*60}")
        print(f"\nLock file: {LOCK_FILE}")
        print(f"PID file: {PID_FILE}")
        print("\nTo force restart, run:")
        print("  screen -ls | grep trader | awk '{print $1}' | xargs -I {} screen -S {} -X quit")
        print("  pkill -f 'run_dashboard.py'")
        print(f"{'='*60}\n")
        sys.exit(1)

def clear_python_cache():
    """Clear Python cache to ensure fresh code is used"""
    cache_dirs = [
        Path(__file__).parent / "__pycache__",
        Path(__file__).parent / "core_v2" / "__pycache__"
    ]
    for cache_dir in cache_dirs:
        if cache_dir.exists():
            import shutil
            shutil.rmtree(cache_dir, ignore_errors=True)


class StdoutCapture:
    """Capture stdout (print statements) and route to dashboard"""

    def __init__(self, integration=None):
        self.integration = integration
        self.original_stdout = sys.stdout
        self.buffer = io.StringIO()

    def set_integration(self, integration):
        """Set integration after initialization"""
        self.integration = integration

    def write(self, text):
        """Capture writes and route to dashboard"""
        # Always write to buffer (for debugging if needed)
        self.buffer.write(text)

        # Skip empty or whitespace-only text
        if not text or not text.strip():
            return

        # Clean up the message
        clean_text = text.strip()

        # Skip separator lines
        if clean_text.startswith('=') and '=' * 10 in clean_text:
            return

        # Route to dashboard if integration available
        if self.integration:
            # Determine log type based on content
            text_lower = clean_text.lower()
            if 'success' in text_lower or 'connected' in text_lower:
                log_type = "success"
            elif 'error' in text_lower or 'failed' in text_lower:
                log_type = "error"
            elif 'warning' in text_lower or 'blocked' in text_lower:
                log_type = "warning"
            else:
                log_type = "info"

            display_text = clean_text.strip()

            # Skip DEBUG lines
            if 'DEBUG:' in display_text:
                return

            self.integration.on_system_event(display_text, log_type)

    def flush(self):
        """Required for stdout compatibility"""
        pass


def suppress_console_logging():
    """Suppress all console logging so rich UI can render cleanly"""
    # Remove all handlers that output to console
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        if isinstance(handler, logging.StreamHandler):
            root_logger.removeHandler(handler)

    # Also suppress specific loggers
    for name in ['httpx', 'trading_engine', 'enhanced_data_sources', 'market_intelligence',
                 'automated_trading_system', 'TradingScheduler', 'system', 'core_v2']:
        logger = logging.getLogger(name)
        logger.handlers = [h for h in logger.handlers if not isinstance(h, logging.StreamHandler)]
        logger.propagate = False


class DashboardLogHandler(logging.Handler):
    """Custom logging handler that sends logs to the dashboard"""

    def __init__(self, dashboard_integration):
        super().__init__()
        self.integration = dashboard_integration
        self.fetch_count = 0
        self.fetch_success_count = 0
        self.fetch_error_count = 0

    def emit(self, record):
        try:
            msg = self.format(record)

            # Filter out noise: only show every 10th successful data fetch
            if "via Polygon.io" in msg or "via Alpha Vantage" in msg or "via Stooq" in msg:
                self.fetch_count += 1
                if "error" not in msg.lower() and "failed" not in msg.lower():
                    self.fetch_success_count += 1
                    if self.fetch_count % 10 == 0:
                        summary_msg = f"Fetched market data for {self.fetch_success_count} stocks ({self.fetch_error_count} errors)"
                        self.integration.on_system_event(summary_msg, "info")
                    return
                else:
                    self.fetch_error_count += 1

            # Determine log type based on level and content
            if record.levelno >= logging.ERROR:
                log_type = "error"
            elif record.levelno >= logging.WARNING:
                log_type = "warning"
            elif "API" in msg or "api" in msg.lower():
                if "error" in msg.lower() or "failed" in msg.lower():
                    log_type = "api_error"
                else:
                    log_type = "api"
            elif "success" in msg.lower():
                log_type = "success"
            else:
                log_type = "info"

            # Don't show debug-level cache hits
            if "Cache hit" in msg:
                return

            self.integration.on_system_event(msg.strip(), log_type)
        except Exception:
            pass  # Don't let logging errors break the system


def get_trading_imports():
    """Lazy import trading system components"""
    from core_v2.automated_trading_system import AutomatedTradingSystem as TradingSystemV2, TradingProfile
    from core_v2.alpaca_broker import AlpacaBroker
    from core_v2.advanced_logging import get_logger
    return TradingSystemV2, TradingProfile, AlpacaBroker, get_logger


class DashboardTradingSystem:
    """Trading system with integrated dashboard UI"""

    def __init__(self, profile: str = "conservative"):
        self.console = Console()

        # Lazy import trading components
        TradingSystemV2, TradingProfile, AlpacaBroker, get_logger = get_trading_imports()

        self.logger = get_logger()

        # Initialize dashboard
        self.dashboard = TradingDashboard()
        self.integration = DashboardIntegration(self.dashboard)

        # Convert string profile to enum
        profile_enum = getattr(TradingProfile, profile.upper(), TradingProfile.CONSERVATIVE)

        # Initialize trading components
        self.broker = AlpacaBroker()
        self.trading_system = TradingSystemV2(profile=profile_enum)

        # Log system initialization
        self.integration.on_system_event("Trading system initialized", "success")
        self.integration.on_system_event(f"Connected to Alpaca Paper Trading API", "success")

        # Don't capture stdout globally - it breaks Rich rendering
        # Instead, we'll use logging handlers to capture output

        # Set up dashboard log handler to capture all logs
        self._setup_dashboard_logging()

        # Connect broker to trading system for real execution
        self._connect_broker_execution()

        # Hook up the dashboard callbacks
        self._setup_hooks()

        self._running = False
        self._update_thread = None
        self._pending_sell_orders = {}  # Track pending SELL orders for P/L when they fill

    def _setup_dashboard_logging(self):
        """Set up logging handler to send logs to dashboard"""
        # Create dashboard log handler
        dashboard_handler = DashboardLogHandler(self.integration)
        dashboard_handler.setLevel(logging.INFO)
        dashboard_handler.setFormatter(logging.Formatter('%(message)s'))

        # List of all loggers to manage
        logger_names = [
            'enhanced_data_sources', 'market_intelligence', 'automated_trading_system',
            'trading_engine', 'alpaca_broker', 'dynamic_universe_manager', 'news_sentiment',
            'core_v2', 'system', 'httpx', 'httpcore', '__main__'
        ]

        # Remove all StreamHandlers and add dashboard handler
        for logger_name in logger_names:
            log = logging.getLogger(logger_name)
            # Remove any existing StreamHandlers (they write to console)
            log.handlers = [h for h in log.handlers if not isinstance(h, logging.StreamHandler)]
            log.addHandler(dashboard_handler)
            log.setLevel(logging.INFO)
            log.propagate = False  # Don't propagate to root logger

        # Also clean up the root logger
        root_logger = logging.getLogger()
        root_logger.handlers = [h for h in root_logger.handlers if not isinstance(h, logging.StreamHandler)]

    def _connect_broker_execution(self):
        """Override trading system's execute_trade_action to use real broker"""
        broker = self.broker
        integration = self.integration
        memory_engine = self.trading_system.memory_engine

        def real_execute_trade_action(action):
            """Execute trades via Alpaca broker with P/L tracking"""
            try:
                if action.action_type == "BUY":
                    integration.on_ai_message(f"Executing BUY {action.shares} {action.ticker}...", "info")
                    integration.on_system_event(f"Placing BUY order: {action.shares} {action.ticker}", "info")
                    order_id = broker.place_buy_order(action.ticker, action.shares)
                    if order_id:
                        integration.on_ai_message(f"BUY order placed: {order_id}", "success")
                        integration.on_system_event(f"Alpaca order submitted: {order_id}", "success")

                        # Track position for P/L calculation
                        filled_price = None
                        try:
                            filled_price = broker.get_order_filled_price(order_id)
                            if not filled_price:
                                filled_price = action.target_price or broker.get_latest_price(action.ticker)
                            if filled_price:
                                decision_id = memory_engine.get_latest_decision_id()
                                memory_engine.track_position_open(
                                    ticker=action.ticker,
                                    entry_price=filled_price,
                                    quantity=action.shares,
                                    decision_id=decision_id
                                )
                                integration.on_system_event(f"Position tracked: {action.shares} {action.ticker} @ ${filled_price:.2f}", "info")
                        except Exception as e:
                            integration.on_system_event(f"P/L tracking error: {str(e)[:30]}", "warning")

                        # Place stop-loss order to protect the position
                        stop_loss_order_id = None
                        stop_loss_price = action.stop_loss if hasattr(action, 'stop_loss') else 0

                        # If AI didn't set a stop-loss, calculate one using default 12%
                        if stop_loss_price <= 0 and filled_price and filled_price > 0:
                            stop_loss_price = filled_price * 0.88  # 12% stop-loss
                            integration.on_system_event(f"Calculated stop-loss: ${stop_loss_price:.2f} (12% below entry)", "info")

                        # Place the stop-loss order
                        if stop_loss_price > 0:
                            try:
                                stop_loss_order_id = broker.place_stop_loss_order(action.ticker, action.shares, stop_loss_price)
                                if stop_loss_order_id:
                                    integration.on_ai_message(f"STOP-LOSS set: ${stop_loss_price:.2f}", "success")
                                    integration.on_system_event(f"Stop-loss order: {action.ticker} @ ${stop_loss_price:.2f} (ID: {stop_loss_order_id})", "success")
                                else:
                                    integration.on_system_event(f"Stop-loss order failed for {action.ticker}", "warning")
                            except Exception as e:
                                integration.on_system_event(f"Stop-loss error: {str(e)[:30]}", "warning")

                        return {"status": "submitted", "order_id": order_id, "action": "BUY", "ticker": action.ticker, "shares": action.shares, "stop_loss_order_id": stop_loss_order_id}
                    else:
                        integration.on_error(f"BUY order failed for {action.ticker}")
                        integration.on_system_event(f"Alpaca order rejected: {action.ticker}", "error")
                        return {"status": "failed", "action": "BUY", "ticker": action.ticker, "error": "Order rejected"}

                elif action.action_type == "SELL":
                    integration.on_ai_message(f"Executing SELL {action.shares} {action.ticker}...", "info")
                    integration.on_system_event(f"Placing SELL order: {action.shares} {action.ticker}", "info")
                    order_id = broker.place_sell_order(action.ticker, action.shares)
                    if order_id:
                        integration.on_ai_message(f"SELL order placed: {order_id}", "success")
                        integration.on_system_event(f"Alpaca order submitted: {order_id}", "success")

                        # Check if order is filled before tracking position close
                        # This prevents premature closing for after-hours/pending orders
                        try:
                            time.sleep(0.5)  # Brief wait for order to process
                            order_details = broker.get_order_details(order_id)

                            if order_details and order_details.get('is_filled'):
                                # Order filled immediately - track position close now
                                filled_price = order_details.get('filled_avg_price')
                                if filled_price:
                                    decision_id = memory_engine.get_latest_decision_id()
                                    memory_engine.track_position_close(
                                        ticker=action.ticker,
                                        exit_price=filled_price,
                                        quantity=action.shares,
                                        decision_id=decision_id
                                    )
                                    integration.on_system_event(f"Position closed: {action.shares} {action.ticker} @ ${filled_price:.2f}", "info")
                            elif order_details and order_details.get('is_pending'):
                                # Order is pending (after-hours, etc) - will be tracked when filled
                                integration.on_system_event(f"SELL order pending - will track when filled", "info")
                                # Store pending order for later processing
                                self._pending_sell_orders[order_id] = {
                                    'ticker': action.ticker,
                                    'shares': action.shares,
                                    'decision_id': memory_engine.get_latest_decision_id()
                                }
                            else:
                                integration.on_system_event(f"Order status unknown - will monitor", "warning")
                        except Exception as e:
                            integration.on_system_event(f"P/L tracking error: {str(e)[:30]}", "warning")

                        return {"status": "submitted", "order_id": order_id, "action": "SELL", "ticker": action.ticker, "shares": action.shares}
                    else:
                        integration.on_error(f"SELL order failed for {action.ticker}")
                        integration.on_system_event(f"Alpaca order rejected: {action.ticker}", "error")
                        return {"status": "failed", "action": "SELL", "ticker": action.ticker, "error": "Order rejected"}

                else:
                    return {"status": "hold", "action": "HOLD", "ticker": action.ticker}

            except Exception as e:
                error_msg = f"Execution error: {str(e)[:50]}"
                integration.on_error(error_msg)
                integration.on_system_event(error_msg, "error")
                return {"status": "error", "action": action.action_type, "ticker": action.ticker, "error": str(e)}

        # Replace the stub with real execution
        self.trading_system.execute_trade_action = real_execute_trade_action

    def _setup_hooks(self):
        """Setup hooks to feed data to dashboard"""
        integration = self.integration

        # Store original methods to wrap them
        original_get_decision = self.trading_system.get_trading_decision

        def wrapped_get_decision(*args, **kwargs):
            integration.on_phase_change("Deciding")
            integration.on_ai_thinking("Calling GPT-4 for trading decision...")
            integration.on_system_event("Calling OpenAI GPT-4o API...", "info")
            start = time.time()
            result = original_get_decision(*args, **kwargs)
            duration = (time.time() - start) * 1000

            if result:
                integration.on_decision({
                    'action': result.action_type,
                    'ticker': result.ticker,
                    'confidence': result.confidence,
                    'reasoning': result.reasoning  # Full reasoning, not truncated
                })
                integration.on_api_call("OpenAI GPT-4o", duration)
                integration.on_phase_change("Executing" if result.action_type != "HOLD" else "Waiting")
            else:
                integration.on_system_event("OpenAI API call failed", "api_error")
                integration.on_phase_change("Waiting")

            return result

        self.trading_system.get_trading_decision = wrapped_get_decision

        # Hook market intelligence screening
        if hasattr(self.trading_system, 'market_intelligence'):
            mi = self.trading_system.market_intelligence
            original_screen = mi.screen_for_opportunities

            def wrapped_screen(*args, **kwargs):
                integration.on_phase_change("Screening")
                integration.on_ai_message(f"Screening {mi.universe_size} stocks...", "thinking")
                result = original_screen(*args, **kwargs)

                if result:
                    # Format opportunities for dashboard
                    opps = []
                    for opp in result[:10]:
                        # Map market_intelligence field names to dashboard expected names
                        change_1d = opp.get('change_1d', 0)
                        change_pct = change_1d * 100 if isinstance(change_1d, float) and abs(change_1d) < 1 else change_1d
                        opps.append({
                            'ticker': opp.get('ticker', ''),
                            'score': opp.get('opportunity_score', 0),  # market_intelligence uses opportunity_score
                            'change': change_pct,  # Convert decimal to percentage
                            'signal': ', '.join(opp.get('technical_signals', [])[:2])
                        })
                    integration.on_opportunities_found(opps)
                    integration.on_market_update(
                        mi.market_phase.value if hasattr(mi, 'market_phase') else 'Unknown',
                        mi.universe_size,
                        None
                    )
                    # Extract signals
                    all_signals = []
                    for opp in result[:5]:
                        all_signals.extend(opp.get('technical_signals', []))
                    if all_signals:
                        integration.on_signals_update(list(set(all_signals))[:5])

                integration.on_phase_change("Analyzing")
                return result

            mi.screen_for_opportunities = wrapped_screen

        # Hook run_trading_session for phase tracking
        original_run_session = self.trading_system.run_trading_session

        def wrapped_run_session(session, *args, **kwargs):
            session_name = session.value if hasattr(session, 'value') else str(session)
            integration.on_phase_change("Starting")
            integration.on_ai_message(f"Starting {session_name} session", "info")
            integration.on_status_change(f"Running {session_name} analysis")

            # Fetch and push universe stats to dashboard
            try:
                if hasattr(self.trading_system, 'market_intelligence'):
                    universe_stats = self.trading_system.market_intelligence.get_universe_stats_for_dashboard()
                    integration.on_universe_update(universe_stats)
                    integration.on_ai_message(
                        f"Universe: {universe_stats['total_stocks']} stocks "
                        f"(T1: {universe_stats['tier1_count']}, T2: {universe_stats['tier2_count']}, T3: {universe_stats['tier3_count']})",
                        "info"
                    )
            except Exception as e:
                # Non-critical - continue if universe stats fail
                self.logger.warning(f"Could not update universe stats: {e}")

            try:
                result = original_run_session(session, *args, **kwargs)
                integration.on_phase_change("Idle")
                integration.on_status_change("Scheduler running - monitoring markets")
                return result
            except Exception as e:
                integration.on_error(f"Session error: {str(e)[:50]}")
                integration.on_phase_change("Error")
                raise

        self.trading_system.run_trading_session = wrapped_run_session

    def _portfolio_update_loop(self):
        """Background thread to update portfolio data"""
        update_count = 0
        first_update = True
        while self._running:
            try:
                # Get account info
                account = self.broker.get_account_info()
                positions = self.broker.get_positions()
                pending_orders = self.broker.get_orders(status='open')

                # Log successful fetch on first update or every 20 updates
                if first_update or update_count % 20 == 0:
                    self.integration.on_system_event(f"Fetched portfolio: {len(positions)} positions, {len(pending_orders)} pending orders", "info")
                    first_update = False

                # Calculate day P&L properly
                equity = float(account.get('equity', 0))
                last_equity = float(account.get('last_equity', equity))
                day_pnl = equity - last_equity if last_equity != equity else 0.0

                portfolio_data = {
                    'equity': equity,
                    'cash': float(account.get('cash', 0)),
                    'stocks_value': float(account.get('long_market_value', 0)),  # Actual stocks value
                    'day_pnl': day_pnl,
                    'day_pnl_pct': (day_pnl / last_equity * 100) if last_equity > 0 else 0.0,
                }

                position_list = []
                for pos in positions:
                    position_list.append({
                        'symbol': pos.get('ticker', pos.get('symbol', '')),
                        'qty': float(pos.get('shares', pos.get('qty', 0))),
                        'market_value': float(pos.get('market_value', 0)),
                        'cost_basis': float(pos.get('cost_basis', 0)),
                        'unrealized_pl': float(pos.get('unrealized_pl', 0)),  # Direct from Alpaca
                        'unrealized_plpc': float(pos.get('unrealized_plpc', 0)),  # Direct from Alpaca
                    })

                # Sort by market value descending
                position_list.sort(key=lambda x: x['market_value'], reverse=True)

                self.dashboard.update_portfolio(portfolio_data, position_list)
                self.dashboard.update_pending_orders(pending_orders)
                update_count += 1

                # Update market status every update (shows live countdown)
                self.dashboard.update_market_status()

                # Load watchlist every 10 updates (every 30 seconds)
                if update_count % 10 == 0:
                    self._load_watchlist()

                # Update trading memory every 20 updates (every 60 seconds)
                if update_count % 20 == 0:
                    self._update_trading_memory()

            except Exception as e:
                error_msg = f"Portfolio update error: {str(e)[:50]}"
                self.integration.on_error(error_msg)
                self.integration.on_system_event(error_msg, "error")

            time.sleep(3)  # Update every 3 seconds

    def _check_orders_loop(self):
        """Background thread to check for filled orders"""
        seen_orders = set()

        while self._running:
            try:
                orders = self.broker.get_orders(status='all')

                for order in orders:
                    order_id = order.get('id', '')
                    if order_id and order_id not in seen_orders:
                        seen_orders.add(order_id)

                        status = order.get('status', '')
                        if status in ['filled', 'partially_filled']:
                            self.integration.on_trade({
                                'symbol': order.get('symbol', ''),
                                'side': order.get('side', 'buy'),
                                'qty': int(float(order.get('filled_qty', 0) or order.get('qty', 0))),
                                'status': status,
                            })

                            # Check if this is a pending SELL order we need to track
                            if order_id in self._pending_sell_orders and order.get('side') == 'sell':
                                pending = self._pending_sell_orders.pop(order_id)
                                filled_price = float(order.get('filled_avg_price', 0))
                                if filled_price > 0:
                                    try:
                                        from core_v2.trading_engine import TradingMemoryEngine
                                        memory_engine = TradingMemoryEngine(
                                            str(Path(__file__).resolve().parent / "automated_trading" / "trading_memory_v2.db")
                                        )
                                        memory_engine.track_position_close(
                                            ticker=pending['ticker'],
                                            exit_price=filled_price,
                                            quantity=pending['shares'],
                                            decision_id=pending['decision_id']
                                        )
                                        self.integration.on_system_event(
                                            f"Position closed (delayed): {pending['shares']} {pending['ticker']} @ ${filled_price:.2f}",
                                            "success"
                                        )
                                    except Exception as e:
                                        self.integration.on_system_event(f"Delayed P/L tracking error: {str(e)[:30]}", "warning")

            except Exception as e:
                self.integration.on_error(f"Order check failed: {str(e)[:50]}")
                self.logger.warning(f"Order check error (will retry): {e}")
                # Continue loop - non-critical, will retry in 10 seconds

            time.sleep(10)  # Check every 10 seconds

    def _load_watchlist(self):
        """Load overnight watchlist from file and update dashboard"""
        try:
            from core_v2.automated_trading_system import load_watchlist
            from pathlib import Path

            data_dir = Path(__file__).resolve().parent / "automated_trading"
            watchlist = load_watchlist(data_dir)

            if watchlist:
                self.integration.on_watchlist_update(watchlist)
            else:
                self.dashboard.set_watchlist([])
        except Exception as e:
            self.logger.warning(f"Failed to load watchlist: {e}")
            self.integration.on_error(f"Watchlist load failed")
            # Continue - watchlist is optional, system works without it

    def _load_recent_decisions(self):
        """Load today's decisions from JSON log file to show in AI Activity"""
        try:
            from pathlib import Path
            from datetime import datetime
            import json

            # Get today's log file
            today = datetime.now().strftime("%Y-%m-%d")
            log_file = Path(__file__).resolve().parent / "logs" / today / "decisions.jsonl"

            if not log_file.exists():
                return

            # Read only last 100 lines efficiently (don't load entire file)
            with open(log_file, 'rb') as f:
                # Seek to end
                f.seek(0, 2)
                file_size = f.tell()
                # Read last ~10KB (roughly 100 lines)
                chunk_size = min(10240, file_size)
                f.seek(max(0, file_size - chunk_size))
                lines = f.read().decode('utf-8', errors='ignore').splitlines()
                # Keep only last 100 lines
                lines = lines[-100:]

            # Collect decisions in a list first, then add them in chronological order (oldest to newest)
            decisions_to_add = []
            for line in reversed(lines[-100:]):  # Last 100 lines to ensure we get recent decisions
                try:
                    entry = json.loads(line)
                    if entry.get('message') == 'Trading decision made':
                        decision = entry.get('data', {}).get('decision', {})
                        action = decision.get('action', '')
                        ticker = decision.get('ticker', '')
                        confidence = decision.get('confidence', 0)
                        reasoning = decision.get('reasoning', '')

                        # Extract original timestamp from log entry
                        timestamp_iso = entry.get('timestamp', '')
                        # Convert from "2026-01-07T10:41:19.213295Z" (UTC) to local time "10:41:19"
                        if timestamp_iso and 'T' in timestamp_iso:
                            try:
                                from datetime import datetime
                                # Parse UTC timestamp
                                dt_utc = datetime.fromisoformat(timestamp_iso.replace('Z', '+00:00'))
                                # Convert to local time
                                dt_local = dt_utc.astimezone()
                                time_str = dt_local.strftime("%H:%M:%S")
                            except Exception:
                                # Fallback: just use the time part as-is
                                time_part = timestamp_iso.split('T')[1]
                                time_str = time_part[:8]  # Get HH:MM:SS
                        else:
                            time_str = None  # Will use current time as fallback

                        if action and reasoning:
                            decisions_to_add.append({
                                'action': action,
                                'ticker': ticker,
                                'confidence': confidence,
                                'reasoning': reasoning,
                                'timestamp': time_str
                            })

                            if len(decisions_to_add) >= 5:  # Limit to last 5
                                break
                except (json.JSONDecodeError, KeyError):
                    continue

            # Now add them in chronological order (oldest to newest)
            for decision_data in reversed(decisions_to_add):
                self.dashboard.add_decision(
                    action=decision_data['action'],
                    ticker=decision_data['ticker'],
                    confidence=decision_data['confidence'],
                    reasoning=decision_data['reasoning'],
                    timestamp=decision_data['timestamp']
                )

            decisions_found = len(decisions_to_add)

            if decisions_found > 0:
                self.integration.on_ai_message(f"Loaded {decisions_found} recent decisions", "info")

        except Exception as e:
            self.logger.warning(f"Failed to load recent decisions: {e}")
            # Continue - this is optional

    def _update_trading_memory(self):
        """Load recent trading decisions from database and update dashboard"""
        try:
            import sqlite3
            from pathlib import Path

            db_path = Path(__file__).resolve().parent / "automated_trading" / "trading_memory_v2.db"
            if not db_path.exists():
                return

            conn = sqlite3.connect(str(db_path), timeout=5.0)
            cursor = conn.cursor()

            # Get last 5 decisions
            cursor.execute("""
                SELECT timestamp, decision, confidence, outcome, profit_loss
                FROM trading_decisions
                ORDER BY timestamp DESC
                LIMIT 5
            """)

            memory = []
            for row in cursor.fetchall():
                memory.append({
                    'timestamp': row[0],
                    'decision': row[1],
                    'confidence': row[2],
                    'outcome': row[3],
                    'profit_loss': row[4]
                })

            conn.close()

            self.dashboard.update_trading_memory(memory)

        except Exception as e:
            self.logger.warning(f"Failed to load trading memory: {e}")
            # Continue - memory display is optional

    def run(self):
        """Run the trading system with dashboard"""
        self._running = True

        # Log session start
        self.logger.log_session_start("dashboard", self.trading_system.profile.name)
        self.integration.on_status_change("Initializing trading system...")

        # Load initial data
        try:
            account = self.broker.get_account_info()
            positions = self.broker.get_positions()

            portfolio_data = {
                'equity': float(account.get('equity', 0)),
                'cash': float(account.get('cash', 0)),
                'day_pnl': 0,
            }

            position_list = []
            for pos in positions:
                position_list.append({
                    'symbol': pos.get('ticker', pos.get('symbol', '')),
                    'qty': float(pos.get('shares', pos.get('qty', 0))),
                    'market_value': float(pos.get('market_value', 0)),
                    'cost_basis': float(pos.get('cost_basis', 0)),
                    'unrealized_pl': float(pos.get('unrealized_pl', 0)),  # Direct from Alpaca
                    'unrealized_plpc': float(pos.get('unrealized_plpc', 0)),  # Direct from Alpaca
                })

            position_list.sort(key=lambda x: x['market_value'], reverse=True)
            self.dashboard.update_portfolio(portfolio_data, position_list)

            self.integration.on_ai_message(f"Loaded {len(positions)} positions", "info")
            self.integration.on_system_event(f"Portfolio loaded: {len(positions)} positions, ${portfolio_data['equity']:,.2f} equity", "success")
            self.integration.on_status_change("Trading system ready - Scheduler running")

            # Load universe stats on startup
            try:
                if hasattr(self.trading_system, 'market_intelligence'):
                    universe_stats = self.trading_system.market_intelligence.get_universe_stats_for_dashboard()
                    self.integration.on_universe_update(universe_stats)
                    self.integration.on_ai_message(
                        f"Universe loaded: {universe_stats['total_stocks']} stocks "
                        f"(T1: {universe_stats['tier1_count']}, T2: {universe_stats['tier2_count']}, T3: {universe_stats['tier3_count']})",
                        "info"
                    )
                    self.integration.on_system_event(f"Dynamic universe initialized with {universe_stats['total_stocks']} stocks", "success")
            except Exception as e:
                self.logger.warning(f"Could not load universe stats on startup: {e}")

            # Load any existing watchlist
            self._load_watchlist()

            # Load recent decisions from today's log
            self._load_recent_decisions()

        except Exception as e:
            self.integration.on_error(f"Init failed: {str(e)}")
            self.integration.on_status_change("Error during initialization")

        # Start background threads
        self._update_thread = threading.Thread(target=self._portfolio_update_loop, daemon=True)
        self._update_thread.start()

        self._order_thread = threading.Thread(target=self._check_orders_loop, daemon=True)
        self._order_thread.start()

        # Start scheduler in background
        scheduler_thread = threading.Thread(target=self._run_scheduler, daemon=True)
        scheduler_thread.start()

        # Run dashboard in main thread
        try:
            with Live(self.dashboard.render(), console=self.console, refresh_per_second=1, screen=True) as live:
                while self._running:
                    live.update(self.dashboard.render())
                    time.sleep(1)
        except KeyboardInterrupt:
            self._running = False
            self.console.print("\n[bold yellow]Shutting down...[/]")
            self.logger.log_session_end("dashboard", {"reason": "user_interrupt"})

    def _run_scheduler(self):
        """Run the trading scheduler"""
        import signal
        from core_v2.trading_scheduler import TradingScheduler, TradingProfile

        try:
            # Temporarily disable signal handling for thread
            original_sigint = signal.getsignal(signal.SIGINT)
            original_sigterm = signal.getsignal(signal.SIGTERM)

            # Create scheduler - will fail on signal setup, but we catch it
            profile_enum = getattr(TradingProfile, self.trading_system.profile.name, TradingProfile.CONSERVATIVE)

            try:
                scheduler = TradingScheduler(profile=profile_enum)
            except ValueError as e:
                if "signal only works in main thread" in str(e):
                    # Monkey-patch the signal setup to do nothing, then retry
                    import signal as sig_module
                    original_signal = sig_module.signal
                    sig_module.signal = lambda *args: None
                    scheduler = TradingScheduler(profile=profile_enum)
                    sig_module.signal = original_signal
                else:
                    raise

            # Replace scheduler's system with our hooked system
            scheduler.system = self.trading_system

            self.integration.on_status_change("Scheduler running - monitoring markets")
            self.integration.on_ai_message("Trading scheduler started", "info")
            self.integration.on_system_event(f"Scheduler started with {profile_enum.name} profile", "success")

            # Store scheduler reference for manual triggers
            self._scheduler = scheduler

            # Run scheduler loop directly
            # Use aggressive scheduling if ultra-aggressive or smart-aggressive profile
            if profile_enum.name in ['ULTRA_AGGRESSIVE', 'SMART_AGGRESSIVE']:
                scheduler.schedule_aggressive_sessions()
            else:
                scheduler.schedule_trading_sessions()

            scheduler.print_schedule_summary()
            scheduler.running = True
            scheduler.run_scheduler_loop()

        except Exception as e:
            self.integration.on_error(f"Scheduler error: {str(e)}")
            self.integration.on_status_change("Scheduler stopped due to error")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="AutoTrader v2 Dashboard")
    parser.add_argument("--profile", "-p", default="conservative",
                       choices=["conservative", "moderate", "aggressive", "ultra_aggressive", "smart_aggressive"],
                       help="Trading profile")
    parser.add_argument("--demo", action="store_true",
                       help="Run in demo mode with fake data")
    parser.add_argument("--force", "-f", action="store_true",
                       help="Force start by killing existing instances")
    parser.add_argument("--no-cache-clear", action="store_true",
                       help="Don't clear Python cache on startup")

    args = parser.parse_args()

    print(f"\n{'='*60}")
    print("  AutoTrader v2 - Dashboard Startup")
    print(f"{'='*60}")

    # Step 1: Kill existing instances if --force or always for safety
    if args.force:
        print("\n[1/4] Force mode: Killing existing instances...")
        kill_existing_instances()
    else:
        # Always kill old instances to prevent duplicates
        print("\n[1/4] Checking for existing instances...")
        killed = kill_existing_instances()
        if not killed:
            print("      No existing instances found")

    # Step 2: Clear Python cache for fresh code
    if not args.no_cache_clear:
        print("[2/4] Clearing Python cache...")
        clear_python_cache()
    else:
        print("[2/4] Skipping cache clear (--no-cache-clear)")

    # Step 3: Acquire exclusive lock
    print("[3/4] Acquiring instance lock...")
    lock_fd = acquire_lock()
    print(f"      Lock acquired (PID: {os.getpid()})")

    # Step 4: Suppress console logging so rich UI can render cleanly
    print("[4/4] Starting dashboard...")
    print(f"{'='*60}\n")
    suppress_console_logging()

    if args.demo:
        # Run standalone demo
        dashboard = TradingDashboard()
        dashboard.run_standalone()
    else:
        # Run with real trading system
        system = DashboardTradingSystem(profile=args.profile)
        system.run()


if __name__ == "__main__":
    main()
