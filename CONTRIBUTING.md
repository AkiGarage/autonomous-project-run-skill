# Contributing

Thank you for helping improve Autonomous Project Run.

## Development flow

1. Create a focused branch from the latest `main`.
2. Keep each change limited to one observable outcome.
3. Update English and Japanese documentation together when user-facing behavior changes.
4. Run `./scripts/validate.sh` and `./tests/validate-public-surface.sh`.
5. Open a pull request describing why the change is needed, how it works, and which checks passed.

Do not commit credentials, private repository references, local machine paths, internal transcripts, handoff files, generated archives, or unrelated build output.

## Skill changes

Keep `SKILL.md` concise and operational. Preserve explicit approval boundaries for public actions, spending, credentials, production mutation, destructive operations, and repository-protection bypasses.

Behavioral changes should include a reproducible validation case or a clear explanation when automated testing is not practical.
