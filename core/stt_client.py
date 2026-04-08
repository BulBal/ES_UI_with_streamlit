from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

import requests


@dataclass(frozen=True)
class SttClientOptions:
    server_url: str
    transcript_path: str = "/transcript"
    status_path: str = "/status"
    lang: str = "ko-KR"
    timeout_seconds: int = 10
    poll_interval_ms: int = 100
    request_timeout_seconds: float = 2.0


def _normalize_base_url(server_url: str) -> str:
    trimmed = server_url.strip()
    if not trimmed:
        raise ValueError("STT server URL is empty.")
    return trimmed.rstrip("/")


def _build_endpoint_url(server_url: str, endpoint_path: str) -> str:
    base = _normalize_base_url(server_url)

    path = endpoint_path.strip()
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def poll_transcript(options: SttClientOptions) -> Optional[str]:
    """Poll the transcript bridge until a final transcript is available."""

    endpoint_url = _build_endpoint_url(options.server_url, options.transcript_path)
    deadline = time.monotonic() + max(0, options.timeout_seconds)

    while time.monotonic() <= deadline:
        try:
            response = requests.get(endpoint_url, timeout=options.request_timeout_seconds)
            if response.status_code == 200:
                payload = response.json()
                text_value = payload.get("text")
                if isinstance(text_value, str):
                    cleaned = text_value.strip()
                    if cleaned:
                        return cleaned
        except requests.RequestException:
            pass

        time.sleep(max(0, options.poll_interval_ms) / 1000.0)

    return None


def get_server_status(
    server_url: str,
    status_path: str = "/status",
    request_timeout_seconds: float = 2.0,
) -> Optional[dict[str, Any]]:
    """Fetch diagnostic status from the browser-based STT bridge."""

    endpoint_url = _build_endpoint_url(server_url, status_path)
    try:
        response = requests.get(endpoint_url, timeout=request_timeout_seconds)
        if response.status_code != 200:
            return None
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except requests.RequestException:
        return None


def transcribe(
    server_url: str,
    lang: str = "ko-KR",
    timeout_seconds: int = 10,
    poll_interval_ms: int = 100,
    transcript_path: str = "/transcript",
) -> Optional[str]:
    """Return the latest transcript produced by the STT server browser."""

    options = SttClientOptions(
        server_url=server_url,
        transcript_path=transcript_path,
        lang=lang,
        timeout_seconds=timeout_seconds,
        poll_interval_ms=poll_interval_ms,
    )
    return poll_transcript(options)
