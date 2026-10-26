"""Shared contracts owned by slice 01: endpoint profile, conformance case, result envelope, evidence, trace.

Later slices may only add optional fields; breaking changes are raised on issue 01 as a blocker.
"""

from glmkit.contracts.case import CheckSpec, ConformanceCase
from glmkit.contracts.envelope import CheckResult, ItemResult, ResultEnvelope, Summary, TargetInfo
from glmkit.contracts.evidence import Exchange, NormalizedResponse
from glmkit.contracts.profile import EndpointProfile
from glmkit.contracts.trace import SwitchTrace

__all__ = [
    "CheckResult",
    "CheckSpec",
    "ConformanceCase",
    "EndpointProfile",
    "Exchange",
    "ItemResult",
    "NormalizedResponse",
    "ResultEnvelope",
    "Summary",
    "SwitchTrace",
    "TargetInfo",
]
