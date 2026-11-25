"""
QUANT HPC OS — Storage Engine
Data Lake local com DuckDB como query engine e Parquet como formato base.
Estrutura: /raw /features /regimes /strategies /backtests /portfolio /logs
"""
from __future__ import annotations
import duckdb
import threading
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import time
import hashlib
import json
import shutil
from core.config import config
from core.logger import get_logger
from .provenance import assess as assess_quality, read_manifest, write_manifest

log = get_logger("Storage")


class StorageEngine:
    """
    Engine de armazenamento do Data Lake.
    - Parquet para dados históricos e features (coluna comprimida)
    - DuckDB para queries ad-hoc rápidas
    - Estrutura de diretórios padronizada e versionada
    """

    def __init__(self):
        self._db_lock = threading.Lock()  # DuckDB não é thread-safe
        self.lake = config.data_lake_dir
        # Use /tmp fallback if data_lake is on a mounted (NTFS/SMB) filesystem
        # that doesn't support DuckDB WAL operations.
        import os, tempfile
        _db_candidate = str(self.lake / "quant_hpc.db")
        try:
            _test = _db_candidate + ".wal_test"
            open(_test, "w").close(); os.remove(_test)
            self.db_path = _db_candidate
        except OSError:
            self.db_path = os.path.join(tempfile.gettempdir(), "quant_hpc.db")
            log.warning(f"DuckDB path fallback → {self.db_path}")
        self._conn: Optional[duckdb.DuckDBPyConnection] = None
        self._quality_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}

    @staticmethod
    def initialize():
        """Inicializa estrutura do data lake."""
        dirs = [
            config.data_lake_dir / "raw",
            config.data_lake_dir / "features",
            config.data_lake_dir / "regimes",
            config.data_lake_dir / "backtests",
            config.data_lake_dir / "portfolio",
            config.data_lake_dir / "logs",
            config.data_lake_dir / "provenance",
            config.strategies_dir,
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
        log.info(f"Data Lake inicializado em: {config.data_lake_dir}")

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        """Conexão lazy com DuckDB.
        Usa timeout de 5s para evitar travar o event loop quando o arquivo
        está bloqueado por um processo anterior (crash/hang no Windows).
        """
        if self._conn is None:
            # 45s, nao 5s: no Windows o handle de um processo que caiu pode
            # levar dezenas de segundos para ser liberado pelo SO.
            self._conn = self._connect_with_timeout(self.db_path, timeout=45.0)
            self._setup_db()
        return self._conn

    @property
    def is_ephemeral(self) -> bool:
        """True quando o banco caiu para :memory: — NADA sera persistido."""
        return bool(getattr(self, "_ephemeral", False))

    def _connect_with_timeout(self, path: str, timeout: float = 45.0):
        """Abre DuckDB com timeout e RETENTATIVAS.

        POR QUE ISTO FOI REESCRITO (2026-08-13)
        ---------------------------------------
        A versao anterior desistia em 5s e caia para `:memory:` com um simples
        WARNING. Consequencia medida no historico de logs: **20 dos 102 boots**
        rodaram com o banco em RAM — uma em cada cinco sessoes descartou tudo
        que "aprendeu" ao fechar, em silencio. Foi isso que fez a evolucao
        parecer que recomecava sozinha e que o warm start achasse o banco vazio.

        Falha silenciosa que destroi dados e um bug pior do que travar. Agora:
        3 tentativas com espera crescente, aviso em nivel ERROR, marcacao do
        estado efemero e ALERTA NO DISCORD.
        """
        import time as _t
        ultimo_erro = None
        for tentativa in range(1, 4):
            result: list = [None]
            error: list = [None]

            def _connect():
                try:
                    result[0] = duckdb.connect(path)
                except Exception as e:
                    error[0] = e

            t = threading.Thread(target=_connect, daemon=True)
            t.start()
            t.join(timeout=timeout)

            if not t.is_alive() and error[0] is None and result[0] is not None:
                if tentativa > 1:
                    log.info(f"DuckDB conectado na tentativa {tentativa}")
                self._ephemeral = False
                return result[0]

            ultimo_erro = error[0] or f"bloqueado apos {timeout}s"
            if tentativa < 3:
                espera = 10 * tentativa
                log.warning(
                    f"DuckDB '{path}' indisponivel (tentativa {tentativa}/3): "
                    f"{ultimo_erro} — aguardando {espera}s. Provavel processo "
                    "anterior ainda segurando o arquivo."
                )
                _t.sleep(espera)

        # Esgotou: o banco vai para RAM e NADA sera persistido. Isto e grave.
        self._ephemeral = True
        log.error(
            "=" * 70 + "\n"
            f"  BANCO EM RAM — NADA SERA PERSISTIDO NESTA SESSAO\n"
            f"  '{path}' segue bloqueado apos 3 tentativas ({ultimo_erro}).\n"
            "  Causa quase certa: processo anterior do QHOS ainda vivo.\n"
            "  ACAO: feche o servidor, rode kill_server.ps1, confirme que nao\n"
            "        ha python.exe orfao, e suba de novo.\n"
            "  Enquanto isso: estrategias, backtests e catalogo NAO sobrevivem\n"
            "  ao fechamento. Os ledgers dos bracos (CSV) NAO sao afetados.\n"
            + "=" * 70
        )
        try:
            from monitoring.discord_notifier import send, make_embed
            send(embeds=[make_embed(
                "🔴 Banco em RAM — a sessão não vai guardar nada",
                ("O sistema subiu com o banco de dados **em memória** porque o "
                 "arquivo continuava travado por um processo antigo.\n\n"
                 "➡️ *Traduzindo: ele vai rodar normalmente, mas tudo que "
                 "aprender hoje some quando fechar.* Os registros dos braços "
                 "(TREND, SYMB-1, SPY) **não** são afetados — esses ficam em CSV.\n\n"
                 "**O que fazer:** feche o servidor, rode `kill_server.ps1`, "
                 "confirme que não sobrou `python.exe` e suba de novo."),
                color=0xE74C3C)])
        except Exception:
            pass
        return duckdb.connect(":memory:")

    def _setup_db(self):
        """Cria tabelas de metadados no DuckDB."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS data_catalog (
                asset      VARCHAR,
                market     VARCHAR,   -- stocks | crypto | futures
                timeframe  VARCHAR,
                path       VARCHAR,
                rows       INTEGER,
                from_dt    TIMESTAMP,
                to_dt      TIMESTAMP,
                updated_at TIMESTAMP DEFAULT current_timestamp
            )
        """)
        # Migração aditiva: carimba a época científica na linha do catálogo.
        # Antes, `status='validated'` não dizia SOB QUAL época o veredito valia,
        # e o portfolio lia julgamento de época morta como se fosse vivo. A
        # coluna nasce NULL nas linhas antigas — e NULL nunca casa com a época
        # corrente, então o histórico é preservado e automaticamente excluído
        # da pesquisa atual, que é o comportamento correto.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_catalog (
                strategy_id  VARCHAR PRIMARY KEY,
                name         VARCHAR,
                version      INTEGER DEFAULT 1,
                fitness      DOUBLE,
                oos_sharpe   DOUBLE,
                status       VARCHAR,  -- pending | validated | rejected | live
                created_at   TIMESTAMP DEFAULT current_timestamp,
                metadata     VARCHAR   -- JSON
            )
        """)
        try:
            self._conn.execute(
                "ALTER TABLE strategy_catalog "
                "ADD COLUMN IF NOT EXISTS scientific_epoch VARCHAR"
            )
        except Exception as _migration_error:      # pragma: no cover
            # Falhar aqui não pode derrubar o boot: sem a coluna, o filtro de
            # época cai para fail-closed em get_validated_strategies.
            log.error(f"Migração scientific_epoch falhou: {_migration_error}")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS backtest_results (
                backtest_id  VARCHAR PRIMARY KEY,
                strategy_id  VARCHAR,
                sharpe       DOUBLE,
                sortino      DOUBLE,
                max_dd       DOUBLE,
                win_rate     DOUBLE,
                n_trades     INTEGER,
                cagr         DOUBLE,
                regime       VARCHAR,
                created_at   TIMESTAMP DEFAULT current_timestamp
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                snapshot_id  VARCHAR,
                timestamp    TIMESTAMP,
                nav          DOUBLE,
                cash         DOUBLE,
                weights      VARCHAR,  -- JSON
                regime       VARCHAR,
                metrics      VARCHAR   -- JSON
            )
        """)

    # ─── Parquet I/O ─────────────────────────────────────────────────

    def _raw_path(self, asset: str, timeframe: str = "1d") -> Path:
        """Retorna caminho do arquivo Parquet raw."""
        safe = asset.replace("/", "_").replace(":", "_").replace("^", "")
        return self.lake / "raw" / f"{safe}_{timeframe}.parquet"

    def _features_path(self, asset: str, timeframe: str = "1d") -> Path:
        safe = asset.replace("/", "_").replace(":", "_").replace("^", "")
        return self.lake / "features" / f"{safe}_{timeframe}_features.parquet"

    def _regime_path(self) -> Path:
        today = datetime.utcnow().strftime("%Y%m%d")
        return self.lake / "regimes" / f"regime_{today}.parquet"

    def _is_valid_parquet(self, path: Path) -> bool:
        """Valida magic bytes do arquivo Parquet (PAR1 no header e footer)."""
        try:
            with open(str(path), "rb") as f:
                header = f.read(4)
                if header != b"PAR1":
                    return False
                f.seek(-4, 2)
                return f.read(4) == b"PAR1"
        except Exception:
            return False

    def _write_parquet_atomic(self, table, path: Path):
        """Escreve Parquet atomicamente via arquivo temporário."""
        import tempfile
        import os
        tmp_path = str(path) + ".tmp"
        try:
            pq.write_table(table, tmp_path, compression="snappy")
            # Windows: os.replace falha com WinError 5 se um LEITOR (dashboard,
            # monitor) estiver com o parquet aberto naquele instante. Retry com
            # backoff resolve — leituras duram milissegundos.
            import time as _t
            _last = None
            for _i in range(6):
                try:
                    os.replace(tmp_path, str(path))
                    _last = None
                    break
                except PermissionError as _e:
                    _last = _e
                    _t.sleep(0.25 * (_i + 1))
            if _last is not None:
                raise _last
        except Exception:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise

    @staticmethod
    def _detect_market(asset: str) -> str:
        """Detecta o mercado do asset: stocks | crypto | futures."""
        a = asset.upper()
        if "/" in a or a.endswith("USDT") or a.endswith("-USD") or a.endswith("BTC"):
            return "crypto"
        if a.endswith("=F") or a.startswith("/"):
            return "futures"
        return "stocks"

    def save_raw(self, asset: str, df: pd.DataFrame, timeframe: str = "1d",
                 source: str = "unknown"):
        """Salva raw e seu contrato de proveniência.

        A barra física pode ser parcial para observabilidade, mas consumidores
        normais recebem somente a versão final e aprovada em ``load_raw``.
        """
        path = self._raw_path(asset, timeframe)
        table = pa.Table.from_pandas(df)
        self._write_parquet_atomic(table, path)
        quality, _safe = assess_quality(asset, df, timeframe, source)
        write_manifest(self.lake, quality)
        self._quality_cache.pop(f"{asset}|{timeframe}", None)
        log.debug(f"Raw salvo: {asset} ({len(df)} rows) → {path.name}")

        # Atualiza catálogo
        try:
            self._exec("""
                INSERT OR REPLACE INTO data_catalog
                (asset, market, timeframe, path, rows, from_dt, to_dt)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [
                asset,
                self._detect_market(asset),
                timeframe,
                str(path),
                len(df),
                str(df.index.min()) if len(df) > 0 else None,
                str(df.index.max()) if len(df) > 0 else None,
            ])
        except Exception:
            pass
        return quality

    def load_raw(self, asset: str, timeframe: str = "1d",
                 allow_partial: bool = False) -> Optional[pd.DataFrame]:
        """Carrega raw seguro para pesquisa.

        ``allow_partial`` é reservado a telas de observabilidade. Nenhum motor
        de features, validação ou execução deve ativá-lo.
        """
        path = self._raw_path(asset, timeframe)
        if not path.exists():
            return None
        if not self._is_valid_parquet(path):
            log.warning(f"Parquet inválido ignorado: {path.name}")
            return None
        try:
            table = pq.read_table(str(path))
            df = table.to_pandas()
            if df.index.name != "Date" and "Date" in df.columns:
                df = df.set_index("Date")
            elif "date" in df.columns:
                df = df.set_index("date")
            df.index = pd.to_datetime(df.index)
            manifest = read_manifest(self.lake, asset, timeframe) or {}
            quality, safe = assess_quality(asset, df, timeframe,
                                           str(manifest.get("source", "unknown")))
            # Atualiza o status temporal (por exemplo, a vela de hoje torna-se
            # final após o fechamento) sem modificar o raw histórico. Evita
            # reescrever o mesmo JSON a cada leitura do dashboard no NTFS.
            _qdict = quality.to_dict()
            _keys = ("source", "status", "reason", "rows", "first_observation",
                     "last_observation", "final_observation", "data_hash", "final_data_hash")
            if not manifest or any(manifest.get(k) != _qdict.get(k) for k in _keys):
                write_manifest(self.lake, quality)
            if quality.status == "quarantined":
                log.error(f"RAW QUARENTENADO: {asset} — {quality.reason}")
                return None
            return df if allow_partial else safe
        except Exception as e:
            log.warning(f"Parquet corrompido ({asset} raw): {e}")
            return None

    def data_quality_summary(self, max_age_seconds: float = 60.0) -> Dict[str, Any]:
        """Resumo factual para health checks; nunca conta arquivo stale como saudável."""
        key = "summary"
        cached = self._quality_cache.get(key)
        if cached and time.time() - cached[0] < max_age_seconds:
            return cached[1]
        assets = list(dict.fromkeys(
            list(config.data_assets_stocks) + list(config.data_assets_crypto)
            + list(getattr(config, "data_assets_reference", []))
        ))
        items: List[Dict[str, Any]] = []
        for asset in assets:
            path = self._raw_path(asset)
            if not path.exists():
                items.append({"asset": asset, "status": "quarantined", "reason": "missing_raw"})
                continue
            manifest = read_manifest(self.lake, asset) or {}
            # Backfill de arquivos existentes na primeira leitura após upgrade.
            if not manifest:
                self.load_raw(asset)
                manifest = read_manifest(self.lake, asset) or {}
            items.append({
                "asset": asset,
                "status": manifest.get("status", "quarantined"),
                "reason": manifest.get("reason", "missing_manifest"),
                "last_observation": manifest.get("last_observation"),
                "final_observation": manifest.get("final_observation"),
                "source": manifest.get("source", "unknown"),
            })
        approved = sum(x["status"] == "approved" for x in items)
        partial = sum(x["status"] == "partial" for x in items)
        quarantined = sum(x["status"] == "quarantined" for x in items)
        result = {"total": len(items), "approved": approved, "partial": partial,
                  "quarantined": quarantined, "items": items,
                  "ok": quarantined == 0}
        self._quality_cache[key] = (time.time(), result)
        return result

    def save_features(self, asset: str, df: pd.DataFrame, timeframe: str = "1d"):
        """Salva features computadas."""
        path = self._features_path(asset, timeframe)
        self._write_parquet_atomic(pa.Table.from_pandas(df), path)
        log.debug(f"Features salvas: {asset} ({df.shape[1]} features)")

    def load_features(self, asset: str, timeframe: str = "1d") -> Optional[pd.DataFrame]:
        """Carrega features somente se o raw correspondente for seguro.

        Features são derivadas, não uma segunda fonte de verdade. Um arquivo
        antigo não pode manter um ativo quarentenado vivo dentro da evolução.
        """
        path = self._features_path(asset, timeframe)
        if not path.exists():
            return None
        if not self._is_valid_parquet(path):
            log.warning(f"Parquet inválido ignorado: {path.name}")
            return None
        raw = self.load_raw(asset, timeframe)
        if raw is None or raw.empty:
            log.error(f"FEATURES BLOQUEADAS: {asset} não possui raw aprovado")
            return None
        try:
            table = pq.read_table(str(path))
            df = table.to_pandas()
            df.index = pd.to_datetime(df.index)
            # Remove qualquer feature calculada com a barra parcial/stale que
            # ainda exista no parquet até o próximo recálculo completo.
            return df.loc[df.index.intersection(raw.index)].copy()
        except Exception as e:
            log.warning(f"Parquet corrompido ({asset} features): {e}")
            return None

    def save_regime(self, df: pd.DataFrame):
        """Salva predições de regime."""
        path = self._regime_path()
        pq.write_table(pa.Table.from_pandas(df), str(path), compression="snappy")
        log.debug(f"Regime salvo: {len(df)} rows")

    def load_regime(self, date: Optional[str] = None) -> Optional[pd.DataFrame]:
        """Carrega predições de regime."""
        if date is None:
            date = datetime.utcnow().strftime("%Y%m%d")
        path = self.lake / "regimes" / f"regime_{date}.parquet"
        if not path.exists():
            # Tenta o mais recente
            files = sorted((self.lake / "regimes").glob("*.parquet"))
            if not files:
                return None
            path = files[-1]
        table = pq.read_table(str(path))
        return table.to_pandas()

    def save_backtest(self, strategy_id: str, result: Dict):
        """Salva resultado de backtest no DuckDB."""
        try:
            self._exec("""
                INSERT OR REPLACE INTO backtest_results
                (backtest_id, strategy_id, sharpe, sortino, max_dd,
                 win_rate, n_trades, cagr, regime)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                f"{strategy_id}_{int(time.time())}",
                strategy_id,
                result.get("sharpe", 0),
                result.get("sortino", 0),
                result.get("max_drawdown", 0),
                result.get("win_rate", 0),
                result.get("n_trades", 0),
                result.get("cagr", 0),
                result.get("regime", "all"),
            ])
        except Exception as e:
            log.error(f"Erro ao salvar backtest: {e}")
        try:
            from governance.experiment_registry import experiments
            experiments.record_backtest(strategy_id, result)
        except Exception as registry_error:
            log.error(f"Registro imutável de backtest falhou: {registry_error}")

        # RETENCAO. `backtest_id` embute o timestamp, entao INSERT OR REPLACE
        # nunca substitui: cada backtest de cada estrategia em cada geracao vira
        # linha nova. Medido em 14/08/2026: ~200 estrategias x 173 geracoes num
        # unico dia = ~34.600 linhas. Sem politica o banco ia a 680 MB (84x os
        # dados brutos que ele descreve) e continuava crescendo.
        # Poda a cada 2.000 gravacoes: mantem 90 dias.
        self._n_saves = getattr(self, "_n_saves", 0) + 1
        if self._n_saves % 2000 == 0:
            self.prune_backtests()

    def prune_backtests(self, dias: int = 90) -> int:
        """Remove backtests com mais de `dias`. Retorna quantas linhas sairam.

        Tudo sob ``_db_lock`` e com fetch imediato: o DuckDB invalida result
        sets abertos quando outro ``execute`` roda na mesma conexão — a causa
        medida do WARNING ``No open result set`` na retenção.
        """
        try:
            cutoff = datetime.utcnow() - timedelta(days=int(dias))
            with self._db_lock:
                antes = self.conn.execute(
                    "SELECT count(*) FROM backtest_results"
                ).fetchone()[0]
                self.conn.execute(
                    "DELETE FROM backtest_results WHERE created_at < ?",
                    [cutoff],
                )
                depois = self.conn.execute(
                    "SELECT count(*) FROM backtest_results"
                ).fetchone()[0]
            n = int(antes) - int(depois)
            if n > 0:
                log.info(f"Retencao: {n:,} backtests com mais de {dias} dias "
                         f"removidos ({antes:,} -> {depois:,})")
            return n
        except Exception as e:
            log.warning(f"Retencao de backtests falhou: {e}")
            return 0

    def save_strategy(self, strategy_id: str, meta: Dict):
        """Registra estratégia no catálogo."""
        import json
        try:
            self._exec("""
                INSERT OR REPLACE INTO strategy_catalog
                (strategy_id, name, fitness, oos_sharpe, status, metadata,
                 scientific_epoch)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [
                strategy_id,
                meta.get("name", strategy_id),
                meta.get("fitness", 0),
                meta.get("oos_sharpe", 0),
                meta.get("status", "pending"),
                json.dumps(meta),
                # Carimbo obrigatório: um veredito sem época não é auditável.
                self._scientific_epoch(fresh=False),
            ])
        except Exception as e:
            log.error(f"Erro ao salvar estratégia: {e}")
        try:
            from governance.experiment_registry import experiments
            experiments.record_strategy(strategy_id, meta)
        except Exception as registry_error:
            log.error(f"Registro imutável de estratégia falhou: {registry_error}")

    def save_portfolio_snapshot(self, snapshot: Dict):
        """Salva snapshot do portfólio."""
        import json
        try:
            self._exec("""
                INSERT INTO portfolio_snapshots
                (snapshot_id, timestamp, nav, cash, weights, regime, metrics)
                VALUES (?, current_timestamp, ?, ?, ?, ?, ?)
            """, [
                f"snap_{int(time.time())}",
                snapshot.get("nav", 0),
                snapshot.get("cash", 0),
                json.dumps(snapshot.get("weights", {})),
                snapshot.get("regime", "unknown"),
                json.dumps(snapshot.get("metrics", {})),
            ])
        except Exception as e:
            log.error(f"Erro ao salvar snapshot: {e}")

    # ─── Backup verificável ──────────────────────────────────────────

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def create_verified_backup(self, retention_days: int | None = None) -> Path:
        """Cria snapshot local com hashes e export lógico do DuckDB.

        Deve ser chamado pelo processo que possui o DuckDB; tentar copiar o
        arquivo .db de fora no Windows pode produzir uma cópia bloqueada ou
        inconsistente. Dados Parquet são copiados após escrita atômica.
        """
        now = datetime.now()
        backup_root = self.lake / "backups"
        # O marcador anterior vivia so em RAM; cinco reboots na mesma madrugada
        # produziram cinco copias identicas. O disco agora e a fonte do gate.
        for existing in sorted(backup_root.glob(now.strftime("snapshot_%Y%m%d_*")), reverse=True):
            if (existing / "manifest.json").exists() and not (existing / "FAILED").exists():
                log.info(f"Backup diario ja existe: {existing.name} — reutilizando")
                return existing
        target = backup_root / now.strftime("snapshot_%Y%m%d_%H%M%S")
        target.mkdir(parents=True, exist_ok=False)
        folders = ["raw", "features", "regimes", "backtests", "portfolio",
                   "track_record", "governance", "audit", "journal", "provenance",
                   "experiments", "events", "events_native", "events_silver",
                   "events_reference"]
        copied: list[Path] = []
        try:
            # Força páginas sujas para disco antes do export lógico.
            try:
                self._exec("CHECKPOINT")
            except Exception as checkpoint_error:
                log.warning(f"Backup: checkpoint DuckDB falhou: {checkpoint_error}")
            for name in folders:
                source = self.lake / name
                if not source.exists():
                    continue
                destination = target / name
                shutil.copytree(source, destination, dirs_exist_ok=True)
                copied.extend(p for p in destination.rglob("*") if p.is_file())

            # Estados soltos que não pertencem a um subdiretório (broker,
            # autonomia, aprendizado e monitor) também fazem parte do snapshot.
            state_dir = target / "state"
            state_dir.mkdir()
            for pattern in ("*.json", "*.jsonl", "*.csv", "*.txt"):
                for source in self.lake.glob(pattern):
                    destination = state_dir / source.name
                    shutil.copy2(source, destination)
                    copied.append(destination)

            # Export lógico é recuperável inclusive se o arquivo WAL mudar.
            db_export = target / "duckdb_export"
            db_export.mkdir()
            escaped = str(db_export.resolve()).replace("\\", "/").replace("'", "''")
            self._exec(f"EXPORT DATABASE '{escaped}' (FORMAT PARQUET)")
            copied.extend(p for p in db_export.rglob("*") if p.is_file())

            files = []
            for file in sorted(copied):
                files.append({"path": str(file.relative_to(target)).replace("\\", "/"),
                              "bytes": file.stat().st_size, "sha256": self._sha256(file)})
            manifest = {"created_at": now.isoformat(), "format": "qhos_verified_backup_v1",
                        "file_count": len(files), "files": files}
            manifest_path = target / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

            # Verificação independente dos hashes recém-gerados.
            for item in files:
                if self._sha256(target / item["path"]) != item["sha256"]:
                    raise RuntimeError(f"checksum inválido: {item['path']}")
            log.warning(f"Backup verificado criado: {target.name} ({len(files)} arquivos)")
            self._prune_backups(retention_days or config.verified_backup_retention_days)
            return target
        except Exception:
            # Mantém o diretório para perícia, mas marca a falha e nunca o chama
            # de backup válido.
            (target / "FAILED").write_text("backup verification failed", encoding="utf-8")
            raise

    def _prune_backups(self, retention_days: int) -> None:
        cutoff = time.time() - max(1, int(retention_days)) * 86400
        root = self.lake / "backups"
        for candidate in root.glob("snapshot_*"):
            try:
                if candidate.stat().st_mtime < cutoff:
                    shutil.rmtree(candidate)
            except Exception as exc:
                log.warning(f"Retenção de backup falhou para {candidate.name}: {exc}")

    # ─── DuckDB Queries ───────────────────────────────────────────────

    def _exec(self, sql: str, params=None):
        """Executa SQL com lock — thread-safe para run_in_executor.

        Materializa o resultado ainda sob o lock. Devolver o cursor e deixar o
        chamador fazer ``.df()``/``fetchone()`` depois libera a conexão para
        outra thread e produz ``No open result set``.
        """
        with self._db_lock:
            result = (
                self.conn.execute(sql, params) if params is not None
                else self.conn.execute(sql)
            )
            try:
                return result.fetchall()
            except Exception:
                # DDL / INSERT / DELETE sem result set — ok.
                return []

    def query(self, sql: str, params: Optional[list] = None) -> pd.DataFrame:
        """Executa query SQL via DuckDB e materializa sob o lock."""
        with self._db_lock:
            if params is not None:
                return self.conn.execute(sql, params).df()
            return self.conn.execute(sql).df()

    # ─── Época científica ────────────────────────────────────────────
    # `governance/epistemics.py` NÃO é alterado aqui: ele já calcula a época
    # corretamente. O defeito era ninguém consumi-la na hora de gravar e ler.

    _EPOCH_MEMO_TTL = 5.0

    def _scientific_epoch(self, fresh: bool = True) -> str:
        """Época corrente. ``fresh=False`` aceita memo curto de escrita.

        Medido: a chamada custa ~74 ms (lê 12 arquivos de código + manifests).
        Em `save_strategy`, que roda por estratégia, isso somaria minutos num
        lote grande — daí o memo.

        O memo é usado SÓ na escrita, e a assimetria é deliberada. Memo velho
        na escrita carimba a linha com a época anterior: ela deixa de casar com
        a época corrente e é EXCLUÍDA da pesquisa — erro conservador. Memo velho
        na LEITURA faria o oposto: filtraria por uma época morta e devolveria
        julgamento vencido como vivo, que é exatamente o bug sendo corrigido.
        Por isso a leitura sempre paga os 74 ms.
        """
        from governance.epistemics import current_scientific_epoch
        if fresh:
            return current_scientific_epoch()
        now = time.time()
        memo = getattr(self, "_epoch_memo", None)
        if memo and (now - memo[1]) < self._EPOCH_MEMO_TTL:
            return memo[0]
        epoch = current_scientific_epoch()
        self._epoch_memo = (epoch, now)
        return epoch

    def get_validated_strategies(self, min_sharpe: float = 0.5, limit: int = 0,
                                 any_epoch: bool = False) -> pd.DataFrame:
        """Aprovadas nos testes internos DESTA época científica.

        Mudança de contrato (18/08/2026): antes retornava qualquer linha com
        ``status='validated'``, sem olhar a época. Quando código ou dados
        mudavam, o validador limpava o cache e o genetic invalidava métricas,
        mas o catálogo DuckDB seguia entregando vereditos de épocas mortas —
        e o portfolio os lia como vivos. Só o PromotionGate segurava, no fim
        da linha. Agora o dado sujo não circula.

        Args:
            min_sharpe: Sharpe OOS mínimo.
            limit: Máximo retornado (0 = sem limite). Use 100 para HRP.
            any_epoch: ignora o filtro de época. Só para auditoria histórica
                offline; nunca para decidir capital, nem paper.
        """
        params: List[Any] = [min_sharpe]
        sql = """
            SELECT * FROM strategy_catalog
            WHERE status = 'validated' AND oos_sharpe >= ?
        """
        if not any_epoch:
            # Linha antiga tem scientific_epoch NULL e a comparação devolve
            # NULL, não TRUE: o histórico é preservado e fica fora da pesquisa
            # corrente sem precisar de DELETE.
            sql += " AND scientific_epoch = ?"
            params.append(self._scientific_epoch(fresh=True))
        sql += " ORDER BY fitness DESC"
        if limit > 0:
            sql += f" LIMIT {int(limit)}"
        try:
            frame = self.query(sql, params)
        except Exception as _query_error:
            # Coluna ausente (migração falhou) não pode virar "sem filtro".
            log.error(f"Filtro de época indisponível, devolvendo vazio: {_query_error}")
            return pd.DataFrame()
        if not any_epoch:
            # Descarte silencioso é como o bug sobreviveu tanto tempo: o número
            # caía e nada dizia por quê.
            try:
                total = int(self.query(
                    "SELECT COUNT(*) AS n FROM strategy_catalog "
                    "WHERE status = 'validated' AND oos_sharpe >= ?",
                    [min_sharpe])["n"].iloc[0])
                descartadas = total - len(frame)
                if descartadas > 0:
                    log.warning(
                        f"Catálogo: {len(frame)} aprovadas na época corrente; "
                        f"{descartadas} de épocas anteriores ignoradas "
                        f"(preservadas no banco, fora da pesquisa)"
                    )
            except Exception:
                pass
        return frame

    def get_backtest_history(self, strategy_id: str) -> pd.DataFrame:
        """Histórico de backtests de uma estratégia."""
        return self.query("""
            SELECT * FROM backtest_results
            WHERE strategy_id = ?
            ORDER BY created_at DESC
        """, [strategy_id])

    def get_portfolio_history(self, days: int = 30) -> pd.DataFrame:
        """Histórico do portfólio."""
        return self.query("""
            SELECT * FROM portfolio_snapshots
            WHERE timestamp >= current_timestamp - INTERVAL ? DAY
            ORDER BY timestamp
        """, [days])

    def get_catalog(self) -> pd.DataFrame:
        """Catálogo de todos os dados no data lake."""
        return self.query("SELECT * FROM data_catalog ORDER BY updated_at DESC")

    def close(self):
        """Fecha a conexão com DuckDB."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
