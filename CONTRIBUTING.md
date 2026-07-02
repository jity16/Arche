# Contributing (Research Repository)

Thanks for your interest in improving this project.

## Scope

This repository is currently maintained as research software. Please prioritize:
- reproducibility
- conservative changes
- transparent documentation

Avoid large feature refactors unless explicitly discussed first.

## Recommended Contribution Types

- documentation clarity improvements
- repository hygiene and structure cleanup
- test robustness and deterministic checks
- bug fixes with minimal behavior change

## Development Notes

- Main public CLI entry point is the controller script in `src/chemistry_multiagent/controllers`.
- Many workflows depend on external chemistry software and local environment setup.
- Internal test harness scripts under `tests/scripts` are not stable public APIs.

## Pull Request Guidance

- Keep PRs focused and small.
- Describe behavior changes and rationale clearly.
- Include test evidence for modified behavior.
- Call out environment assumptions and caveats.

## License

License selection is currently pending. See `LICENSE`.
