"""
 Trading Memory Engine
Persistent storage and analysis of all trading decisions, market data, and performance
"""

import sqlite3
import json
import pandas as pd
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
import logging
import hashlib

@dataclass
class TradingDecision:
    timestamp: str
    session_type: str
    portfolio_value: float
    cash_balance: float
    market_data: Dict[str, Any]
    gpt_prompt: str
    gpt_response: str
    actions: List[Dict[str, Any]]
    reasoning: str
    confidence: float
    executed_trades: List[Dict[str, Any]]

@dataclass
class MarketSnapshot:
    timestamp: str
    ticker: str
    price: float
    volume: float
    volatility: float
    price_change_1d: float
    price_change_5d: float
    price_change_1mo: float
    data_source: str
    technical_indicators: Dict[str, float]

@dataclass
class PerformanceMetrics:
    timestamp: str
    total_equity: float
    cash_balance: float
    portfolio_value: float
    daily_pnl: float
    total_pnl: float
    win_rate: float
    avg_trade_return: float
    sharpe_ratio: Optional[float]
    max_drawdown: float
    active_positions: int

class TradingMemoryEngine:
    """Advanced memory system that tracks everything and enables learning"""
    
    def __init__(self, db_path: Optional[Path] = None):
        """Initialize the memory engine"""
        if db_path is None:
            db_path = Path("automated_trading/trading_memory.db")
        elif isinstance(db_path, str):
            db_path = Path(db_path)
            
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.logger = logging.getLogger(__name__)
        self._init_database()
        
    def _init_database(self):
        """Initialize SQLite database with proper schema and robust connection handling"""
        max_retries = 3
        retry_delay = 0.5
        
        for attempt in range(max_retries):
            try:
                # Use proper connection parameters for reliability
                with sqlite3.connect(
                    self.db_path, 
                    timeout=15.0,
                    isolation_level=None  # autocommit mode for better reliability
                ) as conn:
                    cursor = conn.cursor()
                    
                    # Configure SQLite for better concurrent access
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")
                    cursor.execute("PRAGMA cache_size=10000")
                    cursor.execute("PRAGMA temp_store=memory")
                    cursor.execute("PRAGMA mmap_size=268435456")  # 256MB
                    
                    # Check if trading_decisions table exists and has correct schema
                    cursor.execute("""
                        SELECT name FROM sqlite_master 
                        WHERE type='table' AND name='trading_decisions'
                    """)
                    table_exists = cursor.fetchone() is not None
                    
                    if table_exists:
                        # Check if table has required columns
                        cursor.execute("PRAGMA table_info(trading_decisions)")
                        columns = [col[1] for col in cursor.fetchall()]
                        required_columns = ['decision', 'reasoning', 'confidence', 'market_data_context', 'execution_weight']
                        
                        if all(col in columns for col in required_columns):
                            self.logger.info("Trading decisions table exists with correct schema")
                        else:
                            self.logger.info("Updating trading_decisions table schema")
                            # Add missing columns without dropping the table
                            for col in required_columns:
                                if col not in columns:
                                    if col == 'execution_weight':
                                        cursor.execute(f"ALTER TABLE trading_decisions ADD COLUMN {col} REAL DEFAULT 1.0")
                                    else:
                                        cursor.execute(f"ALTER TABLE trading_decisions ADD COLUMN {col} TEXT")
                                    self.logger.info(f"Added column: {col}")
                    else:
                        # Create new table only if it doesn't exist
                        self.logger.info("🆕 Creating new trading_decisions table")
                        # Trading decisions table with complete corrected schema
                        cursor.execute("""
                            CREATE TABLE trading_decisions (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                timestamp TEXT NOT NULL,
                                decision TEXT NOT NULL,
                                reasoning TEXT NOT NULL,
                                confidence REAL NOT NULL,
                                market_data_context TEXT,
                                execution_result TEXT,
                                actual_price REAL,
                                execution_weight REAL DEFAULT 1.0,
                                outcome TEXT,
                                profit_loss REAL,
                                session_type TEXT,
                                portfolio_value REAL,
                                cash_balance REAL,
                                market_data TEXT,
                                gpt_prompt TEXT,
                                gpt_response TEXT,
                                actions TEXT,
                                executed_trades TEXT,
                                prompt_hash TEXT,
                                outcome_score REAL
                            )
                        """)
                    
                    # Create other tables if they don't exist
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS market_snapshots (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            timestamp TEXT NOT NULL,
                            ticker TEXT NOT NULL,
                            price REAL NOT NULL,
                            volume REAL,
                            volatility REAL,
                            price_change_1d REAL,
                            price_change_5d REAL,
                            price_change_1mo REAL,
                            data_source TEXT,
                            technical_indicators TEXT  -- JSON
                        )
                    """)
                    
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS performance_metrics (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            timestamp TEXT NOT NULL,
                            total_equity REAL NOT NULL,
                            cash_balance REAL NOT NULL,
                            portfolio_value REAL NOT NULL,
                            daily_pnl REAL,
                            total_pnl REAL,
                            win_rate REAL,
                            avg_trade_return REAL,
                            sharpe_ratio REAL,
                            max_drawdown REAL,
                            active_positions INTEGER
                        )
                    """)
                    
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS positions (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            ticker TEXT NOT NULL,
                            entry_price REAL NOT NULL,
                            quantity INTEGER NOT NULL,
                            entry_decision_id INTEGER,
                            entry_timestamp TEXT NOT NULL,
                            exit_price REAL,
                            exit_decision_id INTEGER,
                            exit_timestamp TEXT,
                            profit_loss REAL,
                            profit_loss_pct REAL,
                            hold_duration_seconds INTEGER,
                            status TEXT DEFAULT 'open',  -- 'open' or 'closed'
                            FOREIGN KEY (entry_decision_id) REFERENCES trading_decisions(id),
                            FOREIGN KEY (exit_decision_id) REFERENCES trading_decisions(id)
                        )
                    """)

                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS learning_patterns (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            pattern_type TEXT NOT NULL,
                            pattern_data TEXT NOT NULL,  -- JSON
                            success_rate REAL,
                            confidence_score REAL,
                            created_at TEXT NOT NULL,
                            last_updated TEXT NOT NULL
                        )
                    """)
                    
                    # Create indices for better query performance
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_decisions_timestamp ON trading_decisions(timestamp)")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_ticker ON market_snapshots(ticker, timestamp)")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_performance_timestamp ON performance_metrics(timestamp)")
                    
                    conn.commit()
                    self.logger.info(f"Trading memory database initialized successfully (attempt {attempt + 1})")
                    return  # Success, exit function
                    
            except sqlite3.OperationalError as e:
                if attempt < max_retries - 1:
                    self.logger.warning(f"Database initialization attempt {attempt + 1} failed, retrying: {e}")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                else:
                    self.logger.error(f"Failed to initialize database after {max_retries} attempts: {e}")
                    raise
            except Exception as e:
                if attempt < max_retries - 1:
                    self.logger.warning(f"Database initialization attempt {attempt + 1} failed, retrying: {e}")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                else:
                    self.logger.error(f"Failed to initialize database after {max_retries} attempts: {e}")
                    raise
    
    def store_trading_decision(self, decision: TradingDecision) -> int:
        """Store a trading decision with full context"""
        try:
            # Calculate prompt hash for similarity analysis
            prompt_hash = hashlib.md5(decision.gpt_prompt.encode()).hexdigest()[:16]
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO trading_decisions 
                    (timestamp, session_type, portfolio_value, cash_balance, market_data, 
                     gpt_prompt, gpt_response, actions, reasoning, confidence, 
                     executed_trades, prompt_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    decision.timestamp,
                    decision.session_type,
                    decision.portfolio_value,
                    decision.cash_balance,
                    json.dumps(decision.market_data),
                    decision.gpt_prompt,
                    decision.gpt_response,
                    json.dumps(decision.actions),
                    decision.reasoning,
                    decision.confidence,
                    json.dumps(decision.executed_trades),
                    prompt_hash
                ))
                
                decision_id = cursor.lastrowid
                conn.commit()
                self.logger.info(f"Stored trading decision #{decision_id}")
                return decision_id
                
        except Exception as e:
            self.logger.error(f"Failed to store decision: {e}")
            return -1
    
    def store_market_snapshot(self, snapshot: MarketSnapshot):
        """Store market data snapshot"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO market_snapshots 
                    (timestamp, ticker, price, volume, volatility, price_change_1d,
                     price_change_5d, price_change_1mo, data_source, technical_indicators)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    snapshot.timestamp,
                    snapshot.ticker,
                    snapshot.price,
                    snapshot.volume,
                    snapshot.volatility,
                    snapshot.price_change_1d,
                    snapshot.price_change_5d,
                    snapshot.price_change_1mo,
                    snapshot.data_source,
                    json.dumps(snapshot.technical_indicators)
                ))
                conn.commit()
                
        except Exception as e:
            self.logger.error(f"Failed to store market snapshot: {e}")
    
    def store_decision(self, decision: str, reasoning: str, confidence: float, 
                       market_data: str, execution_weight: float = 1.0):
        """Store a trading decision in the database with robust error handling"""
        max_retries = 3
        retry_delay = 0.1
        
        for attempt in range(max_retries):
            try:
                # Use timeout and proper connection parameters
                with sqlite3.connect(
                    self.db_path, 
                    timeout=10.0,
                    isolation_level=None  # autocommit mode
                ) as conn:
                    # Enable WAL mode for better concurrency
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=NORMAL")
                    conn.execute("PRAGMA cache_size=10000")
                    conn.execute("PRAGMA temp_store=memory")
                    
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO trading_decisions 
                        (timestamp, decision, reasoning, confidence, market_data_context, execution_weight)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        datetime.now().isoformat(),
                        decision,
                        reasoning,
                        confidence,
                        market_data,
                        execution_weight
                    ))
                    
                    # Explicit commit for safety
                    conn.commit()
                    self.logger.info(f"Decision stored successfully (attempt {attempt + 1})")
                    return  # Success, exit function
                    
            except sqlite3.OperationalError as e:
                error_msg = str(e).lower()
                if 'locked' in error_msg or 'busy' in error_msg:
                    self.logger.warning(f"Database busy on attempt {attempt + 1}, retrying in {retry_delay}s")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                    continue
                else:
                    self.logger.error(f"SQLite operational error: {e}")
                    break
            except (BrokenPipeError, OSError) as e:
                if attempt < max_retries - 1:
                    self.logger.warning(f"Connection error on attempt {attempt + 1}, retrying: {e}")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                else:
                    self.logger.error(f"Final attempt failed with connection error: {e}")
                    break
            except Exception as e:
                self.logger.error(f"Unexpected error storing decision (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                else:
                    break
        
        # If we get here, all attempts failed
        self.logger.error(f"Failed to store decision after {max_retries} attempts: {decision}")
        
        # Fallback: store to file as backup
        try:
            backup_file = self.db_path.parent / "decision_backup.log"
            with open(backup_file, 'a', encoding='utf-8') as f:
                f.write(f"{datetime.now().isoformat()}: {decision} | {reasoning} | {confidence}\n")
            self.logger.info(f" Decision backed up to file: {backup_file}")
        except Exception as backup_error:
            self.logger.error(f"Even backup storage failed: {backup_error}")
    
    def store_execution(self, decision_id: int, execution_result: Dict[str, Any], 
                       actual_price: float, execution_weight: float = 1.0):
        """Store execution result with robust error handling"""
        max_retries = 3
        retry_delay = 0.1
        
        for attempt in range(max_retries):
            try:
                # Use timeout and proper connection parameters
                with sqlite3.connect(
                    self.db_path, 
                    timeout=10.0,
                    isolation_level=None  # autocommit mode
                ) as conn:
                    # Enable WAL mode for better concurrency
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=NORMAL")
                    
                    cursor = conn.cursor()
                    
                    # First get the latest decision ID if not provided
                    if decision_id is None:
                        cursor.execute("SELECT MAX(id) FROM trading_decisions")
                        result = cursor.fetchone()
                        decision_id = result[0] if result[0] else 0
                    
                    # Store execution result
                    cursor.execute("""
                        UPDATE trading_decisions 
                        SET execution_result = ?, actual_price = ?, execution_weight = ?
                        WHERE id = ?
                    """, (
                        json.dumps(execution_result),
                        actual_price,
                        execution_weight,
                        decision_id
                    ))
                    
                    # Verify update was successful
                    if cursor.rowcount > 0:
                        conn.commit()
                        self.logger.info(f"Execution result stored for decision #{decision_id} (attempt {attempt + 1})")
                        return  # Success
                    else:
                        self.logger.warning(f"No decision found with ID {decision_id} to update")
                        return
                        
            except sqlite3.OperationalError as e:
                error_msg = str(e).lower()
                if 'locked' in error_msg or 'busy' in error_msg:
                    if attempt < max_retries - 1:
                        self.logger.warning(f"Database busy on execution storage attempt {attempt + 1}, retrying...")
                        time.sleep(retry_delay)
                        retry_delay *= 2
                        continue
                    else:
                        self.logger.error(f"Database busy error persists: {e}")
                        break
                else:
                    self.logger.error(f"SQLite operational error: {e}")
                    break
            except (BrokenPipeError, OSError) as e:
                if attempt < max_retries - 1:
                    self.logger.warning(f"Connection error on execution storage attempt {attempt + 1}, retrying: {e}")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                else:
                    self.logger.error(f"Final attempt failed with connection error: {e}")
                    break
            except Exception as e:
                self.logger.error(f"Unexpected error storing execution result (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                else:
                    break
        
        # If we get here, all attempts failed
        self.logger.error(f"Failed to store execution result after {max_retries} attempts for decision #{decision_id}")
        
        # Fallback: store to file as backup
        try:
            backup_file = self.db_path.parent / "execution_backup.log"
            with open(backup_file, 'a', encoding='utf-8') as f:
                f.write(f"{datetime.now().isoformat()}: Decision #{decision_id} | {json.dumps(execution_result)} | Price: {actual_price}\n")
            self.logger.info(f" Execution result backed up to file: {backup_file}")
        except Exception as backup_error:
            self.logger.error(f"Even execution backup storage failed: {backup_error}")
    
    def get_latest_decision_id(self) -> Optional[int]:
        """Get the ID of the latest trading decision"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT MAX(id) FROM trading_decisions")
                result = cursor.fetchone()
                return result[0] if result[0] else None
                
        except Exception as e:
            self.logger.error(f"Failed to get latest decision ID: {e}")
            return None
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get performance summary"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Get basic trading stats
                cursor.execute("SELECT COUNT(*) FROM trading_decisions WHERE execution_result IS NOT NULL")
                total_trades = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM trading_decisions WHERE outcome = 'win'")
                wins = cursor.fetchone()[0]
                
                cursor.execute("SELECT AVG(profit_loss) FROM trading_decisions WHERE profit_loss IS NOT NULL")
                avg_return = cursor.fetchone()[0] or 0.0
                
                win_rate = wins / total_trades if total_trades > 0 else 0.0
                
                return {
                    'total_trades': total_trades,
                    'wins': wins,
                    'losses': total_trades - wins,
                    'win_rate': win_rate,
                    'avg_return': avg_return
                }
                
        except Exception as e:
            self.logger.error(f"Failed to get performance summary: {e}")
            return {
                'total_trades': 0,
                'wins': 0, 
                'losses': 0,
                'win_rate': 0.0,
                'avg_return': 0.0
            }
    
    def get_recent_decisions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent trading decisions"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM trading_decisions 
                    ORDER BY timestamp DESC LIMIT ?
                """, (limit,))
                
                columns = [desc[0] for desc in cursor.description]
                results = []
                
                for row in cursor.fetchall():
                    decision = dict(zip(columns, row))
                    
                    # Parse JSON fields safely
                    try:
                        if decision.get('market_data') and decision['market_data'] != 'None':
                            decision['market_data'] = json.loads(decision['market_data'])
                        else:
                            decision['market_data'] = {}
                    except (json.JSONDecodeError, TypeError):
                        decision['market_data'] = {}
                    
                    try:
                        if decision.get('actions') and decision['actions'] != 'None':
                            decision['actions'] = json.loads(decision['actions'])
                        else:
                            decision['actions'] = []
                    except (json.JSONDecodeError, TypeError):
                        decision['actions'] = []
                    
                    try:
                        if decision.get('executed_trades') and decision['executed_trades'] != 'None':
                            decision['executed_trades'] = json.loads(decision['executed_trades'])
                        else:
                            decision['executed_trades'] = []
                    except (json.JSONDecodeError, TypeError):
                        decision['executed_trades'] = []
                        
                    results.append(decision)
                
                return results
                
        except Exception as e:
            self.logger.error(f"Failed to get recent decisions: {e}")
            return []
    
    def get_performance_history(self, days: int = 30) -> pd.DataFrame:
        """Get performance history as DataFrame"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
                
                return pd.read_sql_query("""
                    SELECT * FROM performance_metrics 
                    WHERE timestamp > ?
                    ORDER BY timestamp
                """, conn, params=(cutoff_date,))
                
        except Exception as e:
            self.logger.error(f"Failed to get performance history: {e}")
            return pd.DataFrame()
    
    def analyze_decision_patterns(self) -> Dict[str, Any]:
        """Analyze patterns in trading decisions to improve future decisions"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Get decisions with outcomes
                cursor.execute("""
                    SELECT session_type, confidence, outcome_score, actions
                    FROM trading_decisions 
                    WHERE outcome_score IS NOT NULL
                    ORDER BY timestamp DESC LIMIT 100
                """)
                
                results = cursor.fetchall()
                
                if not results:
                    return {"message": "Not enough data for pattern analysis"}
                
                # Analyze session performance
                session_performance = {}
                confidence_analysis = {"high": [], "medium": [], "low": []}
                
                for session_type, confidence, outcome_score, actions_json in results:
                    # Session performance
                    if session_type not in session_performance:
                        session_performance[session_type] = []
                    session_performance[session_type].append(outcome_score)
                    
                    # Confidence analysis
                    if confidence >= 0.7:
                        confidence_analysis["high"].append(outcome_score)
                    elif confidence >= 0.5:
                        confidence_analysis["medium"].append(outcome_score)
                    else:
                        confidence_analysis["low"].append(outcome_score)
                
                # Calculate averages
                analysis = {
                    "session_performance": {
                        session: {
                            "avg_outcome": sum(scores) / len(scores),
                            "count": len(scores)
                        }
                        for session, scores in session_performance.items()
                    },
                    "confidence_performance": {
                        level: {
                            "avg_outcome": sum(scores) / len(scores) if scores else 0,
                            "count": len(scores)
                        }
                        for level, scores in confidence_analysis.items()
                    }
                }
                
                return analysis
                
        except Exception as e:
            self.logger.error(f"Failed to analyze patterns: {e}")
            return {}
    
    def get_market_context_for_gpt(self, ticker: str = None) -> Dict[str, Any]:
        """Get historical context to enhance GPT prompts"""
        try:
            context = {}
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Recent performance trend
                cursor.execute("""
                    SELECT total_equity, daily_pnl, timestamp
                    FROM performance_metrics 
                    ORDER BY timestamp DESC LIMIT 7
                """)
                
                recent_performance = cursor.fetchall()
                if recent_performance:
                    context["recent_performance"] = {
                        "current_equity": recent_performance[0][0],
                        "avg_daily_pnl": sum(row[1] for row in recent_performance) / len(recent_performance),
                        "equity_trend": "up" if recent_performance[0][0] > recent_performance[-1][0] else "down"
                    }
                
                # Decision patterns
                cursor.execute("""
                    SELECT session_type, AVG(confidence), COUNT(*)
                    FROM trading_decisions 
                    WHERE timestamp > datetime('now', '-30 days')
                    GROUP BY session_type
                """)
                
                session_patterns = cursor.fetchall()
                if session_patterns:
                    context["session_patterns"] = {
                        row[0]: {
                            "avg_confidence": row[1],
                            "decision_count": row[2]
                        }
                        for row in session_patterns
                    }
                
                # Recent similar situations (if ticker provided)
                if ticker:
                    cursor.execute("""
                        SELECT gpt_response, actions, outcome_score
                        FROM trading_decisions 
                        WHERE market_data LIKE ? AND outcome_score IS NOT NULL
                        ORDER BY timestamp DESC LIMIT 5
                    """, (f'%{ticker}%',))
                    
                    similar_decisions = cursor.fetchall()
                    if similar_decisions:
                        context["similar_situations"] = len(similar_decisions)
                        context["similar_outcomes"] = [row[2] for row in similar_decisions if row[2]]
                
                return context
                
        except Exception as e:
            self.logger.error(f"Failed to get market context: {e}")
            return {}
    
    def update_decision_outcome(self, decision_id: int, outcome_score: float):
        """Update a decision with its actual outcome for learning"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE trading_decisions
                    SET outcome_score = ?
                    WHERE id = ?
                """, (outcome_score, decision_id))
                conn.commit()
                self.logger.info(f"Updated decision #{decision_id} outcome: {outcome_score}")

        except Exception as e:
            self.logger.error(f"Failed to update decision outcome: {e}")

    def track_position_open(self, ticker: str, entry_price: float, quantity: int, decision_id: int):
        """Track when a BUY position is opened"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO positions
                    (ticker, entry_price, quantity, entry_decision_id, entry_timestamp, status)
                    VALUES (?, ?, ?, ?, ?, 'open')
                """, (ticker, entry_price, quantity, decision_id, datetime.now().isoformat()))
                conn.commit()
                self.logger.info(f"Position opened: {quantity} {ticker} @ ${entry_price:.2f}")

        except Exception as e:
            self.logger.error(f"Failed to track position open: {e}")

    def track_position_close(self, ticker: str, exit_price: float, quantity: int, decision_id: int):
        """
        Track when a SELL position is closed and calculate P/L
        Uses FIFO (First In, First Out) for matching positions
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Find open positions for this ticker (FIFO)
                cursor.execute("""
                    SELECT id, entry_price, quantity, entry_decision_id, entry_timestamp
                    FROM positions
                    WHERE ticker = ? AND status = 'open'
                    ORDER BY entry_timestamp ASC
                """, (ticker,))

                open_positions = cursor.fetchall()

                if not open_positions:
                    self.logger.warning(f" No open position found for {ticker} to close")
                    return

                remaining_to_close = quantity
                total_pnl = 0.0
                exit_timestamp = datetime.now().isoformat()

                for pos_id, entry_price, pos_qty, entry_decision_id, entry_timestamp in open_positions:
                    if remaining_to_close <= 0:
                        break

                    # Determine how much of this position to close
                    qty_to_close = min(pos_qty, remaining_to_close)

                    # Calculate P/L for this portion
                    pnl = (exit_price - entry_price) * qty_to_close
                    pnl_pct = ((exit_price - entry_price) / entry_price) * 100

                    # Calculate hold duration
                    entry_dt = datetime.fromisoformat(entry_timestamp)
                    exit_dt = datetime.fromisoformat(exit_timestamp)
                    hold_duration = int((exit_dt - entry_dt).total_seconds())

                    total_pnl += pnl

                    if qty_to_close == pos_qty:
                        # Close entire position
                        cursor.execute("""
                            UPDATE positions
                            SET exit_price = ?, exit_decision_id = ?, exit_timestamp = ?,
                                profit_loss = ?, profit_loss_pct = ?, hold_duration_seconds = ?, status = 'closed'
                            WHERE id = ?
                        """, (exit_price, decision_id, exit_timestamp, pnl, pnl_pct, hold_duration, pos_id))

                        # Update entry decision with outcome
                        outcome = 'win' if pnl > 0 else 'loss'
                        cursor.execute("""
                            UPDATE trading_decisions
                            SET outcome = ?, profit_loss = ?
                            WHERE id = ?
                        """, (outcome, pnl, entry_decision_id))

                        self.logger.info(f"Position closed: {qty_to_close} {ticker} @ ${exit_price:.2f} | P/L: ${pnl:+.2f} ({pnl_pct:+.1f}%) | Duration: {hold_duration//60}min")
                    else:
                        # Partial close - reduce position quantity
                        new_qty = pos_qty - qty_to_close
                        cursor.execute("""
                            UPDATE positions
                            SET quantity = ?
                            WHERE id = ?
                        """, (new_qty, pos_id))

                        # Create a closed position record for the sold portion
                        cursor.execute("""
                            INSERT INTO positions
                            (ticker, entry_price, quantity, entry_decision_id, entry_timestamp,
                             exit_price, exit_decision_id, exit_timestamp, profit_loss, profit_loss_pct,
                             hold_duration_seconds, status)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'closed')
                        """, (ticker, entry_price, qty_to_close, entry_decision_id, entry_timestamp,
                              exit_price, decision_id, exit_timestamp, pnl, pnl_pct, hold_duration))

                        self.logger.info(f"Partial close: {qty_to_close}/{pos_qty} {ticker} @ ${exit_price:.2f} | P/L: ${pnl:+.2f} ({pnl_pct:+.1f}%)")

                    remaining_to_close -= qty_to_close

                # Update exit decision with total P/L and outcome
                if total_pnl != 0:
                    outcome = 'win' if total_pnl > 0 else 'loss'
                    cursor.execute("""
                        UPDATE trading_decisions
                        SET outcome = ?, profit_loss = ?
                        WHERE id = ?
                    """, (outcome, total_pnl, decision_id))

                conn.commit()
                self.logger.info(f"Total P/L for {ticker} SELL: ${total_pnl:+.2f}")

        except Exception as e:
            self.logger.error(f"Failed to track position close: {e}")
            import traceback
            traceback.print_exc()

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Get all currently open positions"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT ticker, entry_price, quantity, entry_timestamp
                    FROM positions
                    WHERE status = 'open'
                    ORDER BY entry_timestamp DESC
                """)

                positions = []
                for row in cursor.fetchall():
                    positions.append({
                        'ticker': row[0],
                        'entry_price': row[1],
                        'quantity': row[2],
                        'entry_timestamp': row[3]
                    })

                return positions

        except Exception as e:
            self.logger.error(f"Failed to get open positions: {e}")
            return []

    def reconcile_positions_with_broker(self) -> Dict[str, Any]:
        """
        Reconcile position database with actual broker positions
        This should be called on system startup to ensure consistency
        """
        try:
            from alpaca_broker import AlpacaBroker
            broker = AlpacaBroker()

            # Get positions from broker
            broker_positions = broker.get_positions()
            broker_holdings = {pos['ticker']: pos['shares'] for pos in broker_positions}

            # Get open positions from database
            db_positions = self.get_open_positions()
            db_holdings = {}
            for pos in db_positions:
                ticker = pos['ticker']
                if ticker not in db_holdings:
                    db_holdings[ticker] = 0
                db_holdings[ticker] += pos['quantity']

            # Find discrepancies
            discrepancies = []

            # Check for tickers in DB but not in broker (likely sold but not tracked)
            for ticker, db_qty in db_holdings.items():
                broker_qty = broker_holdings.get(ticker, 0)
                if db_qty != broker_qty:
                    discrepancies.append({
                        'ticker': ticker,
                        'db_quantity': db_qty,
                        'broker_quantity': broker_qty,
                        'difference': broker_qty - db_qty
                    })

            # Check for tickers in broker but not in DB (manual trades)
            for ticker, broker_qty in broker_holdings.items():
                if ticker not in db_holdings:
                    discrepancies.append({
                        'ticker': ticker,
                        'db_quantity': 0,
                        'broker_quantity': broker_qty,
                        'difference': broker_qty
                    })

            # Log results
            if discrepancies:
                self.logger.warning(f"Position reconciliation found {len(discrepancies)} discrepancies:")
                for disc in discrepancies:
                    self.logger.warning(
                        f"  {disc['ticker']}: DB={disc['db_quantity']}, "
                        f"Broker={disc['broker_quantity']}, Diff={disc['difference']}"
                    )
            else:
                self.logger.info("Position reconciliation: All positions match")

            return {
                'status': 'success',
                'discrepancies': discrepancies,
                'db_positions': len(db_positions),
                'broker_positions': len(broker_positions),
                'match': len(discrepancies) == 0
            }

        except Exception as e:
            self.logger.error(f"Failed to reconcile positions: {e}")
            return {
                'status': 'error',
                'error': str(e)
            }

    def get_position_performance_summary(self) -> Dict[str, Any]:
        """
        Get performance summary from closed positions (P/L tracking)
        This provides actual win rate and P/L statistics
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Get all closed positions with P/L
                cursor.execute("""
                    SELECT
                        COUNT(*) as total_trades,
                        SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END) as wins,
                        SUM(CASE WHEN profit_loss < 0 THEN 1 ELSE 0 END) as losses,
                        SUM(CASE WHEN profit_loss = 0 THEN 1 ELSE 0 END) as breakeven,
                        AVG(profit_loss) as avg_pl,
                        SUM(profit_loss) as total_pl,
                        AVG(profit_loss_pct) as avg_pl_pct,
                        MAX(profit_loss) as best_trade,
                        MIN(profit_loss) as worst_trade,
                        AVG(hold_duration_seconds) as avg_hold_duration_sec
                    FROM positions
                    WHERE status = 'closed'
                """)

                row = cursor.fetchone()

                if not row or row[0] == 0:
                    return {
                        'total_trades': 0,
                        'wins': 0,
                        'losses': 0,
                        'breakeven': 0,
                        'win_rate': 0.0,
                        'avg_pl': 0.0,
                        'total_pl': 0.0,
                        'avg_pl_pct': 0.0,
                        'best_trade': 0.0,
                        'worst_trade': 0.0,
                        'avg_hold_hours': 0.0
                    }

                total_trades, wins, losses, breakeven, avg_pl, total_pl, avg_pl_pct, best, worst, avg_duration = row

                return {
                    'total_trades': int(total_trades) if total_trades else 0,
                    'wins': int(wins) if wins else 0,
                    'losses': int(losses) if losses else 0,
                    'breakeven': int(breakeven) if breakeven else 0,
                    'win_rate': float(wins / total_trades) if total_trades > 0 else 0.0,
                    'avg_pl': float(avg_pl) if avg_pl else 0.0,
                    'total_pl': float(total_pl) if total_pl else 0.0,
                    'avg_pl_pct': float(avg_pl_pct) if avg_pl_pct else 0.0,
                    'best_trade': float(best) if best else 0.0,
                    'worst_trade': float(worst) if worst else 0.0,
                    'avg_hold_hours': float(avg_duration / 3600) if avg_duration else 0.0
                }

        except Exception as e:
            self.logger.error(f"Failed to get position performance: {e}")
            return {
                'total_trades': 0,
                'wins': 0,
                'losses': 0,
                'breakeven': 0,
                'win_rate': 0.0,
                'avg_pl': 0.0,
                'total_pl': 0.0,
                'avg_pl_pct': 0.0,
                'best_trade': 0.0,
                'worst_trade': 0.0,
                'avg_hold_hours': 0.0
            }

    def get_stats(self) -> Dict[str, Any]:
        """Get overall memory engine statistics"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                stats = {}
                
                # Decision count
                cursor.execute("SELECT COUNT(*) FROM trading_decisions")
                stats["total_decisions"] = cursor.fetchone()[0]
                
                # Market snapshots count
                cursor.execute("SELECT COUNT(*) FROM market_snapshots")
                stats["market_snapshots"] = cursor.fetchone()[0]
                
                # Performance records count
                cursor.execute("SELECT COUNT(*) FROM performance_metrics")
                stats["performance_records"] = cursor.fetchone()[0]
                
                # Date range
                cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM trading_decisions")
                date_range = cursor.fetchone()
                if date_range[0]:
                    stats["data_range"] = {
                        "from": date_range[0],
                        "to": date_range[1]
                    }
                
                return stats
                
        except Exception as e:
            self.logger.error(f"Failed to get stats: {e}")
            return {}

# Global memory engine instance
_memory_engine = None

def get_memory_engine() -> TradingMemoryEngine:
    """Get global memory engine instance"""
    global _memory_engine
    if _memory_engine is None:
        _memory_engine = TradingMemoryEngine()
    return _memory_engine

if __name__ == "__main__":
    # Simple test of the memory engine
    engine = get_memory_engine()
    stats = engine.get_stats()
    
    print(" TRADING MEMORY ENGINE TEST")
    print("=" * 40)
    print(f"Database: {engine.db_path}")
    print(f"Decisions stored: {stats.get('total_decisions', 0)}")
    print(f"Market snapshots: {stats.get('market_snapshots', 0)}")
    print(f"Performance records: {stats.get('performance_records', 0)}")
    
    if stats.get('data_range'):
        print(f"Data range: {stats['data_range']['from']} to {stats['data_range']['to']}")
    
    print("Memory engine working!")