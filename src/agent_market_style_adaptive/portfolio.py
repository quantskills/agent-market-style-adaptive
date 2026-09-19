from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class RiskOverlay:
    config: dict
    peak: float = 0.0
    tier: str = "normal"
    above_ma_days: int = 0
    recovery_days: int = 0
    history: list[float] = field(default_factory=list)

    def update(self, nav: float) -> tuple[str, float]:
        self.history.append(float(nav))
        self.peak = max(self.peak, float(nav))
        drawdown = 1 - nav / self.peak if self.peak else 0.0
        if drawdown >= float(self.config["crisis_drawdown"]):
            self.tier, self.above_ma_days, self.recovery_days = "crisis", 0, 0
        elif drawdown >= float(self.config["warning_drawdown"]):
            self.tier, self.above_ma_days, self.recovery_days = "warning", 0, 0
        elif self.tier in {"warning", "crisis"}:
            window = int(self.config["recovery_ma"])
            ma = pd.Series(self.history).tail(window).mean() if len(self.history) >= window else float("inf")
            self.above_ma_days = self.above_ma_days + 1 if nav > ma else 0
            if self.above_ma_days >= int(self.config["recovery_confirmation_days"]):
                self.tier, self.recovery_days = "recovery_1", 0
        elif self.tier in {"recovery_1", "recovery_2"}:
            self.recovery_days += 1
            week = int(self.config["recovery_week_days"])
            if self.recovery_days >= 2 * week:
                self.tier = "normal"
            elif self.recovery_days >= week:
                self.tier = "recovery_2"
        return self.tier, drawdown


def target_allocation(mode: str, regime: str, allocation_config: dict, risk_tier: str, risk_config: dict) -> dict[str, float]:
    # The structural configuration keeps sleeve budgets fixed at 40/40/20.
    # Regime and drawdown overlays still flow through the engine and are used
    # by each sleeve's selector, but must not silently move capital between
    # the three top-level sleeves.
    if mode != "adaptive":
        raise ValueError("mode must be adaptive")
    base = dict(allocation_config[regime])
    if risk_tier == "warning":
        released = 0.5 * (base["small_cap"] + base["blue_chip"])
        base["small_cap"] *= 0.5
        base["blue_chip"] *= 0.5
        base["cash"] += released
    elif risk_tier == "crisis":
        base["small_cap"] = 0.0
        investable = 1.0 - float(risk_config["crisis_min_cash"])
        denominator = base["etf"] + base["blue_chip"]
        scale = investable / denominator if denominator else 0.0
        base["etf"] *= scale
        base["blue_chip"] *= scale
        base["cash"] = float(risk_config["crisis_min_cash"])
    elif risk_tier in {"recovery_1", "recovery_2"}:
        protected = target_allocation(mode, regime, allocation_config, "warning", risk_config)
        base_weight = 0.5 if risk_tier == "recovery_1" else 0.75
        base = {key: base_weight * base[key] + (1 - base_weight) * protected[key] for key in base}
    total = sum(base.values())
    return {key: value / total for key, value in base.items()}


