"""Run with:  python -m unittest discover -s tests"""
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from scope import classify as C
from scope import fit as F
from scope import followups
from scope import pipeline
from scope.config import Settings
from scope.resolve import Board, board_from_job_url, board_ident, canonical_key, detect_kind, workday_key
from scope.sources import ats, lists

FIX = Path(__file__).parent / "fixtures"
TARGETS = {"terms": ["Summer 2027", "Fall 2027"], "include_unknown_term": True, "unknown_term_max_age_days": 60}
TODAY = date(2026, 9, 21)


def decide(title, url="", list_terms=(), posted=None, default_terms=None):
    return C.decide_term(C.parse_terms(title), C.parse_terms(C.url_text(url), "url"), None, list(list_terms), None,
                         posted, TARGETS, TODAY, default_terms=default_terms)


class Terms(unittest.TestCase):
    def test_explicit(self):
        self.assertEqual(decide("Software Engineer Intern - Summer 2027").terms, ["Summer 2027"])
        self.assertTrue(decide("Summer/Fall 2027 Hardware Co-op").ok)
        self.assertEqual(decide("Intern, ASIC Design (Fall '27)").terms, ["Fall 2027"])
        self.assertEqual(decide("Développeur - Stagiaire - l'été 2027").terms, ["Summer 2027"])

    def test_month_ranges(self):
        self.assertEqual(decide("Hardware Intern (May - Dec 2027)").terms, ["Summer 2027"])
        self.assertFalse(decide("Controls Intern - January - August 2027").ok)
        self.assertEqual(decide("September 2027 - April 2028 Co-op").terms, ["Fall 2027"])
        self.assertEqual(C.parse_terms("Hardware Intern (May - Dec 2027)").months, {8})

    def test_title_beats_list_tag(self):
        self.assertFalse(decide("Co-op - Embedded Test: January - June 2027", list_terms=["Summer 2027"]).ok)
        self.assertTrue(decide("Embedded Intern", list_terms=["Summer 2027"]).ok)

    def test_inferred_and_years(self):
        d = decide("Software Engineer - Intern - Summer or Winter", posted="2026-09-10")
        self.assertEqual((d.ok, d.terms, d.quality), (True, ["Summer 2027"], "inferred"))
        self.assertFalse(decide("Robotics Fall Intern/Co-op - 2026").ok)
        self.assertEqual(decide("Intern 2027").quality, "year")
        self.assertEqual(decide("FPGA Engineer Intern", posted="2026-09-01").quality, "unknown")
        self.assertFalse(decide("FPGA Engineer Intern", posted="2026-05-01").ok)
        self.assertEqual(decide("Quant Researcher Intern", default_terms=["Summer 2027"]).quality, "list")

    def test_spring_boot_is_not_a_season(self):
        self.assertEqual(C.parse_terms("Java Spring Boot Intern").seasons, set())

    def test_jd_ignores_graduation_years(self):
        jd = "This is a 12-week internship starting in May 2027.\nMust be graduating between December 2027 and June 2028."
        info = C.parse_terms(jd, "jd")
        self.assertEqual(info.terms, {"Summer 2027"})
        self.assertEqual(info.months, {3})


class Locations(unittest.TestCase):
    CASES = {
        "Toronto, ON, Canada": {"CA"}, "San Jose, CA": {"US"}, "US, CA, Santa Clara": {"US"}, "Toronto, CA": {"CA"},
        "London, ON, Canada": {"CA"}, "London, UK": {"OTHER"}, "Bangalore, IN": {"OTHER"}, "Indianapolis, IN": {"US"},
        "Paris, TX": {"US"}, "Paris, France": {"OTHER"}, "Remote in USA": {"US"}, "Remote": set(), "NYC": {"US"},
        "CAN, ON, Toronto": {"CA"}, "Mexico City, Mexico": {"OTHER"}, "New Mexico": {"US"}, "Aveiro, pt": {"OTHER"},
        "Latin America": {"OTHER"}, "Kanata, Ottawa, ON, Canada": {"CA"},
    }

    def test_cases(self):
        for text, want in self.CASES.items():
            with self.subTest(text=text):
                self.assertEqual(C.countries_of(text), want)

    def test_lists_and_codes(self):
        cc, remote = C.classify_locations(["Seattle, WA; Toronto, ON"], {})
        self.assertEqual(cc, {"US", "CA"})
        cc, remote = C.classify_locations(["Remote"], {"country_codes": ["CAN"]})
        self.assertEqual((cc, remote), ({"CA"}, True))


