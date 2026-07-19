# Autonomous Project Run

![Autonomous Project Run — Escape AI babysitting](assets/readme/hero-en.png)

Autonomous Project Run (APR) helps a coding agent carry a multi-issue GitHub
project from an unclear goal to a verified finish—with fewer check-ins from you.

You describe the outcome. APR works out what remains, handles the routine GitHub
workflow, checks its work, and asks you only when a real decision or safety
boundary needs your input.

> **Early release:** `v0.5.0` is not yet stable. Try it first on a repository
> where your work is committed or backed up.

[日本語](README.ja.md)

## When it helps

APR is useful when:

- a project has several related Issues or an unfinished implementation plan;
- work may take longer than one coding-agent task;
- you want tests, reviews, pull requests, merges, and Issue closure checked as
  one continuous job;
- you are tired of repeatedly asking an agent to continue or reminding it what
  was already finished.

## What you can expect

- **Starts from the real current state.** APR checks the repository and GitHub,
  finds the earliest unfinished step, and avoids repeating completed work.
- **Keeps work separated.** Each implementation Issue gets its own task,
  branch, and separate Git work folder (a worktree), so unrelated changes do
  not get mixed together.
- **Recovers from interruptions.** It saves verified progress and resumes from
  that point instead of asking you to reopen the project or repeat the request.
- **Avoids duplicate actions.** Before creating tasks, opening pull requests,
  merging, or closing Issues, it checks whether the action already happened.
- **Checks before moving on.** Tests, code review, CI, and the exact commit are
  verified at the relevant stage.
- **Finishes with a project-wide audit.** APR checks that every promised Issue
  is accounted for before it says the project is complete.

Long conversations sometimes need to be shortened by the coding app. APR saves
its place and reviews the remaining work when that happens; the shortening by
itself is not treated as a reason to abandon the job.

## Install

APR builds on the workflow skills from
[`mattpocock/skills`](https://github.com/mattpocock/skills). Install those first:

```sh
npx skills@latest add mattpocock/skills
```

Choose the workflow suite when prompted. Then install APR:

```sh
npx skills@latest add AkiGarage/autonomous-project-run-skill
```

For the full workflow, you also need a coding app that supports Agent Skills, a
GitHub repository, and an authenticated `gh` CLI.

When APR starts in a repository, it checks the required project setup. If the
Matt Pocock workflow configuration is missing, APR runs the official setup skill
and verifies the result before it plans or changes code. You do not need to run
`/setup-matt-pocock-skills` in advance.

## Use it

Tell the agent which repository to work in and what finished should look like:

```text
Use $autonomous-project-run to finish this project with minimal supervision.
```

A clear end-to-end request lets APR perform the routine repository work needed
for that run, including branches, tests, reviews, commits, pull requests, and
merges. Merely mentioning or inspecting APR does not grant that authority.

## Safety boundaries

APR checks that it is working in the intended project and isolated worktree
before it changes anything. It also re-checks uncertain remote results before
retrying, which helps prevent duplicate tasks, pull requests, and merges.

APR does not treat an end-to-end request as permission to publish publicly,
spend money, access credentials, change production, perform destructive cleanup,
force-push, or bypass repository protections. Those actions still need their
own clear authorization.

For fully automatic continuation between separate tasks, the coding app must
support task creation, isolated worktrees, and safe task handoff. If it does not,
APR preserves progress and continues when the app next runs it instead of
installing a background polling service.

## Technical documentation

The detailed lifecycle design, safety rules, state machines, protocols, and
verification matrix are in
[`docs/architecture/apr-lifecycle-v1/`](docs/architecture/apr-lifecycle-v1/README.md).
Start with the shipped
[`SKILL.md`](skills/autonomous-project-run/SKILL.md) for the agent-facing
workflow and its bundled runtime helpers.

## Attribution and license

This project composes and extends workflow concepts from
[Matt Pocock's Skills for Real Engineers](https://github.com/mattpocock/skills),
including Wayfinder. The upstream project is licensed under the MIT License.
With thanks to Matt Pocock for Wayfinder and the composable workflow design.

This repository is independently maintained and is not affiliated with or
endorsed by Matt Pocock. See [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for validation and pull-request guidance.
Do not put vulnerability details in a public Issue; follow
[SECURITY.md](SECURITY.md) for private reporting or the detail-free contact
fallback.
