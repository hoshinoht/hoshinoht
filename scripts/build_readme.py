#!/usr/bin/env python3
"""Generate README.md and the three SVGs from data/*.yaml.

Python 3.12, stdlib + PyYAML only. Deterministic given `today`.
Set READMEBUILD_TODAY=YYYY-MM-DD to override "today" for tests.
"""

from __future__ import annotations

import base64
import calendar
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"

GH = "https://github.com/"
MONO = "JetBrains Mono, ui-monospace, monospace"
EM = 0.6  # JetBrains Mono advance width: 600/1000 em

HERO_MAX_COLS = 100
HERO_COLS = 96


class BuildError(Exception):
    pass


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def load_yaml(name: str):
    with open(DATA / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def today() -> date:
    override = os.environ.get("READMEBUILD_TODAY")
    if override:
        return date.fromisoformat(override.strip())
    return date.today()


# --------------------------------------------------------------------------
# dates
# --------------------------------------------------------------------------

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_name) if m})
MON_ABBR = list(calendar.month_abbr)

DASHES = "–—-"


def parse_point(token: str, *, end: bool) -> date | None:
    """Parse 'Feb 2026', '2018' or 'Present'. Returns None for open-ended."""
    token = token.strip()
    if not token or token.lower() in {"present", "now", "current", "ongoing"}:
        return None
    m = re.fullmatch(r"([A-Za-z]+)\.?\s+(\d{4})", token)
    if m:
        month = MONTHS.get(m.group(1).lower())
        if month is None:
            raise BuildError(f"unknown month in period: {token!r}")
        year = int(m.group(2))
    else:
        m = re.fullmatch(r"(\d{4})", token)
        if not m:
            raise BuildError(f"cannot parse date {token!r}")
        year = int(m.group(1))
        month = 12 if end else 1
    day = calendar.monthrange(year, month)[1] if end else 1
    return date(year, month, day)


@dataclass
class Span:
    start: date
    end: date | None  # None => open-ended ("Present")
    end_precise: bool = False  # True when the source gave a day, not a month

    def state(self, ref: date) -> str:
        if self.start > ref:
            return "queued"
        if self.end is None or self.end >= ref:
            return "running"
        return "done"


def parse_period(period: str) -> Span:
    parts = re.split(f"\\s*[{DASHES}]\\s*", str(period).strip(), maxsplit=1)
    if len(parts) == 1:
        start = parse_point(parts[0], end=False)
        end = parse_point(parts[0], end=True)
    else:
        start = parse_point(parts[0], end=False)
        end = parse_point(parts[1], end=True)
    if start is None:
        raise BuildError(f"period has no start: {period!r}")
    return Span(start, end)


def as_date(value, *, end: bool) -> date:
    """YAML scalars: date, 'YYYY-MM-DD' or 'YYYY-MM'."""
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}", text):
        year, month = (int(x) for x in text.split("-"))
        day = calendar.monthrange(year, month)[1] if end else 1
        return date(year, month, day)
    return date.fromisoformat(text)


def decimal_year(d: date) -> float:
    days = 366 if calendar.isleap(d.year) else 365
    return d.year + (d.timetuple().tm_yday - 1) / days


def days_between(a: date, b: date) -> int:
    return (b - a).days


# --------------------------------------------------------------------------
# text helpers
# --------------------------------------------------------------------------

def esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def text_width(text: str, size: float) -> float:
    return len(text) * EM * size


def first_sentence(text: str) -> str:
    text = str(text).strip()
    m = re.search(r"(?<=[a-z0-9\)])\.\s", text + " ")
    if m:
        return text[: m.start() + 1].strip()
    return text.rstrip(".")


def md_escape(text: str) -> str:
    return str(text).replace("|", "\\|")


# --------------------------------------------------------------------------
# repos
# --------------------------------------------------------------------------

