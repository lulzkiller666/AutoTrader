#!/usr/bin/env python3
"""
AutoTrader v2 - Live Trading Dashboard

Split-screen terminal UI showing:
- Left: Recent trades and decisions
- Right: Live portfolio value and positions
- Bottom: AI decision-making output
"""

import os
import sys
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from collections import deque
import pytz

# Timezone for display
LOCAL_TZ = pytz.timezone('Europe/Copenhagen')

def local_now():
    """Get current time in local timezone"""
    return datetime.now(LOCAL_TZ)


def get_market_status():
    """Get current market status with countdown"""
    et = pytz.timezone('US/Eastern')
    now = datetime.now(et)

    # Market hours: 9:30 AM - 4:00 PM ET
    market_open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close_time = now.replace(hour=16, minute=0, second=0, microsecond=0)

    is_weekend = now.weekday() >= 5

    if is_weekend:
        # Weekend - calculate next Monday
        days_until_monday = (7 - now.weekday())
        next_open = market_open_time + timedelta(days=days_until_monday)
        hours_until = (next_open - now).total_seconds() / 3600
        # Convert to local time for display
        next_open_local = next_open.astimezone(LOCAL_TZ)

        return {
            'is_open': False,
            'status_text': 'CLOSED (Weekend)',
            'countdown': f'Opens in {hours_until:.1f}h ({next_open_local.strftime("%a %H:%M")})'
        }

    elif now.hour < 9 or (now.hour == 9 and now.minute < 30):
        # Before market open today
        minutes_until = (market_open_time - now).total_seconds() / 60

        return {
            'is_open': False,
            'status_text': 'CLOSED (Pre-market)',
            'countdown': f'Opens in {int(minutes_until)}m'
        }

    elif now.hour >= 16:
        # After market close
        next_open = market_open_time + timedelta(days=1)
        # Check if tomorrow is weekend
        if next_open.weekday() >= 5:
            days_until_monday = (7 - next_open.weekday())
            next_open = next_open + timedelta(days=days_until_monday)

        hours_until = (next_open - now).total_seconds() / 3600
        # Convert to local time for display
        next_open_local = next_open.astimezone(LOCAL_TZ)

        return {
            'is_open': False,
            'status_text': 'CLOSED (After-hours)',
            'countdown': f'Opens in {hours_until:.1f}h ({next_open_local.strftime("%a %H:%M")})'
        }

    else:
        # Market is open
        minutes_until_close = (market_close_time - now).total_seconds() / 60

        return {
            'is_open': True,
            'status_text': 'OPEN',
            'countdown': f'Closes in {int(minutes_until_close)}m'
        }


def get_next_trading_session():
    """Get the next scheduled trading session"""
    et = pytz.timezone('US/Eastern')
    now = datetime.now(et)
    current_time = now.time()

    # Session times (ET)
    sessions = [
        ('08:00', 'Pre-market 08:00'),
        ('09:30', 'Opening 09:30'),
        ('09:50', 'Session 09:50'),
        ('10:10', 'Session 10:10'),
        ('10:30', 'Session 10:30'),
        ('11:00', 'Mid-morning 11:00'),
        ('12:00', 'Lunch 12:00'),
        ('13:00', 'Session 13:00'),
        ('14:00', 'Session 14:00'),
        ('15:00', 'Afternoon 15:00'),
        ('15:30', 'Session 15:30'),
        ('17:00', 'After-hours 17:00'),
    ]

    # Find next session today
    for time_str, description in sessions:
        hour, minute = map(int, time_str.split(':'))
        session_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if now < session_time:
            minutes_until = (session_time - now).total_seconds() / 60
            return f'{description} (in {int(minutes_until)}m)'

    # If no sessions left today, return tomorrow's first session
    if now.weekday() < 4:  # Mon-Thu
        return 'Pre-market 08:00 (tomorrow)'
    elif now.weekday() == 4:  # Friday
        return 'Pre-market 08:00 (Monday)'
    else:  # Weekend
        days_until_monday = (7 - now.weekday())
        return f'Pre-market 08:00 ({days_until_monday} days)'


# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console, Group
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.live import Live
from rich.text import Text
from rich.align import Align
from rich import box
from rich.style import Style


