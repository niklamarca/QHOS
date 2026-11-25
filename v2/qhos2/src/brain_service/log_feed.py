"""Durable, read-only journal collection. Full messages, resumable client cursors."""
from __future__ import annotations
import json
import re
import sqlite3
import subprocess
import threading
import time
import os
from datetime import datetime
from pathlib import Path

UNITS = ['_SYSTEMD_USER_UNIT=qhos2-coletor.service', '_SYSTEMD_USER_UNIT=qhos2-brain.service',
         '_SYSTEMD_USER_UNIT=qhos2-brain-voice.service']
MAX_LOG_RECORDS = 100_000


def _journal_text(value):
    """journalctl --output=json emits an array for a field set more than once; the
    sqlite bind accepts only scalars (QHOS-AUD-292, revisao)."""
    if isinstance(value, (list, tuple)):
        return ','.join(str(item) for item in value)
    return '' if value is None else str(value)


def safe_message(value):
    text = str(value or '')
    text = re.sub(r'\x1b\[[0-9;]*m', '', text)
    text = re.sub(r'(?i)(bearer\s+)[\w.\-]+', r'\1<redacted>', text)
    text = re.sub(r'https://discord(?:app)?\.com/api/webhooks/\S+', '<redacted-webhook>', text)
    text = re.sub(r'(?i)(https?://)[^\s/@:]+:[^\s/@]+@', r'\1<redacted>@', text)
    return re.sub(r'(?i)((?:api[_-]?key|password|secret|token)\s*[:=]\s*)[^\s,]+', r'\1<redacted>', text)


