"""Versioned goal shared by generation, review, automation and evaluation."""

QUALITY_GOAL_VERSION = "application-quality-v1"
QUALITY_GOAL = """Shared quality goal (application-quality-v1):
Produce grounded, risk-focused tests that expose application defects and prevent regressions.
Preserve source obligations and defect-sensitive assertions across every handoff and refinement.
Judge observable behavior, source fidelity and explicit execution prerequisites; case counts,
passing scripts and model self-scores alone do not establish application quality.
Record missing actor roles, fixture identities, variables, setup and cleanup as prerequisites.
Synthetic input values may be designed explicitly; never invent provisioned users, credentials,
existing records or product behavior. Missing facts remain unknown or blocked.
Treat downstream findings as evidence: distinguish application defects from test-data, access,
environment and script failures. Backfill relevant artifacts and propose scoped reusable lessons.
Never weaken an assertion to hide an application defect. A correction requires revalidation;
saving a prerequisite or lesson is not proof of successful execution.
Use credential references only; resolve secrets at execution time, never in prompts or memory.
"""
