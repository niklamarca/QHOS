"""VoiceClient com provedor simulado: o motivo da recusa e um codigo estavel, nao o nome da classe (QHOS-AUD-293)."""
import io
from urllib.error import HTTPError, URLError

import pytest

from brain_service import voice
from brain_service.voice import VoiceClient, VoiceUnavailable


def _levanta(exc):
    def abrir(request, timeout):
        raise exc
    return abrir


@pytest.mark.parametrize("exc,codigo,causa", [
    (URLError(ConnectionRefusedError(111, "recusado")), "voice_unreachable", "URLError"),
    (URLError(TimeoutError("lento")), "voice_timeout", "URLError"),
    (TimeoutError("lento"), "voice_timeout", "TimeoutError"),
    (HTTPError("http://127.0.0.1:8092/v1/synthesize", 503, "carregando", {}, io.BytesIO(b"")), "voice_http_503", "HTTPError"),
    (ValueError("url_refused"), "voice_request_invalid", "ValueError"),
    (OSError("disco"), "voice_io_error", "OSError"),
])
def test_voice_refusal_reason_is_a_stable_code(monkeypatch, exc, codigo, causa):
    monkeypatch.setattr(voice, "open_local", _levanta(exc))
    client = VoiceClient(url="http://127.0.0.1:8092", enabled=True, timeout_s=1.0)
    with pytest.raises(VoiceUnavailable) as info:
        client.synthesize("ola")
    assert str(info.value) == codigo
    assert info.value.cause == causa


def test_voice_disabled_and_wrong_content_type_keep_their_codes(monkeypatch):
    desligado = VoiceClient(url="http://127.0.0.1:8092", enabled=False, timeout_s=1.0)
    with pytest.raises(VoiceUnavailable, match="^voice_disabled$"):
        desligado.synthesize("ola")
    class Resposta:
        headers = {"Content-Type": "text/html"}
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, n): return b"<html>"
    monkeypatch.setattr(voice, "open_local", lambda request, timeout: Resposta())
    ligado = VoiceClient(url="http://127.0.0.1:8092", enabled=True, timeout_s=1.0)
    with pytest.raises(VoiceUnavailable, match="^voice_content_type$"):
        ligado.synthesize("ola")
