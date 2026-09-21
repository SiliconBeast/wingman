"""One scan: fetch every source, keep the internships you want, score them, write data/, report what's new.

Sources, cheapest first:
  1. GitHub lists (a few requests cover thousands of roles, including big-company career sites)
  2. Company boards in config/companies.yaml (complete and fast: roles appear there before lists)
  3. Boards discovered automatically from list links: every company a list mentions gets its whole
     board read directly, a third of them per scan, so each is visited about twice a day
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import classify as C
from . import fit as F
from .config import Settings
from .resolve import board_from_config, board_from_job_url, board_ident
from .sources import GitHubList, Job, make_adapter
from .util import (DATA, clean_url, day, days_between, iso, norm_company, norm_title, read_json, short_id,
                   utcnow, write_json, write_map_json, write_rows_json)

log = logging.getLogger("scope")

JOBS = DATA / "jobs.json"
INDEX = DATA / "index.json"
HEALTH = DATA / "health.json"
DISCOVERED = DATA / "discovered.json"
JD_DIR = DATA / "jd"
SUMMARY = DATA / ".summary"
AUTO = "auto:"
DISCOVERABLE = {"greenhouse", "lever", "ashby", "workday", "smartrecruiters", "oracle", "eightfold", "jibe"}


@dataclass
class Candidate:
    job: Job
    adapter: object
    sources: set = field(default_factory=set)


@dataclass
class ScanResult:
    included: list
    new: list
    health: dict
    seed: bool
    explain: list


# ------------------------------------------------------------------ sources

def build_adapters(settings: Settings, only: str | None = None, discovered: dict | None = None, run_no: int = 0,
                   priority_only: bool = False):
    adapters, problems = [], []
    rx = re.compile(only, re.I) if only else None
    for name, value in settings.companies.items():
        if rx and not rx.search(name):
            continue
        if priority_only and not C.is_priority(name, "", settings.raw):
            continue
        try:
            adapters.append(make_adapter(board_from_config(name, value)))
        except Exception as e:  # bad URL in companies.yaml
            problems.append((name, str(e)))
    if rx:
        return adapters, problems
    if not priority_only:
        adapters += [GitHubList(spec) for spec in settings.lists]
    cfg = settings.discover
    if cfg.get("enabled") and discovered:
        rotate = max(1, int(cfg.get("rotate", 3)))
        for ident, d in discovered.get("boards", {}).items():
            if d.get("off"):
                continue
            pri = C.is_priority(d["name"], "", settings.raw)
            if priority_only and not pri:
                continue
            slot = int(hashlib.sha1(ident.encode()).hexdigest(), 16) % rotate
            if not priority_only and slot != run_no % rotate and not pri:
                continue
            try:
                adapters.append(make_adapter(board_from_config(AUTO + d["name"], d["value"])))
            except Exception as e:
                log.debug("skip discovered board %s: %s", ident, e)
    return adapters, problems


def _fetch(adapter):
    t0 = time.time()
    try:
        return adapter, adapter.list_jobs(), None, time.time() - t0
    except Exception as e:
        return adapter, [], f"{type(e).__name__}: {e}"[:300], time.time() - t0


def update_discovered(discovered: dict, list_jobs: list[Job], settings: Settings, today: date) -> dict:
    """Remember boards that list roles point to, so later scans read those boards directly."""
    configured = {board_ident(v) for v in settings.companies.values()}
    boards = discovered.setdefault("boards", {})
    seen: dict[str, dict] = {}
    for job in list_jobs:
        found = board_from_job_url(job.url)
        if not found or found[0] not in DISCOVERABLE:
            continue
        ident = board_ident(found[1])
        if ident in configured:
            continue
        rec = seen.setdefault(ident, {"value": found[1], "names": {}, "n": 0})
        rec["n"] += 1
        rec["names"][job.company] = rec["names"].get(job.company, 0) + 1
    for ident, rec in seen.items():
        if rec["n"] < int(settings.discover.get("min_roles", 1)):
            continue
        name = max(rec["names"].items(), key=lambda kv: kv[1])[0]
        cur = boards.get(ident, {"first": day(today)})
        boards[ident] = {**cur, "name": name, "value": rec["value"], "roles": rec["n"], "last": day(today)}
    for ident in configured & set(boards):
        del boards[ident]                              # now listed in companies.yaml
    cutoff = day(today - timedelta(days=45))
    kept = sorted(((k, v) for k, v in boards.items() if v.get("last", "") >= cutoff),
                  key=lambda kv: (-kv[1].get("roles", 0), kv[0]))[: int(settings.discover.get("max_boards", 300))]
    discovered["boards"] = dict(kept)
    return discovered


# ------------------------------------------------------------------ filters

def prefilter(job: Job, s: Settings) -> str | None:
    """Cheap title/location checks before any detail request. Returns a reason to drop, or None."""
    if not C.is_internship(job.title, job.extra):
        return "not an internship"
    for rx in s.exclude_title:
        if rx.search(job.title):
            return f"title matches '{rx.pattern}'"
    if not C.degree_ok(job.extra.get("degrees")):
        return "needs an advanced degree"
    countries, _ = C.classify_locations(job.locations, job.extra)
    if countries and not (countries & s.countries):
        return "outside target countries"
    return None


def evaluate(job: Job, entry: dict, s: Settings, today: date):
    """Full decision with everything known about the job. Returns (record | None, reason)."""
    locs = entry.get("loc") or job.locations
    countries, remote = C.classify_locations(locs, job.extra)
    if countries and not (countries & s.countries):
        return None, "outside target countries"
    if not countries and not s.include_unknown_location:
        return None, "location not stated"
    title_info = C.parse_terms(job.title, "title")
    url_info = C.parse_terms(C.url_text(entry.get("url") or job.url), "url")
    jd_info = None
    if entry.get("jt") is not None:
        jd_info = C.TermInfo(terms=set(entry["jt"]), months=set(entry.get("jm") or []), coop=entry.get("jc", False))
    decision = C.decide_term(title_info, url_info, jd_info, job.extra.get("list_terms") or [],
                             job.extra.get("list_year"), job.posted, s.raw, today,
                             default_terms=job.extra.get("default_terms"))
    if not decision.ok:
        return None, f"term {', '.join(decision.terms) or 'not stated (too old)'}"
    category = C.classify_category(job.title, job.extra.get("category_hint"), job.extra.get("team") or "")
    if category not in s.categories:
        return None, f"category {category}"
    months = sorted(title_info.months | url_info.months | (jd_info.months if jd_info else set()))
    coop = (title_info.coop or bool(jd_info and jd_info.coop)
            or bool(re.search(r"co-?op", str(job.extra.get("commitment") or ""), re.I)))
    rec = {
        "company": job.company, "title": job.title, "url": entry.get("url") or job.url,
        "loc": [x for x in locs if x][:8], "cc": sorted(countries), "remote": remote,
        "terms": decision.terms, "tq": decision.quality, "dur": months[:3], "coop": coop,
        "cat": category, "pri": C.is_priority(job.company, job.title, s.raw), "posted": job.posted,
    }
    if category == "hardware":
        hw = C.classify_hw_subtype(job.title)
        if hw:
            rec["hw"] = hw
    spons = job.extra.get("sponsorship")
    if spons and spons != "Other":
        rec["spons"] = spons
    return rec, "ok"


def _quality(c: Candidate):
    """Lower is better: the company's own board, then a list link we can resolve, then a plain link."""
    return (not c.adapter.direct, c.job.key.startswith("ls:"))


