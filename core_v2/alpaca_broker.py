"""
Alpaca Broker Interface
Real broker integration for paper trading with Alpaca using modern alpaca-py SDK
"""

import os
import logging
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
import pandas as pd
from decimal import Decimal, ROUND_DOWN
import time

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest, StopOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
except ImportError:
    print("Alpaca-py not installed. Run: pip install alpaca-py")
    exit(1)

from dotenv import load_dotenv
load_dotenv()

class AlpacaBroker:
    """Professional Alpaca broker interface for automated trading using modern alpaca-py SDK"""
    
    def __init__(self):
        """Initialize Alpaca connection with modern SDK"""
        self.api_key = os.getenv('ALPACA_API_KEY')
        self.secret_key = os.getenv('ALPACA_SECRET_KEY')
        self.base_url = os.getenv('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets')
        
        if not all([self.api_key, self.secret_key]):
            raise ValueError("Alpaca API credentials not found in environment variables")
            
        # Initialize Alpaca clients
        self.trading_client = TradingClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
            paper=True  # Always use paper trading
        )
        
        self.data_client = StockHistoricalDataClient(
            api_key=self.api_key,
            secret_key=self.secret_key
        )
        
        self.logger = logging.getLogger(__name__)
        
        # Verify connection
        self._verify_connection()
        
    def _verify_connection(self):
        """Verify connection to Alpaca and account status"""
        try:
            account_info = self.get_account_info()
            self.logger.info(f"Connected to Alpaca Paper Trading (modern SDK)")
            self.logger.info(f"Account Equity: ${account_info['equity']:,.2f}")
            self.logger.info(f"Buying Power: ${account_info['buying_power']:,.2f}")
            
            # Basic validation for paper trading
            if account_info['status'] not in ['ACTIVE', 'UNKNOWN']:
                self.logger.warning(f"Account status is {account_info['status']}")
                
        except Exception as e:
            raise ValueError(f"Failed to connect to Alpaca: {e}")
            
    def get_account_info(self) -> Dict[str, Any]:
        """Get account information using modern SDK"""
        try:
            account = self.trading_client.get_account()

            return {
                'equity': float(account.equity),
                'cash': float(account.cash),
                'buying_power': float(account.buying_power),
                'portfolio_value': float(account.portfolio_value),
                'long_market_value': float(account.long_market_value),  # Actual stocks value
                'last_equity': float(account.last_equity),  # Previous day's equity for P&L
                'status': str(account.status),
                'trading_blocked': account.trading_blocked
            }
        except Exception as e:
            self.logger.error(f"Failed to get account info: {e}")
            # Return default values for paper trading
            return {
                'equity': 100000.0,  # Default paper trading amount
                'cash': 100000.0,
                'buying_power': 100000.0,
                'portfolio_value': 0.0,
                'status': 'ACTIVE',
                'trading_blocked': False
            }
            
    def get_positions(self) -> List[Dict[str, Any]]:
        """Get current positions using modern SDK"""
        try:
            positions = self.trading_client.get_all_positions()
            position_list = []
            
            for position in positions:
                # Only include positions with actual shares
                if float(position.qty) != 0:
                    position_list.append({
                        'ticker': position.symbol,
                        'shares': float(position.qty),
                        'market_value': float(position.market_value),
                        'cost_basis': float(position.cost_basis),
                        'unrealized_pl': float(position.unrealized_pl),
                        'unrealized_plpc': float(position.unrealized_plpc),
                        'avg_entry_price': float(position.avg_entry_price),
                        'current_price': float(position.current_price) if position.current_price else 0
                    })
                    
            return position_list
            
        except Exception as e:
            self.logger.error(f"Failed to get positions: {e}")
            return []
            
    def get_orders(self, status: str = 'open') -> List[Dict[str, Any]]:
        """Get orders by status using modern SDK"""
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            
            # Map status string to enum
            status_map = {
                'open': QueryOrderStatus.OPEN,
                'closed': QueryOrderStatus.CLOSED,
                'all': QueryOrderStatus.ALL
            }
            
            request = GetOrdersRequest(
                status=status_map.get(status, QueryOrderStatus.OPEN),
                limit=100
            )
            
            orders = self.trading_client.get_orders(request)
            order_list = []
            
            for order in orders:
                # Parse status enum (e.g., "OrderStatus.FILLED" -> "filled")
                status_str = str(order.status).lower()
                if 'orderstatus.' in status_str:
                    status_str = status_str.split('orderstatus.')[1]

                # Parse side enum (e.g., "OrderSide.BUY" -> "buy")
                side_str = str(order.side).lower()
                if 'orderside.' in side_str:
                    side_str = side_str.split('orderside.')[1]

                order_list.append({
                    'id': order.id,
                    'ticker': order.symbol,
                    'symbol': order.symbol,  # Add both for compatibility
                    'side': side_str,
                    'qty': float(order.qty),
                    'filled_qty': float(order.filled_qty) if order.filled_qty else 0,
                    'order_type': str(order.order_type),
                    'status': status_str,
                    'submitted_at': order.submitted_at,
                    'filled_at': order.filled_at
                })
                
            return order_list
            
        except Exception as e:
            self.logger.error(f"Failed to get orders: {e}")
            return []
            
    def place_order(self, symbol: str = None, ticker: str = None, shares: int = None, qty: int = None, side: str = None, order_type: str = 'market') -> Optional[str]:
        """Generic place order method with flexible parameter names for compatibility"""
        try:
            # Handle flexible parameter names
            final_ticker = ticker or symbol
            final_shares = shares or qty
            
            if not final_ticker or not final_shares or not side:
                logging.error(f"Missing required parameters: ticker={final_ticker}, shares={final_shares}, side={side}")
                return None
            
            if side.upper() == 'BUY':
                return self.place_buy_order(final_ticker, final_shares, order_type)
            elif side.upper() == 'SELL':
                return self.place_sell_order(final_ticker, final_shares, order_type)
            else:
                logging.error(f"Invalid order side: {side}")
                return None
        except Exception as e:
            logging.error(f"Error placing {side} order for {final_ticker}: {e}")
            return None

    def place_buy_order(self, ticker: str, shares: int, order_type: str = 'market') -> Optional[str]:
        """Place buy order using modern SDK"""
        try:
            # Input validation
            if shares <= 0:
                self.logger.error(f"Invalid shares amount: {shares}")
                return None
                
            # Check buying power
            account = self.trading_client.get_account()
            current_price = self.get_latest_price(ticker)
            if not current_price:
                self.logger.error(f"Could not get price for {ticker}")
                return None
                
            estimated_cost = current_price * shares
            if estimated_cost > float(account.buying_power):
                self.logger.error(f"Insufficient buying power: ${estimated_cost:.2f} > ${float(account.buying_power):.2f}")
                return None
                
            # Create market order request
            market_order_data = MarketOrderRequest(
                symbol=ticker,
                qty=shares,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY
            )
            
            # Place order
            order = self.trading_client.submit_order(market_order_data)
            
            self.logger.info(f"BUY order placed: {shares} {ticker} (Order ID: {order.id})")
            return str(order.id)
            
        except Exception as e:
            self.logger.error(f"Failed to place buy order for {ticker}: {e}")
            return None
            
    def place_sell_order(self, ticker: str, shares: int, order_type: str = 'market') -> Optional[str]:
        """Place sell order using modern SDK"""
        try:
            # Input validation
            if shares <= 0:
                self.logger.error(f"Invalid shares amount: {shares}")
                return None
                
            # Check if we own enough shares
            positions = self.get_positions()
            owned_shares = 0
            for pos in positions:
                if pos['ticker'] == ticker:
                    owned_shares = abs(pos['shares'])  # abs() in case of short positions
                    break
                    
            if shares > owned_shares:
                self.logger.error(f"Cannot sell {shares} {ticker}: only own {owned_shares}")
                return None
                
            # Create market order request
            market_order_data = MarketOrderRequest(
                symbol=ticker,
                qty=shares,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY
            )
            
            # Place order
            order = self.trading_client.submit_order(market_order_data)
            
            self.logger.info(f"SELL order placed: {shares} {ticker} (Order ID: {order.id})")
            return str(order.id)
            
        except Exception as e:
            self.logger.error(f"Failed to place sell order for {ticker}: {e}")
            return None
            
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order using modern SDK"""
        try:
            self.trading_client.cancel_order_by_id(order_id)
            self.logger.info(f"Order cancelled: {order_id}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def get_order_filled_price(self, order_id: str) -> Optional[float]:
        """Get the filled average price for an order"""
        try:
            order = self.trading_client.get_order_by_id(order_id)
            if order.filled_avg_price:
                return float(order.filled_avg_price)
            return None
        except Exception as e:
            self.logger.error(f"Failed to get order {order_id}: {e}")
            return None

    def get_order_details(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Get full order details including status"""
        try:
            order = self.trading_client.get_order_by_id(order_id)
            status_str = str(order.status).lower().replace('orderstatus.', '')
            return {
                'id': str(order.id),
                'symbol': order.symbol,
                'side': str(order.side).lower().replace('orderside.', ''),
                'qty': float(order.qty) if order.qty else 0,
                'filled_qty': float(order.filled_qty) if order.filled_qty else 0,
                'status': status_str,
                'filled_avg_price': float(order.filled_avg_price) if order.filled_avg_price else None,
                'is_filled': status_str == 'filled',
                'is_pending': status_str in ['new', 'accepted', 'pending_new', 'partially_filled'],
            }
        except Exception as e:
            self.logger.error(f"Failed to get order details {order_id}: {e}")
            return None
            
    def get_latest_price(self, ticker: str) -> Optional[float]:
        """Get latest price for ticker using modern SDK with fallbacks"""
        max_retries = 3
        retry_delay = 0.5
        
        for attempt in range(max_retries):
            try:
                # Method 1: Try latest quote first
                request = StockLatestQuoteRequest(symbol_or_symbols=[ticker])
                latest_quote = self.data_client.get_stock_latest_quote(request)
                
                if ticker in latest_quote:
                    quote = latest_quote[ticker]
                    # Use mid price between bid and ask
                    bid = float(quote.bid_price) if quote.bid_price else 0
                    ask = float(quote.ask_price) if quote.ask_price else 0
                    if bid > 0 and ask > 0:
                        price = (bid + ask) / 2
                        self.logger.info(f"Got price for {ticker}: ${price:.2f} (quote)")
                        return price
                
                # Method 2: Fallback to latest trade
                from alpaca.data.requests import StockLatestTradeRequest
                trade_request = StockLatestTradeRequest(symbol_or_symbols=[ticker])
                latest_trade = self.data_client.get_stock_latest_trade(trade_request)
                
                if ticker in latest_trade:
                    trade = latest_trade[ticker]
                    if trade.price and trade.price > 0:
                        price = float(trade.price)
                        self.logger.info(f"Got price for {ticker}: ${price:.2f} (trade)")
                        return price
                
                # Method 3: Fallback to recent bar
                from alpaca.data.requests import StockBarsRequest
                from alpaca.data.timeframe import TimeFrame
                from datetime import datetime, timedelta
                
                end_time = datetime.now()
                start_time = end_time - timedelta(days=2)
                
                bars_request = StockBarsRequest(
                    symbol_or_symbols=[ticker],
                    timeframe=TimeFrame.Hour,
                    start=start_time,
                    end=end_time,
                    limit=1
                )
                
                bars = self.data_client.get_stock_bars(bars_request)
                if ticker in bars.data and bars.data[ticker]:
                    bar = bars.data[ticker][-1]  # Get the most recent bar
                    price = float(bar.close)
                    self.logger.info(f"Got price for {ticker}: ${price:.2f} (bar)")
                    return price
                
                # If all methods failed on this attempt, retry
                if attempt < max_retries - 1:
                    self.logger.warning(f"No price data for {ticker} on attempt {attempt + 1}, retrying...")
                    time.sleep(retry_delay)
                    retry_delay *= 1.5
                    continue
                    
            except Exception as e:
                self.logger.warning(f"Price fetch attempt {attempt + 1} failed for {ticker}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 1.5
                    continue
                else:
                    self.logger.error(f"All price fetch attempts failed for {ticker}: {e}")
        
        # All attempts failed
        self.logger.error(f"Could not get price for {ticker} after {max_retries} attempts")
        return None
            
    def get_historical_data(self, ticker: str, days: int = 30) -> pd.DataFrame:
        """Get historical price data using modern SDK"""
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            
            request = StockBarsRequest(
                symbol_or_symbols=[ticker],
                timeframe=TimeFrame.Day,
                start=start_date,
                end=end_date
            )
            
            bars = self.data_client.get_stock_bars(request)
            
            if ticker in bars.data:
                df_data = []
                for bar in bars.data[ticker]:
                    df_data.append({
                        'Open': float(bar.open),
                        'High': float(bar.high),
                        'Low': float(bar.low),
                        'Close': float(bar.close),
                        'Volume': float(bar.volume),
                        'timestamp': bar.timestamp
                    })
                
                if df_data:
                    df = pd.DataFrame(df_data)
                    df.set_index('timestamp', inplace=True)
                    return df
                    
            return pd.DataFrame()
            
        except Exception as e:
            self.logger.error(f"Failed to get historical data for {ticker}: {e}")
            return pd.DataFrame()
            
    def wait_for_order_fill(self, order_id: str, timeout_seconds: int = 30) -> Dict[str, Any]:
        """Wait for order to fill and return execution details"""
        start_time = time.time()
        
        while time.time() - start_time < timeout_seconds:
            try:
                order = self.trading_client.get_order_by_id(order_id)
                
                if str(order.status) == 'OrderStatus.FILLED':
                    return {
                        'status': 'filled',
                        'filled_qty': float(order.filled_qty) if order.filled_qty else 0,
                        'filled_avg_price': float(order.filled_avg_price) if order.filled_avg_price else 0,
                        'filled_at': order.filled_at
                    }
                elif str(order.status) in ['OrderStatus.CANCELED', 'OrderStatus.REJECTED']:
                    return {
                        'status': str(order.status).lower().replace('orderstatus.', ''),
                        'filled_qty': 0,
                        'filled_avg_price': 0,
                        'filled_at': None
                    }
                    
                time.sleep(1)  # Wait 1 second before checking again
                
            except Exception as e:
                self.logger.error(f"Error checking order status: {e}")
                break
                
        # Timeout reached
        return {
            'status': 'timeout',
            'filled_qty': 0,
            'filled_avg_price': 0,
            'filled_at': None
        }
        
    def place_stop_loss_order(self, ticker: str, shares: int, stop_price: float) -> Optional[str]:
        """Place stop-loss order using modern SDK"""
        try:
            stop_order_data = StopOrderRequest(
                symbol=ticker,
                qty=shares,
                side=OrderSide.SELL,
                stop_price=stop_price,
                time_in_force=TimeInForce.GTC  # Good till canceled
            )
            
            order = self.trading_client.submit_order(stop_order_data)
            
            self.logger.info(f"STOP-LOSS order placed: {shares} {ticker} @ ${stop_price:.2f} (Order ID: {order.id})")
            return str(order.id)
            
        except Exception as e:
            self.logger.error(f"Failed to place stop-loss order for {ticker}: {e}")
            return None
            
    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Get comprehensive portfolio summary"""
        try:
            account_info = self.get_account_info()
            positions = self.get_positions()
            
            total_value = 0
            total_cost = 0
            total_unrealized_pl = 0
            
            for pos in positions:
                total_value += pos['market_value']
                total_cost += pos['cost_basis']
                total_unrealized_pl += pos['unrealized_pl']
                
            return {
                'cash': account_info.get('cash', 0),
                'equity': account_info.get('equity', 0),
                'portfolio_value': total_value,
                'total_cost_basis': total_cost,
                'unrealized_pl': total_unrealized_pl,
                'position_count': len(positions),
                'buying_power': account_info.get('buying_power', 0),
                'positions': positions
            }
            
        except Exception as e:
            self.logger.error(f"Failed to get portfolio summary: {e}")
            return {}
            
    def is_market_open(self) -> bool:
        """Check if market is currently open using modern SDK"""
        try:
            clock = self.trading_client.get_clock()
            return clock.is_open
        except Exception as e:
            self.logger.error(f"Failed to check market status: {e}")
            return False