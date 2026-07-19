# Issue tracker: GitHub

Issues and PRDs for this repository live in GitHub Issues. Use the authenticated `gh` CLI and infer the repository from the checked-out remote.

## Conventions

- Read and list with `gh issue view` and `gh issue list`.
- Create, comment, label, and close with the matching `gh issue` commands only when the current task has write authority.
- Treat a bare `#<number>` as ambiguous until `gh pr view` or `gh issue view` identifies it.
- When a skill says to publish to the issue tracker, create a GitHub Issue.
- When a skill says to fetch a ticket, read the matching GitHub Issue and its comments.

## Pull requests as a triage surface

**PRs as a request surface: no.** Pull requests remain code-review and delivery objects; `/triage` must not add external PRs to the issue queue automatically.

## Wayfinding

Use one GitHub Issue as the map and linked sub-issues as tickets. Prefer native sub-issues and dependencies; if unavailable, use an explicit task list and `Blocked by: #<number>` references. Claim work by assignment only after the run has mutation authority.
