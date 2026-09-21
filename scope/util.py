"""Shared helpers: HTTP, text cleanup, dates, URLs, ids and file I/O."""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("scope")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG = ROOT / "config"
RESUME = ROOT / "resume"

TIMEOUT = 25
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36")

# ------------------------------------------------------------------ HTTP

_local = threading.local()


def http() -> requests.Session:
    """One session per thread (requests.Session is not guaranteed thread-safe)."""
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        retry = Retry(total=3, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=frozenset(["GET", "POST"]), respect_retry_after_header=True)
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=16)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        s.headers.update({"User-Agent": UA,
                          "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
                          "Accept-Language": "en-US,en;q=0.9"})
        _local.session = s
    return s


def get_json(url: str, params: dict | None = None, headers: dict | None = None):
    r = http().get(url, params=params, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def post_json(url: str, body: dict, headers: dict | None = None):
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        h.update(headers)
    r = http().post(url, json=body, headers=h, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def get_text(url: str, headers: dict | None = None) -> str:
    r = http().get(url, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def http_status(exc: Exception) -> int | None:
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", None)


# ------------------------------------------------------------------ text

_BLOCK_TAG = re.compile(r"</?(?:p|div|br|ul|ol|h[1-6]|tr|table|section|article|header|footer)\b[^>]*>", re.I)


def html_to_text(s: str | None) -> str:
    """Turn job-description HTML into readable plain text."""
    if not s:
        return ""
    if "&lt;" in s and "<" not in s:          # Greenhouse double-escapes its HTML
        s = html.unescape(s)
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", s)
    s = re.sub(r"(?i)<li\b[^>]*>", "\n• ", s)
    s = re.sub(r"(?i)</li>", "\n", s)
    s = _BLOCK_TAG.sub("\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def clean_inline(s: str | None) -> str:
    """Strip markdown/HTML decoration from a short table cell or title."""
    if not s:
        return ""
    s = html.unescape(str(s))
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)                 # markdown images
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)               # markdown links -> label
    s = re.sub(r"<img\b[^>]*>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"(\*\*|__|~~|`)", "", s)
    s = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200d]", " ", s)  # emoji markers
    return " ".join(s.split()).strip(" -–|")


# ------------------------------------------------------------------ dates

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day(dt: datetime | date) -> str:
    return dt.strftime("%Y-%m-%d")


def to_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def days_between(a: str | date | None, b: str | date | None) -> int | None:
    a = to_date(a) if isinstance(a, str) else a
    b = to_date(b) if isinstance(b, str) else b
    if a is None or b is None:
        return None
    return (b - a).days


def parse_when(v) -> str | None:
    """Best-effort: epoch seconds/ms, ISO strings, 'Sep 18, 2026', '5d', 'Sep 18' -> 'YYYY-MM-DD'."""
    if v is None or v == "":
        return None
    try:
        if isinstance(v, (int, float)):
            ts = v / 1000 if v > 1e11 else v
            return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        s = str(v).strip()
        if re.fullmatch(r"\d{10,13}", s):
            return parse_when(int(s))
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
        if m:
            return f"{m[1]}-{m[2]}-{m[3]}"
        for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%m/%d/%Y", "%b %d %Y"):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass
        m = re.fullmatch(r"(\d+)\s*(h|hr|hrs|hours?|d|days?|w|wk|weeks?|mo|months?)", s, re.I)
        if m:
            n, unit = int(m[1]), m[2].lower()
            days = 0 if unit.startswith("h") else n if unit.startswith("d") else n * 7 if unit.startswith("w") else n * 30
            return day(utcnow() - timedelta(days=days))
        m = re.fullmatch(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})", s)
        if m:
            now = utcnow()
            d = datetime.strptime(f"{m[1].title()} {m[2]} {now.year}", "%b %d %Y").replace(tzinfo=timezone.utc)
            if d > now + timedelta(days=2):
                d = d.replace(year=now.year - 1)
            return day(d)
    except (ValueError, OverflowError, OSError):
        return None
    return None


def parse_workday_posted(s: str | None) -> str | None:
    s = (s or "").lower()
    if "today" in s:
        n = 0
    elif "yesterday" in s:
        n = 1
    else:
        m = re.search(r"(\d+)\+?\s*day", s)
        if not m:
            return None
        n = int(m[1])
    return day(utcnow() - timedelta(days=n))


# ------------------------------------------------------------------ urls & ids

_TRACKING = re.compile(r"^(utm_\w+|ref|src|source|gh_src|lever-source.*|lever-origin|trk|refid)$", re.I)


def clean_url(u: str | None) -> str | None:
    """Keep only http(s) links and drop tracking parameters."""
    if not u or not isinstance(u, str):
        return None
    u = u.strip()
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not _TRACKING.match(k)]
    return urlunparse(p._replace(query=urlencode(q), fragment=""))


def short_id(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|company|plc|limited|holdings|group|the)\b\.?", re.I)


def norm_company(s: str | None) -> str:
    s = html.unescape(s or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = _SUFFIX.sub(" ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def norm_title(s: str | None) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split())


# ------------------------------------------------------------------ files

def read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        log.warning("could not parse %s (%s); starting fresh", path, e)
        return default


def write_json(path: Path, obj, indent: int | None = 1) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_rows_json(path: Path, head: dict, key: str, rows: list[dict]) -> None:
    """JSON with one row per line so git diffs stay small between scans."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    head_txt = json.dumps(head, ensure_ascii=False)[:-1]
    body = ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows)
    sep = ", " if head else ""
    txt = f'{head_txt}{sep}"{key}": [\n{body}\n]}}\n'
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(txt, encoding="utf-8")
    tmp.replace(path)


def write_map_json(path: Path, obj: dict) -> None:
    """A JSON object with one key per line, so git diffs show only the entries that changed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ",\n".join(f"{json.dumps(k)}: {json.dumps(v, ensure_ascii=False, separators=(',', ':'))}" for k, v in obj.items())
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("{\n" + body + "\n}\n", encoding="utf-8")
    tmp.replace(path)


def load_yaml(path: Path):
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
