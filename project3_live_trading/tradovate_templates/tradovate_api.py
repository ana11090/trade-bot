"""
Tradovate REST/WebSocket API wrapper stub.

Replace with actual implementation connecting to Tradovate's API.
Docs: https://api.tradovate.com/
"""


class TradovateAPI:
    def __init__(self, api_key, api_secret, demo=True):
        self.api_key    = api_key
        self.api_secret = api_secret
        self.base_url   = "https://demo.tradovateapi.com/v1" if demo else "https://live.tradovateapi.com/v1"
        self.token      = None

    async def authenticate(self):
        """Authenticate and get access token."""
        raise NotImplementedError("Implement Tradovate OAuth authentication")

    async def get_account(self):
        """Return account balance and equity."""
        raise NotImplementedError

    async def place_order(self, symbol, action, qty, order_type="Market"):
        """Place a market order. Returns order dict."""
        raise NotImplementedError

    async def get_positions(self):
        """Return list of open positions."""
        raise NotImplementedError

    async def close_position(self, position_id):
        """Close a specific position."""
        raise NotImplementedError

    async def subscribe_market_data(self, symbol, callback):
        """Subscribe to real-time market data via WebSocket."""
        raise NotImplementedError