def repo_path(pid: str, projects_by_id: dict, overrides: dict) -> str | None:
    if pid in overrides:
        return overrides[pid]
    raw = (projects_by_id.get(pid) or {}).get("repo")
    if not raw:
        return None
    raw = str(raw).strip()
    if "(" in raw or " " in raw:
        return None
    return raw


def repo_link(path: str | None, label: str) -> str:
    if not path:
        return f"`{label}`"
    return f"[`{label}`]({GH}{path})"


# --------------------------------------------------------------------------
# fonts
# --------------------------------------------------------------------------

_FONT_CACHE: dict[str, str] = {}


def font_face(weight: int) -> str:
    name = "JetBrainsMono-Bold.woff2" if weight >= 700 else "JetBrainsMono-Regular.woff2"
    if name not in _FONT_CACHE:
        path = FONTS / name
        if not path.exists():
            raise BuildError(
                f"missing {path}. Fetch the JetBrains Mono webfonts into assets/fonts/ "
                "(https://github.com/JetBrains/JetBrainsMono/releases -> fonts/webfonts/)."
            )
        _FONT_CACHE[name] = base64.b64encode(path.read_bytes()).decode("ascii")
    return (
        '@font-face{font-family:"JetBrains Mono";font-style:normal;'
        f"font-weight:{weight};"
        f'src:url(data:font/woff2;base64,{_FONT_CACHE[name]}) format("woff2")}}'
    )


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------

@dataclass
class Job:
    label: str
    span: Span
    note: str
    state: str


def build_jobs(exp, awards, cfg, ref: date) -> list[Job]:
    window = int(cfg.get("jobs_window_days", 120))
    labels = cfg.get("jobs_labels") or {}
    notes = cfg.get("jobs_notes") or {}
    jobs: list[Job] = []

    for role in exp.get("roles", []):
        span = parse_period(role["period"])
        state = span.state(ref)
        if state == "done" and span.end and days_between(span.end, ref) > window:
            continue
        rid = role["id"]
        label = labels.get(rid) or re.sub(r"[^a-z0-9]+", "-", role["title"].lower()).strip("-")
        jobs.append(Job(label, span, notes.get(rid, ""), state))

    for award in awards.get("awards", []):
        if "start" not in award or "end" not in award:
            continue
        span = Span(as_date(award["start"], end=False), as_date(award["end"], end=True),
                    end_precise=True)
        state = span.state(ref)
        if state == "done" and span.end and days_between(span.end, ref) > window:
            continue
        label = award.get("job_label") or award["id"]
        jobs.append(Job(label, span, award.get("job_note", ""), state))

    jobs.sort(key=lambda j: (j.span.start, j.label))
    return jobs


def fmt_job_period(span: Span) -> str:
    if span.end is None:
        return f"{MON_ABBR[span.start.month]} {span.start.year} –"
    if span.start.year == span.end.year:
        right = (f"{span.end.day} {MON_ABBR[span.end.month]} {span.end.year}"
                 if span.end_precise else f"{MON_ABBR[span.end.month]} {span.end.year}")
        return f"{MON_ABBR[span.start.month]} – {right}"
    return (f"{MON_ABBR[span.start.month]} {span.start.year} – "
            f"{MON_ABBR[span.end.month]} {span.end.year}")


def render_jobs(jobs: list[Job]) -> str:
    running = [j for j in jobs if j.state == "running"]
    running.sort(key=lambda j: j.span.start, reverse=True)
    flags = {}
    if running:
        flags[id(running[0])] = "+"
    if len(running) > 1:
        flags[id(running[1])] = "-"

    lines = []
    for n, job in enumerate(jobs, start=1):
        flag = flags.get(id(job), " ")
        row = f"[{n}]  {flag} {job.state:<10}{job.label:<31}{fmt_job_period(job.span):<22}{job.note}"
        lines.append(row.rstrip())
    return "\n".join(lines)


# --------------------------------------------------------------------------
# route-map layout
# --------------------------------------------------------------------------

