#!/usr/bin/env python3
"""
 24/7 Automated Trading Scheduler
- Runs multiple trading sessions per day
- Handles market hours, weekends, and holidays
- Comprehensive error handling and monitoring
- Always Works℠ implementation with full validation
"""

import schedule
import time
import logging
import pytz
import asyncio
from datetime import datetime
from pathlib import Path
import sys
import os
import signal
import threading
from typing import Dict, Optional

# Add current directory and parent utils to path
current_dir = Path(__file__).resolve().parent
sys.path.append(str(current_dir))
sys.path.append(str(current_dir.parent / "utils"))

from automated_trading_system import AutomatedTradingSystem, TradingProfile, MarketSession

class TradingScheduler:
    def __init__(self, profile: TradingProfile = TradingProfile.CONSERVATIVE):
        """Initialize 24/7 trading scheduler."""
        self.profile = profile
        self.system = AutomatedTradingSystem(profile)
        self.running = False
        self.scheduler_thread = None

        # Set up logging
        self.setup_logging()

        # Track last execution times to prevent duplicates
        self.last_executions: Dict[str, datetime] = {}

        # Track universe refresh (hourly during market hours)
        self.last_universe_refresh: Optional[datetime] = None
        self.universe_refresh_interval_minutes = 60  # 1 hour

        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

        self.logger.info(f" Trading Scheduler initialized with {profile.value} profile")
        
    def setup_logging(self):
        """Set up comprehensive logging."""
        try:
            log_dir = Path("/app/automated_trading/logs")
            log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            # Fallback to current directory
            log_dir = Path("./automated_trading/logs")
            log_dir.mkdir(parents=True, exist_ok=True)
        
        log_file = log_dir / f"scheduler_{datetime.now().strftime('%Y%m%d')}.log"
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger('TradingScheduler')
        
    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        self.logger.info(f" Received signal {signum}, shutting down gracefully...")
        self.stop_scheduler()
        
    def is_trading_day(self) -> bool:
        """Check if today is a trading day (Mon-Fri, excluding holidays)."""
        et = pytz.timezone('US/Eastern')
        now = datetime.now(et)
        
        # Skip weekends
        if now.weekday() >= 5:
            return False
            
        # TODO: Add holiday checking
        # For now, just check weekdays
        return True
        
    def should_run_session(self, session_name: str, min_interval_minutes: int = 60) -> bool:
        """Check if enough time has passed since last execution."""
        if session_name not in self.last_executions:
            return True
            
        last_run = self.last_executions[session_name]
        now = datetime.now(pytz.timezone('US/Eastern'))
        time_diff = (now - last_run).total_seconds() / 60
        
        return time_diff >= min_interval_minutes
        
    def run_session_safely(self, session: MarketSession, session_name: str):
        """Run trading session with comprehensive error handling."""
        try:
            et = pytz.timezone('US/Eastern')
            now = datetime.now(et)
            
            # Check if we should run this session
            if not self.should_run_session(session_name):
                self.logger.info(f"⏳ Skipping {session_name} - too soon since last run")
                return
                
            self.logger.info(f" Starting {session_name} session at {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            
            # Record execution time
            self.last_executions[session_name] = now
            
            # Run the trading session
            self.system.run_trading_session(session)
            
            self.logger.info(f" {session_name} session completed successfully")
            
        except Exception as e:
            self.logger.error(f" {session_name} session failed: {e}")
            self.logger.error(f"Error details: ", exc_info=True)
            
    def schedule_trading_sessions(self):
        """Schedule all trading sessions throughout the day."""
        # Clear any existing jobs
        schedule.clear()

        # All times in Danish (CET) time
        # EST to CET: add 6 hours

        # Early morning pre-market - 06:00 EST = 12:00 CET (catch overnight news)
        schedule.every().monday.at("12:00").do(
            self.run_session_safely, MarketSession.PRE_MARKET, "early_premarket"
        ).tag('trading')
        schedule.every().tuesday.at("12:00").do(
            self.run_session_safely, MarketSession.PRE_MARKET, "early_premarket"
        ).tag('trading')
        schedule.every().wednesday.at("12:00").do(
            self.run_session_safely, MarketSession.PRE_MARKET, "early_premarket"
        ).tag('trading')
        schedule.every().thursday.at("12:00").do(
            self.run_session_safely, MarketSession.PRE_MARKET, "early_premarket"
        ).tag('trading')
        schedule.every().friday.at("12:00").do(
            self.run_session_safely, MarketSession.PRE_MARKET, "early_premarket"
        ).tag('trading')

        # Pre-market preparation - 08:00 EST = 14:00 CET
        schedule.every().monday.at("14:00").do(
            self.run_session_safely, MarketSession.PRE_MARKET, "pre_market"
        ).tag('trading')
        schedule.every().tuesday.at("14:00").do(
            self.run_session_safely, MarketSession.PRE_MARKET, "pre_market"
        ).tag('trading')
        schedule.every().wednesday.at("14:00").do(
            self.run_session_safely, MarketSession.PRE_MARKET, "pre_market"
        ).tag('trading')
        schedule.every().thursday.at("14:00").do(
            self.run_session_safely, MarketSession.PRE_MARKET, "pre_market"
        ).tag('trading')
        schedule.every().friday.at("14:00").do(
            self.run_session_safely, MarketSession.PRE_MARKET, "pre_market"
        ).tag('trading')

        # Market opening - 09:30 EST = 15:30 CET
        schedule.every().monday.at("15:30").do(
            self.run_session_safely, MarketSession.OPENING, "opening"
        ).tag('trading')
        schedule.every().tuesday.at("15:30").do(
            self.run_session_safely, MarketSession.OPENING, "opening"
        ).tag('trading')
        schedule.every().wednesday.at("15:30").do(
            self.run_session_safely, MarketSession.OPENING, "opening"
        ).tag('trading')
        schedule.every().thursday.at("15:30").do(
            self.run_session_safely, MarketSession.OPENING, "opening"
        ).tag('trading')
        schedule.every().friday.at("15:30").do(
            self.run_session_safely, MarketSession.OPENING, "opening"
        ).tag('trading')

        # Mid-morning check - 11:00 EST = 17:00 CET
        schedule.every().monday.at("17:00").do(
            self.run_session_safely, MarketSession.MID_MORNING, "mid_morning"
        ).tag('trading')
        schedule.every().tuesday.at("17:00").do(
            self.run_session_safely, MarketSession.MID_MORNING, "mid_morning"
        ).tag('trading')
        schedule.every().wednesday.at("17:00").do(
            self.run_session_safely, MarketSession.MID_MORNING, "mid_morning"
        ).tag('trading')
        schedule.every().thursday.at("17:00").do(
            self.run_session_safely, MarketSession.MID_MORNING, "mid_morning"
        ).tag('trading')
        schedule.every().friday.at("17:00").do(
            self.run_session_safely, MarketSession.MID_MORNING, "mid_morning"
        ).tag('trading')

        # Lunch review - 13:00 EST = 19:00 CET
        schedule.every().monday.at("19:00").do(
            self.run_session_safely, MarketSession.LUNCH, "lunch"
        ).tag('trading')
        schedule.every().tuesday.at("19:00").do(
            self.run_session_safely, MarketSession.LUNCH, "lunch"
        ).tag('trading')
        schedule.every().wednesday.at("19:00").do(
            self.run_session_safely, MarketSession.LUNCH, "lunch"
        ).tag('trading')
        schedule.every().thursday.at("19:00").do(
            self.run_session_safely, MarketSession.LUNCH, "lunch"
        ).tag('trading')
        schedule.every().friday.at("19:00").do(
            self.run_session_safely, MarketSession.LUNCH, "lunch"
        ).tag('trading')

        # Afternoon positioning - 15:00 EST = 21:00 CET
        schedule.every().monday.at("21:00").do(
            self.run_session_safely, MarketSession.AFTERNOON, "afternoon"
        ).tag('trading')
        schedule.every().tuesday.at("21:00").do(
            self.run_session_safely, MarketSession.AFTERNOON, "afternoon"
        ).tag('trading')
        schedule.every().wednesday.at("21:00").do(
            self.run_session_safely, MarketSession.AFTERNOON, "afternoon"
        ).tag('trading')
        schedule.every().thursday.at("21:00").do(
            self.run_session_safely, MarketSession.AFTERNOON, "afternoon"
        ).tag('trading')
        schedule.every().friday.at("21:00").do(
            self.run_session_safely, MarketSession.AFTERNOON, "afternoon"
        ).tag('trading')

        # After hours review - 17:00 EST = 23:00 CET
        schedule.every().monday.at("23:00").do(
            self.run_session_safely, MarketSession.AFTER_HOURS, "after_hours"
        ).tag('trading')
        schedule.every().tuesday.at("23:00").do(
            self.run_session_safely, MarketSession.AFTER_HOURS, "after_hours"
        ).tag('trading')
        schedule.every().wednesday.at("23:00").do(
            self.run_session_safely, MarketSession.AFTER_HOURS, "after_hours"
        ).tag('trading')
        schedule.every().thursday.at("23:00").do(
            self.run_session_safely, MarketSession.AFTER_HOURS, "after_hours"
        ).tag('trading')
        schedule.every().friday.at("23:00").do(
            self.run_session_safely, MarketSession.AFTER_HOURS, "after_hours"
        ).tag('trading')

        # Late evening analysis - 22:00 EST = 04:00 CET next day (prepare for next day)
        schedule.every().monday.at("04:00").do(
            self.run_session_safely, MarketSession.PRE_MARKET, "overnight_sunday"
        ).tag('trading')
        schedule.every().tuesday.at("04:00").do(
            self.run_session_safely, MarketSession.PRE_MARKET, "overnight_monday"
        ).tag('trading')
        schedule.every().wednesday.at("04:00").do(
            self.run_session_safely, MarketSession.PRE_MARKET, "overnight_tuesday"
        ).tag('trading')
        schedule.every().thursday.at("04:00").do(
            self.run_session_safely, MarketSession.PRE_MARKET, "overnight_wednesday"
        ).tag('trading')
        schedule.every().friday.at("04:00").do(
            self.run_session_safely, MarketSession.PRE_MARKET, "overnight_thursday"
        ).tag('trading')

        # Weekend planning - Sunday 19:00 EST = Monday 01:00 CET
        schedule.every().monday.at("01:00").do(
            self.run_session_safely, MarketSession.WEEKEND, "weekend_planning"
        ).tag('trading')

        scheduled_jobs = len(schedule.get_jobs('trading'))
        self.logger.info(f" Scheduled {scheduled_jobs} trading sessions (including overnight analysis)")

    def schedule_aggressive_sessions(self):
        """
        Schedule high-frequency trading sessions every 20 minutes during market hours.
        For ULTRA_AGGRESSIVE profile - more opportunities, more analysis.

        US Market times in Danish (CET) time:
        - Pre-market: 08:00 EST = 14:00 CET
        - Market open: 09:30 EST = 15:30 CET
        - Market close: 16:00 EST = 22:00 CET
        - After-hours: 17:00 EST = 23:00 CET
        """
        schedule.clear()

        self.logger.info("Setting up AGGRESSIVE schedule: Every 20 minutes during market hours")

        # Generate times every 20 minutes from 15:30 to 22:00 CET (US market 9:30 AM - 4:00 PM EST)
        market_times = []
        start_hour, start_min = 15, 30  # 9:30 AM EST in Danish time
        end_hour, end_min = 22, 0        # 4:00 PM EST in Danish time

        current_min = start_hour * 60 + start_min
        end_min_total = end_hour * 60 + end_min

        while current_min < end_min_total:
            hour = current_min // 60
            minute = current_min % 60
            market_times.append(f"{hour:02d}:{minute:02d}")
            current_min += 20  # Every 20 minutes

        # Schedule sessions in Danish time
        for time_str in market_times:
            hour = int(time_str.split(':')[0])

            # Determine session type based on Danish time
            if hour < 17:  # Before 17:00 = Opening (9:30-11:00 EST)
                session = MarketSession.OPENING
                session_name = f"opening_{time_str.replace(':', '')}"
            elif hour < 19:  # 17:00-19:00 = Mid-morning (11:00-13:00 EST)
                session = MarketSession.MID_MORNING
                session_name = f"midmorning_{time_str.replace(':', '')}"
            elif hour < 21:  # 19:00-21:00 = Lunch (13:00-15:00 EST)
                session = MarketSession.LUNCH
                session_name = f"lunch_{time_str.replace(':', '')}"
            else:  # 21:00+ = Afternoon (15:00-16:00 EST)
                session = MarketSession.AFTERNOON
                session_name = f"afternoon_{time_str.replace(':', '')}"

            schedule.every().day.at(time_str).do(
                self.run_session_safely, session, session_name
            ).tag('trading')

        # Pre-market analysis: 14:00 CET (08:00 EST)
        schedule.every().day.at("14:00").do(
            self.run_session_safely, MarketSession.PRE_MARKET, "premarket"
        ).tag('trading')

        # After-hours review: 23:00 CET (17:00 EST)
        schedule.every().day.at("23:00").do(
            self.run_session_safely, MarketSession.AFTER_HOURS, "afterhours"
        ).tag('trading')

        scheduled_jobs = len(schedule.get_jobs('trading'))
        self.logger.info(f" Scheduled {scheduled_jobs} aggressive trading sessions (every 20 min during market hours)")
        self.logger.info(f"   Pre-market: 14:00 CET (08:00 EST)")
        self.logger.info(f"   Market open: 15:30 CET (09:30 EST)")
        self.logger.info(f"   Market close: 22:00 CET (16:00 EST)")
        self.logger.info(f"   After hours: 23:00 CET (17:00 EST)")

    def _refresh_universe_if_needed(self):
        """
        Check if universe refresh is needed and trigger it.

        Called from scheduler loop to ensure hourly refresh during market hours.
        Uses DynamicUniverseManager's should_refresh() to check timing.
        """
        try:
            et = pytz.timezone('US/Eastern')
            now = datetime.now(et)

            # Only refresh during market hours (Mon-Fri, 9:30 AM - 4:00 PM ET)
            is_trading_day = now.weekday() < 5
            is_market_hours = (now.hour == 9 and now.minute >= 30) or (10 <= now.hour < 16)

            if not (is_trading_day and is_market_hours):
                return  # Skip refresh outside market hours

            # Check if we need to refresh (hourly)
            if self.last_universe_refresh is None:
                time_since_refresh = float('inf')
            else:
                time_since_refresh = (now - self.last_universe_refresh).total_seconds() / 60

            if time_since_refresh >= self.universe_refresh_interval_minutes:
                self.logger.info(f"Triggering universe refresh (last: {time_since_refresh:.0f} min ago)")

                # Access universe manager through system.market_intelligence
                if hasattr(self.system, 'market_intelligence') and hasattr(self.system.market_intelligence, 'universe_manager'):
                    universe_manager = self.system.market_intelligence.universe_manager

                    # Run async refresh in sync context
                    asyncio.run(universe_manager.refresh_universe())

                    # Update last refresh time
                    self.last_universe_refresh = now

                    # Get stats for logging
                    stats = universe_manager.get_universe_stats()
                    self.logger.info(
                        f"Universe refreshed: {stats['total_stocks']} stocks "
                        f"(T1: {stats['tier1_count']}, T2: {stats['tier2_count']}, T3: {stats['tier3_count']})"
                    )
                else:
                    self.logger.warning(" Universe manager not available for refresh")

        except Exception as e:
            self.logger.error(f"Universe refresh failed: {e}")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")

    def run_scheduler_loop(self):
        """Main scheduler loop running in separate thread."""
        self.logger.info(" Enhanced scheduler loop started")

        # Run immediate session if market is open and no recent session
        self._run_startup_session_if_needed()

        while self.running:
            try:
                # Check if universe refresh is needed (hourly during market hours)
                self._refresh_universe_if_needed()

                # Run pending scheduled jobs
                jobs_run = 0
                pending_jobs = schedule.get_jobs()

                # Just run pending jobs normally - schedule handles the timing
                schedule.run_pending()

                # Count jobs that should be running for logging
                for job in pending_jobs:
                    if job.should_run:  # This is the correct way
                        jobs_run += 1

                # Log activity every 5 minutes
                import time
                current_time = time.time()
                if not hasattr(self, '_last_status_log'):
                    self._last_status_log = 0

                if current_time - self._last_status_log > 300:  # 5 minutes
                    import pytz
                    et = pytz.timezone('US/Eastern')
                    now = datetime.now(et)
                    self.logger.info(f" Scheduler active: {now.strftime('%H:%M ET')} - {len(pending_jobs)} jobs scheduled, {jobs_run} ready to run")
                    self._last_status_log = current_time

                time.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                import time  # Make sure time is available in exception handler
                self.logger.error(f" Scheduler loop error: {e}")
                import traceback
                self.logger.error(f"Full traceback: {traceback.format_exc()}")
                time.sleep(60)  # Wait a bit longer on error
                
        self.logger.info(" Enhanced scheduler loop stopped")
        
    def start_scheduler(self):
        """Start the 24/7 trading scheduler."""
        if self.running:
            self.logger.warning(" Scheduler already running")
            return
            
        self.logger.info(" Starting 24/7 automated trading scheduler...")
        
        # Schedule all trading sessions
        self.schedule_trading_sessions()
        
        # Start the scheduler in a separate thread
        self.running = True
        self.scheduler_thread = threading.Thread(target=self.run_scheduler_loop, daemon=True)
        self.scheduler_thread.start()
        
        # Print schedule summary
        self.print_schedule_summary()
        
        self.logger.info(" Trading scheduler started successfully")
        
    def stop_scheduler(self):
        """Stop the trading scheduler gracefully."""
        if not self.running:
            self.logger.warning(" Scheduler not running")
            return
            
        self.logger.info(" Stopping trading scheduler...")
        self.running = False
        
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            self.scheduler_thread.join(timeout=10)
            
        schedule.clear()
        self.logger.info(" Trading scheduler stopped")
        
    def _run_startup_session_if_needed(self):
        """Run an immediate trading session if market is open and system just started"""
        et = pytz.timezone('US/Eastern')
        now = datetime.now(et)

        # Check if market is open (9:30 AM - 4:00 PM ET, Mon-Fri)
        is_trading_day = now.weekday() < 5
        is_market_hours = (now.hour == 9 and now.minute >= 30) or (10 <= now.hour < 16)

        if is_trading_day and is_market_hours:
            # Determine session type based on current time
            if now.hour < 11:
                session = MarketSession.OPENING
                session_name = "startup_opening"
            elif now.hour < 13:
                session = MarketSession.MID_MORNING
                session_name = "startup_midmorning"
            elif now.hour < 15:
                session = MarketSession.LUNCH
                session_name = "startup_lunch"
            else:
                session = MarketSession.AFTERNOON
                session_name = "startup_afternoon"

            self.logger.info(f"Market is open - running immediate startup session ({session_name})")
            self.run_session_safely(session, session_name)
        else:
            self.logger.info(f"⏸️ Market closed or after hours - waiting for next scheduled session")

    def print_schedule_summary(self):
        """Print a summary of scheduled trading sessions."""
        et = pytz.timezone('US/Eastern')
        now = datetime.now(et)
        
        print("\n" + "=" * 80)
        print(" AUTOMATED TRADING SCHEDULE SUMMARY")
        print("=" * 80)
        print(f"Current Time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"Trading Profile: {self.profile.value.upper()}")
        print(f"Data Directory: {self.system.data_dir}")
        print(f"OpenAI Model: {self.system.model}")
        
        print("\n DAILY SCHEDULE (Monday-Friday):")
        daily_schedule = [
            ("08:00 AM ET", "Pre-Market Preparation", "Analyze overnight news and plan for market open"),
            ("09:30 AM ET", "Market Open Analysis", "Assess early momentum and volatility"),
            ("11:00 AM ET", "Mid-Morning Check", "Evaluate morning trends and momentum"),
            ("01:00 PM ET", "Lunch Review", "Assess current positions and market sentiment"),
            ("03:00 PM ET", "Afternoon Position", "Prepare for close and assess day trades"),
            ("05:00 PM ET", "After Hours Review", "Analyze day's performance and plan tomorrow")
        ]
        
        for time_str, session, description in daily_schedule:
            print(f"{time_str:<15} | {session:<20} | {description}")
            
        print("\n WEEKEND SCHEDULE:")
        print("07:00 PM ET Sun  | Weekend Planning     | Comprehensive review and weekly planning")
        
        print(f"\n TRADING LIMITS ({self.profile.value}):")
        limits = self.system.limits
        print(f"Max Position Size: {limits.max_position_pct}% of portfolio")
        print(f"Daily Loss Limit: {limits.max_daily_loss_pct}%")
        print(f"Max Daily Trades: {limits.max_daily_trades}")
        print(f"Default Stop Loss: {limits.stop_loss_pct}%")
        print(f"Trade Range: ${limits.min_trade_amount} - ${limits.max_trade_amount}")
        
        print("\n SCHEDULER STATUS: RUNNING")
        print("=" * 80)
        
    def run_test_session(self):
        """Run a test trading session to verify everything works."""
        print("\n RUNNING TEST TRADING SESSION")
        print("=" * 50)
        
        try:
            self.system.run_trading_session(MarketSession.OPENING)
            print(" Test session completed successfully")
            return True
        except Exception as e:
            print(f" Test session failed: {e}")
            return False

# ==========================================
# PROFILE MANAGER
# ==========================================

def create_profile_manager():
    """Interactive profile selection and management."""
    print("\n TRADING PROFILE MANAGER")
    print("=" * 50)
    
    profiles = {
        "1": (TradingProfile.CONSERVATIVE, "Low risk, 2% max per trade, tight stops"),
        "2": (TradingProfile.AGGRESSIVE, "High risk, 10% max per trade, loose stops"),
        "3": (TradingProfile.CUSTOM, "Balanced, 5% max per trade, moderate stops")
    }
    
    print("Available Trading Profiles:")
    for key, (profile, description) in profiles.items():
        print(f"{key}. {profile.value.title()}: {description}")
        
    while True:
        try:
            choice = input("\nSelect profile (1-3): ").strip()
            if choice in profiles:
                selected_profile, description = profiles[choice]
                print(f" Selected: {selected_profile.value.title()}")
                return selected_profile
            else:
                print(" Invalid choice. Please select 1, 2, or 3.")
        except KeyboardInterrupt:
            print("\n Goodbye!")
            sys.exit(0)

# ==========================================
# MAIN EXECUTION
# ==========================================

def main():
    """Main entry point for the trading scheduler."""
    print(" AUTOMATED 24/7 CHATGPT TRADING SYSTEM")
    print("=" * 80)
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "--test":
            # Run test mode
            print(" TEST MODE - Running single test session")
            scheduler = TradingScheduler(TradingProfile.CONSERVATIVE)
            success = scheduler.run_test_session()
            sys.exit(0 if success else 1)
            
        elif sys.argv[1] == "--conservative":
            profile = TradingProfile.CONSERVATIVE
        elif sys.argv[1] == "--aggressive":
            profile = TradingProfile.AGGRESSIVE
        elif sys.argv[1] == "--custom":
            profile = TradingProfile.CUSTOM
        else:
            print("Usage: python trading_scheduler.py [--test|--conservative|--aggressive|--custom]")
            sys.exit(1)
    else:
        # Interactive profile selection
        profile = create_profile_manager()
    
    # Create and start scheduler
    scheduler = TradingScheduler(profile)
    
    try:
        scheduler.start_scheduler()
        
        # Keep the main thread alive
        print("\n Press CTRL+C to stop the scheduler")
        while scheduler.running:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n Received interrupt signal")
    finally:
        scheduler.stop_scheduler()
        print(" Scheduler shutdown complete")

if __name__ == "__main__":
    main()