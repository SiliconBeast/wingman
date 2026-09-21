"""Decide whether a posting is an internship you want: role, term, location, category."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import unquote, urlparse

from .util import days_between, norm_company, to_date

# ================================================================== internship?

INTERN_RE = re.compile(
    r"(?<![\w])(interns?|internships?|co-?ops?|coop|student|apprentice(?:ship)?|trainee|"
    r"summer (?:analyst|engineer|associate|intern|program)|industrial placement|placement year|"
    r"pey|work[- ]term|stagiaire|werkstudent|university intern)(?![\w])"
    r"|(?<![\w])stage(?=\s*(?:d['’]|en\b|[-–:]))",
    re.I,
)

DEFAULT_EXCLUDE_TITLE = [
    r"\bph\.?d\b", r"\bdoctoral\b", r"\bpost-?doc", r"\bmba\b", r"summer associate",
    r"(?:^|[\s(\-–|,])(?:ms|msc|masters?|master's)(?:[\s)\-–|,]|$)",
    r"\bnew grad", r"\bsenior\b", r"\bsr\.?\s", r"\bstaff\b", r"\bprincipal\b", r"\bdirector\b",
    r"graduate student", r"vice president", r"\bavp\b",
    r"\bpart[- ]time\b.*\bschool year\b",
]


def is_internship(title: str, extra: dict) -> bool:
    if extra.get("assume_intern"):
        return True
    if INTERN_RE.search(title or ""):
        return True
    commitment = str(extra.get("commitment") or "").lower()
    return bool(re.search(r"intern|co-?op|student", commitment))


# ================================================================== terms

SEASON = {"spring": "Spring", "summer": "Summer", "fall": "Fall", "autumn": "Fall", "winter": "Winter",
          "printemps": "Spring", "été": "Summer", "automne": "Fall", "hiver": "Winter"}
_S = r"(?:spring(?!\s*(?:boot|framework|mvc|cloud|batch|security|data))|summer|fall|autumn|winter|printemps|été|automne|hiver)"
_SEP = r"(?:\s*(?:/|&|\+|,|-|–|—|\bor\b|\band\b|\bet\b)\s*|\s+)"
SEASON_YEAR = re.compile(
    rf"(?<![\w])({_S}(?:{_SEP}{_S})*)"
    r"(?:\s+(?:term|semester|session|internship|intern|co-?op|program(?:me)?))?"
    r"\s*[-–,]?\s*(?:(20\d\d)|['’‘](\d\d))(?!\d)", re.I)
YEAR_SEASON = re.compile(rf"(?<!\d)(20\d\d)\s*[-–]?\s*({_S}(?:{_SEP}{_S})*)(?![\w])", re.I)
SEASON_ONLY = re.compile(rf"(?<![\w]){_S}(?![\w])", re.I)
SEASON_WORD = re.compile(r"spring|summer|fall|autumn|winter|printemps|été|automne|hiver", re.I)

MONTH = {"jan": 1, "january": 1, "janvier": 1, "feb": 2, "february": 2, "février": 2, "fevrier": 2,
         "mar": 3, "march": 3, "mars": 3, "apr": 4, "april": 4, "avril": 4, "may": 5, "mai": 5,
         "jun": 6, "june": 6, "juin": 6, "jul": 7, "july": 7, "juillet": 7, "aug": 8, "august": 8,
         "août": 8, "aout": 8, "sep": 9, "sept": 9, "september": 9, "septembre": 9, "oct": 10,
         "october": 10, "octobre": 10, "nov": 11, "november": 11, "novembre": 11, "dec": 12,
         "december": 12, "décembre": 12, "decembre": 12}
_M = (r"(jan(?:uary)?|janvier|feb(?:ruary)?|f[ée]vrier|mar(?:ch)?|mars|apr(?:il)?|avril|may|mai|"
      r"june?|juin|july?|juillet|aug(?:ust)?|ao[uû]t|sept?(?:ember)?|septembre|oct(?:ober)?|octobre|"
      r"nov(?:ember)?|novembre|dec(?:ember)?|d[ée]cembre)\.?")
MONTH_RANGE = re.compile(
    rf"(?<![\w]){_M}\s*,?\s*(20\d\d)?\s*(?:-|–|—|to|through|thru|until|à|au)\s*{_M}\s*,?\s*(20\d\d)?(?![\w])", re.I)
MONTH_START = re.compile(
    rf"(?:start(?:s|ing)?|begin(?:s|ning)?|commenc\w*|d[ée]but\w*)(?:\s+(?:date|in|on|of|around|at))*"
    rf"\s*[:\-–]?\s*(?:early|mid|late)?[\s-]*{_M}\s*,?\s*(20\d\d)", re.I)
MONTH_YEAR = re.compile(rf"(?<![\w]){_M}\s*,?\s*(20\d\d)(?!\d)", re.I)
BARE_YEAR = re.compile(r"(?<!\d)(20[2-3]\d)(?!\d)")
DUR_MONTHS = re.compile(r"(?<![\w.])(\d{1,2})(?:\s*(?:-|–|to|or|/)\s*(\d{1,2}))?[\s-]*(?:months?|mos?|mois)(?![\w])", re.I)
DUR_WEEKS = re.compile(r"(?<![\w.])(\d{1,2})(?:\s*(?:-|–|to)\s*(\d{1,2}))?[\s-]*(?:weeks?|wks?|semaines)(?![\w])", re.I)
COOP = re.compile(r"(?<![\w])(co-?op|coop|pey|work[- ]term)(?![\w])", re.I)
# Sentences about graduation dates mention years that are not the work term.
GRAD = re.compile(r"graduat|class of|degree|enrol|diploma|expected to (?:finish|complete)|"
                  r"return(?:ing)? to (?:school|your studies)|academic|pursuing|currently in", re.I)
TERM_CONTEXT = re.compile(r"intern|co-?op|work term|placement|program|duration|length|term", re.I)


def month_num(s: str) -> int:
    return MONTH[s.lower().rstrip(".")]


def season_of(month: int, year: int) -> str:
    if month <= 3:
        return f"Winter {year}"
    if month <= 7:
        return f"Summer {year}"
    if month <= 10:
        return f"Fall {year}"
    return f"Winter {year + 1}"


@dataclass
class TermInfo:
    terms: set = field(default_factory=set)     # "Summer 2027"
    seasons: set = field(default_factory=set)   # "Summer" (no year given)
    years: set = field(default_factory=set)     # bare years in a title: "Intern 2027"
    months: set = field(default_factory=set)    # durations in months
    coop: bool = False


def parse_terms(text: str | None, mode: str = "title") -> TermInfo:
    """mode: 'title' (everything counts), 'url' (season/month + year only), 'jd' (filtered sentences)."""
    info = TermInfo()
    if not text:
        return info
    t = html.unescape(text)
    if mode == "jd":
        parts = re.split(r"(?<=[.!?;])\s+|\n+", t[:9000])
        t = "\n".join(p for p in parts if not GRAD.search(p))
    spans: list[tuple[int, int]] = []

    for m in SEASON_YEAR.finditer(t):
        year = int(m[2]) if m[2] else 2000 + int(m[3])
        for s in SEASON_WORD.findall(m[1]):
            info.terms.add(f"{SEASON[s.lower()]} {year}")
        spans.append(m.span())
    for m in YEAR_SEASON.finditer(t):
        for s in SEASON_WORD.findall(m[2]):
            info.terms.add(f"{SEASON[s.lower()]} {int(m[1])}")
        spans.append(m.span())
    for m in MONTH_RANGE.finditer(t):
        m1, y1, m2, y2 = month_num(m[1]), m[2], month_num(m[3]), m[4]
        if y1 is None and y2 is None:
            if mode == "title":
                info.seasons.add(season_of(m1, 2000).split()[0])
                info.months.add((m2 - m1) % 12 + 1)
            continue
        y2 = int(y2) if y2 else (int(y1) if m2 >= m1 else int(y1) + 1)
        y1 = int(y1) if y1 else (y2 if m1 <= m2 else y2 - 1)
        info.terms.add(season_of(m1, y1))
        length = (y2 * 12 + m2) - (y1 * 12 + m1) + 1
        if 1 <= length <= 24:
            info.months.add(length)
        spans.append(m.span())
    for m in MONTH_START.finditer(t):
        info.terms.add(season_of(month_num(m[1]), int(m[2])))
        spans.append(m.span())
    if mode in ("title", "url"):
        for m in MONTH_YEAR.finditer(t):
            a, b = m.span()
            if any(a < y and x < b for x, y in spans):   # already part of a range like "May - Dec 2027"
                continue
            info.terms.add(season_of(month_num(m[1]), int(m[2])))
            spans.append(m.span())

    if mode == "title":
        masked = list(t)
        for a, b in spans:
            masked[a:b] = " " * (b - a)
        masked_s = "".join(masked)
        for m in SEASON_ONLY.finditer(masked_s):
            info.seasons.add(SEASON[SEASON_WORD.match(m[0]).group(0).lower()])
        info.years.update(int(y) for y in BARE_YEAR.findall(masked_s))

    dur_text = t if mode != "jd" else " ".join(s for s in re.split(r"(?<=[.!?;])\s+|\n+", t) if TERM_CONTEXT.search(s))
    for m in DUR_MONTHS.finditer(dur_text):
        for g in (m[1], m[2]):
            if g and 2 <= int(g) <= 24:
                info.months.add(int(g))
    for m in DUR_WEEKS.finditer(dur_text):
        for g in (m[1], m[2]):
            if g and 6 <= int(g) <= 52:
                info.months.add(max(2, round(int(g) / 4.33)))
    info.coop = bool(COOP.search(t))
    return info


def url_text(url: str | None) -> str:
    if not url:
        return ""
    p = urlparse(url)
    return re.sub(r"[-_/+.]+", " ", unquote(p.path))


_SEASON_START = {"Winter": 1, "Spring": 1, "Summer": 5, "Fall": 9}


def next_occurrence(season: str, base: date) -> int:
    return base.year if base.month < _SEASON_START[season] else base.year + 1


@dataclass
class TermDecision:
    ok: bool
    terms: list
    quality: str      # explicit | inferred | year | unknown


def decide_term(title: TermInfo, url: TermInfo, jd: TermInfo | None, list_terms: list, list_year: int | None,
                posted: str | None, settings: dict, today: date, default_terms: list | None = None) -> TermDecision:
    """Order of evidence: the posting's own term fields/title/URL, then its description, then
    seasons without a year, then a bare year, then the list's defaults, then 'not stated'."""
    targets = set(settings["terms"])
    target_years = {int(t.split()[-1]) for t in targets}
    # The employer's own words win: title, then link, then the list's tag, then the description.
    explicit = set(title.terms) or set(url.terms) or set(list_terms or [])
    if not explicit and jd is not None:
        explicit = set(jd.terms)
    if explicit:
        hit = explicit & targets
        return TermDecision(bool(hit), sorted(hit or explicit, key=_term_key), "explicit")
    seasons = title.seasons | url.seasons
    if seasons and title.years:                      # "Fall Intern/Co-op - 2026"
        paired = {f"{s} {y}" for s in seasons for y in title.years}
        hit = paired & targets
        return TermDecision(bool(hit), sorted(hit or paired, key=_term_key), "explicit")
    if seasons:
        base = to_date(posted) or today
        inferred = {f"{s} {next_occurrence(s, base)}" for s in seasons}
        hit = inferred & targets
        return TermDecision(bool(hit), sorted(hit or inferred, key=_term_key), "inferred")
    if title.years:
        return TermDecision(bool(title.years & target_years), [str(y) for y in sorted(title.years)], "year")
    if default_terms:
        hit = set(default_terms) & targets
        return TermDecision(bool(hit), sorted(hit or set(default_terms), key=_term_key), "list")
    if list_year:
        return TermDecision(list_year in target_years, [str(list_year)], "year")
    if not settings.get("include_unknown_term", True):
        return TermDecision(False, [], "unknown")
    age = days_between(posted, today)
    return TermDecision(age is None or age <= int(settings.get("unknown_term_max_age_days", 60)), [], "unknown")


