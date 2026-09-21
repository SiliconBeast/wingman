"""Adapters for the job boards most companies use. Each talks to the board's public JSON API."""
from __future__ import annotations

import time
from urllib.parse import parse_qsl, quote, urlparse

from ..classify import split_locations
from ..resolve import segments, workday_key, workday_parts
from ..util import get_json, html_to_text, parse_when, parse_workday_posted, post_json
from .base import Adapter, Job, queries

MAX_PER_QUERY = 400


class Greenhouse(Adapter):
    kind = "greenhouse"

    def __init__(self, board):
        super().__init__(board)
        p = urlparse(board.url)
        segs = segments(p.path)
        token = board.opts.get("token") or dict(parse_qsl(p.query)).get("for")
        if not token and segs:
            token = segs[1] if segs[0] == "embed" and len(segs) > 1 else segs[0]
        if not token:
            raise ValueError(f"{board.name}: can't find the Greenhouse board token in {board.url}")
        self.api = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"

    def list_jobs(self):
        out = []
        for j in get_json(self.api).get("jobs", []):
            loc = (j.get("location") or {}).get("name") or ""
            out.append(Job(key=f"gh:{j['id']}", company=self.board.name, title=(j.get("title") or "").strip(),
                           url=j.get("absolute_url") or "", locations=split_locations(loc),
                           posted=parse_when(j.get("first_published") or j.get("updated_at")),
                           needs_detail=True, extra={"id": j["id"]}))
        return out

    def detail(self, job):
        d = get_json(f"{self.api}/{job.extra['id']}")
        locs = [o.get("location") or o.get("name") for o in d.get("offices") or []]
        return html_to_text(d.get("content")), [x for x in locs if x]


class Lever(Adapter):
    kind = "lever"

    def __init__(self, board):
        super().__init__(board)
        p = urlparse(board.url)
        slug = segments(p.path)[0]
        host = "api.eu.lever.co" if ".eu." in p.netloc else "api.lever.co"
        self.api = f"https://{host}/v0/postings/{slug}?mode=json"

    def list_jobs(self):
        out = []
        for p in get_json(self.api) or []:
            cats = p.get("categories") or {}
            locs = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
            lists = [f"{x.get('text', '')}\n{html_to_text(x.get('content'))}" for x in p.get("lists") or []]
            desc = "\n\n".join(s for s in [p.get("descriptionPlain"), *lists, p.get("additionalPlain")] if s)
            out.append(Job(key=f"lv:{p['id']}", company=self.board.name, title=(p.get("text") or "").strip(),
                           url=p.get("hostedUrl") or "", locations=locs, posted=parse_when(p.get("createdAt")),
                           description=desc,
                           extra={"commitment": cats.get("commitment"), "country_codes": [p.get("country")],
                                  "remote": p.get("workplaceType") == "remote", "team": cats.get("team")}))
        return out


class Ashby(Adapter):
    kind = "ashby"

    def __init__(self, board):
        super().__init__(board)
        self.org = segments(urlparse(board.url).path)[0]
        self.api = f"https://api.ashbyhq.com/posting-api/job-board/{self.org}?includeCompensation=false"

    def list_jobs(self):
        out = []
        for j in get_json(self.api).get("jobs", []):
            if j.get("isListed") is False:
                continue
            secondary = j.get("secondaryLocations") or []
            locs = [j.get("location")] + [s.get("location") for s in secondary]
            countries = [((x.get("address") or {}).get("postalAddress") or {}).get("addressCountry")
                         for x in [j, *secondary]]
            out.append(Job(key=f"ab:{j['id']}", company=self.board.name, title=(j.get("title") or "").strip(),
                           url=j.get("jobUrl") or f"https://jobs.ashbyhq.com/{self.org}/{j['id']}",
                           locations=[x for x in locs if x], posted=parse_when(j.get("publishedAt")),
                           description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
                           extra={"commitment": j.get("employmentType"), "remote": bool(j.get("isRemote")),
                                  "country_names": [c for c in countries if c]}))
        return out


