"""
QUANT HPC OS — GPU Regime Model Engine
Transformer temporal multi-asset para classificação de regimes de mercado.
Roda em RTX 5090 (CUDA). Classifica: trend, mean_reversion, vol_expansion, liq_stress.
"""
from __future__ import annotations
import asyncio
import warnings
import numpy as np
import pandas as pd

# Suprime warning interno do PyTorch sobre nested_tensor com norm_first
warnings.filterwarnings(
    "ignore",
    message="enable_nested_tensor is True.*",
    category=UserWarning,
)
try:
    import torch
    import torch.nn as nn
    from torch.utils.data import Dataset, DataLoader
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    # Stubs para evitar erros de importação quando torch não está instalado
    class _FakeObj:
        """Objeto stub que absorve qualquer atributo/chamada sem explodir."""
        def __init__(self, *a, **kw): pass
        def __call__(self, *a, **kw): return self
        def __getattr__(self, n): return _FakeObj()
        def __iter__(self): return iter([])
        def __repr__(self): return "FakeObj()"
    class _FakeCuda:
        @staticmethod
        def is_available(): return False
    class _FakeTorch:
        Tensor = np.ndarray
        cuda = _FakeCuda()
        @staticmethod
        def device(s): return s
        @staticmethod
        def zeros(*a, **kw): return np.zeros(a[0] if a else (1,))
        @staticmethod
        def tensor(data, **kw): return np.asarray(data)
        @staticmethod
        def no_grad(): return _FakeObj()
        @staticmethod
        def softmax(x, dim=-1): return x
        def __getattr__(self, n): return _FakeObj()
    torch = _FakeTorch()
    class _FakeNN:
        Module = _FakeObj
        Linear = _FakeObj
        LayerNorm = _FakeObj
        ReLU = _FakeObj
        GELU = _FakeObj
        Dropout = _FakeObj
        Sequential = _FakeObj
        TransformerEncoderLayer = _FakeObj
        TransformerEncoder = _FakeObj
        def __getattr__(self, n): return _FakeObj
    nn = _FakeNN()
    Dataset = object
    DataLoader = list
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from datetime import datetime
import time
from core.config import config
from core.events import EventBus, EventType, Event, event_bus
from core.logger import get_logger
from data.storage import StorageEngine

log = get_logger("RegimeModel")
if not TORCH_AVAILABLE:
    log.warning("PyTorch não encontrado — RegimeModel rodará em modo stub (sem GPU)")

REGIME_NAMES = {
    0: "trend",
    1: "mean_reversion",
    2: "vol_expansion",
    3: "liq_stress",
}

REGIME_COLORS = {
    "trend":           "#00C851",
    "mean_reversion":  "#2196F3",
    "vol_expansion":   "#FF9800",
    "liq_stress":      "#F44336",
}


class TemporalEmbedding(nn.Module):
    """Embedding temporal posicional para séries financeiras."""

    def __init__(self, d_model: int, max_len: int = 512):
        super().__init__()
        self.d_model = d_model
        pe = torch.zeros(max_len, d_model)
        pos = torch.arange(0, max_len).unsqueeze(1).float()
        div = torch.exp(
            torch.arange(0, d_model, 2).float() * (-np.log(10000.0) / d_model)
        )
        pe[:, 0::2] = torch.sin(pos * div)
        pe[:, 1::2] = torch.cos(pos * div)
        self.register_buffer("pe", pe.unsqueeze(0))  # (1, max_len, d_model)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.pe[:, :x.size(1), :]