def _term_key(t: str):
    order = {"Winter": 0, "Spring": 1, "Summer": 2, "Fall": 3}
    parts = t.split()
    return (int(parts[-1]) if parts[-1].isdigit() else 0, order.get(parts[0], 9))


# ================================================================== location

US_STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado",
    "CT": "connecticut", "DE": "delaware", "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho",
    "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana",
    "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
    "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york", "NC": "north carolina",
    "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
    "RI": "rhode island", "SC": "south carolina", "SD": "south dakota", "TN": "tennessee", "TX": "texas",
    "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west virginia",
    "WI": "wisconsin", "WY": "wyoming", "DC": "district of columbia", "PR": "puerto rico",
}
CA_PROV_STRONG = {"ON", "QC", "BC", "AB"}
# US state codes that are also ISO country codes (CA=Canada, IN=India, DE=Germany, ...)
ISO_COLLIDE = {"CA", "IN", "DE", "GA", "IL", "PA", "CO", "AL", "AR", "AZ", "MA", "MD", "ME", "MN", "MT",
               "NE", "SC", "SD", "TN", "VA", "KY", "LA", "ID", "NC"}
ISO_OTHER = set("""AE AR AT AU BD BE BG BR CH CL CN CO CR CZ DE DK EE EG ES FI FR GB GR HK HR HU ID IE IL IN IT JP KE KR
LK LT LU LV MA MX MY NG NL NO NZ PE PH PK PL PT QA RO RS SA SE SG SI SK TH TR TW UA UK VN ZA""".split())
CA_PROV_WEAK = {"MB", "SK", "NS", "NB", "NL", "PE", "YT", "NT", "NU"}
CA_PROV_NAMES = ["ontario", "quebec", "québec", "british columbia", "alberta", "manitoba", "saskatchewan",
                 "nova scotia", "new brunswick", "newfoundland", "prince edward island", "yukon", "nunavut",
                 "northwest territories"]
