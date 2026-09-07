#!/usr/bin/env python3
"""Extract a repository-prompt template verbatim from upstream OpenWiki.

The port reproduces upstream prompt text. Retyping it introduces drift, so pull
it from source instead. At v0.5.0 the repository prompts are function-built
template literals in src/agent/repository-prompts.ts. Their bodies carry
${...} interpolation points (including nested template literals inside
interpolations) that the port resolves per run; they are extracted verbatim
and marked, never silently filled.
"""
import argparse
import json
import sys
import urllib.request

RAW = "https://raw.githubusercontent.com/langchain-ai/openwiki/{ref}/src/agent/repository-prompts.ts"

PARTS = {
    "planner": "createRepositoryPlannerPrompt",
    "worker": "createRepositoryPagePrompt",
}

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "`": "`", "$": "$", "\\": "\\"}


def fetch(ref: str) -> str:
    with urllib.request.urlopen(RAW.format(ref=ref)) as r:
        return r.read().decode("utf-8")


def extract_template_body(source: str, start: int):
    """Scan a template literal starting just after its opening backtick.

    Understands ${ ... } interpolations, including nested template literals
    inside them, and escaped characters (kept raw here; decoded by unescape).
    Returns (body, end_index).
    """
    body = []
    i = start
    stack = []
    while i < len(source):
        c = source[i]
        if c == "\\":
            body.append(source[i:i + 2])
            i += 2
            continue
        if not stack:
            if c == "`":
                return "".join(body), i
            if c == "$" and source[i + 1:i + 2] == "{":
                stack.append(1)
                body.append("${")
                i += 2
                continue
            body.append(c)
            i += 1
            continue
        if c == "{":
            stack[-1] += 1
        elif c == "}":
            stack[-1] -= 1
            if stack[-1] == 0:
                stack.pop()
        elif c == "`":
            nested, end = extract_template_body(source, i + 1)
            body.append("`" + nested + "`")
            i = end + 1
            continue
        body.append(c)
        i += 1
    raise SystemExit("unterminated template literal")


def extract(source: str, part: str) -> str:
    """Return the raw returned-template body of the named prompt builder."""
    fn_at = source.find(PARTS[part])
    if fn_at == -1:
        raise SystemExit("builder not found: %s" % part)
    ret_at = source.find("return `", fn_at)
    if ret_at == -1:
        raise SystemExit("no returned template literal in %s" % part)
    body, _ = extract_template_body(source, ret_at + len("return `"))
    return body


def unescape(body: str) -> str:
    """Decode TypeScript template-literal escape sequences, left to right.

    Unknown escapes decode to the character itself, matching untagged-template
    semantics. Order-free because scanning is positional, not replacement-based.
    """
    out = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            out.append(_ESCAPES.get(body[i + 1], body[i + 1]))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def interpolations(body: str):
    """Distinct top-level ${...} spans, brace-counted so nested templates survive."""
    found = []
    i = 0
    while i < len(body):
        if body[i] == "$" and body[i + 1:i + 2] == "{":
            depth = 1
            j = i + 2
            while j < len(body) and depth:
                if body[j] == "{":
                    depth += 1
                elif body[j] == "}":
                    depth -= 1
                j += 1
            span = body[i:j]
            if span not in found:
                found.append(span)
            i = j
            continue
        i += 1
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="v0.5.0")
    ap.add_argument("--part", required=True, choices=["planner", "worker"])
    ap.add_argument("--placeholders", action="store_true",
                    help="list the ${...} interpolation points instead of the body")
    args = ap.parse_args()

    body = unescape(extract(fetch(args.ref), args.part))
    if args.placeholders:
        print(json.dumps(interpolations(body), indent=2))
        return 0
    sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
