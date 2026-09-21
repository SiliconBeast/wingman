"""How well your resume matches a posting, and what in the posting could rule you out.

The keyword list lives in docs/keywords.json so the dashboard and the scanner score
the same way. Requirements count double, "nice to have" counts once, and boilerplate
(benefits, EEO statements) is ignored.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache

from .util import RESUME, ROOT

KEYWORDS = ROOT / "docs" / "keywords.json"
ACRONYM = re.compile(r"(?<![\w+#.])[A-Z][A-Z0-9+/&-]{1,7}(?![\w+#])")
HEAD_PREF = re.compile(r"preferred|nice to have|bonus|plus(?:es)?\b|desired|additional qualifications|good to have", re.I)
HEAD_REQ = re.compile(r"required|requirements|minimum|basic qualifications|must[- ]have|what you('|’)ll need|"
                      r"you (?:have|bring|should have)|qualifications|who you are|looking for|skills", re.I)
HEAD_RESP = re.compile(r"responsibilit|what you('|’)ll do|the role|your impact|day[- ]to[- ]day|you will", re.I)
HEAD_SKIP = re.compile(r"about (?:us|the company)|benefits|perks|compensation|equal (?:employment )?opportunity|"
                       r"salary|pay range|accommodation|privacy", re.I)
US_ONLY_FLAGS = {"us_citizen", "clearance", "no_sponsor"}
_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
GRAD = re.compile(rf"(?:graduat\w*|class of|degree completion|complete (?:your|their) degree)[^.\n]{{0,60}}?"
                  rf"((?:{_MON}\s+)?20\d\d)(?:\s*(?:-|–|—|and|to|through|thru)\s*((?:{_MON}\s+)?20\d\d))?", re.I)
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


@lru_cache(maxsize=1)
def lexicon():
    d = json.loads(KEYWORDS.read_text(encoding="utf-8"))
    b0, b1 = d["boundary"]
    terms = [(label, re.compile(b0 + rx + b1, 0 if cs else re.I)) for label, rx, cs, _grp in d["terms"]]
    flags = [(key, label, re.compile(rx, re.I)) for key, label, rx in d["flags"]]
    return terms, flags, set(d["stop"])


def tex_to_text(t: str) -> str:
    t = re.sub(r"(^|[^\\])%.*$", r"\1", t or "", flags=re.M)
    t = re.sub(r"\\([#%&_$])", r"\1", t)
    t = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", t)
    t = re.sub(r"\\[a-zA-Z@]+\*?(?:\[[^\]]*\])?", " ", t)
    t = re.sub(r"[{}$~^]", " ", t).replace("\\\\", " ")
    return re.sub(r"\s+", " ", t)


TEX = re.compile(r"\\(?:documentclass|begin\{document\}|section|resumeItem)")


def load_resume() -> tuple[str, str]:
    """-> (plain text an ATS would read, short hash). Empty strings when resume/ has no resume."""
    for name in ("resume.tex", "resume.md", "resume.txt"):
        p = RESUME / name
        if p.exists():
            raw = p.read_text(encoding="utf-8", errors="replace")
            return (tex_to_text(raw) if TEX.search(raw) else raw), hashlib.sha1(raw.encode()).hexdigest()[:10]
    return "", ""


def blocks(jd: str) -> dict[float, str]:
    """Group posting lines by how much they matter (2 = requirement, 1.5 = duties, 1 = nice to have)."""
    out: dict[float, list[str]] = {}
    w = 1.2
    for raw in re.split(r"\n+", jd or ""):
        line = raw.strip()
        if not line:
            continue
        heading = (len(line) < 70 and not re.match(r"^[•\-*·▪◦]", line)
                   and not re.search(r"[.;]$", re.sub(r":$", "", line)))
        if heading:
            if HEAD_PREF.search(line):
                w = 1
            elif HEAD_REQ.search(line):
                w = 2
            elif HEAD_RESP.search(line):
                w = 1.5
            elif HEAD_SKIP.search(line):
                w = 0
        if w:
            out.setdefault(w, []).append(line)
    return {w: "\n".join(lines) for w, lines in out.items()}


def scan(jd: str, resume: str) -> dict:
    terms, _flags, stop = lexicon()
    parts = blocks(jd)
    found: dict[str, float] = {}
    compiled = dict(terms)
    for label, rx in terms:
        for w, text in parts.items():
            if w > found.get(label, 0) and rx.search(text):
                found[label] = w
    counts: dict[str, int] = {}
    for w, text in parts.items():
        for a in ACRONYM.findall(text):
            if a in stop or any(rx.fullmatch(a) or rx.search(a) for _l, rx in terms):
                continue
            counts[a] = counts.get(a, 0) + 1
    for a, n in sorted(counts.items(), key=lambda kv: -kv[1])[:8]:
        if n >= 2:
            found[a] = max(found.get(a, 0), 1)
            compiled[a] = re.compile(r"(?<![\w+#.])" + re.escape(a) + r"(?![\w+#])")
    hit, miss = [], []
    for label, w in found.items():
        (hit if compiled[label].search(resume) else miss).append((label, w))
    total = sum(found.values())
    got = sum(w for _, w in hit)
    order = lambda x: (-x[1], x[0].lower())
    return {"score": int(100 * got / total + 0.5) if total else None,
            "hit": sorted(hit, key=order), "miss": sorted(miss, key=order)}


def _ym(s: str | None):
    if not s:
        return None
    m = re.match(rf"(?:({_MON})\s+)?(20\d\d)", s.strip(), re.I)
    if not m:
        return None
    month = MONTHS.get((m[1] or "")[:3].lower())
    return int(m[2]), month


def grad_window(jd: str):
    """-> ((y, m), (y, m)) for 'graduating between December 2027 and June 2028', or None."""
    m = GRAD.search(jd or "")
    if not m:
        return None
    a, b = _ym(m[1]), _ym(m[2])
    if a is None:
        return None
    start = (a[0], a[1] or 1)
    end = (b[0], b[1] or 12) if b else (a[0], a[1] or 12)
    return (start, end) if start <= end else (end, start)


def red_flags(jd: str, countries=(), sponsorship: str | None = None, graduation: str | None = None) -> list[str]:
    _terms, flags, _stop = lexicon()
    out = [key for key, _label, rx in flags if rx.search(jd or "")]
    if sponsorship == "U.S. Citizenship is Required":
        out.append("us_citizen")
    elif sponsorship == "Does Not Offer Sponsorship":
        out.append("no_sponsor")
    if countries and "US" not in countries:
        out = [f for f in out if f not in US_ONLY_FLAGS]
    grad = _ym(graduation) if graduation else None
    win = grad_window(jd) if grad else None
    if grad and win:
        g = (grad[0], grad[1] or 6)
        if not (win[0] <= g <= win[1]):
            out.append("grad")
    return sorted(set(out))


def assess(jd: str, resume: str, countries=(), sponsorship=None, graduation=None) -> dict:
    s = scan(jd, resume) if resume else {"score": None, "miss": []}
    gap = [label for label, w in s["miss"] if w >= 2][:5] or [label for label, _ in s["miss"]][:3]
    return {"fit": s["score"], "gap": gap, "flags": red_flags(jd, countries, sponsorship, graduation)}
