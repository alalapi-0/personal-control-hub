"""Small transport-independent types for the local Hub application service."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ServiceError(Exception):
    """A public, fixed-code error; underlying exception text is never delivered."""

    def __init__(self, code: str, *, status: int = 400, retryable: bool = False,
                 outcome: str = "NOT_COMMITTED", details: dict[str, Any] | None = None):
        super().__init__(code)
        self.code, self.status, self.retryable = code, status, retryable
        self.outcome, self.details = outcome, details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "retryable": self.retryable,
                "outcome": self.outcome, "details": self.details}


@dataclass(frozen=True)
class OwnerAction:
    """Internal capability supplied only after the HTTP interaction is checked.

    This object is never parsed from JSON. Local processes running as the owner
    are inside the trust boundary; a browser page must pass Host, Origin,
    session-cookie and session-bound anti-CSRF checks before it is constructed.
    """
    fixture: bool = False

    def source(self, request_id: str) -> dict[str, Any]:
        return {"type": "synthetic_fixture" if self.fixture else "trusted_owner_reference",
                "reference": ("fixture://local-http/" if self.fixture else
                              "local-http://owner-action/") + request_id,
                "trusted_owner": not self.fixture, "fixture": self.fixture}


@dataclass(frozen=True)
class ArtifactResponse:
    data: bytes
    content_type: str
    filename: str
    disposition: str = "attachment"
    sha256: str | None = None
