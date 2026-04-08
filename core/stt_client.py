
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import requests


@dataclass(frozen=True)
class SttClientOptions:
 server_url: str
 transcript_path: str = "/transcript"
 lang: str = "ko-KR"
 timeout_seconds: int = 10
 poll_interval_ms: int = 100
 request_timeout_seconds: float = 2.0


def _normalize_base_url(server_url: str) -> str:
 trimmed = server_url.strip()
 if not trimmed:
    raise ValueError("STT 서버 URL이 비어 있습니다.")
 return trimmed.rstrip("/")


def _build_transcript_url(server_url: str, transcript_path: str) -> str:
 base = _normalize_base_url(server_url)

 path = transcript_path.strip()
 if not path.startswith("/"):
    path = f"/{path}"
 return f"{base}{path}"


def poll_transcript(options: SttClientOptions) -> Optional[str]:
 # STT 서버에서 인식된 텍스트가 준비될 때까지 폴링한다.
 endpoint_url = _build_transcript_url(options.server_url, options.transcript_path)
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
        # PoC 단계에서는 네트워크 단절 등을 조용히 재시도한다.
        pass

    time.sleep(max(0, options.poll_interval_ms) / 1000.0)

 return None


def transcribe(
    server_url: str,
    lang: str = "ko-KR",
    timeout_seconds: int = 10,
    poll_interval_ms: int = 100,
    transcript_path: str = "/transcript",
) -> Optional[str]:
 #STT 서버에서 인식된 텍스트를 가져온다.

#  계획 문서의 transcribe() 시그니처를 최대한 유지하기 위해 lang 파라미터를 보존한다.
#  실제 인식은 STT 서버 웹앱(브라우저)에서 수행되며, 본 함수는 결과 수신 역할만 한다.
    options = SttClientOptions(
        server_url=server_url,
        transcript_path=transcript_path,
        lang=lang,
        timeout_seconds=timeout_seconds,
        poll_interval_ms=poll_interval_ms,
    )
    return poll_transcript(options)
