"""DuckDB context helpers kept separate for callers that import this module."""

from .loader import BusinessContext, load_business

__all__ = ["BusinessContext", "load_business"]
