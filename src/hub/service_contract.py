"""Small transport-independent error type for Hub application services."""
from __future__ import annotations

from typing import Any


class ServiceError(Exception):
    """A fixed-code public error that never exposes its underlying exception."""

    def __init__(self, code: str, *, status: int = 400, retryable: bool = False,
                 outcome: str = "NOT_COMMITTED",
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.retryable = retryable
        self.outcome = outcome
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "retryable": self.retryable,
                "outcome": self.outcome, "details": self.details}
