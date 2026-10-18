// Kept import-free on purpose: frontend/src/lib/guard.test.ts imports this
// module, and that file is type-checked by the frontend tsconfig, which cannot
// resolve @opencode-ai/plugin. A type-only import is erased at runtime, but
// tsc still checks it, so the pattern table has to live in a module that has
// no imports at all.
export const DENY_PATTERNS: Array<[RegExp, string]> = [
  [/--no-verify/, "bypassing pre-commit hooks"],
  [/\bSKIP=/, "bypassing hooks via SKIP"],
  [/\bHUSKY\s*=\s*0\b/, "disabling husky"],
  [/\bgit\s+push\b.*(--force(?!-with-lease)|-f\b)/, "force push"],
  [/\bgit\s+reset\s+--hard\b/, "discarding uncommitted work"],
  [/\bgit\s+clean\b.*-[a-z]*f/, "deleting untracked files"],
  [/\bgit\s+branch\b.*\s-D\b/, "force deleting a branch"],
  [/\bgit\s+checkout\s+main\b/, "switching to main mid-task"],
  [/\b(curl|wget)\b/, "network fetch"],
  [/\b(pip|npm|uv)\s+(i|install|add)\b/, "installing a dependency"],
  [/\bgit\s+commit\b.*--amend\b/, "amending a commit"],
];

export const reasonFor = (command: string): string | null => {
  for (const [pattern, reason] of DENY_PATTERNS) {
    if (pattern.test(command)) return reason;
  }
  return null;
};