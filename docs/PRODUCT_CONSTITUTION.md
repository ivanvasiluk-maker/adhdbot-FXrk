# Product Constitution — MASTER PATCH-0

**Priority: supreme.** This is the repository's single highest-priority product document. Product notes, roadmaps, experiments, and implementation convenience cannot override it. Changes to these principles require an explicit new master patch and review; ordinary feature work must not edit them.

## Immutable principles

1. **The product changes behavior, not merely screens.** Every new user-facing scenario must measurably help a person **start**, **return**, **persist**, **recover**, or **master** a skill.
2. **One declared goal.** Every feature specification must include `behavioral_goal: start | return | persist | recover | master` and a `success_metric` (or `measurable_effect`). Missing or invalid declarations block review.
3. **Evidence before complexity.** Learning and ranking engines remain independently gated. A disabled engine must not be silently simulated or exposed.
4. **Quality is explicit.** The active skill-library contour is selected by `ACTIVE_SKILL_QUALITY_LEVEL`; application code must not embed an implicit quality tier.
5. **Commercial terms are configuration.** The base offer is supplied by `BASE_OFFER_EUR`; user-facing flows and analytics must not embed the base price as a magic number.
6. **Safe defaults and reversibility.** Experimental engines default off. A rollout must be reversible through configuration without deleting user history.
7. **The gate is executable.** `core.product_policy.evaluate_feature` is the canonical review decision. A rejected feature does not enter implementation until its specification is corrected.

## Feature specification contract

```yaml
name: resume_after_interruption
behavioral_goal: return
success_metric: percentage of interrupted users resuming within 24 hours
requires_engine: learning  # optional: learning | ranking
```

Run `python scripts/check_product_policy.py path/to/feature.json` in review/CI. It exits non-zero when the constitution rejects the proposal and prints the stable reason code.

## Runtime flags

| Variable | Default | Meaning |
|---|---:|---|
| `LEARNING_ENGINE_ENABLED` | `false` | Enable learning-engine features. |
| `RANKING_ENGINE_ENABLED` | `false` | Enable ranking-engine features. |
| `ACTIVE_SKILL_QUALITY_LEVEL` | `validated` | Active skill-library quality contour. |
| `BASE_OFFER_EUR` | `14.98` | Base offer in euros. |
