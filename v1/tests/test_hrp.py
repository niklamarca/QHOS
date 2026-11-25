"""Hierarchical Risk Parity weights: they sum to one, respect the bounds when the bounds are feasible, and favour
low-volatility assets. Uses seeded synthetic returns only."""
import numpy as np
import pandas as pd
import pytest

from portfolio.hrp import HierarchicalRiskParity


def synthetic_returns(vols, n_obs=500, seed=0, common=0.0):
    """Returns with the given daily volatilities; `common` mixes in a shared factor to create correlation."""
    rng = np.random.default_rng(seed)
    factor = rng.normal(0, 1, n_obs)
    data = {}
    for i, vol in enumerate(vols):
        own = rng.normal(0, 1, n_obs)
        data[f"A{i}"] = vol * (np.sqrt(1 - common ** 2) * own + common * factor)
    return pd.DataFrame(data)


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("n_assets", [4, 6, 10])
def test_weights_sum_to_one_and_stay_inside_the_bounds(seed, n_assets):
    vols = np.linspace(0.005, 0.03, n_assets)
    hrp = HierarchicalRiskParity(min_weight=0.01, max_weight=0.30, shrinkage=0.1)
    weights = hrp.fit(synthetic_returns(vols, seed=seed, common=0.3))

    assert set(weights) == {f"A{i}" for i in range(n_assets)}
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-5)
    # 1e-4 slack: see the xfail below, the clip-and-renormalise loop stops a little above the cap
    assert all(0.01 - 1e-4 <= w <= 0.30 + 1e-4 for w in weights.values())


@pytest.mark.xfail(strict=True, reason=(
    "_apply_bounds clips and renormalises up to 20 times, but that loop converges slowly and stops above the cap: "
    "with 4 assets and max_weight=0.30 the top weight comes out at 0.3041. Capping, then handing the excess only to "
    "the uncapped assets, lands exactly in at most N passes."))
def test_the_max_weight_cap_holds_exactly():
    hrp = HierarchicalRiskParity(min_weight=0.01, max_weight=0.30, shrinkage=0.0)
    weights = hrp.fit(synthetic_returns([0.005, 0.01, 0.02, 0.06], seed=8))
    assert max(weights.values()) <= 0.30 + 1e-6


def test_lower_volatility_assets_get_more_weight():
    hrp = HierarchicalRiskParity(min_weight=0.0, max_weight=1.0, shrinkage=0.0)
    weights = hrp.fit(synthetic_returns([0.005, 0.01, 0.02, 0.04], seed=3))
    ordered = [weights[f"A{i}"] for i in range(4)]
    assert ordered == sorted(ordered, reverse=True)


def test_identical_uncorrelated_assets_get_roughly_equal_weight():
    hrp = HierarchicalRiskParity(min_weight=0.0, max_weight=1.0, shrinkage=0.0)
    weights = hrp.fit(synthetic_returns([0.01] * 4, n_obs=5000, seed=1))
    assert all(w == pytest.approx(0.25, abs=0.03) for w in weights.values())


def test_same_input_gives_the_same_weights():
    returns = synthetic_returns([0.01, 0.02, 0.015, 0.03], seed=9, common=0.4)
    hrp = HierarchicalRiskParity(shrinkage=0.1)
    assert hrp.fit(returns) == hrp.fit(returns.copy())


def test_empty_returns_give_no_weights():
    assert HierarchicalRiskParity().fit(pd.DataFrame()) == {}


def test_a_single_asset_gets_everything():
    returns = synthetic_returns([0.01])
    assert HierarchicalRiskParity().fit(returns) == {"A0": 1.0}


def test_too_few_observations_fall_back_to_equal_weight():
    returns = synthetic_returns([0.01, 0.05, 0.02], n_obs=9)
    weights = HierarchicalRiskParity().fit(returns)
    assert weights == {"A0": pytest.approx(1 / 3), "A1": pytest.approx(1 / 3), "A2": pytest.approx(1 / 3)}


