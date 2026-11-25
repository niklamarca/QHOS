"""
QUANT HPC OS — Hierarchical Risk Parity (HRP)
Implementação do algoritmo de Marcos López de Prado.
Método de alocação de portfólio robusto que minimiza correlação e concentração.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import squareform
from typing import Dict, List, Optional, Tuple
from core.config import config
from core.logger import get_logger

log = get_logger("HRP")


class HierarchicalRiskParity:
    """
    Hierarchical Risk Parity (HRP) — López de Prado (2016).

    Vantagens vs. Markowitz:
    - Não requer inversão da matriz de covariância (instável)
    - Robusto a outliers e dados escassos
    - Diversificação genuína (não concentra em correlações espúrias)
    - Funciona bem com muitos ativos/estratégias
    """

    def __init__(self, min_weight: float = 0.01, max_weight: float = 0.30,
                 shrinkage: Optional[float] = None):
        self.min_weight = min_weight
        self.max_weight = max_weight
        # Shrinkage da correlacao (Ledoit-Wolf, alvo correlacao-constante).
        # Estabiliza estimacao com poucas/curtas series. None => default do config.
        self.shrinkage = (
            getattr(config, "hrp_shrinkage", 0.0) if shrinkage is None else shrinkage
        )

    def fit(self, returns: pd.DataFrame) -> Dict[str, float]:
        """
        Calcula pesos HRP para um DataFrame de retornos.

        Args:
            returns: DataFrame (T x N) de retornos. Colunas = assets/estratégias.

        Returns:
            Dict[asset_name, weight]  — soma 1.0
        """
        if returns.empty or returns.shape[1] < 2:
            if not returns.empty:
                n = returns.shape[1]
                return dict(zip(returns.columns, [1.0 / n] * n))
            return {}

        returns = returns.dropna()
        if len(returns) < 10:
            n = returns.shape[1]
            return dict(zip(returns.columns, [1.0 / n] * n))

        n_assets = returns.shape[1]
        if len(returns) < 2 * n_assets:
            log.warning(
                f"HRP instavel: {len(returns)} obs para {n_assets} ativos "
                f"(ideal >> {2*n_assets}). Correlacao ruidosa; shrinkage={self.shrinkage}."
            )

        # 1. Matriz de correlação (com shrinkage opcional p/ estabilidade)
        corr = returns.corr()
        corr = corr.clip(-0.999, 0.999)
        corr_arr = corr.to_numpy().copy()
        np.fill_diagonal(corr_arr, 1.0)
        lam = float(self.shrinkage)
        if lam > 0 and corr_arr.shape[0] > 2:
            m = corr_arr.shape[0]
            offdiag = corr_arr[~np.eye(m, dtype=bool)]
            rbar = float(offdiag.mean())               # correlacao media (alvo)
            target = np.full_like(corr_arr, rbar)
            np.fill_diagonal(target, 1.0)
            corr_arr = (1.0 - lam) * corr_arr + lam * target
            np.fill_diagonal(corr_arr, 1.0)
        corr = pd.DataFrame(corr_arr, index=corr.index, columns=corr.columns)

        # 2. Matriz de distância (correlação → distância)
        dist = np.sqrt((1 - corr) / 2)
        dist_sq = squareform(dist.to_numpy())

        # 3. Clustering hierárquico (single linkage)
        link = linkage(dist_sq, method="single")

        # 4. Quasi-diagonal ordering (reordena para aproximar bloco diagonal)
        sort_ix = self._get_quasi_diag(link, corr.shape[0])
        sorted_names = [returns.columns[i] for i in sort_ix]

        # 5. Alocação recursiva bisecção
        cov = returns.cov()
        weights = self._recursive_bisection(cov, sorted_names)

        # 6. Aplica limites min/max
        weights = self._apply_bounds(weights)

        return {k: float(round(v, 6)) for k, v in weights.items()}

    def _get_quasi_diag(self, link: np.ndarray, n: int) -> List[int]:
        """Reordena itens pelo dendrograma para aproximar estrutura diagonal.
        
        Usa DFS com stack: nós internos (>= n) expandem filhos,
        nós folha (< n) vão direto para o resultado.
        Raiz scipy linkage = índice 2*n-2.
        """
        link = link.astype(int)
        result = []
        stack = [2 * n - 2]  # raiz do dendrograma

        while stack:
            node = stack.pop()
            if node >= n:
                # Nó interno: empurra filhos (direita primeiro → esquerda processada antes)
                i = node - n
                stack.append(int(link[i, 1]))
                stack.append(int(link[i, 0]))
            else:
                # Nó folha: adiciona ao resultado
                result.append(node)

        return result

    def _get_cluster_var(self, cov: pd.DataFrame, cluster: List[str]) -> float:
        """Variância do cluster com IVP (Inverse Variance Portfolio)."""
        sub_cov = cov.loc[cluster, cluster]
        ivp_w = self._ivp_weights(sub_cov)
        w_arr = np.array([ivp_w.get(c, 0) for c in cluster])
        return float(w_arr @ sub_cov.values @ w_arr)

    def _ivp_weights(self, cov: pd.DataFrame) -> Dict[str, float]:
        """Inverse Variance Portfolio weights."""
        ivp = 1.0 / (np.diag(cov.values) + 1e-10)
        total = ivp.sum()
        return dict(zip(cov.columns, ivp / total))

    def _recursive_bisection(
        self, cov: pd.DataFrame, sorted_names: List[str]
    ) -> Dict[str, float]:
        """Alocação recursiva por bisecção do dendrograma."""
        weights = {n: 1.0 for n in sorted_names}
        clusters = [sorted_names]

        # Forma canonica (Lopez de Prado): processa todos os clusters do nivel,
        # acumula os filhos em lista NOVA. Sem mutar a lista durante a iteracao
        # (o codigo antigo fazia clusters.remove dentro do for — armadilha).
        while clusters:
            next_clusters = []
            for cluster in clusters:
                if len(cluster) <= 1:
                    continue
                mid = len(cluster) // 2
                left = cluster[:mid]
                right = cluster[mid:]

                var_left  = self._get_cluster_var(cov, left)
                var_right = self._get_cluster_var(cov, right)
                alpha = 1 - var_left / (var_left + var_right + 1e-10)

                for item in left:
                    weights[item] *= alpha
                for item in right:
                    weights[item] *= (1 - alpha)

                next_clusters.append(left)
                next_clusters.append(right)
            clusters = next_clusters

        return weights

    def _apply_bounds(self, weights: Dict[str, float]) -> Dict[str, float]:
        """Aplica limites min/max e renormaliza (iterativo ate estabilizar).

        O codigo antigo aplicava min->max->renorm UMA vez, e a renormalizacao
        podia reviolar os limites. Itera ate convergir. Usa min efetivo p/
        nao ficar inviavel quando min_weight*N > 1.
        """
        if not weights:
            return weights
        n = len(weights)
        eff_min = min(self.min_weight, 1.0 / n)   # evita inviabilidade
        w = dict(weights)
        for _ in range(20):
            for k in w:
                w[k] = min(max(w[k], eff_min), self.max_weight)
            total = sum(w.values())
            if total <= 0:
                break
            w = {k: v / total for k, v in w.items()}
            if all(eff_min - 1e-9 <= v <= self.max_weight + 1e-9 for v in w.values()):
                break
        return w

    def fit_with_regime(
        self,
        returns: pd.DataFrame,
        regime: str,
        regime_weights: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        """
        HRP com ajuste por regime de mercado.
        Em vol_expansion/liq_stress: reduz pesos mais voláteis.
        """
        base_weights = self.fit(returns)
        if not base_weights:
            return base_weights

        # Ajuste por regime
        if regime in ("vol_expansion", "liq_stress"):
            # Reduz pesos proporcionalmente à volatilidade de cada ativo
            vols = returns.std()
            adjustment = 1.0 / (vols + 1e-8)
            adj_norm = adjustment / adjustment.sum()

            blended = {}
            for asset in base_weights:
                adj = float(adj_norm.get(asset, 1.0 / len(base_weights)))
                # 50% HRP + 50% vol-adjusted
                blended[asset] = 0.5 * base_weights[asset] + 0.5 * adj

            total = sum(blended.values())
            base_weights = {k: v / total for k, v in blended.items()}

        return self._apply_bounds(base_weights)