CA_CITIES = ["toronto", "montreal", "montréal", "vancouver", "ottawa", "waterloo", "kitchener", "calgary",
             "edmonton", "markham", "mississauga", "burnaby", "kanata", "halifax", "winnipeg", "victoria, bc",
             "richmond hill", "vaughan", "oakville", "brampton", "laval", "saskatoon", "regina", "gatineau",
             "guelph", "surrey, bc", "thornhill", "north york", "etobicoke", "scarborough", "longueuil",
             "sherbrooke", "fredericton", "moncton", "st. john's", "kelowna"]
US_CITIES = ["nyc", "sf", "la", "south sf", "bay area", "silicon valley", "new york", "san francisco",
             "seattle", "boston", "austin", "chicago", "los angeles", "san jose", "santa clara", "sunnyvale",
             "mountain view", "palo alto", "menlo park", "redmond", "bellevue", "cupertino", "atlanta",
             "denver", "pittsburgh", "philadelphia", "san diego", "irvine", "portland", "houston", "dallas",
             "miami", "raleigh", "durham", "phoenix", "salt lake city", "minneapolis", "detroit", "ann arbor",
             "princeton", "jersey city", "hoboken", "stamford", "greenwich", "cambridge, ma", "washington, dc",
             "arlington, va", "mclean", "reston", "baltimore", "columbus", "nashville", "charlotte"]
