# AI Operating Guide

## Fast Path

For low-risk local work: inspect relevant files, make the smallest correct change, review, validate when practical, and report gaps.

## Route

Task class:

- documentation: docs/comments/governance text
- feature: new behavior or capability
- bugfix: faulty behavior correction
- refactor: structure change preserving behavior
- review: findings only
- release: packaging, versioning, publishing
- maintenance: repo setup or housekeeping

Risk:

- low: local/docs-only/easy inspection/no public or architecture impact
- medium: bounded behavior change, multi-file local edit, partial validation, relevant dirty files
- high: architecture boundary, public interface, release/destructive action, broad refactor, low context confidence

Escalate when uncertain.

## Execute

1. State objective, success criteria, and non-goals when they affect correctness.
2. Inspect before editing.
3. Preserve user changes and architecture.
4. Patch the source of the issue; do not mask defects with fallback behavior.
5. Keep changes scoped.
6. Validate the primary path when behavior changes.
7. Review for regressions, scope drift, and missing tests.
8. Report validation status and residual risk.

## Validation Efficiency

- Do not add or expand automated tests by default. Add a test only when the user explicitly requests it or when one basic public input-output case is necessary to establish that the primary boundary works.
- Keep tests at observable boundaries: provide representative input through a public entry point and assert the resulting output, serialized contract, or externally visible state.
- Do not test private methods, internal call counts or order, temporary orchestration state, or behavior that has been replaced by a mock. Coverage percentage is not a reason to add a test.
- Prefer direct acceptance checks, static checks, and one minimal input-output smoke case over large mocked unit suites.
- Do not rerun an unchanged test suite. Run only the smallest relevant check after a meaningful change, and report intentionally skipped validation.
- Preserve upstream tests unless they directly conflict with the current architecture. Clean project-added transition tests when they only lock in obsolete implementation details.

## Checklists

- implementation: scoped, style preserved, assumptions visible, unrelated files untouched
- review: correctness, regressions, validation gaps, architecture boundaries, severity order
- bugfix: root cause checked, primary path validated, fallback not treated as proof
- refactor: behavior preserved, boundaries stable, rollback safe
- safety: no secrets, no unapproved destructive/public action, user work preserved

## Continuity

Re-check task class, risk, scope, workflow, assumptions, validation, and correction state after user changes, validation failure, context resume, unexpected worktree changes, or major implementation phases.

## State

Update `.ai/state.yaml` for medium/high-risk work when useful, and for high-risk work when required to keep assumptions visible. Ask before recording long-lived architecture decisions or persistent project risks.

Do not update state performatively.
