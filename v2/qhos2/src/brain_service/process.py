"""Own a BrainRuntime that is not bound to QuantHPCSystem."""
from __future__ import annotations

from pathlib import Path
import asyncio
import functools
import os
import threading
import time
from typing import Any, Optional

from brain.local_logger import get_logger
from brain.redact import sanitize_error, trace_tail
from brain.runtime import BrainRuntime, bind_brain, peek_brain, unbind_brain
from brain_service.config import ServiceConfig, load_config
from brain_service.observer import QuantObserver
from brain_service.relight import RelightController
from brain_service.tester import SoftwareTester
from brain_service.assistant import OperationalAssistant
from brain_service.regression import RegressionRunner
from brain_service.investigation import InvestigationEngine
from brain_service.attestation import RuntimeAttestor
from brain_service.linux_actions import LinuxActionLog
from brain_service.voice import VoiceClient
from brain_service.log_feed import RuntimeLogFeed
from brain_service.file_browser import FileBrowser

log = get_logger("Brain.Service")
MONITOR_INTERVAL_S = 30
# Cancelling a task that awaits to_thread() does not stop the thread; stop() waits
# this long for in-flight collect/tick/observe_logs work before closing shared
# resources such as the journal sqlite (QHOS-AUD-298).
SHUTDOWN_GRACE_S = 10.0


