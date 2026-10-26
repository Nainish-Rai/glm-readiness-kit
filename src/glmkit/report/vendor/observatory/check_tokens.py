#!/usr/bin/env python3
"""Design-token check for dashboard.template.html (stdlib only). Exit 0 clean, 1 on findings.

    python3 -I check_tokens.py [file ...]

Rules
  raw-color      a hex, rgb()/rgba() or hsl() colour outside the primitive token block
  token-parity   a var(--x) that no custom property defines
  reduced-motion no prefers-reduced-motion block, or script animation without a `reduced` guard
  literal-duration a transition/animation with a fixed duration, which reduced motion cannot zero
                 (use a --dur-* token)
"""
import re
import sys

PRIM_START = "/* ---- primitives"
PRIM_END = "/* ---- semantic roles"
COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
MOTION = re.compile(r"\b(?:transition|animation)(?:-duration)?\s*:([^;}\"]*)")
DURATION = re.compile(r"(?<![\w.-])(\d*\.?\d+)m?s\b")


def check(path):
    text = open(path, encoding="utf-8").read()
    findings = []
    lines = text.split("\n")
    in_prim = False
    for n, line in enumerate(lines, 1):
        if PRIM_START in line:
            in_prim = True
        if PRIM_END in line:
            in_prim = False
        if in_prim:
            continue
        code = re.sub(r"&#\w+;|#[a-z][\w-]*\b(?=[\s,{:.)\"'\[]|$)", "", line)  # ids and entities are not colours
        for m in COLOR.finditer(code):
            findings.append((n, "raw-color", m.group(0)))
    defined = set(re.findall(r"(--[\w-]+)\s*:", text))
    for n, line in enumerate(lines, 1):
        for name in re.findall(r"var\((--[\w-]+)", line):
            if name not in defined and not name.startswith("--k"):  # --kc / --kv are set inline per element
                findings.append((n, "token-parity", name))
    for n, line in enumerate(lines, 1):
        for decl in MOTION.findall(line):
            for m in DURATION.finditer(decl):
                if float(m.group(1)) > 0:
                    findings.append((n, "literal-duration", m.group(0)))
    if "prefers-reduced-motion" not in text:
        findings.append((0, "reduced-motion", "no prefers-reduced-motion block"))
    if re.search(r"\.animate\(|requestAnimationFrame", text) and not re.search(r"\breduced\b", text):
        findings.append((0, "reduced-motion", "script animation without a reduced-motion guard"))
    return findings


def main():
    files = sys.argv[1:] or ["dashboard.template.html"]
    bad = 0
    for f in files:
        for n, rule, what in check(f):
            print("%s:%s %s %s" % (f, n, rule, what))
            bad += 1
    print("%d finding(s) in %d file(s)" % (bad, len(files)))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