class TradingDashboard:
    """Live trading dashboard with split-screen UI"""

    def __init__(self, trading_system=None):
        self.console = Console()
        self.trading_system = trading_system

        # Data stores
        self.portfolio_data: Dict[str, Any] = {}
        self.positions: List[Dict] = []
        self.pending_orders: List[Dict] = []  # Active pending orders
        self.recent_trades: deque = deque(maxlen=20)
        self.recent_decisions: deque = deque(maxlen=10)
        self.ai_output: deque = deque(maxlen=100)  # Increased to keep more history
        self.system_logs: deque = deque(maxlen=50)  # System/API logs
        self.system_status = "Initializing..."
        self.last_update = None

        # New: Current analysis state
        self.current_analysis: Dict[str, Any] = {
            'phase': 'Idle',
            'opportunities': [],
            'watchlist': [],
            'market_phase': 'Unknown',
            'stocks_screened': 0,
            'next_session': 'Unknown',
            'technical_signals': [],
            'sentiment_summary': '',
            # Universe statistics
            'universe_total': 0,
            'universe_tier1': 0,
            'universe_tier2': 0,
            'universe_tier3': 0,
            'universe_last_refresh': None
        }

        # Trading memory/history for AI learning display
        self.trading_memory: List[Dict] = []

        # Market status tracking
        self.market_status: Dict[str, Any] = {
            'is_open': False,
            'status_text': 'Checking...',
            'countdown': ''
        }

        # Threading
        self._running = False
        self._lock = threading.Lock()

    def make_layout(self) -> Layout:
        """Create the dashboard layout"""
        layout = Layout(name="root")

        # Split into top and bottom
        layout.split(
            Layout(name="main", ratio=3),
            Layout(name="bottom", ratio=2),
        )

        # Split top into left and right
        layout["main"].split_row(
            Layout(name="left", ratio=1),
            Layout(name="right", ratio=1),
        )

        # Split bottom into 3 columns: AI log, Market analysis, System log
        layout["bottom"].split_row(
            Layout(name="ai_log", ratio=1),
            Layout(name="market_analysis", ratio=1),
            Layout(name="system_log", ratio=1),
        )

        return layout

    def generate_portfolio_panel(self) -> Panel:
        """Generate the portfolio panel (right side)"""
        table = Table(
            show_header=True,
            header_style="bold cyan",
            box=box.SIMPLE,
            expand=True,
            padding=(0, 1),
        )

        table.add_column("Symbol", style="bold white", width=8)
        table.add_column("Shares", justify="right", width=8)
        table.add_column("Value", justify="right", width=12)
        table.add_column("P&L", justify="right", width=12)
        table.add_column("%", justify="right", width=8)

        total_value = 0
        total_pnl = 0

        for pos in self.positions:
            symbol = pos.get('symbol', 'N/A')
            shares = pos.get('qty', 0)
            value = pos.get('market_value', 0)
            cost = pos.get('cost_basis', 0)
            # Use Alpaca's direct unrealized_pl if available, otherwise calculate
            pnl = pos.get('unrealized_pl', value - cost if cost else 0)
            pnl_pct = pos.get('unrealized_plpc', 0) * 100  # Alpaca returns as decimal (0.05 = 5%)
            if pnl_pct == 0 and cost:  # Fallback calculation if not provided
                pnl_pct = (pnl / cost * 100)

            total_value += value
            total_pnl += pnl

            # Color based on P&L
            if pnl > 0:
                pnl_style = "green"
                pnl_str = f"+${pnl:,.2f}"
                pct_str = f"+{pnl_pct:.1f}%"
            elif pnl < 0:
                pnl_style = "red"
                pnl_str = f"-${abs(pnl):,.2f}"
                pct_str = f"{pnl_pct:.1f}%"
            else:
                pnl_style = "white"
                pnl_str = "$0.00"
                pct_str = "0.0%"

            table.add_row(
                symbol,
                f"{float(shares):.0f}",
                f"${value:,.2f}",
                Text(pnl_str, style=pnl_style),
                Text(pct_str, style=pnl_style),
            )

        # Summary section
        equity = self.portfolio_data.get('equity', total_value)
        cash = self.portfolio_data.get('cash', 0)
        stocks_value = self.portfolio_data.get('stocks_value', total_value)
        day_pnl = self.portfolio_data.get('day_pnl', 0)
        day_pnl_pct = self.portfolio_data.get('day_pnl_pct', 0)

        summary = Table(show_header=False, box=None, expand=True, padding=(0, 1))
        summary.add_column("Label", style="bold")
        summary.add_column("Value", justify="right")

        summary.add_row("", "")  # Spacer
        summary.add_row("─" * 20, "─" * 15)
        summary.add_row("Stocks Value", f"${stocks_value:,.2f}")
        summary.add_row("Cash Available", f"${cash:,.2f}")
        summary.add_row("Total Equity", f"[bold cyan]${equity:,.2f}[/]")
        summary.add_row("Unrealized P&L",
                       f"[{'green' if total_pnl >= 0 else 'red'}]${total_pnl:+,.2f}[/]")
        summary.add_row("Day P&L",
                       f"[{'green' if day_pnl >= 0 else 'red'}]${day_pnl:+,.2f} ({day_pnl_pct:+.2f}%)[/]")

        content = Group(table, summary)

        update_time = self.last_update.strftime("%H:%M:%S") if self.last_update else "..."

        # Add market status to title
        market_status = self.market_status.get('status_text', 'Unknown')
        status_color = "bold green" if self.market_status.get('is_open') else "bold red"
        countdown = self.market_status.get('countdown', '')

        return Panel(
            content,
            title=f"[bold white] Portfolio[/] [dim]({update_time})[/] [{status_color}] {market_status}[/] [dim]{countdown}[/]",
            border_style="cyan",
            box=box.ROUNDED,
        )

    def generate_trades_panel(self) -> Panel:
        """Generate the trades/decisions panel (left side)"""
        content_parts = []

        # Recent Decisions Section
        decisions_table = Table(
            show_header=True,
            header_style="bold yellow",
            box=box.SIMPLE,
            expand=True,
            padding=(0, 1),
        )
        decisions_table.add_column("Time", width=8)
        decisions_table.add_column("Action", width=6)
        decisions_table.add_column("Symbol", width=8)
        decisions_table.add_column("Conf", width=6)

        for decision in list(self.recent_decisions)[-5:]:
            action = decision.get('action', 'HOLD')
            action_style = {
                'BUY': 'bold green',
                'SELL': 'bold red',
                'HOLD': 'dim white'
            }.get(action, 'white')

            ticker = decision.get('ticker', '')
            # Show "All positions" for HOLD, otherwise show ticker or '-'
            if action == 'HOLD' and not ticker:
                display_ticker = 'All positions'
            elif ticker:
                display_ticker = ticker
            else:
                display_ticker = '-'

            decisions_table.add_row(
                decision.get('time', ''),
                Text(action, style=action_style),
                display_ticker,
                f"{decision.get('confidence', 0):.0%}",
            )

        content_parts.append(Text("Recent Decisions", style="bold yellow"))
        content_parts.append(decisions_table)

        # Pending Orders Section
        if self.pending_orders:
            pending_table = Table(
                show_header=True,
                header_style="bold cyan",
                box=box.SIMPLE,
                expand=True,
                padding=(0, 1),
            )
            pending_table.add_column("Symbol", width=8)
            pending_table.add_column("Side", width=6)
            pending_table.add_column("Qty", width=6)
            pending_table.add_column("Status", width=10)

            for order in self.pending_orders[:5]:  # Show max 5
                side = str(order.get('side', 'buy')).replace('OrderSide.', '')
                side_style = 'green' if 'BUY' in side.upper() else 'red'
                status = str(order.get('status', 'NEW')).replace('OrderStatus.', '')

                pending_table.add_row(
                    order.get('ticker', '-'),
                    Text(side.upper(), style=side_style),
                    str(int(order.get('qty', 0))),
                    Text(status, style='yellow'),
                )

            content_parts.append(Text("\nPending Orders", style="bold cyan"))
            content_parts.append(pending_table)

        # Recent Trades Section
        trades_table = Table(
            show_header=True,
            header_style="bold magenta",
            box=box.SIMPLE,
            expand=True,
            padding=(0, 1),
        )
        trades_table.add_column("Time", width=8)
        trades_table.add_column("Side", width=6)
        trades_table.add_column("Symbol", width=8)
        trades_table.add_column("Qty", width=6)
        trades_table.add_column("Status", width=10)

        for trade in list(self.recent_trades)[-7:]:
            side = trade.get('side', 'buy')
            side_style = 'green' if side == 'buy' else 'red'
            status = trade.get('status', 'pending')
            status_style = 'green' if status in ['filled', 'accepted'] else 'yellow'

            trades_table.add_row(
                trade.get('time', ''),
                Text(side.upper(), style=side_style),
                trade.get('symbol', '-'),
                str(trade.get('qty', 0)),
                Text(status, style=status_style),
            )

        content_parts.append(Text("\nRecent Trades", style="bold magenta"))
        content_parts.append(trades_table)

        # Add next session to title
        next_session = self.current_analysis.get('next_session', 'Unknown')

        return Panel(
            Group(*content_parts),
            title=f"[bold white] Trades & Decisions[/] [dim]Next: {next_session}[/]",
            border_style="yellow",
            box=box.ROUNDED,
        )

    def generate_ai_panel(self) -> Panel:
        """Generate the AI decision-making panel (bottom)"""
        lines = []

        # Status line
        status_color = "green" if "running" in self.system_status.lower() else "yellow"
        lines.append(Text(f"Status: {self.system_status}", style=f"bold {status_color}"))
        lines.append(Text(""))

        # AI output
        for entry in list(self.ai_output):
            timestamp = entry.get('time', '')
            msg_type = entry.get('type', 'info')
            message = entry.get('message', '')

            type_styles = {
                'thinking': 'italic cyan',
                'decision': 'bold green',
                'reasoning': 'white',
                'error': 'bold red',
                'info': 'dim white',
                'api': 'dim magenta',
            }
            style = type_styles.get(msg_type, 'white')

            prefix = {
                'thinking': '>',
                'decision': '+',
                'reasoning': '-',
                'error': '!',
                'info': '*',
                'api': '~',
            }.get(msg_type, '*')

            # Wrap long messages instead of truncating
            max_width = 120  # Increased from 100
            if len(message) > max_width:
                # Split into multiple lines, preserving word boundaries
                import textwrap
                wrapped = textwrap.wrap(message, width=max_width, break_long_words=False, break_on_hyphens=False)

                # First line with timestamp and prefix
                lines.append(Text(f"[{timestamp}] {prefix} {wrapped[0]}", style=style))

                # Continuation lines (indented to align with message start)
                # Format is "[HH:MM] X message" = 10 chars before message
                indent = " " * 10
                for continuation in wrapped[1:]:
                    lines.append(Text(f"{indent}{continuation}", style=style))
            else:
                lines.append(Text(f"[{timestamp}] {prefix} {message}", style=style))

        # Show last N lines (allow for wrapped messages)
        # Don't enforce minimum, just show what we have
        if len(lines) > 30:  # Keep last 30 lines max (including wrapped)
            lines = lines[-30:]

        return Panel(
            Group(*lines),
            title="[bold white] AI Activity Log[/]",
            border_style="cyan",
            box=box.ROUNDED,
        )

    def generate_system_log_panel(self) -> Panel:
        """Generate the system/API log panel"""
        lines = []

        # System logs
        for entry in list(self.system_logs):
            timestamp = entry.get('time', '')
            log_type = entry.get('type', 'info')
            message = entry.get('message', '')

            # Style based on log type
            type_styles = {
                'api': 'cyan',
                'api_error': 'bold red',
                'error': 'red',
                'warning': 'yellow',
                'info': 'dim white',
                'success': 'green',
            }
            style = type_styles.get(log_type, 'white')

            # Prefix based on type
            prefix = {
                'api': '→',
                'api_error': 'x',
                'error': '!',
                'warning': '!',
                'info': '·',
                'success': '+',
            }.get(log_type, '·')

            # Wrap long messages
            max_width = 80
            if len(message) > max_width:
                import textwrap
                wrapped = textwrap.wrap(message, width=max_width, break_long_words=False, break_on_hyphens=False)

                # First line with timestamp and prefix
                lines.append(Text(f"[{timestamp}] {prefix} {wrapped[0]}", style=style))

                # Continuation lines (indented)
                indent = " " * 10
                for continuation in wrapped[1:]:
                    lines.append(Text(f"{indent}{continuation}", style=style))
            else:
                lines.append(Text(f"[{timestamp}] {prefix} {message}", style=style))

        # Show last N lines
        if len(lines) > 30:
            lines = lines[-30:]

        # If empty, show waiting message
        if not lines:
            lines.append(Text("Waiting for system events...", style="dim"))

        return Panel(
            Group(*lines),
            title="[bold white] System & API Log[/]",
            border_style="magenta",
            box=box.ROUNDED,
        )

    def generate_market_analysis_panel(self) -> Panel:
        """Generate the market analysis panel (bottom right)"""
        lines = []

        # Current phase
        phase = self.current_analysis.get('phase', 'Idle')
        phase_colors = {
            'Idle': 'dim white',
            'Screening': 'yellow',
            'Analyzing': 'cyan',
            'Deciding': 'magenta',
            'Executing': 'green',
            'Waiting': 'dim white',
        }
        phase_color = phase_colors.get(phase, 'white')
        lines.append(Text(f"Phase: {phase}", style=f"bold {phase_color}"))

        # Market phase & stocks screened
        market_phase = self.current_analysis.get('market_phase', 'Unknown')
        stocks = self.current_analysis.get('stocks_screened', 0)
        lines.append(Text(f"Market: {market_phase} | Screened: {stocks} stocks", style="dim white"))
        lines.append(Text(""))

        # Universe statistics
        universe_total = self.current_analysis.get('universe_total', 0)
        universe_tier1 = self.current_analysis.get('universe_tier1', 0)
        universe_tier2 = self.current_analysis.get('universe_tier2', 0)
        universe_tier3 = self.current_analysis.get('universe_tier3', 0)
        universe_last_refresh = self.current_analysis.get('universe_last_refresh', None)

        if universe_total > 0:
            lines.append(Text(" Dynamic Universe:", style="bold cyan"))
            lines.append(Text(f"  Total: {universe_total} stocks", style="white"))

            # Tier breakdown with colors
            tier1_pct = (universe_tier1 / universe_total * 100) if universe_total else 0
            tier2_pct = (universe_tier2 / universe_total * 100) if universe_total else 0
            tier3_pct = (universe_tier3 / universe_total * 100) if universe_total else 0

            lines.append(Text(f"  T1 (Hot):    {universe_tier1:3d} ({tier1_pct:4.1f}%)", style="bold red"))
            lines.append(Text(f"  T2 (Active): {universe_tier2:3d} ({tier2_pct:4.1f}%)", style="bold yellow"))
            lines.append(Text(f"  T3 (Core):   {universe_tier3:3d} ({tier3_pct:4.1f}%)", style="dim white"))

            # Last refresh time
            if universe_last_refresh:
                try:
                    if isinstance(universe_last_refresh, str):
                        from datetime import datetime
                        refresh_time = datetime.fromisoformat(universe_last_refresh)
                    else:
                        refresh_time = universe_last_refresh

                    now = datetime.now()
                    age_minutes = (now - refresh_time).total_seconds() / 60

                    if age_minutes < 60:
                        age_str = f"{int(age_minutes)}m ago"
                    elif age_minutes < 1440:  # Less than 24 hours
                        age_str = f"{int(age_minutes/60)}h ago"
                    else:
                        age_str = f"{int(age_minutes/1440)}d ago"

                    lines.append(Text(f"  Refreshed: {age_str}", style="dim cyan"))
                except:
                    lines.append(Text(f"  Refreshed: Recently", style="dim cyan"))
        else:
            lines.append(Text(" Universe: Initializing...", style="dim"))

        lines.append(Text(""))

        # Top opportunities
        opportunities = self.current_analysis.get('opportunities', [])
        if opportunities:
            lines.append(Text(" Top Opportunities:", style="bold yellow"))
            for opp in opportunities[:5]:
                ticker = opp.get('ticker', '?')
                score = opp.get('score', 0)
                change = opp.get('change', 0)
                signal = opp.get('signal', '')
                color = "green" if change >= 0 else "red"
                lines.append(Text(f"  {ticker}: {change:+.1f}% (score: {score:.2f}) {signal}", style=color))
        else:
            lines.append(Text(" Opportunities: Waiting for scan...", style="dim"))

        lines.append(Text(""))

        # Trading Memory / Recent Decisions
        if self.trading_memory:
            lines.append(Text(" Recent Decisions (AI Memory):", style="bold magenta"))
            for mem in self.trading_memory[:4]:
                decision = mem.get('decision', '?')
                confidence = mem.get('confidence', 0)
                timestamp = mem.get('timestamp', '')[:10]  # Date only
                outcome = mem.get('outcome', '')

                # Color based on outcome
                if outcome == 'win':
                    color = "green"
                    outcome_icon = "+"
                elif outcome == 'loss':
                    color = "red"
                    outcome_icon = "x"
                else:
                    color = "dim white"
                    outcome_icon = "•"

                lines.append(Text(f"  {outcome_icon} {decision} (conf: {confidence:.0%})", style=color))
        else:
            lines.append(Text(" Trading Memory: Loading...", style="dim"))

        # Technical signals summary
        lines.append(Text(""))
        signals = self.current_analysis.get('technical_signals', [])
        if signals:
            lines.append(Text(f" Signals: {', '.join(signals[:3])}", style="dim"))

        # Fill remaining space
        while len(lines) < 12:
            lines.append(Text(""))

        return Panel(
            Group(*lines),
            title="[bold white] Market Analysis[/]",
            border_style="yellow",
            box=box.ROUNDED,
        )

    def generate_header(self) -> Panel:
        """Generate header with title and time"""
        now = local_now()
        market_status = self._get_market_status()

        title = Text()
        title.append("AutoTrader v2", style="bold cyan")
        title.append(" │ ", style="dim")
        title.append(now.strftime("%Y-%m-%d %H:%M:%S"), style="white")
        title.append(" │ ", style="dim")
        title.append(f"Market: {market_status}", style="bold yellow" if "Open" in market_status else "dim red")

        return Panel(
            Align.center(title),
            box=box.MINIMAL,
            style="dim",
        )

    def update_universe_stats(self, stats: Dict[str, Any]):
        """
        Update universe statistics for dashboard display

        Args:
            stats: Dict with keys: total_stocks, tier1_count, tier2_count,
                   tier3_count, last_refresh
        """
        with self._lock:
            self.current_analysis['universe_total'] = stats.get('total_stocks', 0)
            self.current_analysis['universe_tier1'] = stats.get('tier1_count', 0)
            self.current_analysis['universe_tier2'] = stats.get('tier2_count', 0)
            self.current_analysis['universe_tier3'] = stats.get('tier3_count', 0)
            self.current_analysis['universe_last_refresh'] = stats.get('last_refresh')

    def _get_market_status(self) -> str:
        """Check if market is open"""
        now = local_now()
        hour = now.hour
        minute = now.minute
        weekday = now.weekday()

        if weekday >= 5:
            return "Closed (Weekend)"

        # Market hours: 9:30 AM - 4:00 PM ET (adjust for your timezone)
        market_open = (hour == 9 and minute >= 30) or (10 <= hour < 16)

        if market_open:
            return "Open"
        elif hour < 9 or (hour == 9 and minute < 30):
            return "Pre-Market"
        else:
            return "After-Hours"

    def update_portfolio(self, portfolio_data: Dict, positions: List[Dict]):
        """Update portfolio data"""
        with self._lock:
            self.portfolio_data = portfolio_data
            self.positions = positions
            self.last_update = local_now()

    def update_pending_orders(self, orders: List[Dict]):
        """Update pending orders"""
        with self._lock:
            self.pending_orders = orders

    def add_decision(self, action: str, ticker: str, confidence: float, reasoning: str = "", timestamp: str = None):
        """Add a trading decision with optional timestamp for historical loading"""
        with self._lock:
            # Use provided timestamp or current time
            time_str = timestamp if timestamp else local_now().strftime("%H:%M:%S")
            time_short = timestamp[:5] if timestamp and len(timestamp) >= 5 else (timestamp if timestamp else local_now().strftime("%H:%M"))

            self.recent_decisions.append({
                'time': time_str,
                'action': action,
                'ticker': ticker,
                'confidence': confidence,
            })

            # Also add to AI output
            self.ai_output.append({
                'time': time_short,
                'type': 'decision',
                'message': f"{action} {ticker} (conf: {confidence:.0%})"
            })

            if reasoning:
                self.ai_output.append({
                    'time': time_short,
                    'type': 'reasoning',
                    'message': reasoning
                })

    def add_trade(self, symbol: str, side: str, qty: int, status: str):
        """Add an executed trade"""
        with self._lock:
            self.recent_trades.append({
                'time': local_now().strftime("%H:%M:%S"),
                'symbol': symbol,
                'side': side,
                'qty': qty,
                'status': status,
            })

    def add_ai_message(self, message: str, msg_type: str = "info"):
        """Add a message to the AI output panel"""
        with self._lock:
            self.ai_output.append({
                'time': local_now().strftime("%H:%M"),
                'type': msg_type,
                'message': message
            })

    def add_system_log(self, message: str, log_type: str = "info"):
        """Add a message to the system log panel"""
        with self._lock:
            self.system_logs.append({
                'time': local_now().strftime("%H:%M:%S"),
                'type': log_type,
                'message': message
            })

    def update_trading_memory(self, memory: List[Dict]):
        """Update trading memory/history for AI learning display"""
        with self._lock:
            self.trading_memory = memory[:5]  # Keep last 5

    def set_status(self, status: str):
        """Update system status"""
        with self._lock:
            self.system_status = status

    def set_phase(self, phase: str):
        """Update current analysis phase"""
        with self._lock:
            self.current_analysis['phase'] = phase

    def set_opportunities(self, opportunities: List[Dict]):
        """Update top opportunities"""
        with self._lock:
            self.current_analysis['opportunities'] = opportunities

    def set_watchlist(self, watchlist: List[Dict]):
        """Update overnight watchlist"""
        with self._lock:
            self.current_analysis['watchlist'] = watchlist

    def update_market_status(self):
        """Update market status and next session"""
        with self._lock:
            self.market_status = get_market_status()
            self.current_analysis['next_session'] = get_next_trading_session()

    def set_market_info(self, market_phase: str, stocks_screened: int, next_session: str = None):
        """Update market information"""
        with self._lock:
            self.current_analysis['market_phase'] = market_phase
            self.current_analysis['stocks_screened'] = stocks_screened
            if next_session:
                self.current_analysis['next_session'] = next_session

    def set_signals(self, signals: List[str]):
        """Update technical signals"""
        with self._lock:
            self.current_analysis['technical_signals'] = signals

    def render(self) -> Layout:
        """Render the full dashboard"""
        layout = self.make_layout()

        layout["left"].update(self.generate_trades_panel())
        layout["right"].update(self.generate_portfolio_panel())
        layout["ai_log"].update(self.generate_ai_panel())
        layout["market_analysis"].update(self.generate_market_analysis_panel())
        layout["system_log"].update(self.generate_system_log_panel())

        return layout

    def run_standalone(self, refresh_rate: float = 1.0):
        """Run dashboard in standalone mode with demo data"""
        self._running = True

        # Demo data
        self.positions = [
            {'symbol': 'AAPL', 'qty': 15, 'market_value': 3450.75, 'cost_basis': 3200.00},
            {'symbol': 'MSFT', 'qty': 10, 'market_value': 4200.50, 'cost_basis': 4100.00},
            {'symbol': 'GOOGL', 'qty': 5, 'market_value': 8750.00, 'cost_basis': 9000.00},
            {'symbol': 'TSLA', 'qty': 8, 'market_value': 2100.00, 'cost_basis': 2400.00},
            {'symbol': 'NVDA', 'qty': 12, 'market_value': 6500.00, 'cost_basis': 5800.00},
        ]
        self.portfolio_data = {
            'equity': 107500.00,
            'cash': 25000.00,
            'day_pnl': 450.25,
        }
        self.system_status = "Scheduler Running - Waiting for next cycle"
        self.last_update = local_now()

        # Demo decisions
        self.add_decision("HOLD", "AAPL", 0.65, "Market consolidating, wait for breakout")
        self.add_decision("BUY", "NVDA", 0.85, "Strong momentum, AI sector leadership")

        # Demo trades
        self.add_trade("NVDA", "buy", 5, "filled")
        self.add_trade("TSLA", "sell", 3, "filled")

        # Demo AI messages
        self.add_ai_message("Analyzing 25 positions for opportunities...", "thinking")
        self.add_ai_message("Market sentiment: Bullish with caution", "info")
        self.add_ai_message("OpenAI API call: 1.2s response time", "api")

        # Demo system logs
        self.add_system_log("System initialized", "success")
        self.add_system_log("Connected to Alpaca Paper Trading API", "success")
        self.add_system_log("OpenAI GPT-4o API: 1250ms", "api")
        self.add_system_log("Polygon market data API: 450ms", "api")
        self.add_system_log("Fetched portfolio data", "info")

        try:
            with Live(self.render(), console=self.console, refresh_per_second=1/refresh_rate, screen=True) as live:
                while self._running:
                    # Simulate updates
                    self.last_update = local_now()
                    live.update(self.render())
                    time.sleep(refresh_rate)
        except KeyboardInterrupt:
            self._running = False
            self.console.print("\n[bold yellow]Dashboard stopped[/]")