def _detail_rank(c: Candidate, index: dict, jid: str, s: Settings):
    return (not C.is_priority(c.job.company, c.job.title, s.raw), jid in index, c.job.key.startswith("ls:"))


ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
TRUNCATED = ("…", "...")


def _clean(text: str | None) -> str:
    return " ".join(ZERO_WIDTH.sub("", text or "").split())


def _richness(job: Job):
    """Between two list copies of one role, prefer a full title, a posted date, then the longer title."""
    return (not job.title.endswith(TRUNCATED), bool(job.posted), len(job.title), job.url)


def _combine(winner: Job, other: Job) -> Job:
    terms = list(dict.fromkeys([*(winner.extra.get("list_terms") or []), *(other.extra.get("list_terms") or [])]))
    extra = {**other.extra, **winner.extra, "list_terms": terms}
    for k in ("sponsorship", "degrees", "category_hint"):
        extra[k] = winner.extra.get(k) or other.extra.get(k)
    title = winner.title
    if title.endswith(TRUNCATED) and not other.title.endswith(TRUNCATED):
        title = other.title
    return Job(winner.key, winner.company, title, winner.url, winner.locations or other.locations,
               winner.posted or other.posted, winner.description or other.description, winner.needs_detail, extra)


def _merge(candidates: dict, jid: str, job: Job, adapter, name: str) -> None:
    cur = candidates.get(jid)
    if cur is None:
        candidates[jid] = Candidate(job, adapter, {name})
        return
    cur.sources.add(name)
    if adapter.direct and not cur.adapter.direct:          # the company's own board wins
        if name.startswith(AUTO):
            job.company = cur.job.company                  # keep the list's display name
        cur.job, cur.adapter = _combine(job, cur.job), adapter
    elif adapter.direct == cur.adapter.direct:
        a, b = (job, cur.job) if _richness(job) > _richness(cur.job) else (cur.job, job)
        cur.job = _combine(a, b)


