"""HTTP ingress for normalized OpenCode events."""

from .events import register_event_routes

__all__ = ["register_event_routes"]
