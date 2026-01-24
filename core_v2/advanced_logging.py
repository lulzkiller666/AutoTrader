#!/usr/bin/env python3
"""
Advanced Logging System for AutoTrader v2

Captures everything for analysis:
- Trading decisions with full context
- Trade executions and results
- API calls (OpenAI, Alpaca, data sources)
- Errors and exceptions
- Performance metrics
- Market data snapshots

All logs are in JSONL format for easy analysis.
"""

import os
import sys
import json
import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from logging.handlers import RotatingFileHandler
import threading


class JSONFormatter(logging.Formatter):
    """Format log records as JSON"""

    def format(self, record):
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add extra fields if present
        if hasattr(record, 'extra_data'):
            log_data["data"] = record.extra_data

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": traceback.format_exception(*record.exc_info) if record.exc_info[0] else None
            }

        return json.dumps(log_data)


class TradingLogger:
    """
    Comprehensive logging system for trading operations.

    Log files created:
    - decisions.jsonl: All trading decisions with full context
    - executions.jsonl: Trade execution results
    - api_calls.jsonl: All API interactions
    - errors.jsonl: Errors and exceptions
    - market_data.jsonl: Market data snapshots
    - system.log: General system logs (human readable)
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, log_dir: str = None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, log_dir: str = None):
        if self._initialized:
            return

        if log_dir is None:
            log_dir = Path(__file__).resolve().parent.parent / "logs"

        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Create dated subdirectory
        today = datetime.now().strftime("%Y-%m-%d")
        self.daily_dir = self.log_dir / today
        self.daily_dir.mkdir(exist_ok=True)

        # Initialize all loggers
        self._setup_loggers()
        self._initialized = True

        self.log_system("info", "Advanced logging system initialized", {
            "log_dir": str(self.log_dir),
            "daily_dir": str(self.daily_dir)
        })

    def _setup_loggers(self):
        """Set up all specialized loggers"""

        # Decision logger (JSONL)
        self.decision_logger = self._create_json_logger(
            "decisions",
            self.daily_dir / "decisions.jsonl",
            max_bytes=50*1024*1024  # 50MB
        )

        # Execution logger (JSONL)
        self.execution_logger = self._create_json_logger(
            "executions",
            self.daily_dir / "executions.jsonl",
            max_bytes=20*1024*1024  # 20MB
        )

        # API call logger (JSONL)
        self.api_logger = self._create_json_logger(
            "api_calls",
            self.daily_dir / "api_calls.jsonl",
            max_bytes=100*1024*1024  # 100MB (API calls can be verbose)
        )

        # Error logger (JSONL)
        self.error_logger = self._create_json_logger(
            "errors",
            self.daily_dir / "errors.jsonl",
            max_bytes=20*1024*1024  # 20MB
        )

        # Market data logger (JSONL)
        self.market_logger = self._create_json_logger(
            "market_data",
            self.daily_dir / "market_data.jsonl",
            max_bytes=100*1024*1024  # 100MB
        )

        # System logger (human readable)
        self.system_logger = logging.getLogger("system")
        self.system_logger.setLevel(logging.DEBUG)

        # Console handler
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%H:%M:%S'
        ))
        self.system_logger.addHandler(console)

        # File handler (rotating)
        file_handler = RotatingFileHandler(
            self.daily_dir / "system.log",
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
        ))
        self.system_logger.addHandler(file_handler)

    def _create_json_logger(self, name: str, filepath: Path, max_bytes: int = 50*1024*1024) -> logging.Logger:
        """Create a JSON-formatted rotating logger"""
        logger = logging.getLogger(f"trading.{name}")
        logger.setLevel(logging.DEBUG)
        logger.handlers = []  # Clear existing handlers

        handler = RotatingFileHandler(
            filepath,
            maxBytes=max_bytes,
            backupCount=10
        )
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)

        return logger

    def _log_json(self, logger: logging.Logger, level: str, message: str, data: Dict[str, Any] = None):
        """Log a message with JSON data"""
        record = logging.LogRecord(
            name=logger.name,
            level=getattr(logging, level.upper()),
            pathname="",
            lineno=0,
            msg=message,
            args=(),
            exc_info=None
        )
        if data:
            record.extra_data = data
        logger.handle(record)

    # ===== PUBLIC LOGGING METHODS =====

    def log_decision(self, decision: Dict[str, Any], context: Dict[str, Any] = None):
        """
        Log a trading decision with full context.

        Args:
            decision: The trading decision (action, ticker, shares, confidence, reasoning, etc.)
            context: Additional context (portfolio state, market data, session info)
        """
        data = {
            "decision": decision,
            "context": context or {},
            "decision_id": datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        }
        self._log_json(self.decision_logger, "info", "Trading decision made", data)
        self.log_system("info", f"Decision: {decision.get('action', 'UNKNOWN')} {decision.get('ticker', '')} ({decision.get('confidence', 0):.0%})")

    def log_execution(self,
                      decision: Dict[str, Any],
                      result: Dict[str, Any],
                      execution_time_ms: float = None):
        """
        Log a trade execution result.

        Args:
            decision: The original decision
            result: Execution result from broker
            execution_time_ms: Time taken to execute
        """
        data = {
            "decision": decision,
            "result": result,
            "execution_time_ms": execution_time_ms,
            "executed_at": datetime.now().isoformat()
        }
        self._log_json(self.execution_logger, "info", "Trade executed", data)

        status = result.get('status', 'unknown') if isinstance(result, dict) else str(result)
        self.log_system("info", f"Execution: {decision.get('action', '')} {decision.get('ticker', '')} -> {status}")

    def log_api_call(self,
                     service: str,
                     endpoint: str,
                     request: Dict[str, Any] = None,
                     response: Dict[str, Any] = None,
                     duration_ms: float = None,
                     error: str = None):
        """
        Log an API call (OpenAI, Alpaca, data sources).

        Args:
            service: Service name (openai, alpaca, polygon, etc.)
            endpoint: API endpoint or method called
            request: Request data (sanitized - no API keys)
            response: Response data (truncated if needed)
            duration_ms: Call duration in milliseconds
            error: Error message if call failed
        """
        data = {
            "service": service,
            "endpoint": endpoint,
            "request": self._sanitize_data(request),
            "response": self._truncate_response(response),
            "duration_ms": duration_ms,
            "error": error,
            "success": error is None
        }

        level = "error" if error else "info"
        self._log_json(self.api_logger, level, f"API call: {service}/{endpoint}", data)

    def log_error(self,
                  error_type: str,
                  message: str,
                  exception: Exception = None,
                  context: Dict[str, Any] = None):
        """
        Log an error with full context.

        Args:
            error_type: Category of error (api_error, parse_error, execution_error, etc.)
            message: Error description
            exception: The exception object if available
            context: Additional context
        """
        data = {
            "error_type": error_type,
            "message": message,
            "context": context or {},
        }

        if exception:
            data["exception"] = {
                "type": type(exception).__name__,
                "message": str(exception),
                "traceback": traceback.format_exc()
            }

        self._log_json(self.error_logger, "error", message, data)
        self.log_system("error", f"[{error_type}] {message}")

    def log_market_data(self,
                        ticker: str,
                        data: Dict[str, Any],
                        source: str,
                        fetch_time_ms: float = None):
        """
        Log fetched market data.

        Args:
            ticker: Stock symbol
            data: Market data (price, volume, etc.)
            source: Data source (polygon, alpha_vantage, stooq)
            fetch_time_ms: Time to fetch data
        """
        log_data = {
            "ticker": ticker,
            "data": data,
            "source": source,
            "fetch_time_ms": fetch_time_ms
        }
        self._log_json(self.market_logger, "info", f"Market data: {ticker}", log_data)

    def log_performance(self, metrics: Dict[str, Any]):
        """
        Log performance metrics.

        Args:
            metrics: Performance data (equity, returns, win_rate, etc.)
        """
        data = {
            "metrics": metrics,
            "snapshot_time": datetime.now().isoformat()
        }
        self._log_json(self.decision_logger, "info", "Performance snapshot", data)

    def log_system(self, level: str, message: str, data: Dict[str, Any] = None):
        """
        Log general system message.

        Args:
            level: Log level (debug, info, warning, error)
            message: Log message
            data: Optional extra data
        """
        log_func = getattr(self.system_logger, level.lower(), self.system_logger.info)
        if data:
            message = f"{message} | {json.dumps(data)}"
        log_func(message)

    def log_session_start(self, session_type: str, profile: str, config: Dict[str, Any] = None):
        """Log trading session start"""
        data = {
            "event": "session_start",
            "session_type": session_type,
            "profile": profile,
            "config": config or {}
        }
        self._log_json(self.decision_logger, "info", f"Session started: {session_type}", data)
        self.log_system("info", f"=== Trading Session Started: {session_type} ({profile}) ===")

    def log_session_end(self, session_type: str, summary: Dict[str, Any] = None):
        """Log trading session end"""
        data = {
            "event": "session_end",
            "session_type": session_type,
            "summary": summary or {}
        }
        self._log_json(self.decision_logger, "info", f"Session ended: {session_type}", data)
        self.log_system("info", f"=== Trading Session Ended: {session_type} ===")

    # ===== HELPER METHODS =====

    def _sanitize_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Remove sensitive data like API keys"""
        if data is None:
            return None

        sanitized = {}
        sensitive_keys = {'api_key', 'secret', 'password', 'token', 'key'}

        for k, v in data.items():
            if any(s in k.lower() for s in sensitive_keys):
                sanitized[k] = "***REDACTED***"
            elif isinstance(v, dict):
                sanitized[k] = self._sanitize_data(v)
            else:
                sanitized[k] = v

        return sanitized

    def _truncate_response(self, response: Any, max_length: int = 5000) -> Any:
        """Truncate large responses"""
        if response is None:
            return None

        if isinstance(response, str) and len(response) > max_length:
            return response[:max_length] + f"... [TRUNCATED {len(response) - max_length} chars]"

        if isinstance(response, dict):
            return {k: self._truncate_response(v, max_length) for k, v in response.items()}

        return response

    def get_log_stats(self) -> Dict[str, Any]:
        """Get statistics about log files"""
        stats = {}
        for log_file in self.daily_dir.glob("*"):
            if log_file.is_file():
                stats[log_file.name] = {
                    "size_bytes": log_file.stat().st_size,
                    "size_mb": round(log_file.stat().st_size / 1024 / 1024, 2)
                }
        return stats