ROUTE_W, ROUTE_H = 800, 236
X0, PPY = 90.0, 65.0          # x = X0 + (decimal_year - base) * PPY
RIGHT_EDGE = 792.0
AWARD_Y, AWARD_BAND = 48.0, 24.0
SCHOOL_Y = 110.0
WORK_Y1, WORK_Y2 = 185.0, 205.0


@dataclass
class Layout:
    base_year: int
    ticks: list[tuple[float, int]] = field(default_factory=list)
    award_dots: list[dict] = field(default_factory=list)
    award_labels: list[dict] = field(default_factory=list)
    school_segments: list[dict] = field(default_factory=list)
    stations: list[dict] = field(default_factory=list)
    ns_bar: dict | None = None
    work_bars: list[dict] = field(default_factory=list)
    work_labels: list[dict] = field(default_factory=list)
    connectors: list[dict] = field(default_factory=list)


def build_layout(exp, awards, cfg, ref: date) -> Layout:
    education = sorted(exp.get("education", []),
                       key=lambda e: parse_period(e["period"]).start)
    if not education:
        raise BuildError("experience.yaml has no education entries")

    base_year = parse_period(education[0]["period"]).start.year
    lay = Layout(base_year=base_year)

    def X(d: date) -> float:
        return X0 + (decimal_year(d) - base_year) * PPY

    # -- year ticks (every 2 years, base .. base+10) -------------------------
    for year in range(base_year, base_year + 11, 2):
        lay.ticks.append((X(date(year, 1, 1)), year))

    # -- awards -------------------------------------------------------------
    on_map = []
    for a in awards.get("awards", []):
        if not a.get("on_map"):
            continue
        if "start" in a:
            start = as_date(a["start"], end=False)
            end = as_date(a["end"], end=True) if "end" in a else None
        else:
            start = as_date(a["date"], end=False)
            end = None
        on_map.append({
            "x": X(start),
            "text": a.get("short") or a["name"],
            "hollow": bool(end and end > ref),
        })
    on_map.sort(key=lambda it: it["x"])

    clusters: list[list[dict]] = []
    for item in on_map:
        if clusters and item["x"] - clusters[-1][-1]["x"] <= 40:
            clusters[-1].append(item)
        else:
            clusters.append([item])
    for cluster in clusters:
        for i in range(1, len(cluster)):
            cluster[i]["x"] = max(cluster[i]["x"], cluster[i - 1]["x"] + 11)

    placed: list[tuple[int, float, float]] = []  # (band, left, right)
    for cluster in clusters:
        widths = [text_width(it["text"], 10) for it in cluster]
        maxw = max(widths)
        dot_lo, dot_hi = cluster[0]["x"] - 4, cluster[-1]["x"] + 4
        if len(cluster) == 1 and cluster[0]["x"] + 9 + maxw <= RIGHT_EDGE:
            anchor, ax = "start", cluster[0]["x"] + 9
            lo, hi = ax, ax + maxw
        else:
            anchor, ax = "end", cluster[0]["x"] - 11
            lo, hi = ax - maxw, ax
        lo, hi = min(lo, dot_lo), max(hi, dot_hi)

        band = 0
        while any(b == band and not (hi < pl or lo > pr) for b, pl, pr in placed):
            band += 1
        placed.append((band, lo, hi))

        cy = AWARD_Y + AWARD_BAND * band
        for item in cluster:
            lay.award_dots.append({"cx": item["x"], "cy": cy, "hollow": item["hollow"]})
        for i, item in enumerate(cluster):
            lay.award_labels.append({
                "x": ax, "y": cy - 4 + 13 * i, "anchor": anchor, "text": item["text"],
            })

    # -- school -------------------------------------------------------------
    edu_short = cfg.get("education_short") or {}
    edu_station = cfg.get("education_station") or {}
    spans = []
    for entry in education:
        span = parse_period(entry["period"])
        end = span.end or ref
        spans.append((entry, X(span.start), X(end)))

    for i, (entry, x1, x2) in enumerate(spans):
        lay.school_segments.append({"x1": x1, "x2": x2, "kind": "solid"})
        if i + 1 < len(spans):
            nxt = spans[i + 1][1]
            if nxt > x2:
                lay.school_segments.append({"x1": x2, "x2": nxt, "kind": "dashed"})
        inst = entry["institution"]
        lay.stations.append({
            "x": x1,
            "name": edu_station.get(inst, inst),
            "sub": edu_short.get(inst, f"{entry.get('degree','')} · {entry.get('period','')}"),
            "dashed": False,
        })

    last_x = spans[-1][2]
    lay.school_segments.append({"x1": last_x, "x2": RIGHT_EDGE, "kind": "faint"})
    lay.stations.append({"x": last_x, "name": None, "sub": None, "dashed": True,
                         "tag": "next stop ▸"})

    ns = cfg.get("national_service")
    if ns and len(spans) >= 2:
        gaps = [(spans[i][2], spans[i + 1][1]) for i in range(len(spans) - 1)]
        gaps = [g for g in gaps if g[1] > g[0]]
        if gaps:
            ns_span = parse_period(ns["period"])
            nx1, nx2 = X(ns_span.start), X(ns_span.end or ref)
            gap = max(gaps, key=lambda g: min(nx2, g[1]) - max(nx1, g[0]))
            lo, hi = max(nx1, gap[0] + 6), min(nx2, gap[1] - 6)
            if hi > lo:
                lay.ns_bar = {"x1": lo, "x2": hi, "label": ns["label"]}

    # -- work ---------------------------------------------------------------
    work_short = cfg.get("work_short") or {}
    roles = [r for r in exp.get("roles", []) if r.get("type") in {"internship", "research"}]
    roles.sort(key=lambda r: parse_period(r["period"]).start)

    prev_row1_end: date | None = None
    row1_boxes: list[tuple[float, float]] = []
    for role in roles:
        span = parse_period(role["period"])
        end = span.end or ref
        x1, x2 = X(span.start), X(end)
        future = span.start > ref
        row2 = (prev_row1_end is not None
                and days_between(prev_row1_end, span.start) <= 30)
        label = work_short.get(role["id"], role["title"])

        if row2:
            lay.connectors.append({"x": x1})
            lay.work_bars.append({"x1": x1, "x2": x2, "y": WORK_Y2,
                                  "dashed": future, "width": 4})
            lay.work_labels.append({"x": x2, "y": 222, "anchor": "end",
                                    "text": label, "dim": future})
        else:
            lay.work_bars.append({"x1": x1, "x2": x2, "y": WORK_Y1,
                                  "dashed": future, "width": 3 if future else 4})
            width = text_width(label, 10)
            if x1 + width <= RIGHT_EDGE:
                lx, anchor = x1, "start"
                lo, hi = x1, x1 + width
            else:
                lx, anchor = RIGHT_EDGE, "end"
                lo, hi = RIGHT_EDGE - width, RIGHT_EDGE
            y = 176.0
            if any(not (hi < pl or lo > pr) for pl, pr in row1_boxes):
                y = 164.0
            else:
                row1_boxes.append((lo, hi))
            lay.work_labels.append({"x": lx, "y": y, "anchor": anchor,
                                    "text": label, "dim": future})
            prev_row1_end = end

    return lay


