"""cmi_common — shared library for the Crypto Market Intelligence platform.

Provides the typed event schemas, Kafka producer/consumer, DB layer, Redis
cache, observability primitives and the FastAPI app factory used by every
microservice.
"""

from .app import create_app
from .config import Settings, get_settings

__version__ = "0.1.0"

__all__ = ["Settings", "create_app", "get_settings"]