OTHER_PLACES = [
    "india", "bangalore", "bengaluru", "hyderabad", "pune", "chennai", "noida", "gurgaon", "gurugram", "mumbai",
    "delhi", "united kingdom", "uk", "england", "scotland", "london", "cambridge, uk", "manchester", "edinburgh",
    "ireland", "dublin", "cork", "germany", "munich", "münchen", "berlin", "frankfurt", "hamburg", "stuttgart",
    "france", "paris", "grenoble", "sophia antipolis", "netherlands", "amsterdam", "eindhoven", "belgium",
    "leuven", "poland", "warsaw", "krakow", "kraków", "wroclaw", "gdansk", "spain", "madrid", "barcelona",
    "portugal", "lisbon", "porto", "italy", "milan", "rome", "switzerland", "zurich", "zürich", "geneva",
    "lausanne", "austria", "vienna, austria", "sweden", "stockholm", "gothenburg", "lund", "denmark",
    "copenhagen", "norway", "oslo", "trondheim", "finland", "helsinki", "oulu", "israel", "tel aviv", "haifa",
    "jerusalem", "herzliya", "singapore", "china", "shanghai", "beijing", "shenzhen", "hangzhou", "hong kong",
    "taiwan", "taipei", "hsinchu", "japan", "tokyo", "osaka", "korea", "seoul", "australia", "sydney",
    "melbourne", "new zealand", "brazil", "são paulo", "sao paulo", "mexico", "guadalajara", "argentina",
    "buenos aires", "colombia", "bogota", "chile", "costa rica", "philippines", "manila", "vietnam", "hanoi",
    "ho chi minh", "malaysia", "penang", "kuala lumpur", "thailand", "bangkok", "indonesia", "jakarta",
    "united arab emirates", "uae", "dubai", "abu dhabi", "saudi", "riyadh", "qatar", "doha", "egypt", "cairo",
    "south africa", "nigeria", "lagos", "kenya", "romania", "bucharest", "iasi", "czech", "prague", "brno",
    "hungary", "budapest", "greece", "athens", "turkey", "istanbul", "ukraine", "kyiv", "serbia", "belgrade",
    "bulgaria", "sofia", "estonia", "tallinn", "latvia", "lithuania", "vilnius", "luxembourg", "slovakia",
    "croatia", "emea", "apac", "latam", "latin america",
]


