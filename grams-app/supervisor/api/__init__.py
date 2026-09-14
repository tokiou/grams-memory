"""HTTP ingress for normalized OpenCode events."""

from supervisor.api.events import register_event_routes

__all__ = ["register_event_routes"]
