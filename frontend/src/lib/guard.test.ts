import { describe, expect, it } from "vitest";
import { DENY_PATTERNS, reasonFor } from "../../../.opencode/plugins/deny-patterns";

const CASES: Array<[string, boolean]> = [
  ["git commit --no-verify -m x", true],
  ["SKIP=flake8 pytest", true],
  ["HUSKY=0 git commit -m x", true],
  ["git push --force origin main", true],
  ["git push --force-with-lease origin main", false],
  ["git reset --hard HEAD~1", true],
  ["git clean -fdx", true],
  ["git branch -D feature", true],
  ["git checkout main", true],
  ["curl https://example.com", true],
  ["pip install requests", true],
  ["npm install left-pad", true],
  ["uv sync --project backend", false],
  ["just test", false],
  ["git status", false],
  ["git push origin hardening/foundation", false],
];

describe("guard", () => {
  it.each(CASES)("%s -> blocked=%s", (command, blocked) => {
    const matched = DENY_PATTERNS.some(([pattern]) => pattern.test(command));
    expect(matched).toBe(blocked);
  });

  it.each(CASES.filter(([, blocked]) => blocked))("names the reason for blocking %s", (command) => {
    expect(reasonFor(command)).toBeTruthy();
  });

  it.each(CASES.filter(([, blocked]) => !blocked))("allows %s", (command) => {
    expect(reasonFor(command)).toBeNull();
  });

  it.each([
    "uv run --project backend --python 3.12 pytest -m unit",
    "uv sync --project backend --python 3.12",
    "npm --prefix frontend ci",
    "npm --prefix frontend run typecheck",
    "npm --prefix frontend test",
    "npm --prefix frontend run build",
    "just bootstrap",
    "just lint",
    "just typecheck",
    "just verify",
    "just catalog",
    "git add .gitignore",
    "git commit -m 'fix: gate the catalog'",
  ])("does not block a real command: %s", (command) => {
    expect(reasonFor(command)).toBeNull();
  });
});