class StandaloneBrain:
    """Process-local owner. The owner is this object, never the trader."""

    def __init__(
        self,
        cfg: Optional[ServiceConfig] = None,
        *,
        lake: Optional[Path] = None,
        observer: Optional[QuantObserver] = None,
        relight: Optional[RelightController] = None,
    ) -> None:
        self.cfg = cfg or load_config()
        self.observer = observer or QuantObserver(self.cfg)
        self.relight = relight or RelightController(self.cfg)
        default_lake = Path(self.cfg.state_dir) / "brain"
        root = Path(lake) if lake is not None else Path(getattr(self.cfg, "brain_lake_dir", default_lake))
        self.runtime = BrainRuntime(root, observer=self._observe)
        self.started = False
        self.tester = SoftwareTester(self.cfg.target_root, self.cfg.state_dir, quant_url=self.cfg.quant_base,
                                     cadence_s=self.cfg.test_interval_s)
        self.regression = RegressionRunner(self.cfg.target_root, self.cfg.state_dir, cadence_s=self.cfg.test_interval_s)
        self.investigation = InvestigationEngine(self.cfg.target_root, self.cfg.state_dir)
        self.log_feed = RuntimeLogFeed(self.cfg.state_dir)
        self.files = FileBrowser(self.cfg.target_root, {
            name: path for name,path in {
                'computer_c':os.environ.get('BRAIN_COMPUTER_C',''),
                'trader_workspace':os.environ.get('BRAIN_TRADER_WORKSPACE',''),
                'runtime_logs':os.environ.get('BRAIN_QUANT_LOGS',''),
                'runtime_data':os.environ.get('BRAIN_QUANT_DATA_LAKE',''),
            }.items() if path})
        self._log_task = None
        self.next_smoke_at = None
        self.assistant = OperationalAssistant(self)
        self.attestor = RuntimeAttestor(self.cfg)
        self.linux_actions = LinuxActionLog(self.cfg.state_dir)
        self.voice = VoiceClient.from_config(self.cfg)
        self._monitor_task = None
        self._stream_task = None
        self._monitor_failures = 0
        self._monitor_failures_total = 0
        self._monitor_last_error: Optional[str] = None
        self._monitor_last_failure_at: Optional[float] = None
        self._monitor_last_success_at: Optional[float] = None
        self._inflight: set = set()
        self._log_investigation_failures = 0
        self._log_investigation_last_error: Optional[str] = None
        self._shutdown_drained: Optional[bool] = None

    async def _in_thread(self, fn, *args, **kwargs):
        """to_thread() whose completion stop() can wait for even after cancellation."""
        done = threading.Event()
        self._inflight.add(done)

        def run():
            try:
                return fn(*args, **kwargs)
            finally:
                done.set()
                self._inflight.discard(done)

        return await asyncio.to_thread(run)

    async def _drain_inflight(self, timeout_s: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        for event in list(self._inflight):
            if event.is_set():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not await asyncio.to_thread(event.wait, remaining):
                pending = sum(1 for item in list(self._inflight) if not item.is_set())
                log.warning("brain stop: %s background call(s) still in flight after %.1fs; shared resources close anyway",
                            pending, timeout_s)
                return False
        return True

    def shutdown_status(self) -> dict:
        return {"drained": self._shutdown_drained,
                "in_flight": sum(1 for item in list(self._inflight) if not item.is_set()),
                "grace_s": SHUTDOWN_GRACE_S}

    def monitor_status(self) -> dict:
        """How the 30 s monitor loop is doing; failures here used to live only in memory (QHOS-AUD-268)."""
        return {
            "interval_s": MONITOR_INTERVAL_S,
            "consecutive_failures": self._monitor_failures,
            "failures_total": self._monitor_failures_total,
            "last_error": self._monitor_last_error,
            "last_failure_at": self._monitor_last_failure_at,
            "last_success_at": self._monitor_last_success_at,
        }

    def configuration_violations(self) -> list:
        """Every environment value rejected or degraded at load: service config, Brain
        flags (brain.local_config) and the chat client (QHOS-AUD-273)."""
        from brain.local_config import config as brain_config
        rows = []
        for source, values in (("service", getattr(self.cfg, "configuration_violations", ())),
                               ("brain", getattr(brain_config, "configuration_violations", ())),
                               ("chat", getattr(getattr(self.assistant, "model", None), "configuration_violations", ()))):
            rows.extend({**dict(row), "source": source} for row in (values or ()))
        return rows

    def _record_configuration_violations(self) -> None:
        """Environment values rejected or degraded at load go to the action trail (QHOS-AUD-273)."""
        for row in self.configuration_violations():
            self._record_linux("configuration_violation", outcome="degraded",
                               detail=f"{row.get('source')}: {row.get('key')}: {row.get('reason')}; applied {row.get('applied')!r}")

    def _record_linux(self, action: str, *, outcome: str = "observed", detail: str = "") -> None:
        action_log = getattr(self, "linux_actions", None)
        if action_log is not None:
            action_log.record(action, outcome=outcome, detail=detail)

    def _observe(self) -> dict:
        snap = dict(self.observer.last)
        if not snap:
            snap = self.observer.collect()
        snap["relight"] = self.relight.status()
        collection = self.log_feed.status()
        telemetry = dict(snap.get("telemetry") or {})
        errors = list(telemetry.get("errors") or [])
        for issue in (collection.get("error"), collection.get("application_file_error")):
            if issue:
                errors.append("log_collection: " + str(issue))
        if not collection.get("connected"):
            errors.append("log_collection: journal_disconnected")
        telemetry.update(errors=errors, log_collection=collection)
        snap["telemetry"] = telemetry
        snap.update(self._attestation_signal())
        return snap

    def _attestation_signal(self) -> dict:
        """Hand this process's own attestation to Discovery (QHOS-AUD-280).

        Nothing is fabricated: the verdict is RuntimeAttestor.snapshot(); the keys
        Discovery reads (running_revision, worktree_revision, drift_reason) are
        derived from it. expected_* stay out of the context on purpose: the
        attestor already folds them into ``drift`` with prefix semantics, while
        Discovery would compare them literally and read the config hash as the
        discovery-policy hash.
        """
        context = {"deployment_context": self.cfg.deployment_context, "required": True}
        attestor = getattr(self, "attestor", None)
        if attestor is None:
            return {"attestation": None, "attestation_context": context}
        att = dict(attestor.snapshot())
        att.setdefault("running_revision", str((att.get("running") or {}).get("source_revision") or ""))
        att.setdefault("worktree_revision", str((att.get("worktree") or {}).get("source_revision") or ""))
        att.setdefault("drift_reason", ", ".join(att.get("drift") or []) or "running_matches_worktree")
        return {"attestation": att, "attestation_context": context}

    async def _monitor_once(self) -> None:
        """One monitor iteration. A failure is counted, logged with its sanitized cause, and never hidden."""
        diagnostics = bool(getattr(self.cfg, "diagnostics_enabled", True))
        try:
            await self._in_thread(self.observer.collect)
            if bool(getattr(self.cfg, "relight_enabled", False)):
                await self._in_thread(self.relight.consider, quant_reachable=self.observer.last_quant_reachable)
            if diagnostics:
                await self._in_thread(self.tester.tick)
                await self._in_thread(self.regression.tick)
                await self._in_thread(self.investigation.observe_regression, self.regression.snapshot())
        except Exception as exc:
            self._monitor_failures += 1
            self._monitor_failures_total += 1
            detail = f"{type(exc).__name__}: {sanitize_error(exc)}"
            self._monitor_last_error = detail
            self._monitor_last_failure_at = time.time()
            log.warning("brain monitor iteration failed (%s consecutive, %s total): %s | %s",
                        self._monitor_failures, self._monitor_failures_total, detail, trace_tail(exc))
            self.assistant.record("monitor_error", detail)
            self._record_linux("monitor_error", outcome="error", detail=detail)
        else:
            self._monitor_failures = 0
            self._monitor_last_success_at = time.time()
        finally:
            if diagnostics:
                # Real schedule, even when the iteration died before tester.tick:
                # a past "next" is the truth (overdue), a frozen one is not.
                self.next_smoke_at = self.tester.snapshot().get("next_due")

    async def _monitor(self):
        while self.started:
            await self._monitor_once()
            await asyncio.sleep(MONITOR_INTERVAL_S)

    async def start(self) -> None:
        try:
            self.log_feed.start()
            await asyncio.to_thread(self.observer.collect)
            await self.runtime.start()
            bind_brain(self.runtime, owner=self)
            self.runtime.interval = max(15, int(self.cfg.cycle_interval_s))
            self.started = True
            self.assistant.record("startup", "Monitor do Brain inicializado; a saúde do Trader depende da telemetria confirmada.")
            self._record_linux("service_started", outcome="active", detail="Monitor independente do Brain inicializado no Linux.")
            self._record_configuration_violations()
            self._monitor_task = asyncio.create_task(self._monitor())
            self._log_task = asyncio.create_task(self._investigate_logs())
            stream = getattr(self.observer, "stream_forever", None)
            if callable(stream) and bool(getattr(self.cfg, "quant_ws_enabled", False)):
                self._stream_task = asyncio.create_task(stream())
        except Exception:
            self.started = False
            tasks = [task for task in (self._monitor_task, self._stream_task, self._log_task) if task is not None]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            stop = getattr(self.runtime, "stop", None)
            try:
                if callable(stop):
                    await stop()
            finally:
                unbind_brain(self.runtime, owner=self)
                await asyncio.to_thread(self.log_feed.close)
            raise

    async def stop(self) -> None:
        self._record_linux("service_stopping", outcome="requested", detail="Encerramento ordenado do processo Brain.")
        self.started = False
        tasks = [task for task in (self._monitor_task, self._stream_task, self._log_task) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._monitor_task = None
        self._stream_task = None
        self._log_task = None
        self._shutdown_drained = await self._drain_inflight(SHUTDOWN_GRACE_S)
        await asyncio.to_thread(self.log_feed.close)
        stop_tester = getattr(self.tester, "stop", None)
        if callable(stop_tester):
            await asyncio.to_thread(stop_tester)
        await asyncio.to_thread(self.regression.stop)
        try:
            await self.runtime.stop()
        finally:
            unbind_brain(self.runtime, owner=self)
            self.started = False

    async def _investigate_logs(self):
        while self.started:
            try:
                incidents=await self._in_thread(self.investigation.observe_logs,self.log_feed)
                if not self.started:
                    break  # never open incidents on a runtime that is stopping
                for incident in incidents:
                    self.runtime.open_incident(component=incident['component'], title=incident['title'],
                        severity='critical' if incident['severity']=='critical' else 'warning',
                        evidence_detail=f"runtime log cursor={incident['last_cursor']}; investigation={incident['id']}")
                    self.assistant.record('incident_detected',incident['title'])
            except Exception as exc:
                detail = f"{type(exc).__name__}: {sanitize_error(exc)}"
                self._log_investigation_failures += 1
                if detail != self._log_investigation_last_error:
                    # One WARNING per distinct cause: a persistent failure is retried every
                    # second (QHOS-AUD-269 keeps the row) and must not flood the journal.
                    log.warning("log investigation failed (%s so far): %s | %s",
                                self._log_investigation_failures, detail, trace_tail(exc))
                self._log_investigation_last_error = detail
                # The persisted trail (linux_actions, served by the API) keeps the static class name;
                # the sanitized cause goes only to the journal WARNING above (QHOS-AUD-313, 290).
                self._record_linux('log_investigation_error',outcome='error',detail=type(exc).__name__)
            else:
                self._log_investigation_last_error = None
            await asyncio.sleep(1)

    def snapshot(self) -> dict:
        runtime = peek_brain()
        if runtime is None:
            return {"schema": "qhos-brain-v1", "available": False, "running": False}
        return runtime.snapshot()

    def identity(self) -> dict[str, Any]:
        from brain.architecture import identity
        from brain_service.authority import authority_card

        stream_status = getattr(self.observer, "stream_status", None)
        return {
            **identity(),
            "process": "standalone",
            "quant_observe_url": self.cfg.quant_base,
            "quant_reachable": self.observer.last_quant_reachable,
            "trader_event_stream": stream_status() if callable(stream_status) else {"enabled": False},
            "relight": self.relight.status(),
            "authority": authority_card(self.cfg),
            "runtime_attestation": self.attestor.snapshot(),
            "configuration_violations": self.configuration_violations(),
        }