class RuntimeLogFeed:
    def __init__(self, state_dir):
        path = Path(state_dir)/'runtime_logs.sqlite3'
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY, source_cursor TEXT UNIQUE, timestamp REAL, level TEXT, unit TEXT, pid TEXT, message TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
        self.db.commit()
        self.stopped = threading.Event()
        self.thread = None
        self.process = None
        self.error = None
        self.connected = False
        self.file_thread = None
        self.file_error = None
        # A record that cannot be stored is counted and skipped; it must not
        # restart journalctl at the same cursor forever (QHOS-AUD-292).
        self.poisoned_records = 0
        self.last_poisoned_cursor = None
        self.last_poisoned_error = None
        self.reader_end_failures = 0

    def append(self, row, journal=True):
        message = safe_message(row.get('MESSAGE', ''))
        level = {0:'critical',1:'critical',2:'critical',3:'error',4:'warning',5:'info',6:'info',7:'debug'}.get(int(row.get('PRIORITY', 6)), 'info')
        match = re.search(r'\|\s*(DEBUG|INFO|SUCCESS|WARNING|ERROR|CRITICAL)\s*\|', message)
        if match: level = match.group(1).lower()
        elif re.match(r'^(ERROR|WARNING|CRITICAL):', message): level = message.split(':',1)[0].lower()
        cursor = str(row['__CURSOR'])
        unit = row.get('_SYSTEMD_USER_UNIT') or row.get('_SYSTEMD_UNIT') or row.get('SYSLOG_IDENTIFIER','unknown')
        with self.lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO logs(source_cursor,timestamp,level,unit,pid,message) VALUES(?,?,?,?,?,?)',
                (cursor, int(row.get('__REALTIME_TIMESTAMP', 0))/1e6, level,
                 _journal_text(unit), _journal_text(row.get('_PID','')), message))
            if journal:self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('journal_cursor', cursor))
            self.db.execute('DELETE FROM logs WHERE id <= (SELECT MAX(id) FROM logs) - ?', (MAX_LOG_RECORDS,))

    def page(self, after=0, limit=100):
        with self.lock:
            rows = self.db.execute('SELECT id,timestamp,level,unit,pid,message FROM logs WHERE id>? ORDER BY id LIMIT ?', (max(0,int(after)),min(100,max(1,int(limit))))).fetchall()
        records = [dict(zip(('cursor','timestamp','level','unit','pid','message'),row)) for row in rows]
        return {'schema':'qhos-log-stream-v1','records':records,'next_cursor':records[-1]['cursor'] if records else int(after),
                'source':'systemd-journal+application-files','collection':self.status()}

    def status(self):
        with self.lock:
            count,last = self.db.execute('SELECT COUNT(*),MAX(timestamp) FROM logs').fetchone()
        return {'connected':self.connected,'error':self.error,'records':count,'last_event_at':last,
                'retention_max_records':MAX_LOG_RECORDS,
                'messages_complete':True,'secrets_redacted':True,'initial_history':'journal_last_24_hours_plus_current_application_file',
                'application_file_error':self.file_error,'units':UNITS,
                'poisoned_records':self.poisoned_records,'last_poisoned_cursor':self.last_poisoned_cursor,
                'last_poisoned_error':self.last_poisoned_error,'reader_end_failures':self.reader_end_failures}

    def start(self):
        if self.thread and self.thread.is_alive(): return
        self.thread=threading.Thread(target=self._run,daemon=True,name='brain-journal-reader')
        self.thread.start()
        if os.environ.get('BRAIN_QUANT_LOGS'):
            self.file_thread=threading.Thread(target=self._files,daemon=True,name='brain-log-file-reader')
            self.file_thread.start()

    def _files(self):
        pattern=re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+) \| ([A-Z]+) \|',re.M)
        while not self.stopped.is_set():
            try:
                files=list(Path(os.environ['BRAIN_QUANT_LOGS']).glob('quant_hpc_*.log'))
                if not files:
                    self.file_error='application_log_missing';self.stopped.wait(2);continue
                path=max(files,key=lambda p:p.stat().st_mtime_ns)
                st=path.stat();identity=f'{path.name}:{st.st_dev}:{st.st_ino}'
                key='file:'+identity
                with self.lock:
                    saved=self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
                offset=int(saved[0]) if saved else 0
                if offset>st.st_size:offset=0
                with path.open('rb') as f:
                    f.seek(offset);raw=f.read(2*1024*1024)
                text=raw.decode('utf-8',errors='replace');matches=list(pattern.finditer(text))
                for i,match in enumerate(matches):
                    end=matches[i+1].start() if i+1<len(matches) else len(text)
                    if i+1==len(matches) and (offset+len(raw)<st.st_size or not text.endswith('\n')):break
                    message=text[match.start():end]
                    end_offset=offset+len(text[:end].encode('utf-8'))
                    self.append({'__CURSOR':key+':'+str(offset+len(text[:match.start()].encode('utf-8'))),
                        '__REALTIME_TIMESTAMP':int(datetime.fromisoformat(match.group(1)).timestamp()*1e6),
                        '_SYSTEMD_USER_UNIT':'qhos2-coletor.service','MESSAGE':message},journal=False)
                    with self.lock,self.db:self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,str(end_offset)))
                self.file_error=None
            except Exception as exc:self.file_error=f'{type(exc).__name__}: {exc}'
            self.stopped.wait(2)

    def _run(self):
        while not self.stopped.is_set():
            with self.lock:
                last=self.db.execute("SELECT value FROM meta WHERE key='journal_cursor'").fetchone()
            command=['journalctl','--no-pager','--output=json','--follow']
            command += ['--after-cursor='+last[0]] if last else ['--since=-24h']
            for unit in UNITS:
                if unit != UNITS[0]:command.append('+')
                command.append(unit)
            try:
                self.process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
                if self.stopped.is_set():
                    # close() ran between the loop test and Popen: do not block on
                    # a --follow stream that nobody will stop (QHOS-AUD-305).
                    self.process.terminate()
                self.connected=True
                for line in self.process.stdout:
                    if self.stopped.is_set():break
                    try: row=json.loads(line)
                    except ValueError:
                        if line.strip():self.error=safe_message(line.strip())
                        continue
                    if isinstance(row,dict) and '__CURSOR' in row:
                        try:
                            self.append(row)
                        except (ValueError,TypeError,KeyError,OverflowError,sqlite3.InterfaceError,sqlite3.ProgrammingError) as exc:
                            # Bind errors are about this record; other sqlite3.Error (locked,
                            # disk full) are transient and keep the cursor where it is.
                            self._skip_poisoned(row,exc)
                            continue
                        self.error=None
                if not self.stopped.is_set():self.error=f'journal_reader_exited:{self.process.wait()}'
            except Exception as exc:self.error=f'{type(exc).__name__}: {exc}'
            finally:
                self.connected=False
                self._end_reader(self.process)
            self.stopped.wait(3)

    def _skip_poisoned(self, row, exc):
        """Record-level failure (malformed PRIORITY/timestamp): count it, move the cursor past it."""
        cursor=str(row.get('__CURSOR'))
        self.poisoned_records+=1
        self.last_poisoned_cursor=cursor
        self.last_poisoned_error=f'{type(exc).__name__}: {str(exc)[:120]}'
        self.error=f'poisoned_record:{self.last_poisoned_error}'
        with self.lock,self.db:self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('journal_cursor',cursor))

    def _end_reader(self, process):
        """terminate, then kill: a reader that ignores SIGTERM must not kill the thread with TimeoutExpired.

        A failure to end our own journalctl is counted (reader_end_failures) and named in
        ``error``, never swallowed: it means a reader may still be running."""
        if not process or process.poll() is not None:return
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=5)
            except (subprocess.TimeoutExpired,OSError) as exc:
                self._reader_end_failed(process,exc)
        except OSError as exc:
            self._reader_end_failed(process,exc)

    def _reader_end_failed(self, process, exc):
        self.reader_end_failures+=1
        self.error=f'journal_reader_not_ended:{type(exc).__name__}: {str(exc)[:120]}'

    def close(self):
        self.stopped.set()
        if self.process and self.process.poll() is None:self.process.terminate()
        if self.thread:self.thread.join(timeout=6)
        if self.file_thread:self.file_thread.join(timeout=6)
        # The reader thread may still be stuck on a stream it never got to end.
        self._end_reader(self.process)
        with self.lock:self.db.close()
