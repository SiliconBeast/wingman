"""Discord alerts through a channel webhook (DISCORD_WEBHOOK_URL)."""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import date

import requests

from .classify import CATEGORY_LABEL, CATEGORY_ORDER, HW_SUBTYPE_LABEL
from .util import to_date

log = logging.getLogger("scope")

COLORS = {"priority": 0xF59A4A, "hardware": 0xF2C94C, "software": 0x4CC9DB, "ai_data": 0xB48EF0,
          "quant": 0xE66BBE, "product": 0x74D17F, "finance": 0x8594A1, "followup": 0xF59A4A, "warn": 0xE9776D}
EMBED_LIMIT = 3900
MESSAGE_LIMIT = 5800


def esc(s: str) -> str:
    return re.sub(r"([\\*_~`|>\[\]])", r"\\\1", s or "")


def safe_link(url: str) -> str:
    return (url or "").replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def short_term(rec: dict) -> str:
    terms = rec.get("terms") or []
    parts = []
    for t in terms[:2]:
        m = re.fullmatch(r"(Winter|Spring|Summer|Fall) 20(\d\d)", t)
        parts.append(f"{m[1][0]}{m[2]}" if m else t)
    text = "/".join(parts) if parts else "term ?"
    if rec.get("dur"):
        text += f" {'/'.join(str(d) for d in rec['dur'][:2])}mo"
    if rec.get("coop"):
        text += " co-op"
    return text


FLAG_TEXT = {"us_citizen": "US citizens only", "clearance": "clearance", "no_sponsor": "no sponsorship",
             "french": "French required", "gpa": "min GPA", "grad": "grad window"}


def role_line(rec: dict) -> str:
    locs = rec.get("loc") or []
    where = locs[0] if locs else ("Remote" if rec.get("remote") else "location ?")
    more = f" +{len(locs) - 1}" if len(locs) > 1 else ""
    bits = [f"{esc(where[:40])}{more}", short_term(rec)]
    if rec.get("hw"):
        bits.append(HW_SUBTYPE_LABEL.get(rec["hw"], rec["hw"]))
    if rec.get("fit") is not None:
        bits.append(f"fit {rec['fit']}%")
    if rec.get("pay"):
        bits.append(esc(rec["pay"]))
    deadline = to_date(rec.get("deadline"))
    if deadline:
        left = (deadline - date.today()).days
        if left < 0:
            bits.append("⏰ deadline passed")
        elif left <= 14:
            bits.append(f"⏰ apply by {rec['deadline']}")
    flags = [FLAG_TEXT.get(f, f) for f in rec.get("flags") or []]
    if flags:
        bits.append("⚠ " + ", ".join(flags))
    return f"**{esc(rec['company'])}** [{esc(rec['title'][:90])}]({safe_link(rec['url'])})\n" + "  |  ".join(bits)


def webhook_url() -> str | None:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    return url or None


def post(payload: dict) -> None:
    url = webhook_url()
    if not url:
        log.info("DISCORD_WEBHOOK_URL not set; skipping Discord")
        return
    for attempt in range(4):
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code == 429:
            wait = float((r.json() or {}).get("retry_after", 2))
            time.sleep(min(wait, 30) + 0.5)
            continue
        if r.status_code >= 400:
            log.warning("Discord returned %s: %s", r.status_code, r.text[:200])
        return
    log.warning("Discord kept rate-limiting; gave up on one message")


def _embeds_for(title: str, lines: list[str], color: int) -> list[dict]:
    embeds, buf = [], ""
    for line in lines:
        if len(buf) + len(line) + 2 > EMBED_LIMIT:
            embeds.append({"title": title, "description": buf.strip(), "color": color})
            title, buf = f"{title} (cont.)", ""
        buf += line + "\n\n"
    if buf.strip():
        embeds.append({"title": title, "description": buf.strip(), "color": color})
    return embeds


def _send(content: str, embeds: list[dict], mention: str | None) -> None:
    allowed = {"users": [mention]} if mention else {"parse": []}
    batch, size, first = [], 0, True
    for e in embeds:
        n = len(e.get("title", "")) + len(e.get("description", "")) + len(e.get("footer", {}).get("text", ""))
        if batch and (len(batch) == 10 or size + n > MESSAGE_LIMIT):
            post({"content": content if first else "", "embeds": batch, "allowed_mentions": allowed})
            batch, size, first = [], 0, False
            time.sleep(1)
        batch.append(e)
        size += n
    if batch:
        post({"content": content if first else "", "embeds": batch, "allowed_mentions": allowed})


