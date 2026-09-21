"""Follow-up rules for tracked applications. docs/index.html implements the same rules in JS."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from .notify import announce_digest, announce_followups
from .util import DATA, read_json, to_date, utcnow, write_json

log = logging.getLogger("scope")

APPS = DATA / "applications.json"
SENT = DATA / "nudges.json"
JOBS = DATA / "jobs.json"
TERMINAL = {"rejected", "ghosted", "withdrawn"}


def _since(app: dict, status: str) -> date:
    days = [to_date(e.get("d")) for e in app.get("events") or [] if e.get("s") == status]
    days = [d for d in days if d]
    return max(days) if days else (to_date(app.get("created")) or date.today())


def _followup_after(app: dict, since: date) -> date | None:
    days = [to_date(e.get("d")) for e in app.get("events") or [] if e.get("s") == "followup"]
    days = [d for d in days if d and d >= since]
    return max(days) if days else None


def compute(app: dict, today: date, cfg: dict) -> list[dict]:
    """Every follow-up this application calls for, due or upcoming."""
    if app.get("deleted"):
        return []
    status = app.get("status")
    items: list[tuple[str, date, str]] = []
    deadline = to_date(app.get("deadline"))
    if status == "saved":
        base = _since(app, "saved")
        items.append(("apply", base + timedelta(days=cfg["saved_days"]),
                      "Saved a while ago. Apply or drop it; internship roles fill fast."))
    elif status == "applied":
        base = _since(app, "applied")
        fu = _followup_after(app, base)
        if fu is None:
            items.append(("followup1", base + timedelta(days=cfg["applied_days"]),
                          "No reply since you applied. Message the recruiter or an engineer on the team."))
        else:
            items.append(("followup2", fu + timedelta(days=cfg["second_days"]),
                          "Still quiet after your follow-up. Send one more, or mark it ghosted."))
        items.append(("ghost", base + timedelta(days=cfg["ghost_days"]),
                      "Weeks without a reply. Mark it ghosted and put the time into new applications."))
    elif status == "oa":
        base = _since(app, "oa")
        if deadline:
            items.append(("oa_deadline", deadline - timedelta(days=1), f"Online assessment due {deadline}."))
        else:
            items.append(("oa", base + timedelta(days=cfg["oa_days"]),
                          "Finish the online assessment; most links expire in about a week."))
    elif status == "interview":
        base = _since(app, "interview")
        fu = _followup_after(app, base)
        if fu is None:
            items.append(("thanks", base, "Send a short thank-you note to your interviewers today."))
        items.append(("interview_check", (fu or base) + timedelta(days=cfg["interview_days"]),
                      "No news since the interview. Ask the recruiter about the timeline."))
    elif status == "offer" and deadline:
        items.append(("offer_deadline", deadline - timedelta(days=3),
                      f"Offer deadline {deadline}. Decide, or ask for an extension now."))
    nxt = app.get("next") or {}
    if nxt.get("d") and status not in TERMINAL:
        d = to_date(nxt["d"])
        if d:
            items.append(("planned", d, nxt.get("what") or "Planned follow-up."))
    dismissed = set(app.get("dismissed") or [])
    out = []
    for rule, due, message in items:
        key = f"{rule}:{due.isoformat()}"
        if key in dismissed:
            continue
        out.append({"app_id": app.get("id"), "rule": rule, "key": key, "due": due, "message": message,
                    "company": app.get("company", ""), "title": app.get("title", ""),
                    "overdue": (today - due).days})
    return out


def in_quiet_hours(hour: int, quiet) -> bool:
    start, end = (quiet or [22, 8])[:2]
    return (hour >= start or hour < end) if start > end else (start <= hour < end)


def nudge(settings, notify: bool = True) -> list[dict]:
    apps = read_json(APPS, {}).get("applications", [])
    sent: dict = read_json(SENT, {})
    now = utcnow().astimezone(settings.tz)
    today = now.date()
    due = []
    for app in apps:
        for f in compute(app, today, settings.raw["followups"]):
            if f["due"] <= today and f"{f['app_id']}:{f['key']}" not in sent:
                due.append(f)
    if due and notify:
        if in_quiet_hours(now.hour, settings.raw["discord"].get("quiet_hours")):
            log.info("%d follow-ups due; holding them until quiet hours end", len(due))
            return due
        announce_followups(due, settings, settings.raw.get("dashboard_url") or "")
        for f in due:
            sent[f"{f['app_id']}:{f['key']}"] = today.isoformat()
    cutoff = (today - timedelta(days=180)).isoformat()
    write_json(SENT, {k: v for k, v in sent.items() if v >= cutoff})
    log.info("%d follow-ups due today", len(due))
    return due


def digest_stats(settings) -> dict:
    """This week's applications against your goal, follow-ups due, and your best unapplied matches."""
    today = utcnow().astimezone(settings.tz).date()
    monday = today - timedelta(days=today.weekday())
    apps = [a for a in read_json(APPS, {}).get("applications", []) if not a.get("deleted")]
    applied = sum(1 for a in apps for e in a.get("events") or []
                 if e.get("s") == "applied" and (to_date(e.get("d")) or date.min) >= monday)
    due = sum(1 for a in apps for f in compute(a, today, settings.raw["followups"]) if f["due"] <= today)
    tracked = {a.get("job_id") for a in apps}
    hidden = set(read_json(APPS, {}).get("hidden") or [])
    jobs = read_json(JOBS, {}).get("jobs", [])
    candidates = [j for j in jobs if not j.get("closed") and j["id"] not in tracked and j["id"] not in hidden
                 and not j.get("flags") and j.get("tq") != "unknown"]
    candidates.sort(key=lambda j: (not j.get("pri"), -(j.get("fit") if j.get("fit") is not None else -1)))
    goal = int((settings.raw.get("goals") or {}).get("weekly_applications", 5))
    return {"applied": applied, "goal": goal, "due": due, "top": candidates[:5], "week_start": monday.isoformat()}


def weekly(settings, notify: bool = True) -> dict:
    stats = digest_stats(settings)
    if notify:
        announce_digest(stats, settings, settings.raw.get("dashboard_url") or "")
    log.info("%d of %d applications this week, %d follow-ups due", stats["applied"], stats["goal"], stats["due"])
    return stats