def _alts(words):
    return re.compile(r"(?<![\w])(?:" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")(?![\w])", re.I)


_CA_WORD = re.compile(r"(?<![\w])(?:canada|canadian)(?![\w])|^\s*can\s*[,\-]", re.I)
_US_WORD = re.compile(r"(?<![\w])(?:united states(?: of america)?|usa|u\.s\.a?\.?|us)(?![\w])", re.I)
_CA_NAMES = _alts(CA_PROV_NAMES)
_CA_CITIES = _alts(CA_CITIES)
_US_NAMES = _alts([v for v in US_STATES.values()])
_US_CITIES = _alts(US_CITIES)
_OTHER = _alts(OTHER_PLACES)
_CODE = re.compile(r"(?:^|,|\(|\s-|\s/)\s*([A-Z]{2})(?![A-Za-z])")
REMOTE = re.compile(r"(?<![\w])(remote|virtual|work from home|wfh|télétravail|anywhere)(?![\w])", re.I)
_SPLIT = re.compile(r"\s*(?:;|\||•|\n|\s/\s|\bor\b|<br\s*/?>)\s*", re.I)
ISO2 = {"US": "US", "USA": "US", "CA": "CA", "CAN": "CA"}


def split_locations(s: str | None) -> list[str]:
    if not s:
        return []
    return [p.strip(" ,") for p in _SPLIT.split(str(s)) if p and p.strip(" ,")]


def countries_of(loc: str) -> set[str]:
    """Return {'US'}, {'CA'}, {'OTHER'} or set() (unknown) for one location string."""
    s = html.unescape(loc or "").strip()
    if not s:
        return set()
    low = s.lower()
    strong: set[str] = set()
    if _CA_WORD.search(low) or _CA_NAMES.search(low) or _CA_CITIES.search(low):
        strong.add("CA")
    if _US_WORD.search(low) or _US_NAMES.search(low) or _US_CITIES.search(low):
        strong.add("US")
    codes = _CODE.findall(s)
    if any(c in CA_PROV_STRONG for c in codes):
        strong.add("CA")
    # Workday style "US, CA, Santa Clara" or "CAN, ON, Toronto"
    head = re.match(r"\s*([A-Z]{2,3})\s*[,\-]", s)
    if head and head[1] in ISO2:
        strong.add(ISO2[head[1]])
    if strong:
        return strong
    # lowercase trailing ISO code, as SmartRecruiters writes it: "Aveiro, pt", "Toronto, ca"
    tail = re.search(r",\s*([a-z]{2})\s*$", s)
    if tail:
        code = tail[1].upper()
        if code in ("US", "CA"):
            return {code}
        if code in ISO_OTHER:
            return {"OTHER"}
    # "Paris, TX" is Texas; "Bangalore, IN" is India (IN is also an ISO country code)
    if any(c in US_STATES and c not in ISO_COLLIDE for c in codes):
        return {"US"}
    if _OTHER.search(low):
        return {"OTHER"}
    weak = set()
    for c in codes:
        if c in US_STATES:
            weak.add("US")
        elif c in CA_PROV_WEAK:
            weak.add("CA")
    return weak


def classify_locations(locs: list[str], extra: dict) -> tuple[set[str], bool]:
    countries: set[str] = set()
    remote = bool(extra.get("remote"))
    for loc in locs:
        for part in split_locations(loc):
            countries |= countries_of(part)
            if REMOTE.search(part):
                remote = True
    for code in extra.get("country_codes") or []:
        if code:
            code = str(code).upper()
            countries.add(ISO2.get(code, "OTHER"))
    for name in extra.get("country_names") or []:
        if name:
            countries |= countries_of(str(name)) or {"OTHER"}
    if countries - {"OTHER"}:
        countries.discard("OTHER")
    return countries, remote


# ================================================================== category

