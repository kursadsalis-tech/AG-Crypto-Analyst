"""Stable data contract for probabilistic price forecasts.

This module intentionally contains no model.  It defines the boundary between
research/forecast generation and the Decision Engine so that future model work
cannot silently change the meaning of a product forecast.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
import re
from typing import Any


CONTRACT_VERSION = "FORECAST_CONTRACT_V1"
MODEL_FAMILY = "POOLED_RIDGE_DISTRIBUTION_V1"
HORIZONS = (1, 4, 24)
ACTIONS = ("AL", "SAT", "BEKLE")
CALIBRATION_STATES = (
    "DEVELOPMENT_ONLY",
    "FORWARD_SHADOW",
    "LIMITED_ALERT",
    "PRODUCTION_ELIGIBLE",
    "REJECTED",
)
RISK_STATES = ("NORMAL", "CAUTION", "DEFENSIVE", "LOCKDOWN", "DATA_BLOCKED")
REFERENCE_PRICE_BASIS = "LAST_CLOSED_1H_CLOSE"
ENTRY_RULE = "NEXT_1H_OPEN"
PROBABILITY_EVENT = "NEXT_OPEN_TO_HORIZON_CLOSE_BEYOND_COST_AND_MIN_MOVE"


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ForecastRecord:
    symbol: str
    as_of: str
    feature_cutoff: str
    due_time: str
    horizon_hours: int
    current_price: float
    target_price: float
    price_p10: float
    price_p90: float
    interval_probability: float
    round_trip_cost_bps: float
    minimum_move_bps: float
    action_probability_threshold: float
    target_reference: str
    entry_rule: str
    probability_event: str
    p_up_net: float
    p_down_net: float
    p_neutral: float
    expected_net_return_pct: float
    shadow_action: str
    risk_adjusted_action: str
    risk_eligible: bool
    risk_state: str
    risk_cap_pct: float
    model_version: str
    artifact_hash: str
    calibration_state: str
    production_eligible: bool
    reason_codes: tuple[str, ...]
    contract_version: str = CONTRACT_VERSION

    def validate(self) -> "ForecastRecord":
        if self.contract_version != CONTRACT_VERSION:
            raise ValueError("unknown forecast contract version")
        if self.horizon_hours not in HORIZONS:
            raise ValueError("unsupported forecast horizon")
        if self.shadow_action not in ACTIONS:
            raise ValueError("unsupported forecast action")
        if self.risk_adjusted_action not in ACTIONS:
            raise ValueError("unsupported risk-adjusted action")
        if self.calibration_state not in CALIBRATION_STATES:
            raise ValueError("unsupported calibration state")
        if self.risk_state not in RISK_STATES:
            raise ValueError("unsupported risk state")
        if self.target_reference != REFERENCE_PRICE_BASIS:
            raise ValueError("unsupported target-price reference")
        if self.entry_rule != ENTRY_RULE:
            raise ValueError("unsupported entry rule")
        if self.probability_event != PROBABILITY_EVENT:
            raise ValueError("unsupported probability event")
        numeric = (
            self.current_price, self.target_price, self.price_p10,
            self.price_p90, self.interval_probability, self.round_trip_cost_bps,
            self.minimum_move_bps, self.action_probability_threshold,
            self.p_up_net, self.p_down_net, self.p_neutral,
            self.expected_net_return_pct, self.risk_cap_pct,
        )
        if any(not math.isfinite(float(value)) for value in numeric):
            raise ValueError("forecast numeric fields must be finite")
        if self.current_price <= 0 or self.target_price <= 0:
            raise ValueError("forecast prices must be positive")
        if not 0 < self.price_p10 <= self.price_p90:
            raise ValueError("invalid price interval")
        if not self.price_p10 <= self.target_price <= self.price_p90:
            raise ValueError("target price must be inside the prediction interval")
        if not 0.0 < self.interval_probability < 1.0:
            raise ValueError("interval probability must be between zero and one")
        if self.round_trip_cost_bps < 0 or self.minimum_move_bps < 0:
            raise ValueError("forecast cost thresholds cannot be negative")
        if not 0.5 < self.action_probability_threshold < 1.0:
            raise ValueError("invalid action probability threshold")
        probabilities = (self.p_up_net, self.p_down_net, self.p_neutral)
        if any(value < 0.0 or value > 1.0 for value in probabilities):
            raise ValueError("forecast probabilities must be in [0, 1]")
        if abs(sum(probabilities) - 1.0) > 1e-6:
            raise ValueError("forecast probabilities must sum to one")
        if not 0.0 <= self.risk_cap_pct <= 100.0:
            raise ValueError("risk cap must be in [0, 100]")

        as_of = _parse_time(self.as_of)
        cutoff = _parse_time(self.feature_cutoff)
        due = _parse_time(self.due_time)
        if cutoff > as_of:
            raise ValueError("feature cutoff cannot be after forecast time")
        if due <= as_of:
            raise ValueError("forecast due time must be after forecast time")
        expected_due = as_of.timestamp() + self.horizon_hours * 3600.0
        if abs(due.timestamp() - expected_due) > 1.0:
            raise ValueError("due time does not match forecast horizon")
        if self.production_eligible and self.calibration_state != "PRODUCTION_ELIGIBLE":
            raise ValueError("only a production-eligible calibration may enable product use")
        if not str(self.model_version).strip() or not str(self.artifact_hash).strip():
            raise ValueError("model version and artifact hash are required")
        if self.production_eligible and not re.fullmatch(
            r"[0-9a-f]{64}", self.artifact_hash
        ):
            raise ValueError("production artifact hash must be a SHA-256 digest")
        if self.shadow_action == "AL" and (
            self.p_up_net < self.action_probability_threshold
            or self.expected_net_return_pct <= 0
        ):
            raise ValueError("AL requires calibrated up probability and positive net expectation")
        if self.shadow_action == "SAT" and (
            self.p_down_net < self.action_probability_threshold
            or self.expected_net_return_pct <= 0
        ):
            raise ValueError("SAT requires calibrated down probability and positive avoided-loss expectation")
        if self.shadow_action == "BEKLE" and abs(self.expected_net_return_pct) > 1e-12:
            raise ValueError("BEKLE must carry zero actionable expected net return")
        if self.risk_eligible and self.risk_state not in {"NORMAL", "CAUTION", "DEFENSIVE"}:
            raise ValueError("blocked global risk states cannot be eligible")
        if self.risk_adjusted_action != "BEKLE" and (
            not self.risk_eligible or self.risk_cap_pct <= 0
        ):
            raise ValueError("risk-adjusted action requires positive risk capacity")
        if self.risk_adjusted_action not in {"BEKLE", self.shadow_action}:
            raise ValueError("risk layer may veto a forecast but cannot invent or reverse direction")
        if not self.risk_eligible and self.risk_adjusted_action != "BEKLE":
            raise ValueError("risk veto must force the adjusted action to BEKLE")
        return self

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        payload = asdict(self)
        payload["reason_codes"] = list(self.reason_codes)
        return payload


def build_forecast_record(**values: Any) -> ForecastRecord:
    """Build and fail-closed validate a forecast at a module boundary."""
    reasons = values.get("reason_codes", ())
    values["reason_codes"] = tuple(str(item) for item in reasons)
    return ForecastRecord(**values).validate()
