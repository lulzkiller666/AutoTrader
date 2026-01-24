#!/usr/bin/env python3
"""
System Health Check Module

Monitors critical system components and provides health status.
"""

import os
import sqlite3
import logging
from datetime import datetime
from typing import Dict, Any, List
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    component: str
    status: HealthStatus
    message: str
    details: Dict[str, Any] = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()
        if self.details is None:
            self.details = {}


class SystemHealthMonitor:
    """Monitors health of all system components"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.last_check: Dict[str, HealthCheckResult] = {}

    def check_all(self) -> Dict[str, HealthCheckResult]:
        """Run all health checks"""
        checks = {
            'database': self.check_database(),
            'api_keys': self.check_api_keys(),
            'broker': self.check_broker_connection(),
            'data_sources': self.check_data_sources(),
            'disk_space': self.check_disk_space(),
            'memory': self.check_memory(),
        }

        self.last_check = checks
        return checks

    def check_database(self) -> HealthCheckResult:
        """Check database connectivity and integrity"""
        try:
            db_path = Path("automated_trading/trading_memory_v2.db")

            if not db_path.exists():
                return HealthCheckResult(
                    component="database",
                    status=HealthStatus.UNHEALTHY,
                    message="Database file not found",
                    details={'path': str(db_path)}
                )

            # Try to connect and query
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            cursor = conn.cursor()

            # Check tables exist
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]

            required_tables = ['trading_decisions', 'market_snapshots', 'performance_metrics']
            missing_tables = [t for t in required_tables if t not in tables]

            if missing_tables:
                conn.close()
                return HealthCheckResult(
                    component="database",
                    status=HealthStatus.DEGRADED,
                    message=f"Missing tables: {', '.join(missing_tables)}",
                    details={'missing': missing_tables, 'found': tables}
                )

            # Check database size
            cursor.execute("SELECT COUNT(*) FROM trading_decisions")
            decision_count = cursor.fetchone()[0]

            conn.close()

            return HealthCheckResult(
                component="database",
                status=HealthStatus.HEALTHY,
                message="Database operational",
                details={
                    'path': str(db_path),
                    'size_mb': db_path.stat().st_size / (1024 * 1024),
                    'decisions': decision_count,
                    'tables': len(tables)
                }
            )

        except sqlite3.OperationalError as e:
            return HealthCheckResult(
                component="database",
                status=HealthStatus.UNHEALTHY,
                message=f"Database error: {e}",
                details={'error': str(e)}
            )
        except Exception as e:
            return HealthCheckResult(
                component="database",
                status=HealthStatus.UNKNOWN,
                message=f"Unexpected error: {e}",
                details={'error': str(e)}
            )

    def check_api_keys(self) -> HealthCheckResult:
        """Check if required API keys are present"""
        required_keys = {
            'OPENAI_API_KEY': 'OpenAI (critical)',
            'ALPACA_API_KEY': 'Alpaca (critical)',
            'ALPACA_SECRET_KEY': 'Alpaca Secret (critical)',
        }

        optional_keys = {
            'POLYGON_API_KEY': 'Polygon.io (data)',
            'ALPHA_VANTAGE_KEY': 'Alpha Vantage (data)',
            'FINNHUB_API_KEY': 'Finnhub (news)',
        }

        missing_required = []
        missing_optional = []

        for key, description in required_keys.items():
            if not os.getenv(key):
                missing_required.append(description)

        for key, description in optional_keys.items():
            if not os.getenv(key):
                missing_optional.append(description)

        if missing_required:
            return HealthCheckResult(
                component="api_keys",
                status=HealthStatus.UNHEALTHY,
                message=f"Missing critical API keys: {', '.join(missing_required)}",
                details={
                    'missing_required': missing_required,
                    'missing_optional': missing_optional
                }
            )

        if missing_optional:
            return HealthCheckResult(
                component="api_keys",
                status=HealthStatus.DEGRADED,
                message=f"Missing optional API keys: {', '.join(missing_optional)}",
                details={'missing_optional': missing_optional}
            )

        return HealthCheckResult(
            component="api_keys",
            status=HealthStatus.HEALTHY,
            message="All API keys present",
            details={
                'required': len(required_keys),
                'optional': len(optional_keys)
            }
        )

    def check_broker_connection(self) -> HealthCheckResult:
        """Check Alpaca broker connectivity"""
        try:
            from alpaca_broker import AlpacaBroker

            broker = AlpacaBroker()
            account = broker.get_account_info()

            if not account:
                return HealthCheckResult(
                    component="broker",
                    status=HealthStatus.UNHEALTHY,
                    message="Failed to fetch account info"
                )

            return HealthCheckResult(
                component="broker",
                status=HealthStatus.HEALTHY,
                message="Broker connected",
                details={
                    'equity': float(account.get('equity', 0)),
                    'buying_power': float(account.get('buying_power', 0)),
                    'account_status': str(account.get('status', 'unknown'))
                }
            )

        except ImportError as e:
            return HealthCheckResult(
                component="broker",
                status=HealthStatus.UNHEALTHY,
                message=f"Import error: {e}"
            )
        except Exception as e:
            return HealthCheckResult(
                component="broker",
                status=HealthStatus.UNHEALTHY,
                message=f"Connection failed: {e}",
                details={'error': str(e)}
            )

    def check_data_sources(self) -> HealthCheckResult:
        """Check data source availability"""
        try:
            from enhanced_data_sources import EnhancedDataFetcher

            fetcher = EnhancedDataFetcher()

            # Try to fetch data for a known ticker
            data = fetcher.get_market_data('SPY', use_cache=False)

            if data and data.get('price', 0) > 0:
                return HealthCheckResult(
                    component="data_sources",
                    status=HealthStatus.HEALTHY,
                    message="Data sources operational",
                    details={
                        'test_ticker': 'SPY',
                        'price': data.get('price'),
                        'source': data.get('source', 'unknown')
                    }
                )
            else:
                return HealthCheckResult(
                    component="data_sources",
                    status=HealthStatus.DEGRADED,
                    message="Data fetch returned invalid data"
                )

        except Exception as e:
            return HealthCheckResult(
                component="data_sources",
                status=HealthStatus.DEGRADED,
                message=f"Data source check failed: {e}",
                details={'error': str(e)}
            )

    def check_disk_space(self) -> HealthCheckResult:
        """Check available disk space"""
        try:
            import shutil

            usage = shutil.disk_usage("/")
            free_gb = usage.free / (1024 ** 3)
            total_gb = usage.total / (1024 ** 3)
            used_pct = (usage.used / usage.total) * 100

            if free_gb < 1.0:  # Less than 1GB free
                status = HealthStatus.UNHEALTHY
                message = f"Critical: Only {free_gb:.1f}GB free"
            elif free_gb < 5.0:  # Less than 5GB free
                status = HealthStatus.DEGRADED
                message = f"Warning: Only {free_gb:.1f}GB free"
            else:
                status = HealthStatus.HEALTHY
                message = f"{free_gb:.1f}GB available"

            return HealthCheckResult(
                component="disk_space",
                status=status,
                message=message,
                details={
                    'free_gb': round(free_gb, 2),
                    'total_gb': round(total_gb, 2),
                    'used_pct': round(used_pct, 1)
                }
            )

        except Exception as e:
            return HealthCheckResult(
                component="disk_space",
                status=HealthStatus.UNKNOWN,
                message=f"Could not check disk space: {e}"
            )

    def check_memory(self) -> HealthCheckResult:
        """Check system memory usage"""
        try:
            import psutil

            memory = psutil.virtual_memory()
            available_mb = memory.available / (1024 * 1024)
            used_pct = memory.percent

            if available_mb < 100:  # Less than 100MB
                status = HealthStatus.UNHEALTHY
                message = f"Critical: Only {available_mb:.0f}MB available"
            elif used_pct > 90:  # More than 90% used
                status = HealthStatus.DEGRADED
                message = f"Warning: {used_pct:.0f}% memory used"
            else:
                status = HealthStatus.HEALTHY
                message = f"{used_pct:.0f}% memory used"

            return HealthCheckResult(
                component="memory",
                status=status,
                message=message,
                details={
                    'available_mb': round(available_mb, 0),
                    'total_mb': round(memory.total / (1024 * 1024), 0),
                    'used_pct': round(used_pct, 1)
                }
            )

        except ImportError:
            return HealthCheckResult(
                component="memory",
                status=HealthStatus.UNKNOWN,
                message="psutil not available (install with: pip install psutil)"
            )
        except Exception as e:
            return HealthCheckResult(
                component="memory",
                status=HealthStatus.UNKNOWN,
                message=f"Could not check memory: {e}"
            )

    def get_overall_status(self) -> HealthStatus:
        """Get overall system health status"""
        if not self.last_check:
            return HealthStatus.UNKNOWN

        statuses = [check.status for check in self.last_check.values()]

        if HealthStatus.UNHEALTHY in statuses:
            return HealthStatus.UNHEALTHY
        elif HealthStatus.DEGRADED in statuses:
            return HealthStatus.DEGRADED
        elif all(s == HealthStatus.HEALTHY for s in statuses):
            return HealthStatus.HEALTHY
        else:
            return HealthStatus.UNKNOWN

    def get_summary(self) -> str:
        """Get human-readable health summary"""
        if not self.last_check:
            return "No health checks run yet"

        overall = self.get_overall_status()
        lines = [f"Overall Status: {overall.value.upper()}"]
        lines.append("-" * 50)

        for component, result in self.last_check.items():
            icon = {
                HealthStatus.HEALTHY: "[OK]",
                HealthStatus.DEGRADED: "[WARN]",
                HealthStatus.UNHEALTHY: "[ERR]",
                HealthStatus.UNKNOWN: "?"
            }.get(result.status, "?")

            lines.append(f"{icon} {result.component}: {result.message}")

        return "\n".join(lines)


# Global health monitor instance
_health_monitor = None


def get_health_monitor() -> SystemHealthMonitor:
    """Get singleton health monitor instance"""
    global _health_monitor
    if _health_monitor is None:
        _health_monitor = SystemHealthMonitor()
    return _health_monitor


if __name__ == "__main__":
    # Test health checks
    monitor = SystemHealthMonitor()
    results = monitor.check_all()

    print("System Health Check Results:")
    print("=" * 60)
    print(monitor.get_summary())
    print("\nDetailed Results:")
    print("=" * 60)

    for component, result in results.items():
        print(f"\n{component.upper()}:")
        print(f"  Status: {result.status.value}")
        print(f"  Message: {result.message}")
        if result.details:
            print(f"  Details: {result.details}")