class RegimeTransformer(nn.Module):
    """
    Transformer para classificação de regimes de mercado.

    Arquitetura:
    - Input projection: features → d_model
    - Positional encoding temporal
    - N camadas de Multi-Head Attention
    - Global Average Pooling
    - Classificador com dropout
    - Saída: distribuição de probabilidade sobre 4 regimes

    Input:  (batch, seq_len, n_features)
    Output: (batch, n_classes)  — probabilidades de regime
    """

    def __init__(
        self,
        n_features: int = 30,
        d_model: int = 256,
        n_heads: int = 8,
        n_layers: int = 4,
        n_classes: int = 4,
        dropout: float = 0.1,
        max_seq_len: int = 128,
    ):
        super().__init__()
        self.n_features = n_features
        self.d_model = d_model
        self.n_classes = n_classes

        # Input projection
        self.input_proj = nn.Sequential(
            nn.Linear(n_features, d_model),
            nn.LayerNorm(d_model),
            nn.ReLU(),
            nn.Dropout(dropout),
        )

        # Positional encoding
        self.pos_enc = TemporalEmbedding(d_model, max_len=max_seq_len)

        # Transformer encoder
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=n_heads,
            dim_feedforward=d_model * 4,
            dropout=dropout,
            batch_first=True,
            norm_first=True,  # Pre-LN (mais estável)
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)

        # Classificador
        self.classifier = nn.Sequential(
            nn.Linear(d_model, d_model // 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(d_model // 2, n_classes),
        )

        # Inicialização Xavier
        self._init_weights()

    def _init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)

    def forward(
        self,
        x: torch.Tensor,
        src_key_padding_mask: Optional[torch.Tensor] = None
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        x: (batch, seq_len, n_features)
        Returns: (logits, probs)  — ambos (batch, n_classes)
        """
        # Project input
        h = self.input_proj(x)

        # Add positional encoding
        h = self.pos_enc(h)

        # Transformer
        h = self.transformer(h, src_key_padding_mask=src_key_padding_mask)

        # Global average pool (ao longo do tempo)
        h = h.mean(dim=1)  # (batch, d_model)

        # Classify
        logits = self.classifier(h)
        probs = torch.softmax(logits, dim=-1)

        return logits, probs


class RegimeDataset(Dataset):
    """Dataset para treino do Regime Transformer."""

    def __init__(
        self,
        sequences: np.ndarray,   # (N, seq_len, n_features)
        labels: np.ndarray,       # (N,) — índice de regime 0-3
    ):
        self.X = torch.tensor(sequences, dtype=torch.float32)
        self.y = torch.tensor(labels, dtype=torch.long)

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        return self.X[idx], self.y[idx]


class RegimeModelEngine:
    """
    Engine completo do Regime Model GPU.

    Responsabilidades:
    - Carregar e pré-processar features multi-asset
    - Treinar / re-treinar o Transformer periodicamente
    - Inferência em batch (alto throughput na RTX 5090)
    - Publicar predições de regime no event bus
    - Persistir predições no data lake
    """

    MODEL_PATH = config.data_lake_dir / "regimes" / "regime_transformer.pt"

    def __init__(self, storage: StorageEngine):
        self.storage = storage
        self.bus = event_bus
        self.device = torch.device(
            config.gpu_device if torch.cuda.is_available() else "cpu"
        )
        self.model: Optional[RegimeTransformer] = None
        self.scaler = None                         # StandardScaler
        # ``model is not None`` nao significa ``modelo treinado``: um objeto
        # recem-construido contem pesos aleatorios. Inferencia so e autorizada
        # depois de carregar um checkpoint ou concluir treino nesta execucao.
        self._model_ready: bool = False
        self._current_regime: Optional[str] = None
        self._regime_probs: Optional[Dict] = None
        self._last_train: float = 0

        log.info(f"Regime Model rodando em: {self.device}")

    # ─── Model Lifecycle ─────────────────────────────────────────────

    def _build_model(self, n_features: int = 30) -> RegimeTransformer:
        """Constrói novo modelo Transformer."""
        model = RegimeTransformer(
            n_features=n_features,
            d_model=256,
            n_heads=8,
            n_layers=4,
            n_classes=config.regime_n_classes,
            dropout=0.1,
            max_seq_len=config.regime_lookback,
        )
        return model.to(self.device)

    def _load_or_build(self, n_features: int = 30):
        """Carrega modelo salvo ou constrói novo."""
        if self.MODEL_PATH.exists():
            try:
                try:
                    import sklearn.preprocessing
                    torch.serialization.add_safe_globals([sklearn.preprocessing._data.StandardScaler])
                except Exception:
                    pass
                checkpoint = torch.load(str(self.MODEL_PATH), map_location=self.device, weights_only=False)
                n_feat = checkpoint.get("n_features", n_features)
                self.model = self._build_model(n_feat)
                self.model.load_state_dict(checkpoint["model_state"])
                self.model.eval()
                self.scaler = checkpoint.get("scaler")
                self._model_ready = True
                log.info(f"Modelo de regime carregado: {self.MODEL_PATH.name}")
                return
            except Exception as e:
                log.warning(f"Falha ao carregar modelo: {e} — construindo novo")

        self.model = self._build_model(n_features)
        self._model_ready = False
        log.info("Novo modelo de regime construído")

    def _save_model(self, n_features: int):
        """Salva checkpoint do modelo."""
        self.MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        torch.save({
            "model_state": self.model.state_dict(),
            "n_features": n_features,
            "scaler": self.scaler,
            "trained_at": datetime.utcnow().isoformat(),
        }, str(self.MODEL_PATH))
        log.info(f"Modelo salvo: {self.MODEL_PATH.name}")

    # ─── Data Preparation ────────────────────────────────────────────

    def _prepare_sequences(
        self,
        features_dict: Dict[str, pd.DataFrame],
        seq_len: int = 60,
        horizon: int = 21,
    ) -> Tuple[np.ndarray, np.ndarray, List[str]]:
        """
        Prepara sequências de input para o Transformer.

        DOIS DEFEITOS CORRIGIDOS EM 2026-08-12 (auditoria regressiva):

        1. VAZAMENTO DE RÓTULO. O rótulo vinha de `_heuristic_regime(row)`, uma
           função determinística de vol_regime/hurst/z_score/mom_21 — features
           que estão NA PRÓPRIA ENTRADA. O modelo era treinado para copiar um
           if-else que já recebia de bandeja: daí `acc=1.000`, que não era
           aprendizado e sim cópia. Agora o rótulo vem do FUTURO (próximos
           `horizon` dias), o que torna a tarefa genuinamente preditiva.

        2. SEQUÊNCIAS ATRAVESSANDO ATIVOS. As features de todos os ativos eram
           empilhadas com pd.concat(axis=0) e as janelas eram cortadas sobre a
           pilha — sequências misturavam o fim de um ativo com o começo de
           outro. Agora cada ativo gera as próprias sequências.

        Returns: (sequences, labels_do_futuro, feature_names)
        """
        from sklearn.preprocessing import StandardScaler

        KEY_FEATURES = [
            "ret_1d", "vol_20", "mom_5", "mom_21", "mom_63",
            "z_score_20", "rsi_14", "macd_hist", "bb_pct_20",
            "trend_5_20", "trend_20_50", "hurst_approx",
            "vol_regime", "atr_pct_14", "obv_trend",
        ]

        # 1) descobre o conjunto comum de colunas
        cols_comuns = None
        limpos = {}
        for asset, df in features_dict.items():
            if df is None or df.empty:
                continue
            cols = [c for c in KEY_FEATURES if c in df.columns]
            if len(cols) < 5 or "close" not in df.columns:
                continue
            sub = df[cols + ["close"]].dropna()
            if len(sub) < seq_len + horizon + 10:
                continue
            limpos[asset] = sub
            cols_comuns = set(cols) if cols_comuns is None else (cols_comuns & set(cols))

        if not limpos or not cols_comuns:
            return np.array([]), np.array([]), []

        feature_names = [c for c in KEY_FEATURES if c in cols_comuns]

        # 2) ajusta o scaler sobre TUDO (só as features, nunca o close)
        pilha = np.vstack([limpos[a][feature_names].values for a in limpos])
        if self.scaler is None:
            self.scaler = StandardScaler()
            self.scaler.fit(pilha)

        # 3) sequências POR ATIVO, rótulo vindo do FUTURO
        sequences, labels = [], []
        for asset, sub in limpos.items():
            X = self.scaler.transform(sub[feature_names].values)
            close = sub["close"].astype(float).values
            n = len(X)
            # precisa de `horizon` barras à frente para rotular
            for i in range(seq_len, n - horizon):
                sequences.append(X[i - seq_len:i])
                labels.append(self._future_regime(close, i, horizon))

        if not sequences:
            return np.array([]), np.array([]), []
        return (np.array(sequences, dtype=np.float32),
                np.array(labels, dtype=np.int64), feature_names)

    @staticmethod
    def _future_regime(close: np.ndarray, i: int, horizon: int) -> int:
        """Rótulo PREDITIVO: o que o mercado FEZ nos próximos `horizon` dias.

        Classificação em 4 estados mutuamente exclusivos e todos alcançáveis
        (a heurística antiga tinha um ramo morto: `>1.8 -> 2` vinha antes de
        `>2.5 -> 3`, tornando liq_stress impossível de produzir).

          0 trend          — direcional forte, sem estresse
          1 mean_reversion — vai e volta, retorno líquido pequeno
          2 vol_expansion  — vol futura muito acima da recente
          3 liq_stress     — vol explode E o preço cai (queda desordenada)
        """
        fut = close[i:i + horizon + 1]
        if len(fut) < 3:
            return 1
        r = np.diff(fut) / np.maximum(fut[:-1], 1e-12)
        vol_fut = float(np.std(r))
        # vol recente como referência (mesma janela, olhando para trás)
        j0 = max(0, i - horizon)
        rp = np.diff(close[j0:i + 1]) / np.maximum(close[j0:i], 1e-12)
        vol_ref = float(np.std(rp)) if len(rp) > 2 else vol_fut
        ratio = vol_fut / max(vol_ref, 1e-9)
        total = float(fut[-1] / max(fut[0], 1e-12) - 1.0)
        caminho = float(np.sum(np.abs(r)))          # distância percorrida

        if ratio > 1.6 and total < -0.02:
            return 3                                 # liq_stress
        if ratio > 1.6:
            return 2                                 # vol_expansion
        # eficiência do movimento: quanto do caminho virou deslocamento
        efic = abs(total) / max(caminho, 1e-12)
        if efic > 0.35 and abs(total) > 0.01:
            return 0                                 # trend
        return 1                                     # mean_reversion

    @staticmethod
    def _heuristic_regime(row: pd.Series) -> int:
        """
        Label heurístico de regime para bootstrap do modelo.
        Em produção, labels virão de episódios históricos anotados.
        """
        # Volatility expansion
        if abs(row.get("vol_regime", 1)) > 1.8:
            return 2  # vol_expansion

        # Liquidity stress (proxy: vol muito alta + queda brusca)
        if abs(row.get("vol_regime", 1)) > 2.5:
            return 3  # liq_stress

        # Mean reversion: Hurst < 0.4 e z-score extremo
        hurst = row.get("hurst_approx", 0.5)
        z = abs(row.get("z_score_20", 0))
        if hurst < 0.4 and z > 1.5:
            return 1  # mean_reversion

        # Trend: Hurst > 0.6 e momentum consistente
        mom = row.get("mom_21", 0)
        if hurst > 0.6 and abs(mom) > 0.05:
            return 0  # trend

        return 1  # default: mean_reversion

    # ─── Training ────────────────────────────────────────────────────

    async def train(
        self,
        features_dict: Dict[str, pd.DataFrame],
        epochs: int = 20,
        batch_size: int | None = None,   # None -> config.gpu_batch_size
        lr: float = 3e-4,
    ):
        """Treina o modelo de regime."""
        # gpu_batch_size era campo INERTE do config: definido e nunca lido por
        # ninguem (auditoria de 14/08/2026). Agora manda de verdade.
        if batch_size is None:
            batch_size = int(getattr(config, "gpu_batch_size", 256) or 256)
        log.info(f"Iniciando treino do Regime Transformer... (batch={batch_size})")

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, self._train_sync, features_dict, epochs, batch_size, lr
        )
        return result

    def _train_sync(
        self,
        features_dict: Dict[str, pd.DataFrame],
        epochs: int,
        batch_size: int,
        lr: float,
    ) -> Dict:
        """Treino síncrono (executa em thread pool)."""
        sequences, labels, feature_names = self._prepare_sequences(
            features_dict,
            seq_len=config.regime_lookback,
        )

        if len(sequences) < 100:
            log.warning("Dados insuficientes para treinar regime model")
            # Bootstrap com dados sintéticos
            sequences, labels, feature_names = self._generate_synthetic_training_data()

        n_features = sequences.shape[-1]
        self._load_or_build(n_features)
        self.model.train()

        # SPLIT CRONOLOGICO 80/20. Antes so existia acuracia de TREINO, que com
        # rotulo vazado dava 1.000 e nao significava nada. Com rotulo do futuro,
        # o numero que importa e a acuracia FORA da amostra — e ela precisa
        # bater o baseline trivial "chutar sempre a classe majoritaria".
        _n = len(sequences); _corte = int(_n * 0.8)
        _Xtr, _ytr = sequences[:_corte], labels[:_corte]
        _Xva, _yva = sequences[_corte:], labels[_corte:]
        import collections as _co
        _dist = _co.Counter(labels.tolist())
        _maj = max(_dist.values()) / max(len(labels), 1)
        log.info(f"  Regime: {_n} sequencias | distribuicao de rotulos "
                 f"{ {REGIME_NAMES.get(k,k): round(v/_n,3) for k,v in sorted(_dist.items())} }")
        log.info(f"  Baseline (chutar a classe majoritaria): acc={_maj:.3f} — o modelo "
                 f"precisa BATER isto para ter valor")

        dataset = RegimeDataset(_Xtr, _ytr)
        loader = DataLoader(
            dataset,
            batch_size=batch_size,
            shuffle=True,
            num_workers=0,
            pin_memory=self.device.type == "cuda",
        )

        optimizer = torch.optim.AdamW(self.model.parameters(), lr=lr, weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.OneCycleLR(
            optimizer, max_lr=lr, steps_per_epoch=len(loader), epochs=epochs
        )
        criterion = nn.CrossEntropyLoss(label_smoothing=0.1)

        history = []
        for epoch in range(epochs):
            total_loss = 0.0
            correct = 0
            total = 0

            for X_batch, y_batch in loader:
                X_batch = X_batch.to(self.device)
                y_batch = y_batch.to(self.device)

                optimizer.zero_grad()
                logits, _ = self.model(X_batch)
                loss = criterion(logits, y_batch)
                loss.backward()

                # Gradient clipping
                nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                optimizer.step()
                scheduler.step()

                total_loss += loss.item() * len(X_batch)
                preds = logits.argmax(dim=-1)
                correct += (preds == y_batch).sum().item()
                total += len(X_batch)

            avg_loss = total_loss / total
            acc = correct / total
            history.append({"epoch": epoch, "loss": avg_loss, "acc": acc})

            if (epoch + 1) % 5 == 0:
                log.info(f"  Epoch {epoch+1}/{epochs} — loss={avg_loss:.4f}, acc={acc:.3f}")

        self.model.eval()

        # VEREDITO HONESTO: acuracia fora da amostra vs baseline trivial
        _acc_va = None
        try:
            if len(_Xva) > 20:
                with torch.no_grad():
                    _lv, _ = self.model(torch.FloatTensor(_Xva).to(self.device))
                    _pv = _lv.argmax(dim=-1).cpu().numpy()
                _acc_va = float((_pv == _yva).mean())
                _maj_va = float(max(_co.Counter(_yva.tolist()).values()) / len(_yva))
                _ganho = _acc_va - _maj_va
                # PERSISTE A PERICIA MEDIDA. A inferencia consulta isto: um modelo
                # sem pericia demonstrada nao tem direito de condicionar decisoes.
                self._skill_gain = _ganho
                self._skill_baseline_class = int(_co.Counter(_yva.tolist()).most_common(1)[0][0])
                try:
                    import json as _j, os as _os
                    _sf = _os.path.join(str(config.data_lake_dir), "regime_skill.json")
                    with open(_sf, "w") as _fh:
                        _j.dump({"gain": _ganho, "acc": _acc_va, "baseline": _maj_va,
                                 "baseline_class": self._skill_baseline_class}, _fh)
                except Exception:
                    pass
                log.info(f"  Regime FORA DA AMOSTRA: acc={_acc_va:.3f} vs baseline "
                         f"{_maj_va:.3f} -> ganho {_ganho:+.3f}")
                if _ganho <= 0.01:
                    log.warning(
                        "  Regime: o modelo NAO bate o chute trivial fora da amostra. "
                        "Isso significa que ele nao carrega informacao preditiva sobre "
                        "o regime futuro. Considere desligar o modulo em vez de "
                        "condicionar decisoes nele."
                    )
        except Exception as _e:
            log.debug(f"avaliacao out-of-sample do regime: {_e}")

        self._save_model(n_features)
        self._model_ready = True
        self._last_train = time.time()

        log.info(f"Treino concluído — acc final: {history[-1]['acc']:.3f}")
        return {"history": history, "n_sequences": len(sequences)}

    def _generate_synthetic_training_data(
        self, n_samples: int = 2000, seq_len: int = 60, n_features: int = 15
    ) -> Tuple[np.ndarray, np.ndarray, List[str]]:
        """Gera dados sintéticos para bootstrap do modelo."""
        np.random.seed(42)
        sequences = np.random.randn(n_samples, seq_len, n_features).astype(np.float32)
        labels = np.random.randint(0, 4, n_samples)
        features = [f"feat_{i}" for i in range(n_features)]
        return sequences, labels, features

    # ─── Inference ───────────────────────────────────────────────────

    async def predict_current_regime(self) -> Dict:
        """Prediz o regime atual do mercado."""
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, self._predict_sync)

        self._current_regime = result["regime"]
        self._regime_probs = result["probabilities"]

        # Salva no data lake
        df_reg = pd.DataFrame([{
            "timestamp": datetime.utcnow(),
            "regime": result["regime"],
            "regime_id": result["regime_id"],
            "confidence": result["confidence"],
            "source": result.get("source", "unavailable"),
            **{f"prob_{k}": v for k, v in result["probabilities"].items()}
        }])
        self.storage.save_regime(df_reg)

        # Publica evento
        await self.bus.publish(Event(
            type=EventType.REGIME_PREDICTED,
            source="regime_model",
            payload=result,
        ))

        log.info(
            f"Regime: {result['regime']} "
            f"(conf={result['confidence']:.2f})"
        )
        return result


    def _fallback_regime(self) -> Dict:
        """Regime heurístico quando modelo não está treinado."""
        return {
            "regime": "trend",
            "regime_id": 0,
            "confidence": 0.25,
            "probabilities": {name: 0.25 for name in REGIME_NAMES.values()},
            "source": "fallback",
        }

    def _predict_sync(self) -> Dict:
        """Inferência síncrona do regime atual."""
        if self.model is None or not self._model_ready:
            # Modelo ausente ou apenas construido com pesos aleatorios.
            return self._fallback_regime()

        # Carrega features recentes
        features_list = []
        for asset in config.data_assets_stocks[:5]:
            df = self.storage.load_features(asset)
            if df is not None and len(df) >= config.regime_lookback:
                key_cols = [c for c in df.columns if c in [
                    "ret_1d", "vol_20", "mom_21", "z_score_20", "rsi_14",
                    "macd_hist", "bb_pct_20", "hurst_approx", "vol_regime",
                    "atr_pct_14", "trend_5_20", "trend_20_50", "obv_trend",
                    "autocorr_1", "stoch_k",
                ]]
                if len(key_cols) >= 5:
                    features_list.append(df[key_cols].tail(config.regime_lookback).values)

        if not features_list:
            return self._fallback_regime()

        # Usa média dos assets como input
        n_features = min(f.shape[-1] for f in features_list)
        sequences = np.array([f[-config.regime_lookback:, :n_features] for f in features_list])
        sequences = np.nan_to_num(sequences, nan=0.0)

        # Normaliza
        if self.scaler is not None:
            try:
                shape = sequences.shape
                flat = sequences.reshape(-1, n_features)
                flat_scaled = self.scaler.transform(flat)
                sequences = flat_scaled.reshape(shape)
            except Exception:
                pass

        # PORTAO DE PERICIA: se o modelo nao bate o chute trivial fora da
        # amostra, ele nao entrega previsao — devolve a classe majoritaria e
        # se declara sem pericia. Medido em 12/08/2026: acc 0.637 contra
        # baseline 0.772 (ganho -0.136). Um modelo pior que uma constante nao
        # pode condicionar selecao de estrategia.
        _gain = getattr(self, "_skill_gain", None)
        if _gain is None:
            try:
                import json as _j, os as _os
                _sf = _os.path.join(str(config.data_lake_dir), "regime_skill.json")
                if _os.path.exists(_sf):
                    _d = _j.load(open(_sf))
                    _gain = _d.get("gain")
                    self._skill_gain = _gain
                    self._skill_baseline_class = int(_d.get("baseline_class", 1))
            except Exception:
                pass
        if _gain is not None and _gain <= 0.01:
            _cid = int(getattr(self, "_skill_baseline_class", 1))
            _nome = REGIME_NAMES.get(_cid, "mean_reversion")
            if not getattr(self, "_avisou_sem_pericia", False):
                self._avisou_sem_pericia = True
                log.warning(
                    f"Regime: modelo SEM PERICIA (ganho {_gain:+.3f} fora da amostra). "
                    f"Servindo a classe majoritaria '{_nome}' e marcando source='baseline'. "
                    "As decisoes deixam de ser condicionadas por um preditor sem valor."
                )
            return {"regime": _nome, "regime_id": _cid, "confidence": 0.0,
                    "probabilities": {n: (1.0 if i == _cid else 0.0)
                                      for i, n in REGIME_NAMES.items()},
                    "source": "baseline_sem_pericia"}

        # Inferência
        try:
            seq_mean = sequences.mean(axis=0, keepdims=True)  # (1, T, F)
            tensor = torch.FloatTensor(seq_mean).to(self.device)
            with torch.no_grad():
                logits, probs_t = self.model(tensor)
                probs = probs_t.cpu().numpy()[0]

            regime_id = int(np.argmax(probs))
            regime_name = REGIME_NAMES.get(regime_id, "trend")
            confidence = float(probs[regime_id])

            return {
                "regime": regime_name,
                "regime_id": regime_id,
                "confidence": confidence,
                "probabilities": {REGIME_NAMES[i]: float(p) for i, p in enumerate(probs)},
                "source": "transformer",
            }
        except Exception as e:
            log.warning(f"Erro na inferência do regime: {e}")
            return self._fallback_regime()

    async def start(self):
        # Carrega a memoria ANTES de o bootstrap decidir adiar o retreino.
        # O comportamento anterior encontrava o arquivo no disco, pulava o
        # treino, mas deixava ``self.model`` vazio durante todo o processo.
        self._load_or_build()
        log.info(f"RegimeModel iniciado (device={self.device})")

    async def stop(self):
        log.info("RegimeModel parado")
