/**
 * openwiki gate for opencode — the `session.idle` counterpart to the Claude Code
 * and Codex `Stop` hooks.
 *
 * All the logic lives in hooks/openwiki-gate.sh, which decides in pure shell whether
 * anything changed and spawns a headless run only when it did. This file exists to
 * deliver the event, nothing more.
 *
 * The script hardcodes `claude -p` on its spawn line; swap it for
 * `opencode run 'update the openwiki docs'` to keep the refresh on this host.
 */
export const OpenWikiGate = async ({ $, directory }) => {
  return {
    event: async ({ event }) => {
      if (event.type !== "session.idle") return

      // The gate re-exports OPENWIKI_HOOK=1 for the run it spawns, so the child's own
      // session.idle lands here and exits at the guard instead of recursing.
      await $`sh hooks/openwiki-gate.sh`.cwd(directory).nothrow().quiet()
    },
  }
}
