"""python -m scope <command>

  run       scan every source, update data/, post new roles to Discord (--priority for a fast, priority-only scan)
  check     test each company in config/companies.yaml and report broken ones
  add       add a company by pasting its careers URL
  suggest   find job boards in the GitHub lists that you don't watch directly yet
  nudge     post follow-up reminders for tracked applications
  digest    post this week's application count against your goal, and top unapplied matches
  serve     open the dashboard on this computer (no GitHub token needed)
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from . import classify as C
from .config import Settings
from .resolve import KINDS
from .util import CONFIG


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(message)s")
    for noisy in ("urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_run(args) -> int:
    from . import notify, pipeline
    s = Settings.load()
    dry = bool(args.only) or args.dry_run
    result = pipeline.run(s, only=args.only, max_details=args.max_details, explain=args.explain, write=not dry,
                          priority_only=args.priority)
    for line in result.explain:
        print(line)
    if dry:
        print(f"Dry run: {len(result.included)} roles would be kept, {len(result.new)} new. Nothing written.")
        return 0
    if args.no_notify:
        return 0
    dash = s.raw.get("dashboard_url") or ""
    if result.seed:
        notify.announce_seed(len(result.included), len(result.health), dash)
    else:
        notify.announce_roles(result.new, s, dash)
    notify.announce_failures([k for k, v in result.health.items() if not v.get("ok") and v.get("fails") == 3])
    return 0


def cmd_check(args) -> int:
    from .pipeline import _fetch, build_adapters
    s = Settings.load()
    adapters, problems = build_adapters(s, args.only)
    if not args.lists:
        adapters = [a for a in adapters if a.direct]
    rows = []
    with ThreadPoolExecutor(max_workers=s.workers) as pool:
        for adapter, jobs, err, secs in pool.map(_fetch, adapters):
            interns = sum(1 for j in jobs if C.is_internship(j.title, j.extra))
            rows.append((adapter.label, adapter.board.kind if adapter.direct else "list", err, len(jobs), interns, secs))
    bad = 0
    width = max([len(r[0]) for r in rows] + [10])
    for name, kind, err, n, i, secs in sorted(rows, key=lambda r: (r[2] is None, r[0].lower())):
        if err:
            bad += 1
            print(f"FAIL  {name:<{width}}  {kind:<15} {err}")
        else:
            flag = "OK  " if n else "EMPTY"
            print(f"{flag}  {name:<{width}}  {kind:<15} {n:5d} postings, {i:4d} look like internships  ({secs:.1f}s)")
    for name, err in problems:
        bad += 1
        print(f"FAIL  {name:<{width}}  config          {err}")
    print(f"\n{len(rows) - bad} working, {bad} failing. Fix URLs in config/companies.yaml or remove the line.")
    return 1 if bad else 0


def _yaml_key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z0-9][\w .&'()-]*", name) and ":" not in name else json.dumps(name)


def cmd_add(args) -> int:
    from .resolve import KINDS, board_from_config, board_from_job_url
    from .sources import make_adapter
    url = args.url.strip()
    value: object = url
    found = board_from_job_url(url)
    if found and not args.ats:
        value = found[1]                             # a posting link -> its board
    if args.ats or args.domain:
        value = {"url": value if isinstance(value, str) else value["url"], **({"ats": args.ats} if args.ats else {}),
                 **({"domain": args.domain} if args.domain else {})}
    board = board_from_config(args.name, value)
    try:
        jobs = make_adapter(board).list_jobs()
    except Exception as e:
        print(f"Could not read {board.kind} board at {board.url}: {e}")
        if not args.force:
            print("Nothing was added. Check the URL, or pass --force to add it anyway.")
            return 1
        jobs = []
    interns = [j for j in jobs if C.is_internship(j.title, j.extra)]
    print(f"{args.name}: {board.kind} board, {len(jobs)} postings, {len(interns)} look like internships")
    for j in interns[:5]:
        print(f"  - {j.title} ({', '.join(j.locations[:2])})")
    if not jobs and not args.force:
        print("The board returned nothing, so it was not added. Pass --force to add it anyway.")
        return 1
    path = CONFIG / "companies.yaml"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if re.search(rf"^{re.escape(_yaml_key(args.name))}\s*:", text, re.M):
        print(f"{args.name} is already in companies.yaml; edit that line instead.")
        return 1
    line = f"{_yaml_key(args.name)}: {json.dumps(value) if isinstance(value, dict) else value}\n"
    with path.open("a", encoding="utf-8") as f:
        if text and not text.endswith("\n"):
            f.write("\n")
        f.write(line)
    print(f"Added to config/companies.yaml: {line.strip()}")
    return 0


def cmd_suggest(args) -> int:
    from .resolve import board_from_job_url, workday_parts
    from .sources import GitHubList
    from .util import norm_company
    s = Settings.load()
    configured_urls, configured_names = set(), set()
    for name, v in s.companies.items():
        u = (v.get("url") if isinstance(v, dict) else str(v)).rstrip("/").lower()
        wd = workday_parts(u)
        configured_urls.add(f"{wd[1]}/{wd[2]}" if wd else u)
        configured_names.add(norm_company(name))
    counts: Counter = Counter()
    boards: dict = {}
    cats: dict = defaultdict(Counter)
    canada: Counter = Counter()
    for spec in s.lists:
        try:
            jobs = GitHubList(spec).list_jobs()
        except Exception as e:
            print(f"skip {spec['repo']}: {e}", file=sys.stderr)
            continue
        for j in jobs:
            found = board_from_job_url(j.url)
            if not found:
                continue
            kind, value = found
            u = (value["url"] if isinstance(value, dict) else value).rstrip("/").lower()
            wd = workday_parts(u)
            ident = f"{wd[1]}/{wd[2]}" if wd else u
            if ident in configured_urls or norm_company(j.company) in configured_names:
                continue
            key = (j.company, ident)
            counts[key] += 1
            boards[key] = value
            cats[key][C.classify_category(j.title, j.extra.get("category_hint"))] += 1
            if "CA" in C.classify_locations(j.locations, j.extra)[0]:
                canada[key] += 1
    rx = re.compile(args.grep, re.I) if args.grep else None
    picked = [k for k, _ in counts.most_common()
              if (not rx or rx.search(k[0])) and (not args.category or cats[k][args.category] > 0)
              and (not args.canada or canada[k] > 0)]
    if not picked:
        print("Nothing new to suggest with those filters.")
        return 0
    print("# Paste the ones you want into config/companies.yaml")
    for k in picked[: args.limit]:
        v = boards[k]
        top = ", ".join(f"{C.CATEGORY_LABEL[c]} {n}" for c, n in cats[k].most_common(2))
        print(f"{_yaml_key(k[0])}: {json.dumps(v) if isinstance(v, dict) else v}   # {counts[k]} listed ({top})"
              + (f", {canada[k]} in Canada" if canada[k] else ""))
    return 0


def cmd_nudge(args) -> int:
    from .followups import nudge
    due = nudge(Settings.load(), notify=not args.no_notify)
    for f in due:
        print(f"{f['due']}  {f['company']} - {f['title']}: {f['message']}")
    return 0


def cmd_serve(args) -> int:
    from .serve import serve
    serve(port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_digest(args) -> int:
    from .followups import weekly
    stats = weekly(Settings.load(), notify=not args.no_notify)
    print(f"{stats['applied']} of {stats['goal']} applications this week, {stats['due']} follow-ups due, "
         f"{len(stats['top'])} suggested roles")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m scope", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="scan all sources and update data/")
    r.add_argument("--no-notify", action="store_true", help="don't post to Discord")
    r.add_argument("--dry-run", action="store_true", help="scan but write nothing")
    r.add_argument("--only", help="scan only companies matching this regex (implies --dry-run)")
    r.add_argument("--explain", metavar="REGEX", help="print the keep/drop reason for matching companies")
    r.add_argument("--max-details", type=int, help="job descriptions to read this run")
    r.add_argument("--priority", action="store_true", help="scan only priority companies (fast)")
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("check", help="test every company board")
    c.add_argument("--only", help="regex on company names")
    c.add_argument("--lists", action="store_true", help="also test the GitHub lists")
    c.set_defaults(fn=cmd_check)

    a = sub.add_parser("add", help="add a company from its careers URL")
    a.add_argument("name")
    a.add_argument("url", help="careers page or any job posting link")
    a.add_argument("--ats", choices=list(KINDS), help="force a board type")
    a.add_argument("--domain", help="Eightfold only: the company's domain, e.g. qualcomm.com")
    a.add_argument("--force", action="store_true", help="add even if the board returns nothing")
    a.set_defaults(fn=cmd_add)

    g = sub.add_parser("suggest", help="boards found in the GitHub lists that you don't watch yet")
    g.add_argument("--grep", help="regex on company names")
    g.add_argument("--category", choices=C.CATEGORY_ORDER)
    g.add_argument("--canada", action="store_true", help="only boards with Canadian roles")
    g.add_argument("--limit", type=int, default=40)
    g.set_defaults(fn=cmd_suggest)

    n = sub.add_parser("nudge", help="post follow-up reminders")
    n.add_argument("--no-notify", action="store_true")
    n.set_defaults(fn=cmd_nudge)

    v = sub.add_parser("serve", help="run the dashboard locally")
    v.add_argument("--port", type=int, default=8765)
    v.add_argument("--no-browser", action="store_true")
    v.set_defaults(fn=cmd_serve)

    d = sub.add_parser("digest", help="post this week's application count against your goal, and top unapplied matches")
    d.add_argument("--no-notify", action="store_true")
    d.set_defaults(fn=cmd_digest)

    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