CATEGORY_ORDER = ["hardware", "software", "ai_data", "quant", "product", "finance", "other"]
CATEGORY_LABEL = {"hardware": "Hardware", "software": "Software", "ai_data": "AI & data", "quant": "Quant",
                  "product": "Product", "finance": "Finance", "other": "Other"}
_HINTS = {"software": "software", "software engineering": "software", "hardware": "hardware",
          "hardware engineering": "hardware", "ai/ml/data": "ai_data",
          "data science, ai & machine learning": "ai_data", "quant": "quant", "quantitative finance": "quant",
          "product": "product", "product management": "product"}
_CATS = [
    ("quant", r"\bquant\w*|quantitative|\btrad(?:er|ing)\b|algorithmic|market[- ]?mak|\bstrats?\b"),
    ("hardware", r"hardware|\basic\b|\bfpga\b|\brtl\b|verilog|\bvhdl\b|silicon|\bsoc\b|\bchip|semiconductor|"
                 r"analog|mixed[- ]signal|circuit|\bpcb\b|electrical|electronic|embedded|firmware|\brf\b|antenna|"
                 r"microwave|power (?:electronics|systems|engineer)|design verification|\bdv\b|\bdft\b|"
                 r"physical design|\blayout\b|photonic|optical|optics|mechatronic|robotic|controls? (?:engineer|systems)|"
                 r"signal integrity|\bemc\b|packaging|validation|product engineer|process engineer|\bdevice|"
                 r"wireless|sensor|avionics|computer architecture|performance model|\bgpu\b|\bcpu\b|memory|"
                 r"test engineer|systems engineer(?:ing)? intern|electrical engineering"),
    ("ai_data", r"machine learning|\bml\b|\bai\b|artificial intelligence|deep learning|computer vision|\bnlp\b|"
                r"\bllms?\b|data scien|data engineer|data analy|analytics|research scientist|research engineer|"
                r"applied scien|business intelligence|\bbi\b|perception|autonomy"),
    ("other", r"mechanical|\bcivil\b|chemical|structural|manufacturing engineer|industrial engineer|"
              r"supply chain|marketing|\bsales\b|human resources|\bhr\b|recruit|\blegal\b|paralegal|"
              r"communications|procurement|logistics|environmental|geolog|mining|biolog|nursing|clinical"),
    ("product", r"product manag|\bapm\b|\bpm\b|product design|\bux\b|\bui\b|user experience|program manag|"
                r"\btpm\b|product owner"),
    ("software", r"software|\bswe\b|\bsde\b|developer|development engineer|programmer|back-?end|front-?end|"
                 r"full[- ]?stack|mobile|\bios\b|android|devops|\bsre\b|site reliability|cloud|infrastructure|"
                 r"security|cyber|platform|\bweb\b|application|\bqa\b|test automation|computer science|"
                 r"\bit\b|information technology|systems? (?:developer|analyst)|technology|\btech\b|"
                 r"engineer|engineering|solutions? engineer|technical|computer|développe|logiciel|informatique"),
    ("finance", r"investment bank|\bib\b|summer analyst|sales (?:and|&) trading|global markets|markets|"
                r"equity research|asset management|wealth|corporate bank|\brisk\b|financ|capital markets|"
                r"private equity|actuar|treasury|audit|accounting|credit|underwriting|valuation|banking|"
                r"investment|portfolio|analyst"),
]
_CATS_RE = [(k, re.compile(p, re.I)) for k, p in _CATS]


def classify_category(title: str, hint: str | None = None, extra_text: str = "") -> str:
    if hint:
        mapped = _HINTS.get(str(hint).strip().lower())
        if mapped:
            return mapped
    for text in (title or "", extra_text or ""):
        for key, rx in _CATS_RE:
            if rx.search(text):
                return key
    return "other"


# ================================================================== priority & degrees

def is_priority(company: str, title: str, settings: dict) -> bool:
    pri = settings.get("priority") or {}
    low = (company or "").lower()
    for c in pri.get("companies") or []:
        c = str(c).strip()
        if c and (norm_company(c) == norm_company(company) or re.search(rf"(?<![\w]){re.escape(c.lower())}(?![\w])", low)):
            return True
    t = (title or "").lower()
    return any(re.search(rf"(?<![\w]){re.escape(str(k).lower())}(?![\w])", t) for k in pri.get("keywords") or [])


def degree_ok(degrees) -> bool:
    """Simplify tags degree requirements; skip postings that need an advanced degree."""
    if not degrees:
        return True
    return any(d in ("Bachelor's", "Associate's") for d in degrees)


