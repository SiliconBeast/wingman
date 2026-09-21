"""Load config/settings.yaml and config/companies.yaml."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from .classify import CATEGORY_ORDER, DEFAULT_EXCLUDE_TITLE
from .util import CONFIG, load_yaml

DEFAULTS = {
    "terms": ["Summer 2027", "Fall 2027"],
    "include_unknown_term": True,
    "unknown_term_max_age_days": 60,
    "countries": ["US", "CA"],
    "include_unknown_location": True,
    "categories": ["hardware", "software", "ai_data", "quant", "product", "finance"],
    "exclude_title": DEFAULT_EXCLUDE_TITLE,
    "priority": {"companies": [], "keywords": []},
    "lists": [],
    "discover": {"enabled": True, "min_roles": 1, "max_boards": 300, "rotate": 3},
    "graduation": "",
    "timezone": "UTC",
    "max_details_per_run": 250,
    "workers": 8,
    "keep_closed_days": 21,
    "forget_after_days": 120,
    "discord": {"mention_user_id": "", "max_roles_per_run": 80, "quiet_hours": [22, 8]},
    "dashboard_url": "",
    "followups": {"applied_days": 14, "second_days": 14, "ghost_days": 45, "saved_days": 5,
                  "oa_days": 5, "interview_days": 7},
    "goals": {"weekly_applications": 5},
}


@dataclass
class Settings:
    raw: dict
    companies: dict

    @classmethod
    def load(cls, config_dir: Path = CONFIG) -> "Settings":
        user = load_yaml(config_dir / "settings.yaml") if (config_dir / "settings.yaml").exists() else {}
        raw = {**DEFAULTS, **(user or {})}
        for key in ("discord", "followups", "priority", "discover", "goals"):
            raw[key] = {**DEFAULTS[key], **((user or {}).get(key) or {})}
        bad = [c for c in raw["categories"] if c not in CATEGORY_ORDER]
        if bad:
            raise ValueError(f"settings.yaml: unknown categories {bad}; use {CATEGORY_ORDER}")
        for t in raw["terms"]:
            if not re.fullmatch(r"(Winter|Spring|Summer|Fall) 20\d\d", t):
                raise ValueError(f"settings.yaml: term '{t}' should look like 'Summer 2027'")
        companies = {}
        path = config_dir / "companies.yaml"
        if path.exists():
            companies = load_yaml(path) or {}
            if not isinstance(companies, dict):
                raise ValueError("companies.yaml should be a list of 'Name: URL' lines")
        return cls(raw=raw, companies=companies)

    @property
    def discover(self) -> dict:
        return self.raw["discover"]

    @property
    def countries(self) -> set:
        return {c.upper() for c in self.raw["countries"]}

    @property
    def categories(self) -> set:
        return set(self.raw["categories"])

    @property
    def exclude_title(self):
        return [re.compile(p, re.I) for p in self.raw["exclude_title"]]

    @property
    def include_unknown_location(self) -> bool:
        return bool(self.raw["include_unknown_location"])

    @property
    def lists(self) -> list:
        return [x if isinstance(x, dict) else {"repo": x} for x in self.raw["lists"] or []]

    @property
    def tz(self):
        return ZoneInfo(self.raw.get("timezone") or "UTC")

    @property
    def max_details(self) -> int:
        return int(self.raw["max_details_per_run"])

    @property
    def workers(self) -> int:
        return int(self.raw["workers"])

    @property
    def keep_closed_days(self) -> int:
        return int(self.raw["keep_closed_days"])

    @property
    def forget_after_days(self) -> int:
        return int(self.raw["forget_after_days"])
