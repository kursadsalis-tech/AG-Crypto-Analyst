"""Real-capital readiness checks; the production code gate remains closed."""
from __future__ import annotations

import math
from dataclasses import dataclass


# Opening this constant requires a separately reviewed code change. Runtime
# configuration, the PAPER agent and an approval payload cannot change it.
REAL_CAPITAL_CODE_GATE_OPEN = False


@dataclass(frozen=True)
class ExecutionTrace:
    symbol: str
    side: str
    status: str
    reason: str
    price: float | None = None
    quantity: float | None = None
    fee: float | None = None


@dataclass(frozen=True)
class ReconciliationResult:
    passed: bool
    compared: int
    mismatches: tuple[str, ...]


@dataclass(frozen=True)
class PromotionAttestations:
    user_approval_id: str | None = None
    orchestrator_attestation_id: str | None = None


@dataclass(frozen=True)
class CapitalGateDecision:
    allowed: bool
    reasons: tuple[str, ...]


def _close(left, right, tolerance):
    if left is None or right is None:
        return left is right
    return (
        math.isfinite(float(left))
        and math.isfinite(float(right))
        and abs(float(left) - float(right)) <= tolerance
    )


def reconcile_execution_traces(
    paper: list[ExecutionTrace], dry_run: list[ExecutionTrace],
    *, price_tolerance: float = 1e-8, quantity_tolerance: float = 1e-8,
    fee_tolerance: float = 1e-8,
) -> ReconciliationResult:
    """Compare ordered PAPER and exchange dry-run traces fail-closed."""
    mismatches = []
    if not paper or not dry_run:
        mismatches.append("EMPTY_RECONCILIATION_INPUT")
    if len(paper) != len(dry_run):
        mismatches.append("TRACE_COUNT_MISMATCH")
    for index, (left, right) in enumerate(zip(paper, dry_run)):
        if (
            left.symbol != right.symbol or left.side != right.side
            or left.status != right.status or left.reason != right.reason
        ):
            mismatches.append(f"TRACE_{index}_DECISION_MISMATCH")
            continue
        if not _close(left.price, right.price, price_tolerance):
            mismatches.append(f"TRACE_{index}_PRICE_MISMATCH")
        if not _close(left.quantity, right.quantity, quantity_tolerance):
            mismatches.append(f"TRACE_{index}_QUANTITY_MISMATCH")
        if not _close(left.fee, right.fee, fee_tolerance):
            mismatches.append(f"TRACE_{index}_FEE_MISMATCH")
    return ReconciliationResult(
        passed=not mismatches, compared=min(len(paper), len(dry_run)),
        mismatches=tuple(mismatches),
    )


def evaluate_capital_gate(
    reconciliation: ReconciliationResult,
    attestations: PromotionAttestations,
) -> CapitalGateDecision:
    reasons = []
    if not reconciliation.passed or reconciliation.compared <= 0:
        reasons.append("RECONCILIATION_NOT_PASSED")
    if not (attestations.user_approval_id or "").strip():
        reasons.append("EXPLICIT_USER_APPROVAL_MISSING")
    if not (attestations.orchestrator_attestation_id or "").strip():
        reasons.append("ORCHESTRATOR_ATTESTATION_MISSING")
    if not REAL_CAPITAL_CODE_GATE_OPEN:
        reasons.append("REVIEWED_CODE_GATE_CLOSED")
    return CapitalGateDecision(allowed=not reasons, reasons=tuple(reasons))
