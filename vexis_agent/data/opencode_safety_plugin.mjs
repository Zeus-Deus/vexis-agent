// Vexis Step 6.5 safety hook — opencode plugin.
//
// Companion to vexis_agent/core/safety_hook.py (claude-code path).
// Mirrors the regex set in vexis_agent/core/safety.py. Drift is
// caught by tests/test_safety_opencode_plugin_parity.py, which runs
// both the JS regex set and the Python one against an identical
// fixture set and asserts byte-for-byte verdict agreement.
//
// Why this lives in vexis_agent/data/ and gets installed into the
// workspace at brain construction time:
//   - opencode loads single-file plugins by absolute or relative
//     path (see packages/opencode/src/plugin/shared.ts:isPathPluginSpec).
//   - The workspace's opencode.json carries the plugin reference,
//     same place vexis already manages the `mcp:` block.
//   - safety_install.ensure_opencode_safety_plugin() copies this
//     file into <workspace>/.vexis-opencode-safety.mjs and merges
//     the path into opencode.json's plugin[] array.
//
// Why "tool.execute.before" instead of "permission.ask":
//   - opencode's shell tool already calls ctx.ask(permission:"bash")
//     with patterns extracted by tree-sitter. The plugin's
//     permission.ask hook can override the verdict to "deny", but
//     when vexis spawns opencode with --dangerously-skip-permissions
//     the auto-reply at run.ts:548 short-circuits the asker before
//     plugins see it. tool.execute.before fires unconditionally on
//     every tool invocation and gives us the raw args object, so
//     it's the surface that actually works under our spawn flags.
//
// Why we mutate the command rather than throw:
//   - plugin.trigger wraps the hook in Effect.promise (see
//     plugin/index.ts:266). A thrown promise rejection becomes an
//     effect die-defect — fatal to the whole turn, not a graceful
//     tool-call block. Mutating output.args.command to a benign
//     `printf … >&2; exit 1` keeps the tool execution succeeding
//     mechanically while delivering a clear, non-zero-exit failure
//     the model treats as a tool error.

// Stays in lockstep with vexis_agent/core/safety.DESTRUCTIVE_PATTERNS.
// Both regex literals AND reason strings are identical so the
// parity test can compare them mechanically.
const DESTRUCTIVE_PATTERNS = [
  [/\brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*[fF][a-zA-Z]*|-[a-zA-Z]*[fF][a-zA-Z]*[rR][a-zA-Z]*|-[rR]\s+-[fF]|-[fF]\s+-[rR])(?=\s|$|[;|&])/, "recursive/forced rm"],
  [/\bdd\s+(if|of)=/, "dd to/from device"],
  [/(curl|wget)\s+[^|;&]+\|\s*(ba)?sh\b/, "pipe remote script to shell"],
  [/\bmkfs(\.\w+)?\s+/, "filesystem creation"],
  [/\bchmod\s+-R\s+0*777\b/, "wide recursive chmod 777"],
  [/\bgit\s+push\s+(-f|--force)\b/, "force push"],
  [/\bgit\s+reset\s+--hard\b/, "hard reset"],
  [/>\s*\/dev\/(sd|nvme|hd|mmcblk)\w*/, "raw device write"],
  [/\bsudo\b/, "sudo invocation"],
];

// Hard cap on the command string we'll regex-match. Matches the
// safety_hook.py guard rail. Beyond this we bail and allow — a
// multi-MB "command" is almost certainly a model error, not a real
// destructive invocation.
const MAX_COMMAND_LEN = 64 * 1024;

// Exported for the parity test. Not part of the plugin contract.
export function checkCommand(command) {
  if (typeof command !== "string" || command.length === 0) return null;
  if (command.length > MAX_COMMAND_LEN) return null;
  for (const [pattern, reason] of DESTRUCTIVE_PATTERNS) {
    if (pattern.test(command)) return reason;
  }
  return null;
}

// Build the replacement command. Single-quote-escaped so the
// reason string can't break out into shell metacharacters even if
// a future pattern includes punctuation.
export function blockedCommand(reason) {
  const msg =
    `Vexis safety hook blocked this command: ${reason}. ` +
    `Ask the user to run it from their terminal directly if intended.`;
  const escaped = msg.replace(/'/g, `'\\''`);
  return `printf 'BLOCKED %s\\n' '${escaped}' >&2; exit 1`;
}

const hooks = {
  "tool.execute.before": async (input, output) => {
    try {
      if (!input || input.tool !== "bash") return;
      const args = output && output.args;
      if (!args || typeof args !== "object") return;
      const reason = checkCommand(args.command);
      if (reason !== null) {
        args.command = blockedCommand(reason);
      }
    } catch (_err) {
      // Fail-open: a broken plugin must never crash the brain.
      // The model proceeds with the original command — degraded
      // safety beats broken turns. Mirrors the philosophy of
      // safety_hook.py's stderr-and-exit-0 error paths.
    }
  },
};

// opencode V1 plugin shape: default export with a `server`
// function returning the hooks object. See packages/opencode/src/
// plugin/shared.ts:readV1Plugin for the loader contract.
export default {
  id: "vexis-safety",
  server: async (_input, _options) => hooks,
};
