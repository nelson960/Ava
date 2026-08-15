"""Ava's production conversation and character-performance backend."""

from .app import create_app
from .version import AVA_BACKEND_VERSION

__all__ = ["AVA_BACKEND_VERSION", "create_app"]
