# Publishing checklist

This preparation repository must remain private. Publish only an allowlisted,
scrubbed snapshot into a new clean publishing repository, kept private until
every gate below passes. Do not reuse this repository's Git history: closed
pull-request refs can retain rewritten commits and are read-only to repository
owners.

## Public-surface gate

- Confirm only intended files are tracked.
- Run `./scripts/validate.sh` successfully.
- Review the staged diff for credentials, private names, local paths, internal notes, generated files, and large binaries.
- Confirm `README.md` and `README.ja.md` describe the same behavior and requirements.
- Confirm every relative Markdown link resolves.
- Confirm the project MIT license remains in `LICENSE` and the full upstream MIT notice remains in `THIRD_PARTY_NOTICES.md`.
- Confirm commit metadata does not expose an unintended personal email address.
- Inspect `refs/pull/*/head` in any source repository. If an old pull ref retains
  private history or unintended personal metadata, do not change that
  repository's visibility.

## Pull-request gate

- Create a new clean publishing repository from the reviewed snapshot only.
- Prepare the public snapshot on a scoped release-preparation branch (`release/v0.2.0` by default; a linked Issue branch is acceptable for the initial snapshot).
- Open a pull request into `main` while the repository is still private.
- Require passing CI and no unresolved material review findings.
- Review both README files using their GitHub-rendered branch or pull-request URLs.
- Obtain explicit maintainer approval for the rendered README before merge.

## Visibility gate

- Re-fetch repository visibility, default branch, Actions status, tags, and releases.
- Confirm the worktree is clean and `main` contains the reviewed commit.
- Obtain explicit maintainer approval before changing the clean publishing
  repository's visibility to public.
- Immediately after the visibility change, enable private vulnerability reporting and verify that **Report a vulnerability** is available.
- Enable or verify secret scanning and push protection, then review any alerts before announcing the repository.
- Enable branch protection or a ruleset for `main`, including the validation check, as soon as the public-repository plan makes it available.
- After the change, verify public README access, repository visibility, Actions status, and clone/install instructions.

Do not publish private development history, internal continuity notes, credentials, or maintainer-only context.