def announce_roles(new: list[dict], settings, dashboard_url: str = "") -> None:
    if not new:
        return
    cfg = settings.raw["discord"]
    cap = int(cfg.get("max_roles_per_run") or 80)
    shown = sorted(new, key=lambda r: (not r.get("pri"), bool(r.get("flags")), CATEGORY_ORDER.index(r.get("cat", "other")),
                                       -(r.get("fit") or 0)))[:cap]
    embeds = []
    pri = [r for r in shown if r.get("pri")]
    if pri:
        embeds += _embeds_for(f"Priority ({len(pri)})", [role_line(r) for r in pri], COLORS["priority"])
    for cat in CATEGORY_ORDER:
        group = [r for r in shown if not r.get("pri") and r.get("cat") == cat]
        if group:
            embeds += _embeds_for(f"{CATEGORY_LABEL[cat]} ({len(group)})", [role_line(r) for r in group],
                                  COLORS.get(cat, 0x8594A1))
    if len(new) > cap or dashboard_url:
        rest = f"{len(new) - cap} more not shown. " if len(new) > cap else ""
        link = f"Open the dashboard: {dashboard_url}" if dashboard_url else ""
        embeds[-1]["footer"] = {"text": (rest + link).strip()}
    mention = str(cfg.get("mention_user_id") or "").strip() or None
    content = f"{len(new)} new role{'s' if len(new) != 1 else ''}"
    if pri and mention:
        content = f"<@{mention}> {len(pri)} priority, {content}"
    _send(content, embeds, mention if pri else None)


def announce_seed(count: int, sources: int, dashboard_url: str = "") -> None:
    text = (f"Scope is running. Found {count} open roles across {sources} sources. "
            f"From the next scan on, only new roles are posted here.")
    if dashboard_url:
        text += f"\n{dashboard_url}"
    post({"content": text, "allowed_mentions": {"parse": []}})


def announce_failures(names: list[str]) -> None:
    if not names:
        return
    text = ("These sources failed 3 scans in a row: " + ", ".join(esc(n) for n in names[:20]) +
            "\nRun `python -m scope check` to see why, then fix or remove them in config/companies.yaml.")
    post({"embeds": [{"title": "Sources need attention", "description": text, "color": COLORS["warn"]}],
          "allowed_mentions": {"parse": []}})


def announce_followups(items: list[dict], settings, dashboard_url: str = "") -> None:
    if not items:
        return
    lines = []
    for f in items:
        lines.append(f"**{esc(f['company'])}** {esc(f['title'][:80])}\n{esc(f['message'])}")
    embeds = _embeds_for(f"Follow-ups due ({len(items)})", lines, COLORS["followup"])
    if dashboard_url:
        embeds[-1]["footer"] = {"text": f"Log what you did in the dashboard: {dashboard_url}"}
    mention = str(settings.raw["discord"].get("mention_user_id") or "").strip() or None
    content = f"<@{mention}> follow-ups due" if mention else "Follow-ups due"
    _send(content, embeds, mention)


def announce_digest(stats: dict, settings, dashboard_url: str = "") -> None:
    goal, applied, due = stats["goal"], stats["applied"], stats["due"]
    met = applied >= goal
    lines = [f"**{applied} of {goal}** applications this week" + (" — goal met" if met else ""),
            f"**{due}** follow-up{'s' if due != 1 else ''} due"]
    top = stats.get("top") or []
    if top:
        lines.append("")
        lines.append("**Best matches you haven't applied to**")
        lines += [role_line(r) for r in top]
    embeds = _embeds_for("Weekly check-in", lines, COLORS["product"] if met else COLORS["priority"])
    if dashboard_url:
        embeds[-1]["footer"] = {"text": f"Open the dashboard: {dashboard_url}"}
    mention = str(settings.raw["discord"].get("mention_user_id") or "").strip() or None
    content = f"<@{mention}> weekly check-in" if mention else "Weekly check-in"
    _send(content, embeds, mention if not met else None)
