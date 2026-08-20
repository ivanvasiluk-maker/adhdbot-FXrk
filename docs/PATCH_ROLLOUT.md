# PATCH rollout contract

`patches/sequence.json` is the executable source of truth for PATCH-00 through PATCH-17.

## Required order

1. PATCH-00–03: product contract, state machine, situation/mechanism, experiment.
2. PATCH-04–08: outcome, memory, skill schema, Ranking Engine, post-experiment policy.
3. PATCH-09–14: map, journal, reminders, safety, sale after value, Learning Engine.
4. PATCH-15–17: legacy migration, analytics, mandatory regression.

Each patch-ownership commit must use a subject that begins with exactly one owner, for example:

```text
PATCH-07: add deterministic ranking reasons
```

Do not combine multiple PATCH owners in one commit. Commits that do not declare PATCH ownership plus
merge/revert commits are exempt. The historical combined rollout is recorded by
`enforced_after_commit`; all later commits are checked.
The baseline resolves from the commit that introduced the ledger path, so PR squash/rebase does not
invalidate CI with a repository-specific commit hash.
PATCH ownership must move forward monotonically; later extensions such as PATCH-18 and PATCH-20 use
the same two-digit prefix contract. CI checks out full history so the baseline can always be verified.

## Acceptance workflow

Before committing a patch, run its dedicated acceptance contract:

```bash
python scripts/run_patch_acceptance.py PATCH-07
```

Then run the complete gate:

```bash
python scripts/regression_gate.py
```

CI validates the ledger, commit ownership, all unit/smoke/regression checks, and startup. A patch is
not complete until both its dedicated acceptance command and the full regression gate pass.