class Roles(unittest.TestCase):
    def test_internship_and_category(self):
        self.assertTrue(C.is_internship("Silicon Validation Intern", {}))
        self.assertTrue(C.is_internship("Stage - Développeur", {}))
        self.assertFalse(C.is_internship("Multi-stage Pipeline Engineer", {}))
        self.assertTrue(C.is_internship("Software Engineer", {"commitment": "Intern"}))
        for title, cat in [("Embedded Firmware Intern", "hardware"), ("Quantitative Researcher Intern", "quant"),
                           ("Machine Learning Intern", "ai_data"), ("Mechanical Engineering Intern", "other"),
                           ("Technology Summer Analyst", "software"), ("Investment Banking Summer Analyst", "finance")]:
            self.assertEqual(C.classify_category(title), cat, title)

    def test_priority(self):
        s = {"priority": {"companies": ["Intel", "AMD"], "keywords": ["fpga"]}}
        self.assertTrue(C.is_priority("Intel", "Intern", s))
        self.assertFalse(C.is_priority("Intelcom | Dragonfly", "Intern", s))
        self.assertTrue(C.is_priority("Acme", "FPGA Intern", s))


class Resolver(unittest.TestCase):
    def test_keys_match_across_sources(self):
        self.assertEqual(canonical_key("https://job-boards.greenhouse.io/doordashusa/jobs/8171041"), "gh:8171041")
        self.assertEqual(canonical_key("https://stripe.com/jobs/listing/x/123?gh_jid=123"), "gh:123")
        a = "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/ASIC-Intern_JR2001234"
        self.assertEqual(canonical_key(a), workday_key("nvidia", "/job/US-CA-Santa-Clara/ASIC-Intern_JR2001234"))
        self.assertEqual(canonical_key("https://wd5.myworkdaysite.com/recruiting/microchiphr/External/job/x_R123"), "wd:microchiphr:r123")
        self.assertEqual(canonical_key("https://qualcomm.eightfold.ai/careers/job/446721143440"), "ef:qualcomm.eightfold.ai:446721143440")
        self.assertEqual(canonical_key("https://apply.careers.microsoft.com/careers/job/1970393556982258"),
                         "ef:apply.careers.microsoft.com:1970393556982258")
        self.assertEqual(canonical_key("https://amazon.jobs/en/jobs/10553947/sde-intern"), "az:10553947")
        self.assertEqual(canonical_key("https://careers.amd.com/jobs/90301?icims=1"), "jb:careers.amd.com:90301")

    def test_boards(self):
        self.assertEqual(detect_kind("https://jobs.ashbyhq.com/Etched"), "ashby")
        self.assertEqual(board_from_job_url("https://jobs.lever.co/zoox/1234abcd-0000-0000-0000-000000000000"),
                         ("lever", "https://jobs.lever.co/zoox"))
        self.assertEqual(board_ident("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"),
                         board_ident("https://nvidia.wd5.myworkdayjobs.com/en-US/nvidiaexternalcareersite/job/x_JR1"))


