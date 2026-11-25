"""HTTP surface for the standalone Brain.

Read-only except human disposition and the explicit Quant relight request.
No FORCE, no Level 3, no trade, no apply.
"""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from typing import Any
from pathlib import Path
from urllib.parse import urlparse
from starlette.websockets import WebSocket, WebSocketDisconnected

from brain import api as brain_api

FORBIDDEN_ROUTE_NAMES = (
    "deploy",
    "apply",
    "trade",
    "enable_sandbox",
    "enable_level3",
    "merge",
    "push",
    "force",
)
MAX_WRITE_BODY_BYTES = 16 * 1024
# Log cursors and file offsets reach sqlite (signed 64-bit) and file seeks (off_t); beyond this they cannot be represented.
MAX_INDEX_VALUE = 2**63 - 1
ORIGIN_REFUSED = "cross_origin_refused"
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
}
_STATIC_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


def origin_allowed(origin: str, host: Any, configured_origin: str) -> bool:
    """One rule for HTTP writes and both WebSockets: same origin (http/https and netloc equal to Host) or the
    configured public origin, trailing slash tolerated. A malformed Origin is refused. An absent Origin is the caller's call."""
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    same_origin = parsed.scheme in {"http", "https"} and bool(host) and parsed.netloc == host
    proxy_origin = bool(configured_origin) and origin.rstrip("/") == configured_origin
    return same_origin or proxy_origin


def static_reason(exc: BaseException) -> str:
    """Static code for an error response: the code the component raised, a class mapping, else the class name.
    Never the exception text, which carries absolute paths and file names."""
    text = str(exc)
    if isinstance(exc, (ValueError, PermissionError)) and getattr(exc, "errno", None) is None and _STATIC_CODE.fullmatch(text):
        return text
    for kind, code in ((FileNotFoundError, "not_found"), (PermissionError, "access_denied"),
                       (NotADirectoryError, "not_a_directory"), (IsADirectoryError, "is_a_directory")):
        if isinstance(exc, kind):
            return code
    return type(exc).__name__


def representable_index(value: int, reason: str) -> int:
    """Refuse, with a static reason, an index that sqlite or a file seek cannot represent."""
    if value > MAX_INDEX_VALUE:
        raise ValueError(reason)
    return value