# ================================================================== hardware sub-type

HW_SUBTYPE_ORDER = ["verification", "physical_design", "analog", "fpga", "rf", "embedded", "test", "pcb", "rtl_design"]
HW_SUBTYPE_LABEL = {"verification": "Verification", "physical_design": "Physical design", "analog": "Analog/mixed-signal",
                    "fpga": "FPGA", "rf": "RF", "embedded": "Embedded", "test": "Validation/test",
                    "pcb": "PCB/electrical", "rtl_design": "RTL design"}
_HW_SUB = [
    ("verification", r"\bverification\b|\bDV\b|design verification|test\s*bench|\bUVM\b|formal verification"),
    ("physical_design", r"physical design|\bP&R\b|\bPnR\b|place\s*(?:and|&)\s*route|floorplan|timing closure|"
                        r"back-?end design|\bSTA\b|\bDFT\b"),
    ("analog", r"\banalog\b|mixed-signal|analog design|\bIC design\b|circuit design"),
    ("fpga", r"\bFPGA\b"),
    ("rf", r"\bRF\b|radio frequency|antenna|microwave|wireless (?:hardware|systems)|\bmmWave\b"),
    ("embedded", r"embedded (?:software|systems|firmware)|firmware engineer|device driver|\bBSP\b|"
                r"bring-?up engineer"),
    ("test", r"\btest engineer\b|silicon validation|post-?silicon|bring-?up|manufacturing test|"
             r"product engineer(?:ing)?|process engineer"),
    ("pcb", r"\bPCB\b|hardware design engineer|electrical (?:design|engineer)|schematic capture|"
            r"signal integrity|power integrity"),
    ("rtl_design", r"\bRTL\b|digital design|logic design|\bASIC design\b|microarchitecture|front-?end design|"
                   r"computer architecture"),
]
_HW_SUB_RE = [(k, re.compile(p, re.I)) for k, p in _HW_SUB]


def classify_hw_subtype(title: str) -> str | None:
    """A finer label within the 'hardware' category, from the title alone."""
    for key, rx in _HW_SUB_RE:
        if rx.search(title or ""):
            return key
    return None


# ================================================================== deadline & pay (from the description)

DEADLINE_CTX = (r"apply\s*(?:by|before|no later than)|application deadline|closing date|"
               r"deadline to apply|must apply by")
DEADLINE_MDY = re.compile(rf"(?:{DEADLINE_CTX})\s*(?:is|:)?\s*{_M}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s*(20\d\d)?", re.I)
DEADLINE_NUM = re.compile(rf"(?:{DEADLINE_CTX})\s*(?:is|:)?\s*(\d{{1,2}})[/-](\d{{1,2}})[/-](20\d\d)", re.I)
PAY_RE = re.compile(
    r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?\s*k?"
    r"(?:\s*(?:-|–|—|to)\s*\$?\s?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?\s*k?)?"
    r"\s*(?:/|per)?\s*(?:hour|hr|hourly|year|yr|annum|annually|month|mo\.?)\b", re.I)


def extract_deadline(text: str, today: date | None = None) -> str | None:
    """'Apply by <date>' from a posting, as YYYY-MM-DD. None unless the phrasing is explicit."""
    if not text:
        return None
    base = today or date.today()
    window = text[:6000]
    m = DEADLINE_MDY.search(window)
    if m:
        try:
            d = date(int(m[3]) if m[3] else base.year, month_num(m[1]), int(m[2]))
        except (KeyError, ValueError):
            d = None
        if d:
            if not m[3] and d < base:
                d = d.replace(year=d.year + 1)
            return d.isoformat()
    m = DEADLINE_NUM.search(window)
    if m:
        a, b, year = int(m[1]), int(m[2]), int(m[3])
        for month, day_ in ((a, b), (b, a)):     # tolerate MM/DD or DD/MM
            try:
                return date(year, month, day_).isoformat()
            except ValueError:
                continue
    return None


def extract_pay(text: str) -> str | None:
    """A pay rate mentioned in a posting ('$28 - $35 / hour'), verbatim and cleaned up. None if unclear."""
    if not text:
        return None
    m = PAY_RE.search(text[:6000])
    if not m:
        return None
    s = re.sub(r"\s+", " ", m.group(0)).strip()
    return s if s.count("$") <= 2 and len(s) <= 40 else None
