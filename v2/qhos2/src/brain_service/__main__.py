"""python -m brain_service

Starts the Brain in its own process. Quant is observed, never owned.
If Quant is down, this process stays up.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Allow `python -m brain_service` from the repo root without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_ROOT_HANDLER_MARK = "qhos_brain_root_handler"


def configure_logging(stream=None, root=None):
    """Give the Brain loggers a destination (stderr, i.e. the journal under systemd).

    uvicorn configures only its own loggers; without this the ``Brain*`` loggers
    had no handler and INFO/WARNING cycle failures were dropped (QHOS-AUD-259).
    Idempotent; does nothing when someone else already configured the root.
    """
    root = root if root is not None else logging.getLogger()
    for existing in root.handlers:
        if getattr(existing, _ROOT_HANDLER_MARK, False):
            return existing
    if root.handlers:
        return None
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    setattr(handler, _ROOT_HANDLER_MARK, True)
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    return handler


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    from brain_service.config import load_config, loopback_host

    cfg = load_config()
    ap = argparse.ArgumentParser(description="Standalone QHOS Brain process")
    ap.add_argument("--host", type=loopback_host, default=cfg.host)
    ap.add_argument("--port", type=int, default=cfg.port)
    ap.add_argument("--quant-url", default=cfg.quant_url)
    ap.add_argument("--cycle-s", type=int, default=cfg.cycle_interval_s)
    return ap.parse_args(argv)


async def _serve(args: argparse.Namespace) -> None:
    import os
    from brain_service.config import load_config, loopback_host

    host = loopback_host(args.host)  # Also protect direct callers, before locks/startup.
    configure_logging()

    os.environ["BRAIN_QUANT_OBSERVE_URL"] = str(args.quant_url)
    os.environ["BRAIN_SERVICE_PORT"] = str(args.port)
    os.environ["BRAIN_SERVICE_CYCLE_S"] = str(args.cycle_s)

    from brain_service.process import StandaloneBrain
    from brain_service.runtime_fs import prepare_runtime_dirs
    from brain_service.server import assert_no_privileged_routes, build_app
    from brain_service.supervisor import SingleInstanceLock

    cfg = load_config()
    prepare_runtime_dirs((cfg.state_dir, cfg.brain_lake_dir, cfg.runtime_dir))
    instance = SingleInstanceLock(cfg.runtime_dir / "brain_service.lock")
    if not instance.acquire():
        if instance.failure_kind == "error":
            raise RuntimeError(f"Brain service lock is unusable: {instance.last_error}")
        raise RuntimeError("Brain service is already running (lock held by another process)")
    brain = StandaloneBrain(cfg)
    await brain.start()
    app = build_app(brain)
    assert_no_privileged_routes(app)

    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=int(args.port), log_level="info")
    )
    try:
        await server.serve()
    finally:
        await brain.stop()
        instance.release()


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    try:
        asyncio.run(_serve(args))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
