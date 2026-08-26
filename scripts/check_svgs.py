#!/usr/bin/env python3
"""Overlap / overflow checker for the generated SVGs.

Text advance is modelled at 0.6 em per character (JetBrains Mono).
Fails (exit 1) on: text-over-text, text-over-circle, anything outside the
viewBox, or a hero line wider than 100 columns.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SVG = "{http://www.w3.org/2000/svg}"

EM = 0.6
ASCENT, DESCENT = 0.8, 0.2
HERO_MAX_COLS = 100


def num(value, default=0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_css(style_text: str) -> dict[str, dict[str, str]]:
    """Very small CSS reader: `.cls{prop:value;...}` rules only."""
    rules: dict[str, dict[str, str]] = {}
    for sel, body in re.findall(r"\.([A-Za-z0-9_-]+)\s*\{([^}]*)\}", style_text or ""):
        props = rules.setdefault(sel, {})
        for decl in body.split(";"):
            if ":" in decl:
                key, _, val = decl.partition(":")
                props[key.strip()] = val.strip()
    return rules


def full_text(el: ET.Element) -> str:
    return "".join(el.itertext())


class Box:
    __slots__ = ("x1", "y1", "x2", "y2", "what")

    def __init__(self, x1, y1, x2, y2, what):
        self.x1, self.y1, self.x2, self.y2, self.what = x1, y1, x2, y2, what

    def hits(self, other: "Box") -> bool:
        return not (self.x2 <= other.x1 or other.x2 <= self.x1
                    or self.y2 <= other.y1 or other.y2 <= self.y1)

    def __repr__(self):
        return f"{self.what} [{self.x1:.1f},{self.y1:.1f} → {self.x2:.1f},{self.y2:.1f}]"


def collect(path: Path):
    root = ET.parse(path).getroot()
    vb = [num(v) for v in root.get("viewBox", "0 0 0 0").split()]
    style_text = "".join(full_text(s) for s in root.iter(f"{SVG}style"))
    css = parse_css(style_text)
    root_size = num(root.get("font-size"), 12.0)

    texts: list[Box] = []
    circles: list[Box] = []
    lines: list[str] = []

    def resolve(el, size, anchor):
        """Presentation attributes and CSS classes cascade from ancestors."""
        for name in (el.get("class") or "").split():
            props = css.get(name, {})
            if "font-size" in props:
                size = num(props["font-size"].replace("px", ""), size)
            if "text-anchor" in props:
                anchor = props["text-anchor"]
        if el.get("font-size"):
            size = num(el.get("font-size"), size)
        if el.get("text-anchor"):
            anchor = el.get("text-anchor")
        return size, anchor

    def walk(el, size, anchor):
        size, anchor = resolve(el, size, anchor)
        if el.tag == f"{SVG}text":
            text = full_text(el)
            lines.append(text)
            width = len(text) * EM * size
            x, y = num(el.get("x")), num(el.get("y"))
            if anchor == "middle":
                x -= width / 2
            elif anchor == "end":
                x -= width
            texts.append(Box(x, y - size * ASCENT, x + width, y + size * DESCENT,
                             f'text {text[:44]!r}'))
            return
        if el.tag == f"{SVG}circle":
            cx, cy, r = num(el.get("cx")), num(el.get("cy")), num(el.get("r"))
            sw = num(el.get("stroke-width"), 0.0) / 2
            circles.append(Box(cx - r - sw, cy - r - sw, cx + r + sw, cy + r + sw,
                               f"circle ({cx:g},{cy:g}) r{r:g}"))
            return
        for child in el:
            walk(child, size, anchor)

    walk(root, root_size, "start")
    return vb, texts, circles, lines


def check(path: Path) -> list[str]:
    problems: list[str] = []
    vb, texts, circles, lines = collect(path)
    _, _, vw, vh = vb

    for box in texts + circles:
        if box.x1 < -0.5 or box.y1 < -0.5 or box.x2 > vw + 0.5 or box.y2 > vh + 0.5:
            problems.append(f"outside viewBox ({vw:g}×{vh:g}): {box}")

    for i, a in enumerate(texts):
        for b in texts[i + 1:]:
            if a.hits(b):
                problems.append(f"text overlaps text:\n      {a}\n      {b}")
        for c in circles:
            if a.hits(c):
                problems.append(f"text overlaps circle:\n      {a}\n      {c}")

    if path.name == "hero.svg":
        for text in lines:
            if len(text) > HERO_MAX_COLS:
                problems.append(f"hero line is {len(text)} cols (max {HERO_MAX_COLS}): {text!r}")
    return problems


def main() -> int:
    files = [ASSETS / n for n in ("hero.svg", "route-dark.svg", "route-light.svg")]
    failed = False
    for path in files:
        if not path.exists():
            print(f"check_svgs: missing {path}", file=sys.stderr)
            failed = True
            continue
        problems = check(path)
        if problems:
            failed = True
            print(f"check_svgs: {path.name} — {len(problems)} problem(s)", file=sys.stderr)
            for p in problems:
                print(f"    {p}", file=sys.stderr)
        else:
            print(f"check_svgs: {path.name} ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
