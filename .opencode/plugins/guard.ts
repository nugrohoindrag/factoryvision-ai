import type { Plugin } from "@opencode-ai/plugin";
import { DENY_PATTERNS, reasonFor } from "./deny-patterns";

// Pattern matching is bypassable by a determined agent and is not a security
// boundary. It raises the cost of an accidental or reflexive bypass, which is
// the realistic case. The boundary is CODEOWNERS plus required status checks.
export const Guard: Plugin = async () => ({
  "tool.execute.before": async (input, output) => {
    if (input.tool !== "bash") return;
    const command = String((output.args as Record<string, unknown>).command ?? "");
    const reason = reasonFor(command);
    if (reason) {
      throw new Error(
        `Blocked: ${reason}.\n` +
          `Fix the underlying failure instead of bypassing the gate. ` +
          `If this is genuinely unavoidable, ask a human.`,
      );
    }
  },
});

export { DENY_PATTERNS };