# --------------------------------------------------------------------------
# SVG writers
# --------------------------------------------------------------------------

DARK = {
    "bg": "#26282D", "bar": "#2E3036", "main": "#C4A2D4", "secondary": "#6EC4B8",
    "award": "#DDA05C", "text": "#F3F5FC", "dim": "#8A8D99", "grey": "#6E7280",
    "tick": "#3A3D44",
}
LIGHT = {
    "bg": "#EFF1F5", "bar": "#DCE0E8", "main": "#8839EF", "secondary": "#179299",
    "award": "#FE640B", "text": "#4C4F69", "dim": "#6C6F85", "grey": "#9CA0B0",
    "tick": "#CCD0DA",
}


def hero_svg(cfg, n_more: int) -> tuple[str, list[str]]:
    c = DARK
    prompt = f'<tspan fill="{c["main"]}">➜</tspan> <tspan fill="{c["secondary"]}">~</tspan> '

    rows: list[dict] = []
    rows.append({"kind": "prompt", "cmd": "whoami"})
    whoami = cfg["whoami"]
    rows.append({"kind": "out", "text": whoami[0]})
    for extra in whoami[1:]:
        rows.append({"kind": "out", "text": extra, "fill": c["dim"]})
    rows.append({"kind": "prompt",
                 "cmd": f'ats-tailor match --index {cfg["handle"]} --jd {cfg["hero_jd_label"]}'})
    for match in cfg["hero_matches"]:
        label = match.get("label") or match["id"]
        rows.append({"kind": "match", "score": f"{float(match['score']):.2f}",
                     "label": label, "text": match["line"]})
    rows.append({"kind": "out", "text": cfg["hero_more_line"].replace("{n}", str(n_more)),
                 "fill": c["dim"]})
    rows.append({"kind": "prompt", "cmd": "cat ~/route", "cursor": True})

    plain: list[str] = []
    for row in rows:
        if row["kind"] == "prompt":
            plain.append(f'➜ ~ {row["cmd"]}' + (" ▍" if row.get("cursor") else ""))
        elif row["kind"] == "match":
            plain.append(f'{row["score"]}  {row["label"]:<21}  {row["text"]}')
        else:
            plain.append(row["text"])

    for line in plain:
        if len(line) > HERO_MAX_COLS:
            raise BuildError(f"hero line is {len(line)} chars (max {HERO_MAX_COLS}): {line!r}")

    ys: list[float] = []
    y = 54.0
    for i, row in enumerate(rows):
        if i:
            prev = rows[i - 1]
            y += 30 if row["kind"] == "prompt" else (22 if prev["kind"] == "prompt" else 20)
        ys.append(y)
    height = int(ys[-1] + 12)

    body: list[str] = []
    delay = 0.2
    for row, yy, raw in zip(rows, ys, plain):
        style = f'animation-delay:{delay:.1f}s'
        delay += 0.7 if row["kind"] == "prompt" else 0.35
        if row["kind"] == "prompt":
            inner = prompt + esc(row["cmd"])
            if row.get("cursor"):
                inner += f' <tspan class="cur">▍</tspan>'
            body.append(f'    <text x="16" y="{yy:g}" class="l" style="{style}">{inner}</text>')
        elif row["kind"] == "match":
            pad = " " * (21 - len(row["label"]) + 2)
            inner = (f'<tspan fill="{c["award"]}">{row["score"]}</tspan>  '
                     f'{esc(row["label"])}{esc(pad)}{esc(row["text"])}')
            body.append(f'    <text x="16" y="{yy:g}" class="l" style="{style}" '
                        f'xml:space="preserve">{inner}</text>')
        else:
            fill = f' fill="{row["fill"]}"' if row.get("fill") else ""
            body.append(f'    <text x="16" y="{yy:g}" class="l"{fill} style="{style}">'
                        f'{esc(row["text"])}</text>')

    title = f'{cfg["handle"]} — zsh — {HERO_COLS}×{len(rows)}'
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 {height}" width="100%" \
role="img" font-family="{MONO}" font-size="12.5">
  <style>
    {font_face(400)}
    .l{{opacity:0;animation:show .01s linear forwards}}
    @keyframes show{{to{{opacity:1}}}}
    .cur{{animation:blink 1s steps(1) infinite}}
    @keyframes blink{{50%{{opacity:0}}}}
    @media (prefers-reduced-motion: reduce){{
      .l{{opacity:1;animation:none}}
      .cur{{animation:none}}
    }}
  </style>
  <rect width="800" height="{height}" rx="8" fill="{c["bg"]}"/>
  <rect width="800" height="28" rx="8" fill="{c["bar"]}"/>
  <circle cx="16" cy="14" r="5" fill="#E27878"/><circle cx="32" cy="14" r="5" fill="#E8C97E"/>\