class Workday(Adapter):
    kind = "workday"

    def __init__(self, board):
        super().__init__(board)
        parts = workday_parts(board.url)
        if not parts:
            raise ValueError(f"{board.name}: not a Workday careers URL: {board.url}")
        host, self.tenant, site, self.public = parts
        self.api = f"https://{host}/wday/cxs/{self.tenant}/{site}"

    def list_jobs(self):
        seen: dict[str, Job] = {}
        for q in queries(self.board, ("intern", "co-op", "student")):
            offset, total = 0, None
            while True:
                data = post_json(f"{self.api}/jobs",
                                 {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": q})
                posts = data.get("jobPostings") or []
                if total is None:
                    total = int(data.get("total") or 0)
                for jp in posts:
                    path = jp.get("externalPath")
                    if not path or path in seen:
                        continue
                    seen[path] = Job(key=workday_key(self.tenant, path), company=self.board.name,
                                     title=(jp.get("title") or "").strip(), url=self.public + path,
                                     locations=[jp["locationsText"]] if jp.get("locationsText") else [],
                                     posted=parse_workday_posted(jp.get("postedOn")), needs_detail=True,
                                     extra={"path": path})
                offset += 20
                if not posts or offset >= min(total, MAX_PER_QUERY):
                    break
                time.sleep(0.25)
        return list(seen.values())

    def detail(self, job):
        info = (get_json(f"{self.api}{job.extra['path']}") or {}).get("jobPostingInfo") or {}
        locs = [info.get("location"), *(info.get("additionalLocations") or [])]
        country = (info.get("country") or {}).get("descriptor")
        if country:
            job.extra["country_names"] = [country]
        if info.get("startDate") and not job.posted:
            job.posted = parse_when(info["startDate"])
        return html_to_text(info.get("jobDescription")), [x for x in locs if x]


class SmartRecruiters(Adapter):
    kind = "smartrecruiters"

    def __init__(self, board):
        super().__init__(board)
        self.company = segments(urlparse(board.url).path)[0]
        self.api = f"https://api.smartrecruiters.com/v1/companies/{self.company}/postings"

    def list_jobs(self):
        seen: dict[str, Job] = {}
        for q in queries(self.board):
            offset = 0
            while True:
                data = get_json(self.api, params={"limit": 100, "offset": offset, "q": q})
                content = data.get("content") or []
                for p in content:
                    pid = str(p.get("id"))
                    if pid in seen:
                        continue
                    loc = p.get("location") or {}
                    text = loc.get("fullLocation") or ", ".join(
                        x for x in [loc.get("city"), loc.get("region"), (loc.get("country") or "").upper()] if x)
                    level = (p.get("experienceLevel") or {}).get("label") or ""
                    kind = (p.get("typeOfEmployment") or {}).get("label") or ""
                    seen[pid] = Job(key=f"sr:{self.company.lower()}:{pid}", company=self.board.name,
                                    title=(p.get("name") or "").strip(),
                                    url=f"https://jobs.smartrecruiters.com/{self.company}/{pid}",
                                    locations=[text] if text else [], posted=parse_when(p.get("releasedDate")),
                                    needs_detail=True,
                                    extra={"id": pid, "commitment": f"{level} {kind}",
                                           "country_codes": [loc.get("country")], "remote": bool(loc.get("remote"))})
                offset += 100
                if not content or offset >= min(int(data.get("totalFound") or 0), MAX_PER_QUERY):
                    break
        return list(seen.values())

    def detail(self, job):
        d = get_json(f"{self.api}/{job.extra['id']}")
        sections = ((d.get("jobAd") or {}).get("sections") or {})
        parts = [(sections.get(k) or {}).get("text") for k in
                 ("jobDescription", "qualifications", "additionalInformation", "companyDescription")]
        if d.get("postingUrl"):
            job.url = d["postingUrl"]
        return "\n\n".join(html_to_text(x) for x in parts if x), []


class Oracle(Adapter):
    """Oracle Recruiting Cloud (JPMorgan, Texas Instruments, Nokia, onsemi, Dell, Honeywell...)."""
    kind = "oracle"

    def __init__(self, board):
        super().__init__(board)
        p = urlparse(board.url)
        segs = segments(p.path)
        if "sites" not in segs or segs.index("sites") + 1 >= len(segs):
            raise ValueError(f"{board.name}: expected .../CandidateExperience/en/sites/<SITE> in {board.url}")
        self.host, self.site = p.netloc, segs[segs.index("sites") + 1]
        self.api = f"https://{self.host}/hcmRestApi/resources/latest"

    def _get(self, resource: str, finder: str, expand: str):
        url = (f"{self.api}/{resource}?onlyData=true&expand={expand}"
               f"&finder={quote(finder, safe=';,=')}")
        return get_json(url)

    def list_jobs(self):
        seen: dict[str, Job] = {}
        for q in queries(self.board):
            offset = 0
            while True:
                finder = (f"findReqs;siteNumber={self.site},limit=50,offset={offset},"
                          f"keyword={q},sortBy=POSTING_DATES_DESC")
                data = self._get("recruitingCEJobRequisitions", finder, "requisitionList.secondaryLocations")
                block = (data.get("items") or [{}])[0]
                reqs = block.get("requisitionList") or []
                for r in reqs:
                    rid = str(r.get("Id"))
                    if rid in seen:
                        continue
                    sec = r.get("secondaryLocations") or []
                    seen[rid] = Job(key=f"or:{self.host.lower()}:{rid}", company=self.board.name,
                                    title=(r.get("Title") or "").strip(),
                                    url=f"https://{self.host}/hcmUI/CandidateExperience/en/sites/{self.site}/job/{rid}",
                                    locations=[x for x in [r.get("PrimaryLocation"), *[s.get("Name") for s in sec]] if x],
                                    posted=parse_when(r.get("PostedDate")), needs_detail=True,
                                    extra={"id": rid, "country_codes": [r.get("PrimaryLocationCountry"),
                                                                        *[s.get("CountryCode") for s in sec]]})
                offset += 50
                if not reqs or offset >= min(int(block.get("TotalJobsCount") or 0), MAX_PER_QUERY):
                    break
        return list(seen.values())

    def detail(self, job):
        finder = f'ById;Id="{job.extra["id"]}",siteNumber={self.site}'
        data = self._get("recruitingCEJobRequisitionDetails", finder, "all")
        item = (data.get("items") or [{}])[0]
        parts = [item.get(k) for k in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr",
                                       "ExternalQualificationsStr", "CorporateDescriptionStr")]
        return "\n\n".join(html_to_text(x) for x in parts if x), []


class Eightfold(Adapter):
    """Eightfold career sites (Qualcomm, Zebra, John Deere...)."""
    kind = "eightfold"

    def __init__(self, board):
        super().__init__(board)
        from ..resolve import EIGHTFOLD_HOSTS
        p = urlparse(board.url)
        self.host = p.netloc
        self.domain = (board.opts.get("domain") or EIGHTFOLD_HOSTS.get(self.host.lower())
                       or f"{self.host.split('.')[0]}.com")
        self.api = f"https://{self.host}/api/apply/v2/jobs"

    def list_jobs(self):
        seen: dict[str, Job] = {}
        for q in queries(self.board):
            start = 0
            while True:
                data = get_json(self.api, params={"domain": self.domain, "query": q, "start": start,
                                                  "num": 50, "sort_by": "timestamp"})
                positions = data.get("positions") or []
                for x in positions:
                    pid = str(x.get("id"))
                    if pid in seen:
                        continue
                    locs = x.get("locations") or ([x["location"]] if x.get("location") else [])
                    seen[pid] = Job(key=f"ef:{self.host.lower()}:{pid}", company=self.board.name,
                                    title=(x.get("name") or "").strip(),
                                    url=x.get("canonicalPositionUrl") or f"https://{self.host}/careers/job/{pid}",
                                    locations=locs, posted=parse_when(x.get("t_create")), needs_detail=True,
                                    extra={"id": pid})
                start += len(positions)
                if not positions or start >= min(int(data.get("count") or 0), MAX_PER_QUERY):
                    break
        return list(seen.values())

    def detail(self, job):
        d = get_json(f"{self.api}/{job.extra['id']}", params={"domain": self.domain})
        return html_to_text(d.get("job_description")), list(d.get("locations") or [])


class Jibe(Adapter):
    """iCIMS 'Jibe' career sites such as careers.amd.com (links end in ?icims=1)."""
    kind = "jibe"

    def __init__(self, board):
        super().__init__(board)
        p = urlparse(board.url)
        self.host = p.netloc
        self.api = f"https://{self.host}/api/jobs"

    def list_jobs(self):
        seen: dict[str, Job] = {}
        for q in queries(self.board):
            fetched = 0
            for page in range(1, 21):
                data = get_json(self.api, params={"keywords": q, "page": page, "limit": 100,
                                                  "sortBy": "posted_date", "descending": "true", "internal": "false"})
                jobs = data.get("jobs") or []
                for item in jobs:
                    d = item.get("data", item)
                    rid = str(d.get("req_id") or d.get("slug") or d.get("id"))
                    if rid in seen:
                        continue
                    loc = d.get("full_location") or d.get("location_name") or ", ".join(
                        x for x in [d.get("city"), d.get("state"), d.get("country")] if x)
                    seen[rid] = Job(key=f"jb:{self.host.lower()}:{rid}", company=self.board.name,
                                    title=(d.get("title") or "").strip(),
                                    url=f"https://{self.host}/jobs/{d.get('slug') or rid}",
                                    locations=[loc] if loc else [],
                                    posted=parse_when(d.get("posted_date") or d.get("create_date")),
                                    description=html_to_text(d.get("description")),
                                    extra={"country_names": [d.get("country")] if d.get("country") else []})
                fetched += len(jobs)
                total = int(data.get("totalCount") or data.get("count") or 0)
                if not jobs or fetched >= min(total, MAX_PER_QUERY):
                    break
        return list(seen.values())


class Amazon(Adapter):
    """amazon.jobs search (US and Canada by default)."""
    kind = "amazon"
    API = "https://www.amazon.jobs/en/search.json"

    def list_jobs(self):
        seen: dict[str, Job] = {}
        for q in queries(self.board, ("intern", "co-op")):
            for country in self.board.opts.get("countries") or ["USA", "CAN"]:
                offset = 0
                while offset < MAX_PER_QUERY:
                    data = get_json(self.API, params={"base_query": q, "offset": offset, "result_limit": 100,
                                                      "sort": "recent", "country[]": country})
                    jobs = data.get("jobs") or []
                    for x in jobs:
                        jid = str(x.get("id_icims") or x.get("id") or "")
                        if not jid or jid in seen:
                            continue
                        parts = [x.get("description"),
                                 x.get("basic_qualifications") and f"Basic qualifications\n{x['basic_qualifications']}",
                                 x.get("preferred_qualifications") and f"Preferred qualifications\n{x['preferred_qualifications']}"]
                        path = x.get("job_path") or f"/en/jobs/{jid}"
                        seen[jid] = Job(key=f"az:{jid}", company=self.board.name, title=(x.get("title") or "").strip(),
                                        url=f"https://www.amazon.jobs{path}",
                                        locations=[x.get("normalized_location") or x.get("location") or ""],
                                        posted=parse_when(x.get("posted_date")),
                                        description="\n\n".join(html_to_text(t) for t in parts if t),
                                        extra={"country_codes": [x.get("country_code")]})
                    offset += 100
                    if not jobs or offset >= int(data.get("hits") or 0):
                        break
        return list(seen.values())


# ------------------------------------------------------------------ descriptions for list-sourced roles

def resolve_redirect(url: str) -> str:
    """Follow a list's link shortener to the real posting."""
    from ..util import TIMEOUT, http
    r = http().get(url, allow_redirects=True, timeout=TIMEOUT, stream=True)
    try:
        return r.url
    finally:
        r.close()


def jsonld_detail(job: Job) -> tuple[str, list[str]]:
    """Description from schema.org JobPosting data on the posting page; nothing if the page has none."""
    from .jsonld import iter_ld, iter_postings, ld_locations
    from ..util import get_text
    page = get_text(job.url)
    for obj in iter_ld(page):
        for jp in iter_postings(obj):
            text = html_to_text(jp.get("description"))
            if text:
                job.posted = job.posted or parse_when(jp.get("datePosted"))
                return text, ld_locations(jp)
    return "", []

def detail_via_url(job: Job) -> tuple[str, list[str]]:
    """Read the description of a posting found in a GitHub list, straight from its job board."""
    from ..resolve import Board, board_from_job_url, canonical_key, is_redirector
    from ..util import http_status, norm_company
    if is_redirector(job.url):
        final = resolve_redirect(job.url)
        if final and final != job.url and not is_redirector(final):
            job.extra["resolved_url"] = final
            job = Job(canonical_key(final) or job.key, job.company, job.title, final, job.locations, job.posted,
                      extra=job.extra)
    p = urlparse(job.url)
    key_id = job.key.split(":")[-1]
    stub = lambda **extra: Job(job.key, job.company, job.title, job.url, extra=extra)
    found = board_from_job_url(job.url)
    kind, board_url = found if found else ("jsonld", None)
    if kind == "jsonld" and job.key.startswith("gh:"):
        # Greenhouse behind a company domain (stripe.com/...?gh_jid=123): guess the board token.
        for token in [g for g in dict.fromkeys([norm_company(job.company), (job.company or "").lower().split(" ")[0]]) if g]:
            try:
                return Greenhouse(Board(job.company, "greenhouse", f"https://job-boards.greenhouse.io/{token}")).detail(
                    stub(id=key_id))
            except Exception as e:
                if http_status(e) != 404:
                    raise
        return jsonld_detail(job)
    if kind == "greenhouse":
        return Greenhouse(Board(job.company, kind, board_url)).detail(stub(id=key_id))
    if kind == "workday" and "/job/" in p.path:
        return Workday(Board(job.company, kind, board_url)).detail(stub(path=p.path[p.path.index("/job/"):]))
    if kind == "eightfold":
        return Eightfold(Board(job.company, kind, board_url)).detail(stub(id=key_id))
    if kind == "oracle":
        return Oracle(Board(job.company, kind, board_url)).detail(stub(id=key_id))
    if kind == "smartrecruiters":
        return SmartRecruiters(Board(job.company, kind, board_url)).detail(stub(id=key_id))
    if kind == "lever":
        slug = segments(p.path)[0]
        host = "api.eu.lever.co" if ".eu." in p.netloc else "api.lever.co"
        d = get_json(f"https://{host}/v0/postings/{slug}/{key_id}?mode=json") or {}
        lists = [f"{x.get('text', '')}\n{html_to_text(x.get('content'))}" for x in d.get("lists") or []]
        text = "\n\n".join(s for s in [d.get("descriptionPlain"), *lists, d.get("additionalPlain")] if s)
        return text, list((d.get("categories") or {}).get("allLocations") or [])
    if kind == "ashby":
        return "", []           # Ashby has no single-posting API; the board is read in full instead
    return jsonld_detail(job)
