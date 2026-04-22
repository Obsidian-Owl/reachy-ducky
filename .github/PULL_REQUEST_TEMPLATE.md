<!--
Thanks for contributing to Reachy Ducky. This template is a hint, not a wall — feel free to delete sections that don't apply to your PR.
-->

## Summary

<!-- What does this PR do? One to three bullets. If it closes an issue, write "Closes #N". -->

## Why now

<!-- Optional: the motivating context. Why is this worth merging at this moment vs. deferring? Especially useful for non-obvious trade-offs. -->

## Test plan

- [ ] `uv run pytest -q` — unit tier green
- [ ] `uv run mypy --strict daemon/src app/src menubar/src protocol/src` — type clean
- [ ] `uv run ruff check .` + `uv run ruff format --check .` — lint/format clean
- [ ] Lefthook `pre-commit` + `pre-push` hooks pass locally
- [ ] (If touching the daemon) bandit clean
- [ ] (If touching Reachy app code) hardware / sim smoke where applicable

## Known gaps or follow-ups

<!-- Anything deliberately deferred? Cross-link follow-up issues. If nothing, delete this section. -->

## Notes for reviewers

<!-- Heads up on anything non-obvious: architectural trade-offs, trickier bits of the diff, hardware/API gotchas. If nothing, delete. -->