<circle cx="48" cy="14" r="5" fill="#82C8A0"/>
  <text x="400" y="18" text-anchor="middle" fill="{c["dim"]}" font-size="11">{esc(title)}</text>
  <g fill="{c["text"]}">
{chr(10).join(body)}
  </g>
</svg>
'''
    return svg, plain


def route_svg(lay: Layout, c: dict) -> str:
    out: list[str] = []
    a = out.append

    a(f'  <rect width="{ROUTE_W}" height="{ROUTE_H}" rx="8" fill="{c["bg"]}"/>')

    a('  <g class="yr">')
    for x, year in lay.ticks:
        a(f'    <text x="{x:g}" y="14">{year}</text>')
    a('  </g>')
    a(f'  <g stroke="{c["tick"]}">')
    for x, _ in lay.ticks:
        a(f'    <line x1="{x:g}" y1="20" x2="{x:g}" y2="26"/>')
    a('  </g>')

    # awards
    a(f'  <text x="8" y="52" class="row">awards</text>')
    a('  <g class="aw">')
    for dot in lay.award_dots:
        fill = "none" if dot["hollow"] else c["award"]
        a(f'    <circle cx="{dot["cx"]:g}" cy="{dot["cy"]:g}" r="4" fill="{fill}" '
          f'stroke="{c["award"]}"/>')
    for lb in lay.award_labels:
        a(f'    <text x="{lb["x"]:g}" y="{lb["y"]:g}" text-anchor="{lb["anchor"]}">'
          f'{esc(lb["text"])}</text>')
    a('  </g>')

    # school
    a(f'  <text x="8" y="114" class="row">school</text>')
    for seg in lay.school_segments:
        if seg["kind"] == "solid":
            a(f'  <line x1="{seg["x1"]:g}" y1="{SCHOOL_Y:g}" x2="{seg["x2"]:g}" y2="{SCHOOL_Y:g}" '
              f'stroke="{c["main"]}" stroke-width="6" stroke-linecap="round"/>')
        elif seg["kind"] == "dashed":
            a(f'  <line x1="{seg["x1"]:g}" y1="{SCHOOL_Y:g}" x2="{seg["x2"]:g}" y2="{SCHOOL_Y:g}" '
              f'stroke="{c["main"]}" stroke-width="2" stroke-dasharray="3 5"/>')
        else:
            a(f'  <line x1="{seg["x1"]:g}" y1="{SCHOOL_Y:g}" x2="{seg["x2"]:g}" y2="{SCHOOL_Y:g}" '
              f'stroke="{c["main"]}" stroke-width="6" stroke-linecap="round" opacity=".3"/>')
    if lay.ns_bar:
        ns = lay.ns_bar
        mid = (ns["x1"] + ns["x2"]) / 2
        a(f'  <line x1="{ns["x1"]:g}" y1="{SCHOOL_Y:g}" x2="{ns["x2"]:g}" y2="{SCHOOL_Y:g}" '
          f'stroke="{c["grey"]}" stroke-width="4" stroke-linecap="round"/>')
        a(f'  <text x="{mid:g}" y="94" class="sd" text-anchor="middle">{esc(ns["label"])}</text>')
    for st in lay.stations:
        dash = ' stroke-dasharray="3 3"' if st["dashed"] else ""
        a(f'  <circle cx="{st["x"]:g}" cy="{SCHOOL_Y:g}" r="7" fill="{c["bg"]}" '
          f'stroke="{c["main"]}" stroke-width="3"{dash}/>')
        if st.get("name"):
            a(f'  <text x="{st["x"]:g}" y="132" class="st">{esc(st["name"])}</text>')
            a(f'  <text x="{st["x"]:g}" y="145" class="sd">{esc(st["sub"])}</text>')
        if st.get("tag"):
            a(f'  <text x="{st["x"] + 8:g}" y="94" class="sd" text-anchor="end">'
              f'{esc(st["tag"])}</text>')

    # work
    a(f'  <text x="8" y="189" class="row">work</text>')
    a(f'  <g stroke="{c["secondary"]}" stroke-linecap="round">')
    for bar in lay.work_bars:
        dash = ' stroke-dasharray="2 4"' if bar["dashed"] else ""
        a(f'    <line x1="{bar["x1"]:g}" y1="{bar["y"]:g}" x2="{bar["x2"]:g}" y2="{bar["y"]:g}" '
          f'stroke-width="{bar["width"]}"{dash}/>')
    for con in lay.connectors:
        a(f'    <line x1="{con["x"]:g}" y1="{WORK_Y1:g}" x2="{con["x"]:g}" y2="{WORK_Y2:g}" '
          f'stroke-width="2"/>')
    a('  </g>')
    a('  <g class="wk">')
    for lb in lay.work_labels:
        fill = f' fill="{c["dim"]}"' if lb["dim"] else ""
        a(f'    <text x="{lb["x"]:g}" y="{lb["y"]:g}" text-anchor="{lb["anchor"]}"{fill}>'
          f'{esc(lb["text"])}</text>')
    a('  </g>')

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {ROUTE_W} {ROUTE_H}" \
width="100%" role="img" font-family="{MONO}">
  <style>
    {font_face(400)}
    {font_face(700)}
    .yr{{fill:{c["grey"]};font-size:10px;text-anchor:middle}}
    .row{{fill:{c["grey"]};font-size:10px}}
    .st{{fill:{c["text"]};font-size:11.5px;font-weight:700}}
    .sd{{fill:{c["dim"]};font-size:10px}}
    .aw{{fill:{c["award"]};font-size:10px}}
    .wk{{fill:{c["secondary"]};font-size:10px}}
  </style>
{chr(10).join(out)}
</svg>
'''


