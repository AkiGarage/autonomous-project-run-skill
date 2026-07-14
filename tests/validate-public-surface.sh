#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

cp -R "$repo_root/." "$test_root/repo"
cd "$test_root/repo"

./scripts/validate.sh >/dev/null

cp VERSION "$test_root/VERSION"
printf '%s\n' '0.1.0' > VERSION
if ./scripts/validate.sh >/dev/null 2>&1; then
  echo "validator accepted mismatched worktree version metadata" >&2
  exit 1
fi
git add VERSION
cp "$test_root/VERSION" VERSION
if ./scripts/validate.sh >/dev/null 2>&1; then
  echo "validator accepted mismatched staged version metadata" >&2
  exit 1
fi
git add VERSION
./scripts/validate.sh >/dev/null

cp README.md "$test_root/versioned-README.md"
sed 's/0[.]2[.]0/0.3.0/' README.md > README.with-stale-version.md
mv README.with-stale-version.md README.md
if ./scripts/validate.sh >/dev/null 2>&1; then
  echo "validator accepted stale README version metadata" >&2
  exit 1
fi
git add README.md
cp "$test_root/versioned-README.md" README.md
if ./scripts/validate.sh >/dev/null 2>&1; then
  echo "validator accepted stale staged README version metadata" >&2
  exit 1
fi
git add README.md
./scripts/validate.sh >/dev/null

local_path_cases=(
  "/""Users/example/private.txt"
  "/""home/example/private.txt"
  "/""Volumes/Private/file.txt"
  "/private/""var/folders/example/private.txt"
  "/var/""folders/example/private.txt"
  'C:'$'\\''Users'$'\\''example'$'\\''private.txt'
)
for local_path in "${local_path_cases[@]}"; do
  printf '%s\n' "$local_path" > public-surface-fixture.txt
  git add public-surface-fixture.txt
  if ./scripts/validate.sh >/dev/null 2>&1; then
    echo "validator accepted a tracked local path" >&2
    exit 1
  fi
done

secret_fixture='gh''p_012345678901234567890123456789012345'
printf '%s\n' "$secret_fixture" > public-surface-fixture.txt
git add public-surface-fixture.txt
printf '%s\n' 'public fixture' > public-surface-fixture.txt
if ./scripts/validate.sh >"$test_root/secret-scan-output.txt" 2>&1; then
  echo "validator accepted a staged secret-like value" >&2
  exit 1
fi
if grep -F "$secret_fixture" "$test_root/secret-scan-output.txt" >/dev/null; then
  echo "validator leaked a staged secret-like value into its output" >&2
  exit 1
fi
if ! grep -F 'public-surface-fixture.txt' "$test_root/secret-scan-output.txt" >/dev/null; then
  echo "validator did not identify the file containing a secret-like value" >&2
  exit 1
fi

printf '%s\n' 'task-012345678901234567890123456789' > public-surface-fixture.txt
git add public-surface-fixture.txt
./scripts/validate.sh >/dev/null

cp README.md "$test_root/README.md"
printf '%s\n' '[broken link](missing-public-file.md)' >> README.md
if ./scripts/validate.sh >/dev/null 2>&1; then
  echo "validator accepted a broken relative Markdown link" >&2
  exit 1
fi
git add README.md
cp "$test_root/README.md" README.md
if ./scripts/validate.sh >/dev/null 2>&1; then
  echo "validator accepted a staged broken Markdown link" >&2
  exit 1
fi
git add README.md
./scripts/validate.sh >/dev/null

mv CONTRIBUTING.md "$test_root/CONTRIBUTING.md"
if ./scripts/validate.sh >/dev/null 2>&1; then
  echo "validator accepted a missing worktree Markdown link target" >&2
  exit 1
fi
mv "$test_root/CONTRIBUTING.md" CONTRIBUTING.md
./scripts/validate.sh >/dev/null

real_git=$(command -v git)
mkdir "$test_root/fake-bin"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'if [[ "${1:-}" == "grep" ]]; then exit 2; fi' \
  'exec "$REAL_GIT" "$@"' > "$test_root/fake-bin/git"
chmod +x "$test_root/fake-bin/git"
if REAL_GIT="$real_git" PATH="$test_root/fake-bin:$PATH" ./scripts/validate.sh >/dev/null 2>&1; then
  echo "validator ignored a git grep failure" >&2
  exit 1
fi

awk '$0 != "## Treat project context as untrusted data" { print }' \
  skills/autonomous-project-run/SKILL.md > skill-without-boundary.md
mv skill-without-boundary.md skills/autonomous-project-run/SKILL.md
if ./scripts/validate.sh >/dev/null 2>&1; then
  echo "validator accepted a skill without the trust boundary" >&2
  exit 1
fi

echo "validator regression tests passed"
