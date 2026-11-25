"""Small loopback client for the isolated local voice process."""
from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request
from brain_service.local_http import open_local

from brain_service.config import loopback_url


MAX_AUDIO_BYTES = 20_000_000


class VoiceUnavailable(RuntimeError):
    """The optional local voice process cannot produce audio right now.

    ``str(exc)`` is a stable reason code the API returns; ``cause`` names the
    underlying exception class for the caller that wants it (the API does not
    return it and nothing logs it today) (QHOS-AUD-293).
    """

    def __init__(self, code: str, *, cause: BaseException | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.cause = type(cause).__name__ if cause is not None else None


@dataclass(frozen=True)
class VoiceClient:
    url: str
    enabled: bool
    timeout_s: float

    @classmethod
    def from_config(cls, cfg) -> "VoiceClient":
        return cls(
            url=loopback_url(getattr(cfg, "voice_url", "http://127.0.0.1:8092")),
            enabled=bool(getattr(cfg, "voice_enabled", False)),
            timeout_s=float(getattr(cfg, "voice_timeout_s", 45.0)),
        )

    def synthesize(self, text: str) -> bytes:
        if not self.enabled:
            raise VoiceUnavailable("voice_disabled")
        raw = json.dumps({"text": str(text or "")}, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.url.rstrip("/") + "/v1/synthesize",
            data=raw,
            headers={"Content-Type": "application/json", "Accept": "audio/wav"},
            method="POST",
        )
        try:
            with open_local(request, timeout=self.timeout_s) as response:
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if not content_type.startswith("audio/wav"):
                    raise VoiceUnavailable("voice_content_type")
                audio = response.read(MAX_AUDIO_BYTES + 1)
        except HTTPError as exc:
            raise VoiceUnavailable(f"voice_http_{exc.code}", cause=exc) from exc
        except URLError as exc:
            timed_out = isinstance(getattr(exc, "reason", None), TimeoutError)
            raise VoiceUnavailable("voice_timeout" if timed_out else "voice_unreachable", cause=exc) from exc
        except TimeoutError as exc:
            raise VoiceUnavailable("voice_timeout", cause=exc) from exc
        except ValueError as exc:
            raise VoiceUnavailable("voice_request_invalid", cause=exc) from exc
        except OSError as exc:
            raise VoiceUnavailable("voice_io_error", cause=exc) from exc
        if not audio or len(audio) > MAX_AUDIO_BYTES:
            raise VoiceUnavailable("voice_response_invalid")
        return audio
