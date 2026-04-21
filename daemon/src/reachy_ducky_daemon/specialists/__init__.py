"""Phase A subagent specialists.

Each specialist is a workflow-style wrapper (per Anthropic's
"Building Effective Agents" taxonomy): deterministic Python pre-loads
the load-bearing context, then hands a single scoped prompt to the
brain. Step ordering never depends on agent compliance.

Currently wired:

* :class:`~reachy_ducky_daemon.specialists.plan_reviewer.PlanReviewer` —
  plan-vs-diff drift detector.
"""

from __future__ import annotations

from .plan_reviewer import PlanReviewer

__all__ = ["PlanReviewer"]