# Global logger instance
_logger: Optional[TradingLogger] = None


def get_logger(log_dir: str = None) -> TradingLogger:
    """Get the global trading logger instance"""
    global _logger
    if _logger is None:
        _logger = TradingLogger(log_dir)
    return _logger


# ===== CONVENIENCE FUNCTIONS =====

def log_decision(decision: Dict, context: Dict = None):
    get_logger().log_decision(decision, context)

def log_execution(decision: Dict, result: Dict, execution_time_ms: float = None):
    get_logger().log_execution(decision, result, execution_time_ms)

def log_api_call(service: str, endpoint: str, **kwargs):
    get_logger().log_api_call(service, endpoint, **kwargs)

def log_error(error_type: str, message: str, exception: Exception = None, context: Dict = None):
    get_logger().log_error(error_type, message, exception, context)

def log_market_data(ticker: str, data: Dict, source: str, fetch_time_ms: float = None):
    get_logger().log_market_data(ticker, data, source, fetch_time_ms)

def log_system(level: str, message: str, data: Dict = None):
    get_logger().log_system(level, message, data)


if __name__ == "__main__":
    # Test the logging system
    logger = get_logger()

    print("Testing Advanced Logging System...")
    print(f"Log directory: {logger.daily_dir}")

    # Test decision logging
    logger.log_decision(
        decision={
            "action": "BUY",
            "ticker": "AAPL",
            "shares": 10,
            "confidence": 0.85,
            "reasoning": "Strong momentum and technical breakout"
        },
        context={
            "session": "mid_morning",
            "portfolio_value": 100000,
            "cash": 25000
        }
    )

    # Test API call logging
    logger.log_api_call(
        service="openai",
        endpoint="chat.completions",
        request={"model": "gpt-4o", "messages": [{"role": "user", "content": "..."}]},
        response={"choices": [{"message": {"content": "..."}}]},
        duration_ms=1250
    )

    # Test error logging
    try:
        raise ValueError("Test error")
    except Exception as e:
        logger.log_error("test_error", "This is a test error", exception=e)

    print("\nLog files created:")
    for name, stats in logger.get_log_stats().items():
        print(f"  {name}: {stats['size_mb']} MB")

    print("\nAdvanced logging system ready!")