def test_rows_with_missing_values_are_dropped_before_fitting():
    returns = synthetic_returns([0.01, 0.02, 0.03, 0.04], seed=5)
    with_gaps = returns.copy()
    with_gaps.iloc[::7, 1] = np.nan
    weights = HierarchicalRiskParity(shrinkage=0.0).fit(with_gaps)
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-5)
    assert not any(np.isnan(w) for w in weights.values())


def test_quasi_diagonal_order_is_a_permutation_of_all_assets():
    from scipy.cluster.hierarchy import linkage
    from scipy.spatial.distance import squareform

    returns = synthetic_returns([0.01] * 7, seed=2, common=0.5)
    dist = np.sqrt((1 - returns.corr().clip(-0.999, 0.999)) / 2).to_numpy().copy()
    np.fill_diagonal(dist, 0.0)
    order = HierarchicalRiskParity()._get_quasi_diag(linkage(squareform(dist), "single"), 7)
    assert sorted(order) == list(range(7))


@pytest.mark.parametrize("n_assets, min_weight", [(5, 0.25), (10, 0.2)])
def test_infeasible_minimum_is_relaxed_to_equal_weight_floor(n_assets, min_weight):
    """min_weight * N > 1 cannot hold; the floor becomes 1/N, which forces (almost) equal weights."""
    hrp = HierarchicalRiskParity(min_weight=min_weight, max_weight=1.0)
    raw = {f"A{i}": w for i, w in enumerate(np.linspace(0.01, 0.5, n_assets))}
    bounded = hrp._apply_bounds(raw)
    assert sum(bounded.values()) == pytest.approx(1.0)
    # abs=5e-3 for the same slow convergence as the cap (with 10 assets the floor ends at 0.0996, not 0.1)
    assert all(w == pytest.approx(1 / n_assets, abs=5e-3) for w in bounded.values())


def test_with_too_few_assets_for_the_cap_the_cap_is_not_enforced():
    """Characterisation: with max_weight=0.30 and 3 assets the cap cannot hold (3 x 0.30 < 1). The code silently
    returns equal weights of 1/3 instead of warning. Worth knowing before trusting max_weight on a small portfolio."""
    hrp = HierarchicalRiskParity(min_weight=0.01, max_weight=0.30)
    weights = hrp.fit(synthetic_returns([0.005, 0.01, 0.04], seed=4))
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-5)
    assert all(w == pytest.approx(1 / 3, abs=1e-5) for w in weights.values())


@pytest.mark.xfail(strict=True, reason=(
    "fit_with_regime says stress regimes cut the most volatile weights, but it blends 50/50 with 1/vol weights. "
    "1/vol is flatter than HRP (which behaves closer to 1/variance), so when HRP already gives the volatile asset "
    "little weight the blend raises it: here A3 goes from 0.5% to 2.4% in liq_stress."))
def test_stress_regime_moves_weight_away_from_the_most_volatile_asset():
    returns = synthetic_returns([0.005, 0.01, 0.02, 0.06], seed=8, common=0.2)
    hrp = HierarchicalRiskParity(min_weight=0.0, max_weight=1.0, shrinkage=0.0)
    normal = hrp.fit_with_regime(returns, "trend")
    stress = hrp.fit_with_regime(returns, "liq_stress")
    assert stress["A3"] < normal["A3"]


def test_stress_regime_weights_still_sum_to_one():
    returns = synthetic_returns([0.005, 0.01, 0.02, 0.06], seed=8, common=0.2)
    stress = HierarchicalRiskParity(min_weight=0.0, max_weight=1.0).fit_with_regime(returns, "vol_expansion")
    assert sum(stress.values()) == pytest.approx(1.0)


def test_non_stress_regime_matches_plain_hrp():
    returns = synthetic_returns([0.005, 0.01, 0.02, 0.06], seed=8)
    hrp = HierarchicalRiskParity(min_weight=0.0, max_weight=1.0, shrinkage=0.0)
    plain = hrp.fit(returns)
    regime = hrp.fit_with_regime(returns, "mean_reversion")
    assert regime == {k: pytest.approx(v, abs=1e-6) for k, v in plain.items()}
