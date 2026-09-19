from pathlib import Path

import pandas as pd
import pytest

from agent_market_style_adaptive.config import load_config
from agent_market_style_adaptive.portfolio import RiskOverlay, target_allocation
from agent_market_style_adaptive.backtest import Backtester
from agent_market_style_adaptive.regime import confirm_regimes


CONFIG = Path(__file__).parents[1] / "config" / "default.yaml"


def test_default_allocations_sum_to_one():
    config = load_config(CONFIG)
    assert all(sum(weights.values()) == pytest.approx(1) for weights in config["allocation"].values())


def test_regime_confirmation_and_immediate_crisis():
    raw = pd.Series(["warm", "cold", "cold", "warm", "overheated", "overheated", "overheated", "crisis", "warm"])
    result = confirm_regimes(raw, 3).tolist()
    assert result[:6] == ["warm"] * 6
    assert result[6] == "overheated"
    assert result[7] == "crisis"
    assert result[8] == "crisis"


def test_drawdown_overlay_and_weight_controls():
    config = load_config(CONFIG)
    overlay = RiskOverlay(config["risk"])
    overlay.update(100)
    tier, _ = overlay.update(89)
    assert tier == "warning"
    warning = target_allocation("adaptive", "warm", config["allocation"], tier, config["risk"])
    assert warning["small_cap"] == pytest.approx(0.20)
    tier, _ = overlay.update(84)
    assert tier == "crisis"
    crisis = target_allocation("adaptive", "warm", config["allocation"], tier, config["risk"])
    assert crisis["small_cap"] == 0
    assert crisis["cash"] >= 0.60
    assert sum(crisis.values()) == pytest.approx(1)


def test_recovery_is_staged_across_two_weeks():
    config = load_config(CONFIG)
    risk = dict(config["risk"])
    risk.update({"recovery_ma": 2, "recovery_confirmation_days": 2, "recovery_week_days": 2})
    overlay = RiskOverlay(risk)
    for nav in (100, 89, 95, 96):
        tier, _ = overlay.update(nav)
    assert tier == "recovery_1"
    overlay.update(97)
    tier, _ = overlay.update(98)
    assert tier == "recovery_2"
    overlay.update(99)
    tier, _ = overlay.update(100)
    assert tier == "normal"


def test_sleeve_risk_budget_releases_capital_to_cash():
    engine = object.__new__(Backtester)
    engine.config = {
        "small_cap": {"target_volatility": .20},
        "etf": {},
        "blue_chip": {},
    }
    stock = pd.DataFrame({"symbol": ["A"], "volatility": [.40]}).set_index("symbol", drop=False)
    fund = pd.DataFrame(columns=["symbol", "volatility"]).set_index("symbol")
    weights, evidence = engine._apply_sleeve_risk_budget(
        {"small_cap": .40, "etf": .40, "blue_chip": .20, "cash": 0.0},
        {"small_cap": ["A"], "etf": [], "blue_chip": []}, stock, fund,
    )
    assert weights["small_cap"] == pytest.approx(.20)
    assert weights["cash"] == pytest.approx(.20)
    assert evidence[0]["scale"] == pytest.approx(.50)


def test_relative_inverse_vol_budget_favors_lower_risk_sleeve_and_is_auditable():
    engine = object.__new__(Backtester)
    engine.config = {
        "small_cap": {}, "etf": {}, "blue_chip": {},
        "sleeve_risk_budget": {"method": "relative_inverse_vol", "min_scale": 0.0, "max_scale": 3.0},
    }
    stock = pd.DataFrame({"symbol": ["S", "B"], "volatility": [.40, .10]}).set_index("symbol", drop=False)
    fund = pd.DataFrame({"symbol": ["E"], "volatility": [.20]}).set_index("symbol", drop=False)
    weights, evidence = engine._apply_sleeve_risk_budget(
        {"small_cap": .4, "etf": .4, "blue_chip": .2, "cash": 0.0},
        {"small_cap": ["S"], "etf": ["E"], "blue_chip": ["B"]}, stock, fund,
    )
    assert weights["blue_chip"] > weights["small_cap"]
    assert sum(weights.values()) == pytest.approx(1.0)
    assert {row["method"] for row in evidence} == {"relative_inverse_vol"}


def test_structural_config_keeps_top_level_sleeves_fixed():
    config = load_config(Path(__file__).parents[1] / "config" / "optimized.rebalance.yaml")
    expected = {"small_cap": .4, "etf": .4, "blue_chip": .2, "cash": 0.0}
    for regime in ("overheated", "warm", "cold", "crisis"):
        weights = target_allocation("adaptive", regime, config["allocation"], "normal", config["risk"])
        assert weights == pytest.approx(expected)
    crisis = target_allocation("adaptive", "warm", config["allocation"], "crisis", config["risk"])
    assert crisis["cash"] == pytest.approx(config["risk"]["crisis_min_cash"])
    assert crisis["small_cap"] == pytest.approx(0.0)

    engine = object.__new__(Backtester)
    engine.config = {"small_cap": {}, "etf": {}, "blue_chip": {}, "sleeve_risk_budget": {"method": "fixed"}}
    weights, evidence = engine._apply_sleeve_risk_budget(
        expected, {"small_cap": ["S"], "etf": ["E"], "blue_chip": ["B"]},
        pd.DataFrame(), pd.DataFrame(),
    )
    assert weights == expected
    assert {row["method"] for row in evidence} == {"fixed"}