# --------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------

def build_markdown(cfg, exp, projects, skills, jobs_block, hero_alt, route_alt) -> str:
    by_id = {p["id"]: p for p in projects.get("projects", [])}
    overrides = cfg.get("repo_overrides") or {}
    out: list[str] = []
    a = out.append

    a("<!-- generated by scripts/build_readme.py — edit data/*.yaml, not this file -->")
    a("")
    a(f'<img src="assets/hero.svg" width="100%" alt="{esc(hero_alt)}">')
    a("")
    a("<picture>")
    a('  <source media="(prefers-color-scheme: dark)" srcset="assets/route-dark.svg">')
    a(f'  <img src="assets/route-light.svg" width="100%" alt="{esc(route_alt)}">')
    a("</picture>")
    a("")

    a("### `$ cat ~/history.log`")
    a("")
    a("| when | stop | what happened there |")
    a("| --- | --- | --- |")
    for row in cfg["history"]:
        a(f'| `{row["when"]}` | {md_escape(row["stop"])} | {md_escape(row["what"])} |')
    a("")

    a("### `$ jobs`")
    a("")
    a("```text")
    a(jobs_block)
    a("```")
    a("")

    a("### `$ ls ~/projects --sort=layer`")
    a("")
    a("| layer | repo | what it proves | measured |")
    a("| --- | --- | --- | --- |")
    for row in cfg["projects"]:
        proj = by_id.get(row["id"])
        if proj is None:
            raise BuildError(f"readme.yaml projects: {row['id']!r} is not in projects.yaml")
        link = repo_link(repo_path(row["id"], by_id, overrides), row["id"])
        proves = first_sentence(proj.get("summary", ""))
        a(f'| {md_escape(row["layer"])} | {link} | {md_escape(proves)} | '
          f'{md_escape(row["number"])} |')
    a("")

    a("### `$ ls ~/agent-tools`")
    a("")
    a("| repo | lang | what it does |")
    a("| --- | --- | --- |")
    for row in cfg["agent_tools"]:
        a(f'| [`{row["repo"]}`]({GH}{cfg["handle"]}/{row["repo"]}) | `{row["lang"]}` | '
          f'{md_escape(row["line"])} |')
    a("")

    a("### `$ ls ~/side-quests`")
    a("")
    a("| repo | lang | why it exists |")
    a("| --- | --- | --- |")
    for row in cfg["side_quests"]:
        a(f'| [`{row["repo"]}`]({GH}{cfg["handle"]}/{row["repo"]}) | `{row["lang"]}` | '
          f'{md_escape(row["line"])} |')
    a("")

    a("<details>")
    a("<summary><code>$ man haoting</code> — skills, by evidence</summary>")
    a("")
    role_names = cfg.get("work_short") or {}
    roles_by_id = {r["id"]: r for r in exp.get("roles", [])}
    for cat in skills.get("categories", []):
        advanced = [s for s in cat.get("skills", []) if s.get("proficiency") == "advanced"]
        if not advanced:
            continue
        a(f'**{cat["name"]}**')
        a("")
        for skill in advanced:
            bits = []
            for ev in (skill.get("evidence") or [])[:3]:
                detail = ev.get("detail")
                if not detail:
                    continue
                ref = ev.get("ref")
                if ev.get("type") == "project" and ref in by_id:
                    link = repo_link(repo_path(ref, by_id, overrides), ref)
                    bits.append(f"{detail} ({link})")
                elif ev.get("type") == "role" and ref in roles_by_id:
                    bits.append(f"{detail} ({role_names.get(ref, roles_by_id[ref]['title'])})")
                else:
                    bits.append(detail)
            if bits:
                a(f'- **{skill["name"]}** — ' + " · ".join(bits))
        a("")
    a("</details>")
    a("")

    a("### `$ cat ~/contact`")
    a("")
    a(" · ".join(f'[{c["label"]}]({c["url"]})' for c in cfg["contact"]))
    a("")
    a("---")
    a("")
    a(f'<sub>{esc(cfg["footer"])}</sub>')
    a("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    ref = today()
    cfg = load_yaml("readme.yaml")
    exp = load_yaml("experience.yaml")
    projects = load_yaml("projects.yaml")
    skills = load_yaml("skills.yaml")
    awards = load_yaml("awards.yaml")

    n_more = max(len(projects.get("projects", [])) - len(cfg["hero_matches"]), 0)
    hero, hero_lines = hero_svg(cfg, n_more)
    lay = build_layout(exp, awards, cfg, ref)
    dark = route_svg(lay, DARK)
    light = route_svg(lay, LIGHT)

    jobs_block = render_jobs(build_jobs(exp, awards, cfg, ref))

    hero_alt = "terminal: whoami — " + cfg["whoami"][0]
    first_year = lay.base_year
    route_alt = (f"route map: education from {first_year} to "
                 f"{lay.ticks[-1][1]}, with work roles and awards")

    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "hero.svg").write_text(hero, encoding="utf-8")
    (ASSETS / "route-dark.svg").write_text(dark, encoding="utf-8")
    (ASSETS / "route-light.svg").write_text(light, encoding="utf-8")
    (ROOT / "README.md").write_text(
        build_markdown(cfg, exp, projects, skills, jobs_block, hero_alt, route_alt),
        encoding="utf-8")

    print(f"build_readme: today={ref.isoformat()}  hero={len(hero_lines)} lines  "
          f"jobs={len(jobs_block.splitlines())} rows")
    print(jobs_block)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BuildError as exc:
        print(f"build_readme: {exc}", file=sys.stderr)
        sys.exit(1)
