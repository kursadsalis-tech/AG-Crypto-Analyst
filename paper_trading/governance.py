"""Fail-closed authority contract for the autonomous PAPER agent."""
from __future__ import annotations

from dataclasses import asdict, dataclass


PAPER_EXECUTION_MODE = "PAPER_ONLY"
AUTHORIZED_POLICY_VERSION = "PAPER_OPPORTUNITY_POLICY_V2_EXECUTION_REALISM"
AUTHORIZED_INITIAL_CAPITAL = 10_000.0


@dataclass(frozen=True)
class AgentAuthority:
    execution_mode: str = PAPER_EXECUTION_MODE
    may_recommend: bool = True
    may_send_real_orders: bool = False
    may_promote_model: bool = False
    may_promote_policy: bool = False
    may_override_risk: bool = False
    may_change_capital: bool = False
    promotion_authority: str = "USER_AND_PRIMARY_ORCHESTRATOR_ONLY"
    real_capital_gate: str = "RECONCILIATION_APPROVAL_ATTESTATION_AND_CODE_REVIEW_REQUIRED"


AUTHORITY = AgentAuthority()


def authority_contract() -> dict:
    return asdict(AUTHORITY)


def validate_agent_authority(
    *, execution_mode: str, policy_version: str, initial_capital: float,
) -> dict:
    """Reject any runtime configuration that expands the agent's authority."""
    if execution_mode != PAPER_EXECUTION_MODE:
        raise RuntimeError("GOVERNANCE_BLOCKED_NON_PAPER_MODE")
    if policy_version != AUTHORIZED_POLICY_VERSION:
        raise RuntimeError("GOVERNANCE_BLOCKED_POLICY_PROMOTION")
    if float(initial_capital) != AUTHORIZED_INITIAL_CAPITAL:
        raise RuntimeError("GOVERNANCE_BLOCKED_CAPITAL_CHANGE")
    return authority_contract()
