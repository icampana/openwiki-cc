## OpenWiki

This repository has documentation located in the /openwiki directory.

Start here:
- [OpenWiki quickstart](openwiki/quickstart.md)

OpenWiki includes repository overview, architecture notes, workflows, domain concepts, operations, integrations, testing guidance, and source maps.

When working in this repository, read the OpenWiki quickstart first, then follow its links to the relevant architecture, workflow, domain, operation, and testing notes.

## Git house rules

- `origin` is `icampana/openwiki-cc`, a fork of `SoulKyu/openwiki-cc`. All PRs and issues belong on `icampana/openwiki-cc` — never the upstream parent.
- Pass `--repo icampana/openwiki-cc` explicitly to every `gh` command. In non-interactive runs (agent shells), `gh` skips its "which repo?" prompt and silently targets the fork's parent otherwise.