class Lists(unittest.TestCase):
    def setUp(self):
        self.lst = lists.GitHubList({"repo": "someone/Summer2027-Internships"})

    def test_pipe_table_with_continuation_rows(self):
        md = """
| Company | Role | Location | Apply | Date Posted |
| --- | --- | --- | --- | --- |
| **[Qualcomm](https://q.com)** | Low Power AI Intern | Markham, ON | [![Apply](https://img.shields.io/x.svg)](https://qualcomm.eightfold.ai/careers/job/446721143440) | Sep 18, 2026 |
| ↳ | RF Systems Intern | Toronto, ON | [Apply](https://qualcomm.eightfold.ai/careers/job/446721143441) | Sep 18, 2026 |
| Closed Co | Old Intern | Austin, TX | 🔒 | Sep 1, 2026 |
"""
        jobs = self.lst.from_markdown(md)
        self.assertEqual([j.company for j in jobs], ["Qualcomm", "Qualcomm"])
        self.assertEqual(jobs[1].key, "ef:qualcomm.eightfold.ai:446721143441")
        self.assertEqual(jobs[0].posted, "2026-09-18")
        self.assertEqual(jobs[0].extra["default_terms"], ["Summer 2027"])

    def test_quant_style_sections(self):
        md = """
## Jane Street
**Locations**: New York, Toronto
|Role|Links|
|---|---|
|QR|[✅ Apply](https://job-boards.greenhouse.io/janestreet/jobs/111) [🔒](https://job-boards.greenhouse.io/janestreet/jobs/112)|
"""
        jobs = self.lst.from_markdown(md)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "Quantitative Researcher Intern (Apply)")
        self.assertEqual(jobs[0].locations, ["New York", "Toronto"])

    def test_listings_json(self):
        data = [{"company_name": "AMD", "title": "DV Intern", "url": "https://careers.amd.com/jobs/1?icims=1&utm_source=Simplify",
                 "locations": ["Markham, ON, Canada"], "terms": ["Summer 2027"], "active": True, "is_visible": True,
                 "date_posted": 1758240000, "degrees": ["Bachelor's"]},
                {"company_name": "Old", "title": "Intern", "url": "https://x.test/1", "active": False}]
        jobs = self.lst.from_listings(data)
        self.assertEqual(len(jobs), 1)
        self.assertEqual((jobs[0].key, jobs[0].url), ("jb:careers.amd.com:1", "https://careers.amd.com/jobs/1?icims=1"))


class Fit(unittest.TestCase):
    def test_scan_and_flags(self):
        jd = (FIX / "jd_dv.txt").read_text()
        resume = F.tex_to_text((FIX / "resume_test.tex").read_text())
        s = F.scan(jd, resume)
        self.assertEqual(s["score"], 53)
        self.assertIn("UVM", [x[0] for x in s["hit"]])
        self.assertIn("PCIe", [x[0] for x in s["miss"]])
        self.assertNotIn("Python", [x[0] for x in s["miss"]])      # benefits text is ignored
        self.assertEqual(F.red_flags(jd, ("US",), None, "2029-04"), ["grad", "us_citizen"])
        self.assertEqual(F.red_flags(jd, ("CA",), None, "2028-04"), [])   # US-only rules skip Canadian roles
        self.assertEqual(F.grad_window(jd), ((2027, 12), (2028, 6)))

    def test_sponsorship_field(self):
        self.assertEqual(F.red_flags("", ("US",), "U.S. Citizenship is Required"), ["us_citizen"])


class Followups(unittest.TestCase):
    CFG = {"applied_days": 14, "second_days": 14, "ghost_days": 45, "saved_days": 5, "oa_days": 5, "interview_days": 7}

    def rules(self, app, today=TODAY):
        return {f["rule"]: f["due"].isoformat() for f in followups.compute(app, today, self.CFG)}

    def test_applied_then_followed_up(self):
        app = {"id": "a", "status": "applied", "events": [{"d": "2026-09-01", "s": "applied"}]}
        self.assertEqual(self.rules(app), {"followup1": "2026-09-15", "ghost": "2026-10-16"})
        app["events"].append({"d": "2026-09-16", "s": "followup"})
        self.assertEqual(self.rules(app)["followup2"], "2026-09-30")

    def test_interview_and_dismiss(self):
        app = {"id": "b", "status": "interview", "events": [{"d": "2026-09-18", "s": "interview"}]}
        self.assertEqual(self.rules(app), {"thanks": "2026-09-18", "interview_check": "2026-09-25"})
        app["dismissed"] = ["thanks:2026-09-18"]
        self.assertNotIn("thanks", self.rules(app))

    def test_quiet_hours(self):
        self.assertTrue(followups.in_quiet_hours(23, [22, 8]))
        self.assertFalse(followups.in_quiet_hours(12, [22, 8]))


def fake_http(routes):
    def get(url, params=None, headers=None):
        for key, payload in routes.items():
            if key in url:
                return payload
        raise AssertionError(f"unexpected request {url}")
    return get


