"""Fallback for career sites without a known board: read schema.org JobPosting data.

Works on server-rendered career pages (most publish JobPosting JSON-LD so Google Jobs can
index them). Pages that only render with JavaScript need a proper adapter instead.

    Some Company: https://company.com/careers
    Other Co: {url: "https://other.com/jobs", link_pattern: "/jobs/\\d+"}
"""
from __future__ import annotations

import html
import json
import re
from urllib.parse import urljoin, urlparse

from ..classify import INTERN_RE
from ..util import clean_url, get_text, html_to_text, parse_when
from .base import Adapter, Job

LD_BLOCK = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S)
ANCHOR = re.compile(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
DEFAULT_LINKS = r"/(?:jobs?|careers?|positions?|openings?|opportunities|vacanc(?:y|ies)|requisitions?)/[^\s\"'?#]+"


def iter_ld(page: str):
    for block in LD_BLOCK.findall(page):
        try:
            yield json.loads(block.strip())
        except json.JSONDecodeError:
            continue


def iter_postings(obj):
    if isinstance(obj, list):
        for x in obj:
            yield from iter_postings(x)
    elif isinstance(obj, dict):
        kind = obj.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "JobPosting" in kinds:
            yield obj
        for k in ("@graph", "itemListElement", "item"):
            if k in obj:
                yield from iter_postings(obj[k])


def ld_locations(jp: dict) -> list[str]:
    locs = jp.get("jobLocation") or []
    locs = locs if isinstance(locs, list) else [locs]
    out = []
    for loc in locs:
        addr = (loc or {}).get("address") or {}
        if isinstance(addr, str):
            out.append(addr)
            continue
        country = addr.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")
        parts = [addr.get("addressLocality"), addr.get("addressRegion"), country]
        text = ", ".join(str(p) for p in parts if p)
        if text:
            out.append(text)
    return out


class JsonLD(Adapter):
    kind = "jsonld"

    def _from_ld(self, jp: dict, page_url: str) -> Job | None:
        url = clean_url(urljoin(page_url, str(jp.get("url") or page_url)))
        title = html.unescape(str(jp.get("title") or jp.get("name") or "")).strip()
        if not url or not title:
            return None
        remote = str(jp.get("jobLocationType") or "").upper() == "TELECOMMUTE"
        return Job(key=f"ld:{url}", company=self.board.name, title=title, url=url, locations=ld_locations(jp),
                   posted=parse_when(jp.get("datePosted")), description=html_to_text(jp.get("description")),
                   extra={"commitment": " ".join(jp.get("employmentType") or []) if isinstance(
                       jp.get("employmentType"), list) else jp.get("employmentType"), "remote": remote})

    def list_jobs(self):
        page = get_text(self.board.url)
        jobs = [j for obj in iter_ld(page) for jp in iter_postings(obj) if (j := self._from_ld(jp, self.board.url))]
        if jobs:
            return jobs
        pattern = re.compile(self.board.opts.get("link_pattern") or DEFAULT_LINKS, re.I)
        base_host = ".".join(urlparse(self.board.url).netloc.split(".")[-2:])
        found: dict[str, Job] = {}
        for href, text in ANCHOR.findall(page):
            url = clean_url(urljoin(self.board.url, html.unescape(href)))
            if not url or url in found or not pattern.search(urlparse(url).path):
                continue
            if not urlparse(url).netloc.endswith(base_host):
                continue
            title = " ".join(html_to_text(text).split())
            if title and INTERN_RE.search(title):
                found[url] = Job(key=f"ld:{url}", company=self.board.name, title=title, url=url, needs_detail=True)
        return list(found.values())[: int(self.board.opts.get("max_links", 60))]

    def detail(self, job):
        page = get_text(job.url)
        for obj in iter_ld(page):
            for jp in iter_postings(obj):
                full = self._from_ld(jp, job.url)
                if full:
                    job.posted = job.posted or full.posted
                    job.extra.update({k: v for k, v in full.extra.items() if v})
                    return full.description or "", full.locations
        return html_to_text(page)[:6000], []