# ------------------------------------------------------------------ run

def run(settings: Settings, only: str | None = None, max_details: int | None = None,
        explain: str | None = None, write: bool = True, priority_only: bool = False) -> ScanResult:
    started = utcnow()
    today = started.astimezone(settings.tz).date()
    prev = {j["id"]: j for j in read_json(JOBS, {}).get("jobs", [])}
    index_raw = read_json(INDEX, None)
    seed = index_raw is None
    index: dict = index_raw or {}
    health: dict = read_json(HEALTH, {})
    discovered: dict = read_json(DISCOVERED, {"run": 0, "boards": {}})
    run_no = int(discovered.get("run", 0))

    adapters, problems = build_adapters(settings, only, discovered, run_no, priority_only)
    for name, err in problems:
        log.warning("config: %s", err)
        health[name] = {"ok": False, "n": 0, "err": err, "at": iso(started),
                        "fails": health.get(name, {}).get("fails", 0) + 1}

    display = {norm_company(n): n for n in settings.companies}   # "Nvidia", "NVIDIA Corp" -> "NVIDIA"

    # 1. list every source in parallel
    ok_sources: set[str] = set()
    candidates: dict[str, Candidate] = {}
    list_jobs: list[Job] = []
    explain_rx = re.compile(explain, re.I) if explain else None
    notes: list[str] = []
    with ThreadPoolExecutor(max_workers=settings.workers) as pool:
        for fut in as_completed([pool.submit(_fetch, a) for a in adapters]):
            adapter, jobs, err, secs = fut.result()
            name = adapter.label
            h = health.get(name, {})
            if err is None and not jobs and h.get("n", 0) >= 10:
                err = f"returned 0 postings (had {h['n']} last time); the page format may have changed"
            if err:
                log.warning("%-40s FAILED %s", name, err)
                health[name] = {**h, "ok": False, "err": err, "at": iso(started), "fails": h.get("fails", 0) + 1}
                continue
            ok_sources.add(name)
            kept = 0
            for job in jobs:
                job.url = clean_url(job.url) or ""
                job.title, job.company = _clean(job.title), _clean(job.company)
                job.locations = [x for x in (_clean(loc) for loc in job.locations) if x]
                if name.startswith(AUTO):
                    job.company = name[len(AUTO):]
                job.company = display.get(norm_company(job.company), job.company)
                if not job.url or not job.title or not job.company:
                    continue
                reason = prefilter(job, settings)
                if explain_rx and explain_rx.search(job.company):
                    notes.append(f"{job.company} | {job.title} | {', '.join(job.locations[:2])} -> {reason or 'candidate'}")
                if reason:
                    continue
                kept += 1
                if not adapter.direct:
                    list_jobs.append(job)
                _merge(candidates, short_id(job.key), job, adapter, name)
            health[name] = {"ok": True, "n": len(jobs), "m": kept, "err": None, "at": iso(started),
                            "last_ok": iso(started), "fails": 0, "secs": round(secs, 1)}
            log.info("%-40s %5d postings, %4d internship candidates (%.1fs)", name, len(jobs), kept, secs)

    # 2. the same role from a list and from a board -> the board's data wins; the same role in two lists
    #    under different links -> one entry. Either way the role keeps the id it already had, so nothing
    #    gets alerted twice and tracked applications stay linked.
    def fold(x: str, y: str) -> str:
        cx, cy = candidates[x], candidates[y]
        best = cx if _quality(cx) <= _quality(cy) else cy
        if (x in index) != (y in index):
            keep = x if x in index else y
        else:
            keep = x if best is cx else y
        other = cy if best is cx else cx
        candidates[keep] = Candidate(_combine(best.job, other.job), best.adapter, cx.sources | cy.sources)
        del candidates[y if keep == x else x]
        return keep

    by_name: dict = {}
    for jid in sorted(candidates, key=lambda j: (_quality(candidates[j]), j)):
        if jid not in candidates:
            continue
        c = candidates[jid]
        name_key = (norm_company(c.job.company), norm_title(c.job.title))
        loc_key = (*name_key, (sorted(c.job.locations) or [""])[0].lower())
        if c.adapter.direct:
            by_name.setdefault(name_key, jid)
            continue
        twin = by_name.get(name_key) or by_name.get(loc_key)
        if twin and twin != jid and twin in candidates:
            keep = fold(twin, jid)
            by_name[name_key if candidates[keep].adapter.direct else loc_key] = keep
        else:
            by_name[loc_key] = jid

    # 3. descriptions for roles we haven't read yet (priority first, known boards before plain links)
    budget = settings.max_details if max_details is None else max_details
    need = [jid for jid, c in candidates.items()
            if c.job.needs_detail and not c.job.description and not index.get(jid, {}).get("jd")
            and index.get(jid, {}).get("tries", 0) < 2]
    need.sort(key=lambda j: _detail_rank(candidates[j], index, j, settings))
    need = need[:budget]
    if need:
        log.info("reading %d job descriptions", len(need))
        with ThreadPoolExecutor(max_workers=settings.workers) as pool:
            futs = {pool.submit(candidates[j].adapter.detail, candidates[j].job): j for j in need}
            for fut in as_completed(futs):
                jid = futs[fut]
                e = index.setdefault(jid, {"first": iso(started)})
                e["tries"] = e.get("tries", 0) + 1
                try:
                    text, locs = fut.result()
                except Exception as ex:
                    log.debug("detail failed for %s: %s", candidates[jid].job.url, ex)
                    continue
                job = candidates[jid].job
                job.description = text or ""
                e["jd"] = True
                if job.extra.get("resolved_url"):
                    e["url"] = clean_url(job.extra["resolved_url"])
                if locs:
                    e["loc"] = list(dict.fromkeys([*locs, *job.locations]))[:10]

    # 4. decide, score against your resume, remember, write descriptions
    JD_DIR.mkdir(parents=True, exist_ok=True)
    resume, resume_hash = F.load_resume()
    graduation = settings.raw.get("graduation") or None
    rescored = 0
    records, new = [], []
    for jid, c in candidates.items():
        e = index.setdefault(jid, {"first": iso(started)})
        e["last"] = day(today)
        if c.job.description:
            info = C.parse_terms(c.job.description, "jd")
            e["jt"], e["jm"], e["jc"] = sorted(info.terms), sorted(info.months), info.coop
        rec, reason = evaluate(c.job, e, settings, today)
        if explain_rx and explain_rx.search(c.job.company):
            notes.append(f"{c.job.company} | {c.job.title} -> {reason}")
        if rec is None:
            continue
        rec.update({"id": jid, "seen": e["first"], "src": sorted(c.sources)})
        jd_path = JD_DIR / f"{jid}.txt"
        if write and c.job.description and not jd_path.exists():
            jd_path.write_text(c.job.description[:20000], encoding="utf-8")
        rec["jd"] = jd_path.exists() or bool(c.job.description)
        cached = e.get("fit")
        stamp = f"{resume_hash}:{graduation or ''}"
        extract = e.get("extract")
        need_fit = rec["jd"] and (not cached or cached[3] != stamp) and rescored < 1500
        need_extract = rec["jd"] and extract is None
        if need_fit or need_extract:
            jd = c.job.description or (jd_path.read_text(encoding="utf-8") if jd_path.exists() else "")
            if need_fit:
                a = F.assess(jd, resume, rec["cc"], rec.get("spons"), graduation)
                e["fit"] = cached = [a["fit"], a["gap"], a["flags"], stamp]
                rescored += 1
            if need_extract:
                e["extract"] = extract = [C.extract_deadline(jd, today), C.extract_pay(jd)]
        elif not rec["jd"] and rec.get("spons"):
            cached = [None, [], F.red_flags("", rec["cc"], rec["spons"]), stamp]
        if cached:
            rec["fit"], rec["gap"], rec["flags"] = cached[0], cached[1], cached[2]
        if extract:
            if extract[0]:
                rec["deadline"] = extract[0]
            if extract[1]:
                rec["pay"] = extract[1]
        records.append(rec)
        if not e.get("alerted"):
            e["alerted"] = day(today)
            new.append(rec)

    # 5. roles that disappeared: closed if every source that listed them answered this time
    kept_ids = {r["id"] for r in records}
    for jid, old in prev.items():
        if jid in kept_ids or jid in candidates:
            continue
        srcs = set(old.get("src") or [])
        if srcs and not srcs <= ok_sources:
            records.append(old)                         # a source was down or not scanned this run
            continue
        closed = old.get("closed") or day(today)
        if (days_between(closed, today) or 0) <= settings.keep_closed_days:
            records.append({**old, "closed": closed})
    records.sort(key=lambda r: (r.get("closed") is None, r.get("seen", ""), r.get("posted") or ""), reverse=True)

    # 6. housekeeping: discovered boards, stale index entries, orphaned descriptions
    if settings.discover.get("enabled") and not only and not priority_only:
        failing_auto = {k[len(AUTO):] for k, v in health.items() if k.startswith(AUTO) and v.get("fails", 0) >= 5}
        for d in discovered.get("boards", {}).values():
            if d.get("name") in failing_auto:
                d["off"] = True
        discovered = update_discovered(discovered, list_jobs, settings, today)
        discovered["run"] = run_no + 1
    cutoff = day(today - timedelta(days=settings.forget_after_days))
    index = {k: v for k, v in index.items() if v.get("last", v.get("first", "")[:10]) >= cutoff}
    tracked = {a.get("job_id") for a in read_json(DATA / "applications.json", {}).get("applications", [])}
    live = {r["id"] for r in records} | tracked
    for f in JD_DIR.glob("*.txt") if write else []:
        if f.stem not in live and f.stem not in index:
            f.unlink(missing_ok=True)

    failing = [k for k, v in health.items() if not v.get("ok") and not k.startswith(AUTO)]
    pri = sum(1 for r in new if r.get("pri"))
    summary = f"+{len(new)} new ({pri} priority), {len(records)} open, {len(failing)} sources failing"
    if write:
        head = {"generated_at": iso(started), "targets": settings.raw["terms"], "followups": settings.raw["followups"],
                "profile": {"graduation": graduation}, "resume": bool(resume)}
        write_rows_json(JOBS, head, "jobs", records)
        write_map_json(INDEX, dict(sorted(index.items())))
        write_json(HEALTH, dict(sorted(health.items())))
        write_json(DISCOVERED, discovered)
        SUMMARY.write_text(summary + "\n", encoding="utf-8")
    log.info(summary)
    return ScanResult(records, new, health, seed, notes)
