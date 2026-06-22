"""Tollbooth Authority — Certified Purchase Order Service."""

from tollbooth.version import resolve_service_version

__version__ = resolve_service_version("tollbooth-authority", __file__)

__all__ = ["__version__"]
