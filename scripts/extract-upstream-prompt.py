#!/usr/bin/env python3
"""Extract a CODE_SYSTEM_PROMPTS template verbatim from upstream OpenWiki.

The port reproduces upstream prompt text. Retyping it introduces drift, so pull
it from source instead. Upstream stores the prompts as TypeScript template
literals, so backticks and dollar signs arrive escaped and must be unescaped.
"""
import argparse
import json
import sys
import urllib.request

RAW = "https://raw.githubusercontent.com/langchain-ai/openwiki/{ref}/src/agent/prompts/code.ts"


def fetch(ref: str) -> str:
    with urllib.request.urlopen(RAW.format(ref=ref)) as r:
        return r.read().decode("utf-8")


def extract(source: str, command: str) -> str:
    """Return the raw template literal body for CODE_SYSTEM_PROMPTS[command]."""
    anchor = source.index("CODE_SYSTEM_PROMPTS")
    needle = "\n  %s: `" % command
    at = source.find(needle, anchor)
    if at == -1:
        raise SystemExit("command not found in CODE_SYSTEM_PROMPTS: %s" % command)
    start = at + len(needle)
    i = start
    while i < len(source):
        if source[i] == "\\":
            i += 2
            continue
        if source[i] == "`":
            break
        i += 1
    else:
        raise SystemExit("unterminated template literal for %s" % command)
    return source[start:i]


def unescape(body: str) -> str:
    """Undo TypeScript template-literal escaping. Order matters: backslash last."""
    return body.replace("\\`", "`").replace("\\$", "$").replace("\\\\", "\\")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="v0.3.3")
    ap.add_argument("--command", required=True, choices=["chat", "init", "update"])
    ap.add_argument("--placeholders", action="store_true",
                    help="list the {PLACEHOLDER} tokens instead of the body")
    args = ap.parse_args()

    body = unescape(extract(fetch(args.ref), args.command))
    if args.placeholders:
        import re
        found = sorted(set(re.findall(r"\{[A-Z_]+\}", body)))
        print(json.dumps(found, indent=2))
        return 0
    sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