class BoundedWriteBodyMiddleware:
    """Buffer at most 16 KiB before routing, including requests without length."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") in {"GET", "HEAD", "OPTIONS"}:
            return await self.app(scope, receive, send)
        from starlette.responses import JSONResponse

        async def reject(reason, code):
            await JSONResponse({"ok": False, "reason": reason}, status_code=code)(scope, receive, send)

        lengths = [value for name, value in scope.get("headers", []) if name.lower() == b"content-length"]
        if lengths:
            if len(lengths) != 1 or not lengths[0].isdigit():
                return await reject("invalid_content_length", 400)
            if len(lengths[0]) > 10 or int(lengths[0]) > MAX_WRITE_BODY_BYTES:
                return await reject("body_too_large", 413)
        body = bytearray()
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            if event["type"] != "http.request":
                return await reject("invalid_request_body", 400)
            chunk = event.get("body", b"")
            if len(body) + len(chunk) > MAX_WRITE_BODY_BYTES:
                return await reject("body_too_large", 413)
            body.extend(chunk)
            if not event.get("more_body", False):
                break

        delivered = False

        async def limited_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, limited_receive, send)


def build_app(process: Any) -> Any:
    import asyncio
    from fastapi import FastAPI, Request, WebSocketDisconnect, HTTPException
    from fastapi.responses import JSONResponse, FileResponse, Response
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    @asynccontextmanager
    async def service_lifespan(_app):
        try:
            yield
        finally:
            stop = getattr(process, "stop", None)
            if callable(stop) and bool(getattr(process, "started", False)):
                await stop()

    # No API documentation surface at all: /docs, /redoc and /openapi.json fall into the 404 catch-all.
    app = FastAPI(title="QHOS Brain", docs_url=None, redoc_url=None, openapi_url=None, lifespan=service_lifespan)
    app.state.process = process
    configured_origin = str(getattr(getattr(process, "cfg", None), "public_origin", "") or "")
    public_host = urlparse(configured_origin).hostname if configured_origin else None
    # Loopback names only; a test client must present one of these (base_url=http://127.0.0.1), never a test-only name.
    allowed_hosts = ["127.0.0.1", "localhost"]
    if public_host:
        allowed_hosts.append(public_host)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    app.add_middleware(BoundedWriteBodyMiddleware)

    @app.middleware("http")
    async def local_writes_only(request: Request, call_next):
        response = None
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and not origin_allowed(origin, request.headers.get("host"), configured_origin):
                response = JSONResponse({"ok": False, "reason": ORIGIN_REFUSED}, status_code=403)
            elif request.headers.get("sec-fetch-site") == "cross-site":
                response = JSONResponse({"ok": False, "reason": "cross_site_refused"}, status_code=403)
            elif request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                response = JSONResponse({"ok": False, "reason": "json_required"}, status_code=415)
        if response is None:
            response = await call_next(request)
        # Refusals carry the same headers as every other response.
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    ui = Path(__file__).resolve().parent / "ui"

    def record_linux(action: str, result: Any, detail: str) -> None:
        action_log = getattr(process, "linux_actions", None)
        if action_log is None:
            return
        accepted = result.get("accepted") if isinstance(result, dict) else None
        outcome = "accepted" if accepted is True else "refused" if accepted is False else "observed"
        action_log.record(action, outcome=outcome, detail=detail)

    @app.get("/")
    def interface():
        return FileResponse(ui / "index.html")

    @app.get("/app.js")
    def javascript():
        return FileResponse(ui / "app.js", media_type="text/javascript")

    @app.get("/styles.css")
    def stylesheet():
        return FileResponse(ui / "styles.css", media_type="text/css")

    @app.get("/rithmic-observation.js")
    def rithmic_observation_javascript():
        return FileResponse(ui / "rithmic-observation.js", media_type="text/javascript")

    @app.get("/rithmic-observation.css")
    def rithmic_observation_stylesheet():
        return FileResponse(ui / "rithmic-observation.css", media_type="text/css")

    @app.get("/api/assistant/state")
    def assistant_state():
        return process.assistant.state()

    @app.post("/api/assistant/chat")
    def assistant_chat(body: dict):
        return process.assistant.chat(body.get("message", ""))

    @app.post("/api/assistant/remote-chat")
    def remote_chat(body: dict):
        return process.assistant.remote_chat(body.get("message", ""), body.get("user_id", ""))

    @app.get("/api/voice/status")
    def voice_status() -> dict:
        voice = getattr(process, "voice", None)
        return {"enabled": bool(getattr(voice, "enabled", False))}

    @app.post("/api/voice/synthesize")
    def voice_synthesize(body: dict):
        from brain_service.voice import VoiceUnavailable
        voice = getattr(process, "voice", None)
        if voice is None:
            return JSONResponse({"ok": False, "reason": "voice_unavailable"}, status_code=503)
        try:
            audio = voice.synthesize(body.get("text", ""))
        except VoiceUnavailable as exc:
            return JSONResponse({"ok": False, "reason": str(exc)}, status_code=503)
        return Response(content=audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @app.post("/api/tester/run")
    def run_tests(body: dict):
        result = process.tester.start_run()
        record_linux("quick_checks_requested", result, "Solicitação de diagnóstico operacional limitado.")
        return result

    @app.get("/api/regression")
    def regression_state():
        return process.regression.snapshot()

    @app.post("/api/regression/run")
    def run_regression(body: dict):
        result = process.regression.start_run(scope="operational", explicit=True)
        record_linux("operational_regression_requested", result, "Solicitação explícita da suíte operacional isolada.")
        return result

    @app.get("/api/cognition")
    def cognition_state():
        return process.investigation.snapshot()

    @app.get("/api/logs")
    def full_logs(after: int = 0, limit: int = 100):
        try:
            representable_index(after, "cursor_out_of_range")
        except ValueError as exc:
            raise HTTPException(400, static_reason(exc)) from exc
        return process.log_feed.page(after,limit)

    @app.websocket("/ws/logs")
    async def full_log_stream(websocket: WebSocket):
        origin=websocket.headers.get('origin')
        if origin and not origin_allowed(origin, websocket.headers.get('host'), configured_origin):
            await websocket.close(code=1008,reason=ORIGIN_REFUSED)
            return
        await websocket.accept()
        try:
            cursor=max(0,representable_index(int(websocket.query_params.get('after','0')),'cursor_out_of_range'))
            while True:
                page=await asyncio.to_thread(process.log_feed.page,cursor,30)
                await asyncio.wait_for(websocket.send_json(page),timeout=10)
                cursor=page['next_cursor']
                await asyncio.sleep(.05 if page['records'] else 1)
        except (WebSocketDisconnect,TimeoutError):
            pass
        except ValueError:
            await websocket.close(code=1008,reason='invalid cursor')

    @app.get("/api/files/roots")
    def read_roots():
        return process.files.snapshot()

    @app.get("/api/files/list")
    def list_files(root: str = 'trader', path: str = '', after: str = ''):
        try:return process.files.list(root,path,after)
        except (OSError,ValueError) as exc:raise HTTPException(400,static_reason(exc)) from exc

    @app.get("/api/files/read")
    def read_file(root: str, path: str, offset: int = 0):
        try:return process.files.read(root,path,representable_index(offset,'offset_out_of_range'))
        except (OSError,ValueError) as exc:raise HTTPException(400,static_reason(exc)) from exc

    @app.get("/health")
    def health() -> dict:
        snap = brain_api.health_only()
        snap["process"] = "qhos-brain-service"
        snap["service_running"] = bool(getattr(process, "started", True))
        snap["quant_reachable"] = bool(process.observer.last_quant_reachable)
        attestor = getattr(process, "attestor", None)
        if attestor is not None:
            att = attestor.snapshot()
            snap["runtime"] = {
                "loaded_code_state": att["loaded_code_state"],
                "running_revision": att["running"]["source_revision"],
                "config_revision": att["running"]["config_revision"],
                "pid": att["running"]["pid"],
            }
        return snap

    @app.get("/ready")
    def ready() -> JSONResponse:
        running = bool(getattr(process, "started", True))
        return JSONResponse(
            {
                "ready": running,
                "process": "qhos-brain-service",
                "trader": {
                    "reachable": bool(process.observer.last_quant_reachable),
                    "required_for_brain_readiness": False,
                },
                "level3_enabled": False,
            },
            status_code=200 if running else 503,
        )

    @app.get("/api/brain")
    def brain_snapshot() -> dict:
        return brain_api.public_snapshot()

    @app.get("/api/brain/view")
    def brain_view() -> dict:
        return brain_api.view_model()

    @app.get("/api/brain/authority")
    def brain_authority() -> dict:
        return process.identity()

    @app.get("/api/brain/runtime")
    def brain_runtime() -> dict:
        attestor = getattr(process, "attestor", None)
        if attestor is None:
            return {"available": False, "reason": "attestor_unavailable"}
        return attestor.snapshot()

    @app.get("/api/linux/actions")
    def linux_actions() -> dict:
        action_log = getattr(process, "linux_actions", None)
        if action_log is None:
            return {
                "schema": "qhos-linux-actions-v1",
                "service": "qhos2-brain.service",
                "read_only": True,
                "source": "unavailable",
                "actions": [],
                "runtime": {"available": False, "reason": "action_log_unavailable"},
            }
        payload = action_log.snapshot()
        attestor = getattr(process, "attestor", None)
        payload["runtime"] = attestor.snapshot() if attestor is not None else {"available": False, "reason": "attestor_unavailable"}
        return payload

    @app.get("/api/brain/audit")
    def brain_audit() -> dict:
        return brain_api.audit_only()

    @app.post("/api/brain/shadow/feedback")
    def shadow_feedback(body: dict) -> dict:
        """Human disposition. Never a ChangeRequest."""
        fingerprint = str(body.get("fingerprint") or "")
        action = str(body.get("action") or "")
        note = str(body.get("note") or "")
        return brain_api.apply_shadow_feedback(fingerprint, action, note=note)

    @app.get("/api/brain/relight")
    def relight_status() -> dict:
        return process.relight.status()

    @app.post("/api/brain/relight")
    def relight_quant(body: dict) -> dict:
        """Guarded Quant relight. Body must be {"confirm": "RELIGHT_QUANT"}."""
        result = process.relight.request(
            confirm=str((body or {}).get("confirm") or ""),
            reason=str((body or {}).get("reason") or "explicit_brain_request"),
        )
        record_linux("trader_relight_requested", result, "Pedido protegido; nenhuma ordem ou configuração de risco é alterada.")
        return result

    @app.websocket("/ws/brain")
    async def brain_websocket(websocket: WebSocket):
        origin = websocket.headers.get("origin")
        if origin and not origin_allowed(origin, websocket.headers.get("host"), configured_origin):
            await websocket.close(code=1008, reason=ORIGIN_REFUSED)
            return
        await websocket.accept()
        try:
            while True:
                await websocket.send_json({
                    "type": "brain_snapshot",
                    "schema": "qhos-brain-ws-v1",
                    "data": brain_api.view_model(),
                })
                try:
                    message = await asyncio.wait_for(websocket.receive_text(), timeout=4.0)
                    if message == "ping":
                        await websocket.send_json({"type": "pong"})
                except asyncio.TimeoutError:
                    pass
        except (WebSocketDisconnect, WebSocketDisconnected):
            # The peer left between a receive and a send (Starlette already turns a send OSError into
            # WebSocketDisconnect 1006). Any other error, RuntimeError or OSError from the view model, surfaces.
            return

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def refuse_everything_else(path: str) -> JSONResponse:
        return JSONResponse(
            {
                "ok": False,
                "reason": "NOT_AVAILABLE",
                "process": "qhos-brain-service",
                "path": path,
            },
            status_code=404,
        )

    return app


def route_names(app: Any) -> list[str]:
    names = []
    for route in getattr(app, "routes", []) or []:
        path = str(getattr(route, "path", "") or "")
        names.append(path)
    return names


def assert_no_privileged_routes(app: Any) -> None:
    blob = " ".join(route_names(app)).lower()
    for token in FORBIDDEN_ROUTE_NAMES:
        if token in blob:
            raise AssertionError(f"privileged route leaked onto brain_service: {token}")
