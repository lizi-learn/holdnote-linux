"""Extension contract. Only Memos is enabled by the v0.1 desktop application."""
from typing import Protocol
from .client import Receipt


class TextDestination(Protocol):
    def send(self,text:str,capture_id:str)->Receipt:
        """Deliver with capture_id as the idempotency key, or raise DeliveryError.

        A Telegram Saved Messages implementation needs a USER MTProto session,
        not a bot token. A WeChat implementation needs an explicit authorized
        ClawBot/bridge session; do not substitute an undocumented public API.
        Destinations must protect credentials, verify TLS and avoid body logs.
        """
        ...
