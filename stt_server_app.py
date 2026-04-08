from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

import streamlit as st


@dataclass
class TranscriptStore:
    """Single-user transcript queue for the PoC."""

    lock: threading.Lock
    latest: Optional[str] = None

    def set(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        with self.lock:
            self.latest = cleaned

    def pop(self) -> Optional[str]:
        with self.lock:
            text = self.latest
            self.latest = None
            return text

    def peek(self) -> Optional[str]:
        with self.lock:
            return self.latest


@dataclass
class ServerStatusStore:
    """Latest browser/runtime diagnostics for the STT page."""

    lock: threading.Lock
    status: str = "idle"
    error: Optional[str] = None
    guidance: Optional[str] = None
    browser_name: Optional[str] = None
    user_agent: Optional[str] = None
    is_online: Optional[bool] = None
    supports_speech_recognition: bool = False
    supports_process_locally: bool = False
    supports_available_api: bool = False
    supports_install_api: bool = False
    pack_status: Optional[str] = None
    microphone_permission: str = "unknown"
    last_transcript: Optional[str] = None

    def update(self, **kwargs: object) -> None:
        with self.lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            payload = asdict(self)
        payload.pop("lock", None)
        payload["transcript_pending"] = bool(_TRANSCRIPT_STORE.peek())
        return payload


_TRANSCRIPT_STORE = TranscriptStore(lock=threading.Lock())
_STATUS_STORE = ServerStatusStore(lock=threading.Lock())


class _TranscriptHandler(BaseHTTPRequestHandler):
    def _write_json(self, payload: dict[str, object], status_code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._write_json({}, status_code=204)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)

        if parsed.path == "/transcript":
            self._write_json({"text": _TRANSCRIPT_STORE.pop()})
            return

        if parsed.path == "/status":
            self._write_json(_STATUS_STORE.snapshot())
            return

        self._write_json({"error": "not found"}, status_code=404)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return


@st.cache_resource
def _start_transcript_api_server(host: str, port: int) -> ThreadingHTTPServer:
    """Start the local HTTP bridge once for transcript and status polling."""

    server = ThreadingHTTPServer((host, port), _TranscriptHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _render_voice_component():
    return st.components.v2.component(
        name="stt_server_voice_v3_diagnostics",
        html="""
        <div class="voice-container">
          <button id="voice-button" type="button" title="Start voice input">Mic</button>
          <span id="voice-status" class="status-text"></span>
        </div>
        """,
        css="""
        .voice-container {
          display: flex;
          align-items: center;
          gap: 10px;
          height: 48px;
        }
        #voice-button {
          width: 44px;
          height: 44px;
          border: 1px solid rgba(0, 0, 0, 0.2);
          border-radius: 10px;
          background: #f3f4f6;
          font-size: 14px;
          cursor: pointer;
        }
        #voice-button.listening {
          background: #ef4444;
          color: #ffffff;
        }
        #voice-button:disabled {
          cursor: not-allowed;
          opacity: 0.55;
        }
        .status-text {
          font-size: 13px;
          color: rgba(0, 0, 0, 0.6);
        }
        """,
        js="""
        export default function(component) {
          const { parentElement, data, setStateValue } = component;
          const button = parentElement.querySelector('#voice-button');
          const statusEl = parentElement.querySelector('#voice-status');
          if (!button || !statusEl) return;

          const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
          const lang = (data && data.lang) || 'ko-KR';
          const ua = navigator.userAgent || '';
          const globalKey = '__sttServerAppDiagnostics';
          const app = window[globalKey] = window[globalKey] || {};

          function detectBrowserName(userAgent) {
            if (/Edg\//.test(userAgent)) return 'Edge';
            if (/Chrome\//.test(userAgent)) return 'Chrome';
            if (/Firefox\//.test(userAgent)) return 'Firefox';
            if (/Safari\//.test(userAgent) && !/Chrome\//.test(userAgent)) return 'Safari';
            return 'Unknown';
          }

          function setGuidance(message) {
            setStateValue('guidance', message || null);
          }

          function render(isListening) {
            button.classList.toggle('listening', Boolean(isListening));
            statusEl.textContent = isListening ? 'Listening' : 'Idle';
          }

          function updateStaticState() {
            const browserName = detectBrowserName(ua);
            const supportsSpeechRecognition = Boolean(SpeechRecognition);
            let supportsProcessLocally = false;

            if (supportsSpeechRecognition) {
              try {
                const probe = new SpeechRecognition();
                supportsProcessLocally = 'processLocally' in probe;
              } catch (err) {
                supportsProcessLocally = false;
              }
            }

            setStateValue('browser_name', browserName);
            setStateValue('user_agent', ua);
            setStateValue('is_online', navigator.onLine);
            setStateValue('supports_speech_recognition', supportsSpeechRecognition);
            setStateValue('supports_process_locally', supportsProcessLocally);
            setStateValue('supports_available_api', Boolean(SpeechRecognition && typeof SpeechRecognition.available === 'function'));
            setStateValue('supports_install_api', Boolean(SpeechRecognition && typeof SpeechRecognition.install === 'function'));

            if (!supportsSpeechRecognition) {
              setGuidance('This browser does not support the Web Speech API.');
              statusEl.textContent = 'Unsupported browser';
              button.disabled = true;
              setStateValue('status', 'unsupported');
              setStateValue('error', 'SpeechRecognition not supported');
              return false;
            }

            if (browserName === 'Edge' && navigator.onLine === false) {
              setGuidance('Edge may fail to recognize speech in an offline environment. Open this page in Chrome if possible.');
            } else {
              setGuidance('This page does not receive audio from the search UI tab. It only uses the microphone of the browser that opened this STT page.');
            }

            button.disabled = false;
            return true;
          }

          async function probeLanguagePack() {
            if (!SpeechRecognition || typeof SpeechRecognition.available !== 'function') {
              setStateValue('pack_status', null);
              return;
            }
            try {
              const status = await SpeechRecognition.available({
                langs: [lang],
                processLocally: true,
              });
              setStateValue('pack_status', status || null);
            } catch (err) {
              setStateValue('pack_status', 'check-failed');
            }
          }

          async function ensureMicrophonePermission() {
            try {
              const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
              stream.getTracks().forEach((track) => track.stop());
              setStateValue('microphone_permission', 'granted');
              return true;
            } catch (err) {
              const name = err && err.name ? err.name : 'unknown';
              const denied = name === 'NotAllowedError' || name === 'PermissionDeniedError';
              setStateValue('microphone_permission', denied ? 'denied' : 'error');
              setStateValue('status', 'permission-error');
              setStateValue('error', err && err.message ? err.message : name);
              setGuidance('Allow microphone access in the browser and try again.');
              return false;
            }
          }

          function attachOnlineHandlers() {
            if (app.onlineHandlerAttached) return;
            app.onlineHandlerAttached = true;
            window.addEventListener('online', () => {
              setStateValue('is_online', true);
              updateStaticState();
            });
            window.addEventListener('offline', () => {
              setStateValue('is_online', false);
              updateStaticState();
            });
          }

          const isSupported = updateStaticState();
          attachOnlineHandlers();
          probeLanguagePack();

          if (!isSupported) {
            return;
          }

          if (!app.recognition) {
            app.recognition = new SpeechRecognition();
            app.recognition.continuous = false;
            app.recognition.interimResults = false;
            app.recognition.maxAlternatives = 1;
            try { app.recognition.processLocally = true; } catch (err) {}
          }

          const recognition = app.recognition;
          let isListening = app.isListening || false;
          let lastFinalText = app.lastFinalText || null;

          recognition.lang = lang;

          recognition.onstart = () => {
            isListening = true;
            app.isListening = true;
            setStateValue('status', 'listening');
            setStateValue('error', null);
            render(true);
          };

          recognition.onerror = (event) => {
            const msg = event && event.error ? event.error : 'unspecified-error';
            isListening = false;
            app.isListening = false;
            setStateValue('status', 'error');
            setStateValue('error', String(msg));

            if (String(msg) === 'network') {
              setGuidance('This browser appears to use a network-backed recognition service. It may fail in an offline environment.');
            }

            render(false);
          };

          recognition.onend = () => {
            isListening = false;
            app.isListening = false;
            setStateValue('status', 'idle');
            render(false);
          };

          recognition.onresult = (event) => {
            let finalText = '';
            for (let i = event.resultIndex; i < event.results.length; i += 1) {
              const res = event.results[i];
              if (res && res.isFinal && res[0] && res[0].transcript) {
                finalText += res[0].transcript;
              }
            }

            finalText = (finalText || '').trim();
            if (!finalText || finalText === lastFinalText) {
              return;
            }

            lastFinalText = finalText;
            app.lastFinalText = finalText;
            setStateValue('last_transcript', finalText);
            setStateValue('status', 'final');
          };

          button.onclick = async () => {
            if (isListening) {
              try { recognition.stop(); } catch (err) {}
              return;
            }

            const ready = await ensureMicrophonePermission();
            if (!ready) {
              render(false);
              return;
            }

            try {
              setStateValue('error', null);
              setStateValue('last_transcript', null);
              recognition.lang = lang;
              recognition.start();
            } catch (err) {
              setStateValue('status', 'start-failed');
              setStateValue('error', err && err.message ? err.message : String(err));
            }
          };

          render(isListening);
        }
        """,
    )


def main() -> None:
    st.set_page_config(page_title="STT Server", layout="wide")
    st.title("Speech Recognition Server (Browser PoC)")

    api_host = st.secrets.get("stt_api_host") if "stt_api_host" in st.secrets else None
    api_port = st.secrets.get("stt_api_port") if "stt_api_port" in st.secrets else None

    resolved_host = str(api_host) if api_host else "0.0.0.0"
    try:
        resolved_port = int(api_port) if api_port else 8502
    except ValueError:
        resolved_port = 8502

    _start_transcript_api_server(resolved_host, resolved_port)

    api_base_url = st.secrets.get("stt_api_base_url") if "stt_api_base_url" in st.secrets else None
    public_base = str(api_base_url) if api_base_url else f"http://localhost:{resolved_port}"

    st.caption(f"transcript API: {public_base}/transcript")
    st.caption(f"status API: {public_base}/status")
    st.info(
        "Important: this app does not receive microphone audio from the search UI tab. It only uses the microphone of the browser that opened this STT page."
    )
    st.write(
        "Polling /transcript from the search UI does not transfer audio to this app. "
        "This architecture only returns text that was recognized directly inside this browser session."
    )

    voice_component = _render_voice_component()
    result = voice_component(
        key="stt_server_voice_component",
        data={"lang": "ko-KR"},
        default={
            "status": "idle",
            "error": None,
            "guidance": None,
            "browser_name": None,
            "user_agent": None,
            "is_online": None,
            "supports_speech_recognition": False,
            "supports_process_locally": False,
            "supports_available_api": False,
            "supports_install_api": False,
            "pack_status": None,
            "microphone_permission": "unknown",
            "last_transcript": None,
        },
        on_status_change=lambda: None,
        on_error_change=lambda: None,
        on_guidance_change=lambda: None,
        on_browser_name_change=lambda: None,
        on_user_agent_change=lambda: None,
        on_is_online_change=lambda: None,
        on_supports_speech_recognition_change=lambda: None,
        on_supports_process_locally_change=lambda: None,
        on_supports_available_api_change=lambda: None,
        on_supports_install_api_change=lambda: None,
        on_pack_status_change=lambda: None,
        on_microphone_permission_change=lambda: None,
        on_last_transcript_change=lambda: None,
        width="content",
        height="content",
    )

    last_transcript_value = getattr(result, "last_transcript", None)
    if isinstance(last_transcript_value, str) and last_transcript_value.strip():
        _TRANSCRIPT_STORE.set(last_transcript_value.strip())

    _STATUS_STORE.update(
        status=str(getattr(result, "status", "idle") or "idle"),
        error=getattr(result, "error", None),
        guidance=getattr(result, "guidance", None),
        browser_name=getattr(result, "browser_name", None),
        user_agent=getattr(result, "user_agent", None),
        is_online=getattr(result, "is_online", None),
        supports_speech_recognition=bool(getattr(result, "supports_speech_recognition", False)),
        supports_process_locally=bool(getattr(result, "supports_process_locally", False)),
        supports_available_api=bool(getattr(result, "supports_available_api", False)),
        supports_install_api=bool(getattr(result, "supports_install_api", False)),
        pack_status=getattr(result, "pack_status", None),
        microphone_permission=str(getattr(result, "microphone_permission", "unknown") or "unknown"),
        last_transcript=last_transcript_value.strip()
        if isinstance(last_transcript_value, str) and last_transcript_value.strip()
        else _STATUS_STORE.last_transcript,
    )

    st.subheader("Runtime Status")
    status_cols = st.columns(3)
    status_cols[0].metric("Browser", getattr(result, "browser_name", None) or "Unknown")
    status_cols[1].metric("Online", "Yes" if getattr(result, "is_online", None) else "No")
    status_cols[2].metric("Microphone", getattr(result, "microphone_permission", "unknown"))

    st.caption(
        "SpeechRecognition support: "
        f"{bool(getattr(result, 'supports_speech_recognition', False))} | "
        "processLocally support: "
        f"{bool(getattr(result, 'supports_process_locally', False))} | "
        "language pack status: "
        f"{getattr(result, 'pack_status', None) or 'unknown'}"
    )

    guidance = getattr(result, "guidance", None)
    if isinstance(guidance, str) and guidance.strip():
        st.info(guidance.strip())

    error = getattr(result, "error", None)
    if isinstance(error, str) and error.strip():
        st.error(f"Latest recognition error: {error.strip()}")

    st.subheader("Latest Transcript")
    if isinstance(last_transcript_value, str) and last_transcript_value.strip():
        st.write(last_transcript_value.strip())
    else:
        st.write("No transcript yet")


if __name__ == "__main__":
    main()
