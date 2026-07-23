# PR Loop Contract

## Quick Reference

| Gate | Required action | Evidence |
|---|---|---|
| Snapshot | Resolve PR/base/head and fetch latest base | Exact remote head SHA |
| Conflict | Resolve intent, verify, push serially | No unmerged paths/markers; tests pass |
| CI | Wait for terminal required checks on current SHA | Current-head check results |
| Claude | Wait for terminal review on current SHA | Current-head review result |
| Threads | Classify every unresolved thread | Reply/disposition per thread |
| Fix | Proportionate spec, TDD, verification | Failing-then-passing test when behavioral |
| Finish | Re-fetch and recheck every gate | Stop contract satisfied simultaneously |

## 1. Snapshot and Conflict Gate

Record PR, base, remote/local SHA, mergeability, required checks, Claude state, and unresolved thread IDs. Fetch the latest base before trusting evidence.

For conflicts:
- Shared branch: merge normally; never rewrite teammate history.
- Sole-owned branch: follow repository policy; `--force-with-lease` only when rebase and history rewrite are authorized.
- Inspect base/ours/theirs per file. Never blanket-apply either side.
- Check unmerged paths and conflict markers, run `git diff --check`, targeted regressions, then repository-required checks.
- Treat clean textual merges as possible semantic conflicts; retest affected contracts, migrations, authentication, and boundaries.
- Push through one integrator, then restart from the new SHA.

## 2. CI and Claude Gate

Poll with bounded waits until required CI and Claude PR Review are terminal for the exact current head. Ignore results for older SHAs.

If CI fails, inspect logs, reproduce when possible, define expected behavior, create test-sized TODOs, apply TDD for behavioral defects, verify, create a Lore commit, push, and restart. Evidence may justify rerunning a flaky external check, but a required failure still blocks completion.

## 3. Thread Triage

Read the entire inline thread, then classify:

| Class | Disposition |
|---|---|
| Actionable | Verify, specify, test, and fix |
| Intentional/design | Reply with code/spec/test evidence |
| Duplicate | Link the canonical thread or fix |
| Outdated/already fixed | Point to the current diff/commit |
| Invalid/YAGNI | Explain the repository-specific mismatch |
| Ambiguous | Obtain clarification before implementation |

Use official/upstream docs or Context7 for unfamiliar or version-sensitive claims. Non-actionable does not mean silent: leave an evidence-backed reply and resolve when authorized.

## 4. Spec, TDD, and Parallel Work

Scale planning to risk:
- Text/import-only: record cause, edit, and verification.
- Behavioral bug: failing regression test, minimal fix, green verification.
- API/design/security: focused SPEC, acceptance criteria, and test-sized TODOs before implementation.

Parallelize only independent lanes. Assign exclusive file ownership; keep shared contracts under one owner. Agents may commit isolated logical changes, but the integrator reviews diffs, reruns tests, and serializes shared-branch pushes.

## 5. Commit, Push, Reply, Repeat

Run targeted tests per fix and full required checks on the integrated candidate. Create Lore-compliant commits per logical review cluster. Fetch immediately before push; integrate remote movement and re-verify instead of forcing.

After the evidence-bearing commit is visible on the remote PR head, reply inside each thread with disposition, behavior change, commit SHA, and test evidence. Resolve only with supporting evidence and authority. Restart because the push changed the SHA.

## Rationalization Check

| Excuse | Reality |
|---|---|
| Deadline says accept every bot comment | External review is a claim to verify, not an order. |
| Old-head evidence is close enough | A new commit invalidates SHA-bound evidence. |
| Repeated rounds justify stopping | Continue recoverable work; classify repeated feedback as duplicate with evidence. |
| Force-push is faster | Unknown remote work can be lost; serialize and use lease protection only when authorized. |
| CI will catch a bad conflict resolution | Semantic validation and local tests precede push. |

## Example

CI fails, Claude reports one authorization bug and one duplicate, and the latest base conflicts in auth code. Resolve the conflict semantically, add a failing authorization regression, implement the minimal fix, verify, create a Lore commit, integrate and push once, reply to both threads with the canonical commit/test, then wait for CI and Claude on the new SHA. Stop only when the main skill's stop contract holds.

## Common Mistakes

- Resolving a thread before the remote head contains its evidence.
- Writing a full SPEC for trivial edits or skipping one for contract changes.
- Trusting subagent summaries without reviewing diffs and rerunning tests.
- Treating review completion as merge permission.
- Forgetting to re-fetch the base immediately before the final stop check.
