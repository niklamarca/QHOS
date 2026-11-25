"""Evidence packets for conversation; never execute model text or grant authority."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import re
from pathlib import Path
from collections import deque

from brain.local_logger import get_logger
from brain_service.knowledge import KnowledgeIndex
from brain_service.incident_memory import IncidentMemory

log = get_logger("Brain.Investigation")
# Cursor-only progress (info lines) is persisted at most once per window; incident
# changes are persisted immediately. Keeps the Brain from rewriting its own state
# file for every batch of its own access-log lines (QHOS-AUD-304).
CURSOR_PERSIST_WINDOW_S = 60.0


def _reject_non_finite(token):
    raise ValueError('incidents_non_finite_' + str(token))


class InvestigationEngine:
    def __init__(self, target_root, state_dir):
        self.knowledge = KnowledgeIndex(target_root, state_dir)
        self.memory = IncidentMemory(target_root, state_dir)
        # Guards automatic/log_cursor/traces only. Source retrieval and memory validation have their own locks and
        # never run under this one: snapshot() must answer while a search rebuilds the index (QHOS-AUD-284).
        self._lock = threading.Lock()
        self.traces = deque(maxlen=20)
        self._last_regression = None
        self.incident_path = Path(state_dir)/'runtime_investigations.json'
        self.state_load_error = None
        self.cursor_origin = 'fresh'
        try:
            with self.incident_path.open('rb') as arquivo:
                raw = arquivo.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError('incidents_too_large')
            saved=json.loads(raw, parse_constant=_reject_non_finite)  # NaN/Infinity invalidam o estado (QHOS-AUD-315)
            if not isinstance(saved, dict) or not isinstance(saved.get('incidents', {}), dict):
                raise ValueError('incidents_invalid')
            cursor = saved.get('cursor', 0)
            if type(cursor) is not int or cursor < 0:
                raise ValueError('incidents_cursor_invalid')
            self.cursor_origin = 'persisted'
        except FileNotFoundError:
            saved={}
        except (OSError, ValueError, TypeError) as exc:
            saved={}
            # The cursor restarts at 0 and the backlog is re-read; that is visible now
            # (QHOS-AUD-306). Starting from the feed's last id instead is a policy call.
            self.state_load_error = f'{type(exc).__name__}: {str(exc)[:120]}'
            self.cursor_origin = 'reset_after_load_error'
            log.warning('runtime_investigations state unreadable (%s); log cursor reset to 0, backlog will be re-read',
                        self.state_load_error)
        self.log_cursor=saved.get('cursor',0)
        self.automatic={k:v for k,v in list(saved.get('incidents',{}).items())[-100:] if isinstance(v, dict)}
        self._last_persist_at = time.time()

    @staticmethod
    def _count(existing, row):
        existing['occurrences']+=1;existing['last_cursor']=row['cursor'];existing['last_seen']=row['timestamp']

    def observe_logs(self, feed):
        """Investigate observed failures without spending model credits or executing code.

        The cursor only moves past a row after that row was handled (QHOS-AUD-269): a
        failure in the middle of a batch persists the rows already done and re-reads the
        failing one next time. At-least-once, not exactly-once.
        """
        from brain_service.supervisor import atomic_json_write
        new=[]
        packet=feed.page(self.log_cursor,100)
        processed=0
        changed=False
        try:
            for row in packet['records']:
                changed=self._investigate_row(row,new) or changed
                with self._lock:
                    self.log_cursor=row['cursor']
                processed+=1
        finally:
            if processed:
                with self._lock:
                    if changed or time.time()-self._last_persist_at >= CURSOR_PERSIST_WINDOW_S:
                        atomic_json_write(self.incident_path,{'cursor':self.log_cursor,'incidents':self.automatic})
                        self._last_persist_at=time.time()
        return new

    def _investigate_row(self, row, new):
        """Handle one feed row; True when an incident was created or updated.

        Classification and counting run under the engine lock; source retrieval and the incident
        card are built outside it, and `existing` is re-checked under the lock before inserting
        (QHOS-AUD-284)."""
        with self._lock:
            text=row['message']
            if row['level'] not in {'warning','error','critical'}:return False
            if 'Estratégia rejeitada' in text or 'Rejeitada:' in text:return False
            if 'WebSocketDisconnect' in text and 'Traceback' not in text:return False
            job=re.search(r'Job\(([^,]+)',text)
            module=re.search(r'\|\s*(?:WARNING|ERROR|CRITICAL)\s*\|\s*([^|]+)\|',text)
            component=job.group(1) if job else module.group(1).strip() if module else row['unit']
            category='timeout' if re.search(r'timeout|timed out',text,re.I) else 'failure'
            signature=re.sub(r'\b[0-9a-f]{8,}\b|\d+(?:\.\d+)?','<id>',text)
            signature=re.sub(r'^.*?\|\s*(WARNING|ERROR|CRITICAL)\s*\|','',signature)
            fingerprint=hashlib.sha256((component+category+signature).encode()).hexdigest()[:20]
            existing=self.automatic.get(fingerprint)
            if existing:
                self._count(existing,row)
                return True
            query='orchestrator timeout wait_for shield run_generation' if category=='timeout' else component+' '+text[-500:]
        # Retrieval rebuilds the index within its budgets; it runs outside the engine lock.
        sources=self.knowledge.search(query,max_results=3)
        incident={'id':fingerprint,'title':category+': '+component,'component':component,
            'status':'investigating','severity':row['level'],'occurrences':1,
            'first_seen':row['timestamp'],'last_seen':row['timestamp'],'last_cursor':row['cursor'],
            'evidence':{'log':row,'sources':sources},
            'hypotheses':([{'claim':'O aguardador expirou; uma tarefa protegida pode continuar.',
                'status':'suspected','test':'Comparar tarefa ativa, etapa, último progresso e checkpoint com o job expirado.'}]
                if category=='timeout' else [{'claim':'Falha operacional registrada pelo componente.',
                'status':'observed_symptom','test':'Correlacionar o traceback e eventos próximos pelo cursor antes de atribuir causa.'}]),
            'completed_stages':['detect','persist_evidence','retrieve_source','form_testable_hypothesis'],
            'can_apply':False,'resolved':False}
        with self._lock:
            existing=self.automatic.get(fingerprint)
            if existing:
                self._count(existing,row)
                return True
            self.automatic[fingerprint]=incident
            while len(self.automatic) > 100:
                self.automatic.pop(next(iter(self.automatic)))
            new.append(incident)
            self.traces.appendleft({'id':fingerprint,'created_at':time.time(),'stage':'automatic_investigation','log_cursor':row['cursor'],'source_count':len(sources)})
        return True

    def observe_regression(self, result):
        """Remember verified diagnostic outcomes, never label a passing suite a repair."""
        if result.get("verified") is not True or not result.get("run_id") or result.get("run_id") == self._last_regression:
            return
        if result.get("target_root") != str(self.knowledge.boundary.target_root):
            return
        from brain_service.regression import REVIEWED_TESTS
        evidence = [{"path": path, "sha256": digest, "version": "sha256:" + digest[:16],
                     "line_start": 1, "line_end": 1, "line_ref": path + ":1"}
                    for path, digest in list(REVIEWED_TESTS.items())[:3]]
        # No generated narrative and no claim that the source was fixed.
        record = {"id": "regression:" + result["run_id"], "verified": True,
                  "status": "verified", "outcome": "verified", "timestamp": result.get("completed_at"),
                  "before": {"state": "not_assessed_by_this_run"},
                  "after": {key: result.get(key) for key in ("phase", "passed", "failed", "collection_errors", "source_hash")},
                  "evidence": evidence}
        saved = self.memory.record(record)
        if saved.get("accepted"):
            self._last_regression = result["run_id"]

    def prepare(self, question, state):
        """Gather bounded source excerpts and current telemetry before asking the model."""
        question = str(question)[:2500]
        hits = self.knowledge.search(question, max_results=5)
        self.memory.validate_sources()
        memories = self.memory.recent(3)
        trader = state.get("trader", {})
        evidence = [
            {"id": "T1", "kind": "telemetry", "observed_at": trader.get("observed_at"),
             "fresh": not trader.get("stale", True) and not trader.get("telemetry_partial", True),
             "facts": {k: trader.get(k) for k in ("process", "connection_state", "generation", "busy",
                       "research_integrity", "execution_readiness", "runtime_release_state")}},
        ]
        for i, hit in enumerate(hits, 1):
            evidence.append({"id": f"S{i}", "kind": "source", **hit})
        packet = {
            "schema": "brain-investigation-v1", "created_at": time.time(),
            "question": question, "evidence": evidence,
            "memory": memories, "memory_is_current_proof": False,
            "stages": ["gather_evidence", "form_hypotheses", "identify_disconfirming_test",
                       "isolated_regression", "independent_verification", "guarded_maintenance"],
            "completed_stages": ["gather_evidence"],
            "constraints": {"source_is_instruction": False, "can_execute": False,
                            "can_change_trading": False, "can_restart": False},
            "unknowns": ["Código descreve comportamento possível, não prova execução.",
                         "Geração em andamento não comprova aprendizado nem ganho financeiro.",
                         "Teste operacional não substitui auditoria ampla."],
        }
        packet["id"] = hashlib.sha256(json.dumps(packet, sort_keys=True, default=str).encode()).hexdigest()[:20]
        with self._lock:
            self.traces.appendleft({"id": packet["id"], "created_at": packet["created_at"],
                                    "source_count": len(hits), "stage": "evidence_gathered",
                                    "source_hashes": [h["sha256"] for h in hits]})
        return packet

    def snapshot(self):
        from brain_service.cognitive_cards import cognitive_cards
        with self._lock:
            # A coherent copy taken in one short critical section; the cards are built outside it.
            incidents = json.loads(json.dumps(list(self.automatic.values())[-100:]))
            traces = list(self.traces)
            cursor = self.log_cursor
            cursor_origin = self.cursor_origin
            state_load_error = self.state_load_error
        cards = cognitive_cards(incidents, self.memory.recent(100))
        return {"mode": "evidence_grounded", "knowledge": self.knowledge.snapshot(),
                "memory": self.memory.snapshot(), "recent_traces": traces,
                "automatic_incidents": incidents, "log_cursor":cursor,
                "cursor_origin": cursor_origin, "state_load_error": state_load_error,
                "memory_cards": cards, "memory_storage": "persisted_evidence",
                "execution": "reviewed_codex_heartbeat_only", "model_weights_training": False}