class Adapters(unittest.TestCase):
    def test_greenhouse(self):
        routes = {"/jobs/77": {"content": "&lt;p&gt;Write &lt;b&gt;Verilog&lt;/b&gt;&lt;/p&gt;", "offices": [{"name": "Toronto"}]},
                  "/boards/tenstorrent/jobs": {"jobs": [{"id": 77, "title": "RTL Intern", "absolute_url": "https://x/77",
                                                          "location": {"name": "Toronto, ON; Austin, TX"},
                                                          "first_published": "2026-09-19T10:00:00Z"}]}}
        with mock.patch.object(ats, "get_json", fake_http(routes)):
            a = ats.Greenhouse(Board("Tenstorrent", "greenhouse", "https://job-boards.greenhouse.io/tenstorrent"))
            jobs = a.list_jobs()
            self.assertEqual((jobs[0].key, jobs[0].locations, jobs[0].posted), ("gh:77", ["Toronto, ON", "Austin, TX"], "2026-09-19"))
            self.assertEqual(a.detail(jobs[0]), ("Write Verilog", ["Toronto"]))

    def test_workday(self):
        page = {"total": 1, "jobPostings": [{"title": "ASIC Intern", "externalPath": "/job/US-CA/ASIC-Intern_JR1",
                                              "locationsText": "2 Locations", "postedOn": "Posted Today"}]}
        detail = {"jobPostingInfo": {"jobDescription": "<p>UVM</p>", "location": "US, CA, Santa Clara",
                                     "additionalLocations": ["Canada, Toronto"], "country": {"descriptor": "United States of America"}}}
        with mock.patch.object(ats, "post_json", lambda url, body, headers=None: page), \
             mock.patch.object(ats, "get_json", fake_http({"/job/US-CA/ASIC-Intern_JR1": detail})):
            a = ats.Workday(Board("NVIDIA", "workday", "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"))
            jobs = a.list_jobs()
            self.assertEqual(jobs[0].key, "wd:nvidia:jr1")
            self.assertEqual(jobs[0].url, "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-CA/ASIC-Intern_JR1")
            text, locs = a.detail(jobs[0])
            self.assertEqual((text, locs), ("UVM", ["US, CA, Santa Clara", "Canada, Toronto"]))

    def test_lever_and_ashby(self):
        lever = [{"id": "11111111-2222-3333-4444-555555555555", "text": "Embedded Intern", "hostedUrl": "https://jobs.lever.co/zoox/1",
                  "categories": {"allLocations": ["Foster City, CA"], "commitment": "Intern"}, "createdAt": 1758240000000,
                  "descriptionPlain": "C++", "lists": [{"text": "Requirements", "content": "<li>RTOS</li>"}], "country": "US"}]
        ashby = {"jobs": [{"id": "abc", "title": "Hardware Intern", "jobUrl": "https://jobs.ashbyhq.com/Etched/abc", "location": "Cupertino",
                           "isListed": True, "employmentType": "Intern", "descriptionPlain": "FPGA",
                           "address": {"postalAddress": {"addressCountry": "United States"}}, "publishedAt": "2026-09-19T00:00:00Z"},
                          {"id": "hidden", "title": "x", "isListed": False}]}
        with mock.patch.object(ats, "get_json", fake_http({"api.lever.co": lever, "posting-api/job-board/Etched": ashby})):
            lv = ats.Lever(Board("Zoox", "lever", "https://jobs.lever.co/zoox")).list_jobs()
            ab = ats.Ashby(Board("Etched", "ashby", "https://jobs.ashbyhq.com/Etched")).list_jobs()
        self.assertIn("RTOS", lv[0].description)
        self.assertEqual(lv[0].extra["commitment"], "Intern")
        self.assertEqual([j.key for j in ab], ["ab:abc"])

    def test_amazon(self):
        data = {"hits": 1, "jobs": [{"id_icims": "10553947", "title": "SDE Intern", "job_path": "/en/jobs/10553947/sde-intern",
                                      "normalized_location": "Vancouver, BC, CAN", "posted_date": "September 18, 2026",
                                      "description": "Build things", "basic_qualifications": "Java or C++", "country_code": "CAN"}]}
        with mock.patch.object(ats, "get_json", lambda url, params=None, headers=None: data):
            jobs = ats.Amazon(Board("Amazon", "amazon", "https://www.amazon.jobs", {"countries": ["CAN"]})).list_jobs()
        self.assertEqual((jobs[0].key, jobs[0].posted), ("az:10553947", "2026-09-18"))
        self.assertIn("Basic qualifications", jobs[0].description)
        self.assertEqual(C.classify_locations(jobs[0].locations, jobs[0].extra)[0], {"CA"})


