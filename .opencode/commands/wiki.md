---
description: Generate/maintain the openwiki/ documentation wiki (init | update)
---

Run the **openwiki** skill against this repository.

This command exists only to give the skill something it cannot get on its own: an explicit mode
from slash arguments. The agent definition — the system prompt, the git evidence commands, the
idempotence logic — lives in the skill, which opencode discovers at
`.agents/skills/openwiki/SKILL.md`. Do not restate it here; load the skill and follow it.

Resolve the mode from `$ARGUMENTS`, then hand it to the skill:

- `init` → **init mode**. Build the wiki from scratch.
- `update` → **update mode**. Refresh only the pages recent changes actually affect.
- empty → **auto-route**: run `test -d openwiki && echo update || echo init` and use the result.
- Anything else → if the first token is `init` or `update` it selects the mode and the rest is an
  **additional user instruction** appended to the run. If no mode token is present, auto-route and
  treat all of `$ARGUMENTS` as the additional instruction.

You are running on opencode, so you **do** have a Task tool. The skill's "Subagent discipline"
section is marked opencode-only: follow it. Fan out read-only subagents for a repository with
several substantial domains, and do every write yourself.
