"""Compatibility facade for Module 4 execution."""

from .validator import run_sql, validate_candidate, validate_candidates, validate_with_retry

__all__ = ["run_sql", "validate_candidate", "validate_candidates", "validate_with_retry"]
