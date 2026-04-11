"""
Tradovate REST/WebSocket API wrapper stub.

⚠️  NOT IMPLEMENTED — DO NOT DEPLOY  ⚠️

This file is a REFERENCE STUB. No methods are implemented. Real Tradovate
integration requires OAuth 2.0 authentication (name / password / appId /
appVersion / cid / sec), WebSocket market data subscriptions, and REST
order-placement endpoints. See https://api.tradovate.com/ for the actual
protocol.

The __init__ fails loudly instead of succeeding-then-crashing-at-first-method
so that a developer cannot accidentally deploy a bot that passes construction
and then raises NotImplementedError from authenticate() after a production
launch.

WHY: Old class let __init__ succeed silently and only raised NotImplementedError
     when individual methods were called. A developer wiring this up would hit
     the error in authenticate() — potentially after a live production deploy.
     Raising at construction guarantees no stub instance ever exists in a
     running process.
CHANGED: April 2026 — Phase 29 Fix 2 — fail loudly at __init__ (audit Part C
         crit #9 + HIGH #52 / #53)
"""


class TradovateAPI:
    def __init__(self, api_key=None, api_secret=None, demo=True, **kwargs):
        # WHY: Fail at construction. Do not let a stub ever be instantiated.
        #      Old code set self.api_key / self.api_secret and succeeded.
        #      Note: real Tradovate OAuth uses name/password/appId/appVersion/
        #      cid/sec — the old api_key/api_secret signature was also wrong
        #      (audit HIGH #52). Keeping the old arg names here as **kwargs
        #      so callers don't get confusing TypeErrors, but the whole
        #      class still refuses to construct.
        raise NotImplementedError(
            "TradovateAPI is a stub. No methods are implemented. "
            "Real Tradovate integration requires OAuth 2.0 and live REST/WebSocket "
            "endpoints — see https://api.tradovate.com/. DO NOT DEPLOY this file."
        )

    async def authenticate(self):
        """Authenticate and get access token."""
        raise NotImplementedError("TradovateAPI is a stub. See class docstring.")

    async def get_account(self):
        """Return account balance and equity."""
        raise NotImplementedError("TradovateAPI is a stub. See class docstring.")

    async def place_order(self, symbol, action, qty, order_type="Market"):
        """Place a market order. Returns order dict."""
        raise NotImplementedError("TradovateAPI is a stub. See class docstring.")

    async def get_positions(self):
        """Return list of open positions."""
        raise NotImplementedError("TradovateAPI is a stub. See class docstring.")

    async def close_position(self, position_id):
        """Close a specific position."""
        raise NotImplementedError("TradovateAPI is a stub. See class docstring.")

    async def subscribe_market_data(self, symbol, callback):
        """Subscribe to real-time market data via WebSocket."""
        raise NotImplementedError("TradovateAPI is a stub. See class docstring.")