# Integration with trading system
class DashboardIntegration:
    """Integrates dashboard with the trading system"""

    def __init__(self, dashboard: TradingDashboard):
        self.dashboard = dashboard

    def on_portfolio_update(self, portfolio_data: Dict, positions: List[Dict]):
        """Called when portfolio is updated"""
        self.dashboard.update_portfolio(portfolio_data, positions)

    def on_decision(self, decision: Dict):
        """Called when AI makes a decision"""
        self.dashboard.add_decision(
            action=decision.get('action', 'HOLD'),
            ticker=decision.get('ticker', ''),
            confidence=decision.get('confidence', 0),
            reasoning=decision.get('reasoning', '')
        )

    def on_trade(self, trade: Dict):
        """Called when a trade is executed"""
        self.dashboard.add_trade(
            symbol=trade.get('symbol', ''),
            side=trade.get('side', 'buy'),
            qty=trade.get('qty', 0),
            status=trade.get('status', 'pending')
        )

    def on_ai_thinking(self, message: str):
        """Called during AI processing"""
        self.dashboard.add_ai_message(message, "thinking")

    def on_universe_update(self, stats: Dict[str, Any]):
        """Called when universe statistics are updated"""
        self.dashboard.update_universe_stats(stats)

    def on_api_call(self, service: str, duration_ms: float, error: str = None):
        """Called after API call"""
        if error:
            self.dashboard.add_system_log(f"{service} API failed: {error}", "api_error")
        else:
            self.dashboard.add_system_log(f"{service} API: {duration_ms:.0f}ms", "api")
        # Also keep old AI message for backward compatibility
        self.dashboard.add_ai_message(f"{service} API: {duration_ms:.0f}ms", "api")

    def on_error(self, message: str):
        """Called on error"""
        self.dashboard.add_ai_message(message, "error")
        self.dashboard.add_system_log(message, "error")

    def on_system_event(self, message: str, log_type: str = "info"):
        """Called for system events"""
        self.dashboard.add_system_log(message, log_type)

    def on_status_change(self, status: str):
        """Called when system status changes"""
        self.dashboard.set_status(status)

    def on_ai_message(self, message: str, msg_type: str = "info"):
        """Called for general AI messages"""
        self.dashboard.add_ai_message(message, msg_type)

    def on_phase_change(self, phase: str):
        """Called when analysis phase changes"""
        self.dashboard.set_phase(phase)
        self.dashboard.add_ai_message(f"Phase: {phase}", "info")

    def on_opportunities_found(self, opportunities: List[Dict]):
        """Called when opportunities are found"""
        self.dashboard.set_opportunities(opportunities)
        if opportunities:
            top = opportunities[0]
            self.dashboard.add_ai_message(
                f"Found {len(opportunities)} opportunities, top: {top.get('ticker', '?')}",
                "decision"
            )

    def on_watchlist_update(self, watchlist: List[Dict]):
        """Called when watchlist is updated"""
        self.dashboard.set_watchlist(watchlist)
        if watchlist:
            self.dashboard.add_ai_message(f"Watchlist: {len(watchlist)} items", "info")

    def on_market_update(self, market_phase: str, stocks_screened: int, next_session: str = None):
        """Called with market info update"""
        self.dashboard.set_market_info(market_phase, stocks_screened, next_session)

    def on_signals_update(self, signals: List[str]):
        """Called when technical signals are detected"""
        self.dashboard.set_signals(signals)
        if signals:
            self.dashboard.add_ai_message(f"Signals: {', '.join(signals[:3])}", "thinking")


if __name__ == "__main__":
    print("Starting AutoTrader Dashboard Demo...")
    dashboard = TradingDashboard()
    dashboard.run_standalone()
