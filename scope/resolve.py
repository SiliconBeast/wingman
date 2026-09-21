"""Work out which job board a URL belongs to, and give every posting a stable key.

Stable keys let the same posting found through a company's own board and through a
GitHub list merge into one row (e.g. both resolve to "gh:8171041").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlparse

LOCALE = re.compile(r"^[a-z]{2}[-_][a-z]{2}$", re.I)
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
KINDS = ("greenhouse", "lever", "ashby", "workday", "smartrecruiters", "oracle", "eightfold", "jibe", "amazon", "jsonld")
# Eightfold career sites on company domains -> the domain their API expects
EIGHTFOLD_HOSTS = {"apply.careers.microsoft.com": "microsoft.com"}
# Link shorteners some lists use; the real posting is behind a redirect
REDIRECTORS = ("zapply.jobs", "simplify.jobs/p/", "jobright.ai/jobs/info")


@dataclass
class Board:
    name: str
    kind: str
    url: str
    opts: dict = field(default_factory=dict)


def segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def detect_kind(url: str) -> str:
    p = urlparse(url)
    host = p.netloc.lower()
    if "greenhouse.io" in host:
        return "greenhouse"
    if host.endswith("lever.co"):
        return "lever"
    if host.endswith("ashbyhq.com"):
        return "ashby"
    if host.endswith("myworkdayjobs.com") or host.endswith("myworkdaysite.com"):
        return "workday"
    if "smartrecruiters.com" in host:
        return "smartrecruiters"
    if "oraclecloud.com" in host:
        return "oracle"
    if host.endswith("eightfold.ai") or host in EIGHTFOLD_HOSTS:
        return "eightfold"
    if host.endswith("amazon.jobs"):
        return "amazon"
    if "icims=1" in p.query:
        return "jibe"
    return "jsonld"


def board_from_config(name: str, value) -> Board:
    if isinstance(value, dict):
        url = str(value.get("url", "")).strip()
        kind = value.get("ats") or detect_kind(url)
        opts = {k: v for k, v in value.items() if k not in ("url", "ats")}
    else:
        url, kind, opts = str(value).strip(), detect_kind(str(value)), {}
    if kind not in KINDS:
        raise ValueError(f"{name}: unknown ats '{kind}' (use one of {', '.join(KINDS)})")
    return Board(name=str(name), kind=kind, url=url, opts=opts)


def workday_parts(url: str) -> tuple[str, str, str, str] | None:
    """-> (host, tenant, site, public_base) for myworkdayjobs.com and myworkdaysite.com URLs."""
    p = urlparse(url)
    host = p.netloc.lower()
    segs = segments(p.path)
    if segs and LOCALE.match(segs[0]):
        segs = segs[1:]
    if host.endswith("myworkdaysite.com"):
        if "recruiting" in segs:
            i = segs.index("recruiting")
            if len(segs) > i + 2:
                tenant, site = segs[i + 1], segs[i + 2]
                return host, tenant, site, f"https://{host}/recruiting/{tenant}/{site}"
        return None
    if host.endswith("myworkdayjobs.com") and segs:
        tenant, site = host.split(".")[0], segs[0]
        return host, tenant, site, f"https://{host}/{site}"
    return None


def workday_key(tenant: str, path: str) -> str:
    m = re.search(r"_([A-Za-z0-9][A-Za-z0-9-]*)/?$", path or "")
    return f"wd:{tenant.lower()}:{(m[1] if m else path).lower()}"


def canonical_key(url: str | None) -> str | None:
    """Key for a posting URL on a known board, or None."""
    if not url:
        return None
    p = urlparse(url)
    host, path = p.netloc.lower(), p.path
    q = dict(parse_qsl(p.query))
    if q.get("gh_jid", "").isdigit():
        return f"gh:{q['gh_jid']}"
    if "greenhouse.io" in host:
        m = re.search(r"/jobs/(\d+)", path)
        return f"gh:{m[1]}" if m else None
    if host.endswith("lever.co") or host.endswith("ashbyhq.com"):
        m = re.search(rf"/({UUID})", path, re.I)
        if m:
            return f"{'lv' if 'lever' in host else 'ab'}:{m[1].lower()}"
        return None
    if host.endswith("myworkdayjobs.com") or host.endswith("myworkdaysite.com"):
        parts = workday_parts(url)
        if parts and "/job/" in path:
            return workday_key(parts[1], path)
        return None
    if "smartrecruiters.com" in host:
        segs = segments(path)
        if len(segs) >= 2:
            m = re.match(r"(\d{6,})", segs[1])
            if m:
                return f"sr:{segs[0].lower()}:{m[1]}"
        return None
    if "oraclecloud.com" in host:
        m = re.search(r"/job/(\d+)", path)
        return f"or:{host}:{m[1]}" if m else None
    if host.endswith("eightfold.ai") or host in EIGHTFOLD_HOSTS:
        m = re.search(r"/job/(\d+)", path)
        return f"ef:{host}:{m[1]}" if m else None
    if host.endswith("amazon.jobs"):
        m = re.search(r"/jobs/(\d+)", path)
        return f"az:{m[1]}" if m else None
    if "icims=1" in p.query:
        m = re.search(r"/jobs/(\d+)", path)
        return f"jb:{host}:{m[1]}" if m else None
    return None


def board_from_job_url(url: str) -> tuple[str, object] | None:
    """Turn a posting URL into the board you would add to companies.yaml (used by `suggest`)."""
    p = urlparse(url)
    host, segs = p.netloc.lower(), segments(p.path)
    kind = detect_kind(url)
    if kind == "workday":
        parts = workday_parts(url)
        return ("workday", parts[3]) if parts else None
    if kind == "greenhouse":
        if segs and segs[0] != "embed":
            return "greenhouse", f"https://job-boards.greenhouse.io/{segs[0]}"
        token = dict(parse_qsl(p.query)).get("for")
        return ("greenhouse", f"https://job-boards.greenhouse.io/{token}") if token else None
    if kind in ("lever", "ashby", "smartrecruiters") and segs:
        return kind, f"https://{host}/{segs[0]}"
    if kind == "oracle":
        m = re.search(r"/sites/([^/]+)", p.path)
        return ("oracle", f"https://{host}/hcmUI/CandidateExperience/en/sites/{m[1]}") if m else None
    if kind == "eightfold":
        return "eightfold", f"https://{host}/careers"
    if kind == "jibe":
        return "jibe", {"ats": "jibe", "url": f"https://{host}"}
    if kind == "amazon":
        return "amazon", "https://www.amazon.jobs"
    return None


def board_ident(value) -> str:
    """Normalized identity of a board, to tell whether two config values are the same board."""
    url = (value.get("url") if isinstance(value, dict) else str(value or "")).strip().rstrip("/").lower()
    wd = workday_parts(url)
    if wd:
        return f"wd:{wd[1]}/{wd[2]}"
    p = urlparse(url)
    segs = segments(p.path)
    kind = value.get("ats") if isinstance(value, dict) and value.get("ats") else detect_kind(url)
    if kind in ("greenhouse", "lever", "ashby", "smartrecruiters") and segs:
        return f"{kind}:{segs[0]}"
    return f"{kind}:{p.netloc}{p.path}"


def is_redirector(url: str | None) -> bool:
    return bool(url) and any(r in url for r in REDIRECTORS)
