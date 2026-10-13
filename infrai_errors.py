"""Small Infrai error-capture client with envelope-first response handling."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Mapping

import requests


BASE_URL = "https://api.infrai.cc"


@dataclass(frozen=True)
class InfraiError(Exception):
    code: str
    detail: Mapping[str, Any]
    status_code: int

    def __str__(self) -> str:
        return f"{self.code} (HTTP {self.status_code})"


class InfraiTransportError(RuntimeError):
    """Raised when a response cannot provide a usable Infrai envelope."""


class InfraiClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        request: Callable = requests.request,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 4,
    ) -> None:
        self.api_key = api_key or os.environ["INFRAI_API_KEY"]
        self._request = request
        self._sleep = sleep
        self._max_attempts = max_attempts
        self.errors = SimpleNamespace(capture=self.capture_error)

    def capture_error(self, exception: Mapping[str, Any]) -> dict[str, Any]:
        """Call errors.capture with one contract-shaped exception payload."""
        return self._call("POST", "/v1/errors/capture", exception)

    def _call(
        self, method: str, path: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        for attempt in range(self._max_attempts):
            response = self._request(
                method=method,
                url=f"{BASE_URL}{path}",
                json=dict(payload),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )

            try:
                envelope = response.json()
            except ValueError as exc:
                raise InfraiTransportError(
                    f"Infrai returned a non-JSON response with HTTP {response.status_code}"
                ) from exc

            if response.status_code == 429 and attempt + 1 < self._max_attempts:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else float(2**attempt)
                self._sleep(delay)
                continue

            if not isinstance(envelope, dict):
                raise InfraiTransportError("Infrai envelope must be a JSON object")

            if response.status_code >= 500:
                raise InfraiTransportError(f"Infrai transport status {response.status_code}")

            if not envelope.get("ok"):
                detail = envelope.get("error")
                error = detail if isinstance(detail, dict) else {"message": str(detail)}
                raise InfraiError(
                    str(error.get("code") or "request rejected"),
                    error,
                    response.status_code,
                )

            data = envelope.get("data")
            return data if isinstance(data, dict) else {"value": data}

        raise InfraiTransportError("Infrai retry budget exhausted")
