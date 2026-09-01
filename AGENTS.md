# Orphograph agent instructions

## Branch safety

- Never start feature work with raw `git switch -c` or from an existing
  checkout whose base has not just been fetched.
- Start work with `python3 tools/start_branch.py <slug>`. It fetches origin and
  creates an isolated worktree exactly at `origin/master`.
- Before pushing a feature branch, run
  `python3 tools/check_branch_freshness.py --base origin/master`.
- Never merge or push directly to `master`. A passing merge to `master`
  automatically deploys Orphograph to production.
- Preserve dirty worktrees. Do not reset, clean, stash, or rewrite another
  agent's changes.

## Verification

- Run focused tests for the changed surface, then the exact CI suites.
- Report actual coverage for new control paths; every failure path needs a
  test.
- Public receipts and cryptographic commitment bytes must never carry internal
  analytics metadata.

## Production deployment invariant

- GitHub Actions must deploy only through `scripts/deploy_fly_ci.sh`.
- Do not replace that script with a direct `flyctl deploy` workflow step or
  add caller-controlled build flags. The production token is app-scoped and
  cannot create or operate Fly remote-builder apps.
- Keep the setup action at an immutable commit and `flyctl` at an explicit
  production version. Upgrade either only in a tested pull request.
- Never weaken `TestDeployBuildLocation`; it is the executable guard for this
  incident class.
