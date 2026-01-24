#!/usr/bin/env python3
"""
Circuit Breaker Pattern Implementation

Prevents repeated failures by temporarily disabling failing operations.

States:
- CLOSED: Normal operation, requests pass through
- OPEN: Failures detected, requests immediately fail
- HALF_OPEN: Testing if service recovered, limited requests allowed
"""

import time
import logging
from enum import Enum
from typing import Callable, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Blocking requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior"""
    failure_threshold: int = 5  # Failures before opening circuit
    success_threshold: int = 2  # Successes to close from half-open
    timeout: float = 60.0  # Seconds before attempting recovery
    half_open_max_calls: int = 3  # Max calls to test in half-open state


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open"""
    pass


class CircuitBreaker:
    """
    Circuit breaker pattern implementation

    Usage:
        breaker = CircuitBreaker("OpenAI_API")

        try:
            result = breaker.call(lambda: api.chat.completions.create(...))
        except CircuitBreakerError:
            logger.error("Circuit breaker is open, service unavailable")
    """

    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.half_open_calls = 0

        logger.info(f" Circuit breaker initialized: {name}")

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function through circuit breaker

        Args:
            func: Function to execute
            *args, **kwargs: Arguments to pass to function

        Returns:
            Function result if successful

        Raises:
            CircuitBreakerError: If circuit is open
            Exception: Original exception from function
        """
        # Check if circuit should transition to half-open
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                logger.info(f"{self.name}: Attempting recovery (HALF_OPEN)")
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
            else:
                raise CircuitBreakerError(
                    f"Circuit breaker is OPEN for {self.name}. "
                    f"Retry in {self._time_until_retry():.0f}s"
                )

        # Limit calls in half-open state
        if self.state == CircuitState.HALF_OPEN:
            if self.half_open_calls >= self.config.half_open_max_calls:
                raise CircuitBreakerError(
                    f"Circuit breaker {self.name} in HALF_OPEN state, "
                    f"max test calls ({self.config.half_open_max_calls}) reached"
                )
            self.half_open_calls += 1

        # Execute the function
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result

        except Exception as e:
            self._on_failure(e)
            raise

    def _on_success(self):
        """Handle successful call"""
        self.failure_count = 0

        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.config.success_threshold:
                logger.info(f"{self.name}: Circuit CLOSED (service recovered)")
                self.state = CircuitState.CLOSED
                self.success_count = 0
                self.half_open_calls = 0

    def _on_failure(self, exception: Exception):
        """Handle failed call"""
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        self.success_count = 0  # Reset success count

        logger.warning(
            f"{self.name}: Failure {self.failure_count}/{self.config.failure_threshold} - {exception}"
        )

        # Open circuit if threshold reached
        if self.failure_count >= self.config.failure_threshold:
            logger.error(f" {self.name}: Circuit OPEN (too many failures)")
            self.state = CircuitState.OPEN

        # If in half-open and fails, go back to open
        elif self.state == CircuitState.HALF_OPEN:
            logger.warning(f" {self.name}: Back to OPEN (recovery test failed)")
            self.state = CircuitState.OPEN

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt recovery"""
        if self.last_failure_time is None:
            return True

        time_since_failure = (datetime.now() - self.last_failure_time).total_seconds()
        return time_since_failure >= self.config.timeout

    def _time_until_retry(self) -> float:
        """Get seconds until retry is allowed"""
        if self.last_failure_time is None:
            return 0.0

        time_since_failure = (datetime.now() - self.last_failure_time).total_seconds()
        return max(0, self.config.timeout - time_since_failure)

    def reset(self):
        """Manually reset circuit breaker to CLOSED state"""
        logger.info(f"{self.name}: Manual reset to CLOSED")
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
        self.half_open_calls = 0

    def get_status(self) -> dict:
        """Get current circuit breaker status"""
        return {
            'name': self.name,
            'state': self.state.value,
            'failure_count': self.failure_count,
            'success_count': self.success_count,
            'last_failure': self.last_failure_time.isoformat() if self.last_failure_time else None,
            'time_until_retry': self._time_until_retry() if self.state == CircuitState.OPEN else 0,
        }


# Global circuit breakers for critical services
CIRCUIT_BREAKERS = {
    'openai': CircuitBreaker('OpenAI_API', CircuitBreakerConfig(
        failure_threshold=3,
        success_threshold=2,
        timeout=120.0  # 2 minutes
    )),
    'alpaca': CircuitBreaker('Alpaca_API', CircuitBreakerConfig(
        failure_threshold=5,
        success_threshold=2,
        timeout=60.0  # 1 minute
    )),
    'polygon': CircuitBreaker('Polygon_API', CircuitBreakerConfig(
        failure_threshold=10,
        success_threshold=3,
        timeout=30.0  # 30 seconds
    )),
    'alpha_vantage': CircuitBreaker('AlphaVantage_API', CircuitBreakerConfig(
        failure_threshold=10,
        success_threshold=3,
        timeout=60.0  # 1 minute (rate limits)
    )),
}


def get_circuit_breaker(name: str) -> CircuitBreaker:
    """Get or create circuit breaker by name"""
    if name not in CIRCUIT_BREAKERS:
        CIRCUIT_BREAKERS[name] = CircuitBreaker(name)
    return CIRCUIT_BREAKERS[name]


def get_all_circuit_status() -> dict:
    """Get status of all circuit breakers"""
    return {
        name: breaker.get_status()
        for name, breaker in CIRCUIT_BREAKERS.items()
    }


if __name__ == "__main__":
    # Test circuit breaker
    import random

    def flaky_api_call():
        """Simulates a flaky API that fails randomly"""
        if random.random() < 0.7:  # 70% failure rate
            raise ConnectionError("API timeout")
        return "Success!"

    breaker = CircuitBreaker("TestAPI", CircuitBreakerConfig(
        failure_threshold=3,
        success_threshold=2,
        timeout=5.0
    ))

    print("Testing circuit breaker...")
    for i in range(15):
        try:
            result = breaker.call(flaky_api_call)
            print(f"Call {i+1}: {result}")
        except CircuitBreakerError as e:
            print(f"Call {i+1}: BLOCKED - {e}")
        except Exception as e:
            print(f"Call {i+1}: FAILED - {e}")

        print(f"  Status: {breaker.get_status()}")
        time.sleep(0.5)
