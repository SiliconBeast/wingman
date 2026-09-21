"""Community-maintained internship lists on GitHub.

Two formats are understood:
  * `.github/scripts/listings.json` (SimplifyJobs and its forks) - structured, preferred.
  * Markdown tables in README files (speedyapply, Canadian lists, Northwestern quant list...).
"""
from __future__ import annotations

import html
import re

from ..classify import split_locations
from ..resolve import Board, canonical_key
from ..util import clean_inline, clean_url, get_json, get_text, http_status, parse_when
from .ats import detail_via_url
from .base import Adapter, Job

LINK_MD = re.compile(r"\[((?:!\[[^\]]*\]\([^)]*\))|[^\]]*)\]\((https?://[^)\s]+)\)")
LINK_HTML = re.compile(r"<a\b[^>]*href=[\"'](https?://[^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
NOT_APPLY = re.compile(r"simplify\.jobs/c/|img\.shields\.io|imgur\.com|github\.com/user-attachments|"
                       r"swelist\.com|/(?:install|copilot)\b", re.I)
CLOSED = ("🔒", "❌", "⛔")
# Boards whose single-posting API we can read, so list roles get descriptions too. Other
# links ("ls:") are tried through the page's JobPosting data, at lower priority.
DETAILABLE = ("gh:", "wd:", "ef:", "or:", "sr:", "lv:", "az:", "ls:")
ROLE_ABBR = {"QR": "Quantitative Researcher Intern", "QT": "Quantitative Trader Intern",
             "QD": "Quantitative Developer Intern", "SWE": "Software Engineer Intern",
             "HW": "Hardware Engineer Intern", "DS": "Data Scientist Intern", "QA": "Quantitative Analyst Intern",
             "ML": "Machine Learning Intern", "PM": "Product Manager Intern", "TR": "Trading Intern"}


def split_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", body)]


def header_roles(cells: list[str]) -> list[str | None] | None:
    roles: list[str | None] = []
    for c in cells:
        t = clean_inline(c).lower()
        if re.search(r"company|employer|firm|organi[sz]ation", t):
            roles.append("company")
        elif re.search(r"role|position|title|program|job", t):
            roles.append("title")
        elif "location" in t:
            roles.append("location")
        elif re.search(r"apply|application|link|posting|url", t):
            roles.append("link")
        elif re.search(r"date|posted|age|added", t):
            roles.append("date")
        elif re.search(r"term|season", t):
            roles.append("term")
        else:
            roles.append(None)
    return roles if ("title" in roles and "link" in roles) else None


def links_in(cell: str) -> list[tuple[str, str]]:
    out = [(label, url) for label, url in LINK_MD.findall(cell)]
    out += [(label, url) for url, label in LINK_HTML.findall(cell)]
    return out


def locations_in(cell: str) -> list[str]:
    if not cell:
        return []
    cell = re.sub(r"(?is)<summary>.*?</summary>", " ", cell)
    parts = re.split(r"</br>|<br\s*/?>|;|\n", cell)
    out = []
    for p in parts:
        p = re.sub(r"\s*\+\d+\s*$", "", clean_inline(p))      # "San Francisco, CA +4"
        if p and not re.fullmatch(r"\d+ locations?", p, re.I):
            out.append(p)
    return out


class GitHubList(Adapter):
    kind = "list"
    direct = False

    def __init__(self, spec: dict):
        self.repo = spec["repo"].strip("/")
        super().__init__(Board(name=f"list:{self.repo}", kind="list", url=f"https://github.com/{self.repo}"))
        self.files = spec.get("files") or ["README.md"]
        self.branch = spec.get("branch", "HEAD")
        self.terms = list(spec.get("terms") or [])
        self.strict = bool(spec.get("strict"))       # list mixes in non-internship roles: require an intern title
        m = re.search(r"(20\d\d)", self.repo)
        self.year = spec.get("year") or (int(m[1]) if m else None)
        s = re.search(r"(Summer|Fall|Winter|Spring)[-_ ]?(20\d\d)", self.repo, re.I)
        self.season_term = f"{s[1].title()} {s[2]}" if s else None

    @property
    def label(self):
        return f"list:{self.repo}"

    def raw(self, path: str) -> str:
        return f"https://raw.githubusercontent.com/{self.repo}/{self.branch}/{path}"

    def list_jobs(self):
        try:
            data = get_json(self.raw(".github/scripts/listings.json"))
        except Exception as e:  # 404 means "no JSON, read the README"
            if http_status(e) != 404:
                raise
            data = None
        if isinstance(data, list):
            return self.from_listings(data)
        jobs: list[Job] = []
        for f in self.files:
            try:
                jobs += self.from_markdown(get_text(self.raw(f)))
            except Exception as e:
                if http_status(e) != 404:
                    raise
        return jobs

    # ---------------------------------------------------------- listings.json
    def from_listings(self, data: list) -> list[Job]:
        out = []
        for x in data:
            if not x.get("is_visible", True) or not x.get("active", True):
                continue
            url = clean_url(x.get("url"))
            if not url:
                continue
            terms = [t for t in (x.get("terms") or []) if t and t != "N/A"]
            season = str(x.get("season") or "").title()
            if not terms and season and self.season_term and self.season_term.startswith(season):
                terms = [self.season_term]
            key = canonical_key(url) or f"ls:{x.get('id') or url}"
            out.append(Job(key=key, needs_detail=key.startswith(DETAILABLE),
                           company=html.unescape(str(x.get("company_name") or "")).strip(),
                           title=html.unescape(str(x.get("title") or "")).strip(), url=url,
                           locations=list(x.get("locations") or []), posted=parse_when(x.get("date_posted")),
                           extra={"assume_intern": not self.strict, "list_terms": terms, "category_hint": x.get("category"),
                                  "degrees": x.get("degrees"), "sponsorship": x.get("sponsorship")}))
        return out

    # ---------------------------------------------------------- markdown tables
    def from_markdown(self, text: str) -> list[Job]:
        out: list[Job] = []
        heading, heading_locs, roles, last_company = None, [], None, None
        for line in text.splitlines():
            s = line.strip()
            h = re.match(r"^#{2,4}\s+(.+?)\s*#*$", s)
            if h:
                heading, heading_locs, roles = clean_inline(h[1]), [], None
                continue
            lm = re.match(r"^\*\*Locations?\*\*:\s*(.*)$", s, re.I)
            if lm:
                heading_locs = [p for p in (clean_inline(x) for x in re.split(r",|/|;| and ", lm[1])) if p]
                continue
            if not s.startswith("|"):
                roles = None                                  # any other line ends the table
                continue
            cells = split_row(s)
            if roles is None:
                roles = header_roles(cells)
                continue
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            out += self._row(cells, roles, heading, heading_locs, last_company)
            if "company" in roles:
                comp = self._cell(cells, roles, "company")
                name = clean_inline(comp)
                if name and name not in ("↳",):
                    last_company = name
        return out

    @staticmethod
    def _cell(cells, roles, role) -> str:
        for i, r in enumerate(roles):
            if r == role and i < len(cells):
                return cells[i]
        return ""

    def _row(self, cells, roles, heading, heading_locs, last_company) -> list[Job]:
        company = clean_inline(self._cell(cells, roles, "company")) if "company" in roles else (heading or "")
        if company in ("↳", "") and "company" in roles:
            company = last_company or ""
        if not company:
            return []
        title = clean_inline(self._cell(cells, roles, "title"))
        locs = locations_in(self._cell(cells, roles, "location")) or list(heading_locs)
        posted = parse_when(clean_inline(self._cell(cells, roles, "date")))
        term_cell = clean_inline(self._cell(cells, roles, "term"))
        link_cell = self._cell(cells, roles, "link")
        row_text = " ".join(cells)
        extra = {"assume_intern": not self.strict, "list_year": self.year,
                 "list_terms": [t.strip() for t in re.split(r",|/", term_cell) if re.search(r"20\d\d", t)],
                 "default_terms": list(self.terms) or ([self.season_term] if self.season_term else [])}
        jobs = []
        if "company" in roles:                       # one posting per row
            if any(c in row_text for c in CLOSED) or re.search(r"\bclosed\b", clean_inline(link_cell), re.I):
                return []
            url = next((clean_url(u) for _, u in links_in(link_cell) if not NOT_APPLY.search(u)), None)
            if url and title:
                jobs.append(self._job(company, title, url, locs, posted, extra))
            return jobs
        base = ROLE_ABBR.get(title.upper(), title)       # quant-list style: one row per role, many links
        for label, url in links_in(link_cell):
            if NOT_APPLY.search(url) or any(c in label for c in CLOSED):
                continue
            name = clean_inline(label)
            full = f"{base} ({name})" if name else base
            if url := clean_url(url):
                jobs.append(self._job(company, full, url, locs, posted, extra))
        return jobs

    def _job(self, company, title, url, locs, posted, extra) -> Job:
        key = canonical_key(url) or f"ls:{url}"
        return Job(key=key, company=company, title=title, url=url, needs_detail=key.startswith(DETAILABLE),
                   locations=[x for loc in locs for x in split_locations(loc)] or locs, posted=posted,
                   extra=dict(extra))

    def detail(self, job):
        return detail_via_url(job)