class HwSubtype(unittest.TestCase):
    def test_classify(self):
        cases = {
            "ASIC Design Verification Intern": "verification", "RTL Design Engineer Intern": "rtl_design",
            "Physical Design Intern": "physical_design", "Analog IC Design Intern": "analog",
            "FPGA Engineer Intern": "fpga", "RF Systems Intern": "rf", "Embedded Software Intern": "embedded",
            "Silicon Validation Intern": "test", "Hardware Design Engineer Intern": "pcb",
        }
        for title, want in cases.items():
            self.assertEqual(C.classify_hw_subtype(title), want, title)

    def test_no_match(self):
        self.assertIsNone(C.classify_hw_subtype("Software Engineer Intern"))


class Extract(unittest.TestCase):
    TODAY = date(2026, 9, 21)

    def test_deadline(self):
        self.assertEqual(C.extract_deadline("Apply by December 5 to be considered.", self.TODAY), "2026-12-05")
        self.assertEqual(C.extract_deadline("Application deadline: 11/20/2026.", self.TODAY), "2026-11-20")
        self.assertIsNone(C.extract_deadline("We offer great benefits and PTO.", self.TODAY))
        # no year given and the date has already passed this year -> rolls to next year
        self.assertEqual(C.extract_deadline("Apply by January 5 for this role.", self.TODAY), "2027-01-05")

    def test_pay(self):
        self.assertEqual(C.extract_pay("Pay: $28.50 - $35.00 per hour."), "$28.50 - $35.00 per hour")
        self.assertEqual(C.extract_pay("Compensation: $65,000 - $85,000 annually."), "$65,000 - $85,000 annually")
        self.assertIsNone(C.extract_pay("We raised $500,000 in funding this year."))
        self.assertIsNone(C.extract_pay("No pay mentioned here."))


class PriorityScan(unittest.TestCase):
    def test_build_adapters_priority_only(self):
        s = Settings.load()
        full, _ = pipeline.build_adapters(s, discovered={"boards": {}})
        pri, _ = pipeline.build_adapters(s, discovered={"boards": {}}, priority_only=True)
        self.assertLess(len(pri), len(full))
        self.assertTrue(pri)
        self.assertTrue(all(a.direct for a in pri))
        self.assertFalse(any(a.label.startswith("list:") for a in pri))
        self.assertTrue(all(C.is_priority(a.label.removeprefix("auto:"), "", s.raw) for a in pri))


class Digest(unittest.TestCase):
    def test_counts_this_week_only(self):
        s = Settings.load()
        monday = TODAY - timedelta(days=TODAY.weekday())
        tmp = Path("/tmp/scope-digest-test")
        tmp.mkdir(exist_ok=True)
        apps = {"applications": [
            {"job_id": "a", "events": [{"d": monday.isoformat(), "s": "applied"}], "status": "applied"},
            {"job_id": "b", "events": [{"d": (monday - timedelta(days=3)).isoformat(), "s": "applied"}], "status": "applied"},
        ], "hidden": []}
        jobs = {"jobs": [{"id": "c", "company": "X", "fit": 80, "tq": "explicit"},
                         {"id": "a", "company": "Y", "fit": 90, "tq": "explicit"}]}
        with mock.patch.object(followups, "APPS", tmp / "apps.json"), mock.patch.object(followups, "JOBS", tmp / "jobs.json"), \
             mock.patch.object(followups, "utcnow", lambda: datetime.combine(TODAY, datetime.min.time().replace(hour=12), tzinfo=timezone.utc)):
            from scope.util import write_json
            write_json(followups.APPS, apps)
            write_json(followups.JOBS, jobs)
            stats = followups.digest_stats(s)
        self.assertEqual(stats["applied"], 1)              # only the Monday-dated one counts
        self.assertEqual(stats["goal"], 5)
        self.assertEqual([j["id"] for j in stats["top"]], ["c"])   # "a" is already tracked, so excluded


if __name__ == "__main__":
    unittest.main()
