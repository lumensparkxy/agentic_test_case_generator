# Unfinished generation and targeted repair

A test case must contain product actions and observable expected results. Internal
instructions such as “review the implemented behavior” or “execute the scenario”
are unfinished generation work, even when they have a test-case ID.

Generation responses expose `generation_tasks` separately from `test_cases`.
Each task identifies its requirement IDs, scenario references, reason, and an
optional source test-case ID. Counts and coverage use delivered cases. Missing
scenarios and known placeholder content prevent approval. Deterministic coverage
augmentation no longer fills a model output with generic cases. Model-backed
retry remains available; fallback output cannot approve itself.

Existing snapshots are immutable. Current project and workspace views withhold
known legacy placeholder content and expose repair tasks. Detection uses content,
not an ID prefix: a real test named `TC-FB-001` remains a real test. Exports reject
placeholder content even with a draft override. This is a conservative guard for
known filler patterns and missing steps, not a guarantee of semantic test quality.

## Repair a suite

1. Review and approve current requirements and the affected Use Cases.
2. In Test Cases, choose **Plan targeted repair**.
3. Select the Add/Update recommendations to accept, then apply them.
4. Review the resulting changed tests. Repair does not grant suite approval.

Analysis does not call the model. Apply generates only the selected requirement
and scenario slices, using saved context and the existing bounded generation
pipeline. At most 40 generation recommendations can be selected per request;
duplicate updates/additions are consolidated. Each distinct recommendation is
generated in sequence, with bounded parallel shards inside that generation run.

The apply API requires `base_project_revision`. Ownership, active status, input
snapshot freshness, requirement approval, and snapshot-bound scenario reviews
are checked before model generation. Rejected, incomplete, fallback, or foreign
coverage cannot be committed. The original suite and history remain intact on
failure. Changed artifact versions and the new project snapshot commit in one
revision-guarded transaction. Unchanged test identities and versions are retained.
Generation evidence, guidance references, usage, and billing consumption follow
the targeted run. Repaired tests are saved for review, without a fabricated pass
score. Old invalid snapshot content is retained as historical evidence.

## Validation boundary

The offline generation benchmark checks safe fallback handling: withholding
placeholders, reporting uncovered scenarios, blocking approval, and truthful
counts. Its model-backed quality thresholds remain unchanged. Offline and mocked
browser checks do not establish live model quality or production repair success.
The separate structured-data/guidance rollout work in issue #280 remains held.
