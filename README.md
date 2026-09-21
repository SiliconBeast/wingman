# Scope

Finds Summer and Fall 2027 tech internships and co-ops in the US and Canada, scores each one against your
resume, flags the ones that can rule you out, and tracks your applications. It runs free on GitHub Actions
every four hours and posts new roles to Discord.

- **Roles**: every open role in one table, with a fit score, missing keywords, and red flags like "US citizens
  only" or a graduation window you fall outside. Hardware roles are also tagged by sub-type (verification, RTL
  design, physical design, analog, FPGA, RF, embedded, PCB, test) as a filterable column. When a posting states a
  pay rate or an application deadline, both show inline, with a one-click "add to calendar" link, and you can
  sort or filter by deadline. "Ready to apply" hides everything you can't or already did apply to. Keyboard
  driven: `j`/`k` move, `c` ATS check, `o` open posting, `a` mark applied.
- **ATS check** on any role: keyword match weighted by what the posting requires, blockers in the posting, and a
  format check of your resume (parser-readable PDF text, headings, contact links, weak bullets). One more
  click gets an AI review from Claude: requirements met and missing, bullet rewrites, likely interview questions,
  and a tailored LaTeX resume that uses only facts you wrote.
- **Tracker**: a board from Saved to Offer, a timeline of every application, and follow-up reminders (in the
  dashboard and in Discord) 14 days after you apply, plus calendar links for every reminder and deadline. Once an
  application reaches Interview or Assessment, its drawer shows a **Prep** section: technical questions (from your
  saved AI review if you ran one, or a general bank for that kind of role otherwise) and your resume's bullets
  framed as stories to practice.
- **Weekly digest** on Discord every Monday: applications made this week against a goal you set, follow-ups due,
  and your best-fit roles you haven't applied to yet.

## Setup (about 15 minutes)

1. **Make a private GitHub repository** and push this folder to it. Private matters: your resume and application
   notes live here.
2. **Add your resume.** Copy the LaTeX you compile into `resume/resume.tex`. Optionally list other real
   experience in `resume/bullets.md`. Set `graduation: "YYYY-MM"` in `config/settings.yaml` so postings with
   graduation windows get checked.
3. **Discord alerts.** In your server: Server Settings, Integrations, Webhooks, New Webhook, copy the URL. In the
   repository: Settings, Secrets and variables, Actions, New repository secret named `DISCORD_WEBHOOK_URL`.
4. **First scan.** Actions tab, enable workflows, choose "scope", then "Run workflow". It takes a few minutes.
   The first run records everything without flooding Discord; after that you only hear about new roles.
5. **Dashboard.** Settings, Pages, "Deploy from a branch", branch `main`, folder `/docs`. Pages for a private
   repository needs GitHub Pro, which is free with the GitHub Student Developer Pack. The page itself holds no data;
   it reads your private repository with a token only you have.
   Then open the dashboard, click the gear, and enter your repository (`you/scope`) and a
   [fine-grained token](https://github.com/settings/personal-access-tokens/new) limited to this repository with
   "Contents: Read and write".
   Or skip Pages and run `python -m scope serve` to use the dashboard on your own computer without a token.
6. **Optional: one-click AI reviews.** Add an Anthropic API key in the dashboard's settings. Without one, the ATS
   check gives you a prompt to paste into a free Claude chat, and you paste the reply back to see the report.

## Applying fast

1. A Discord alert arrives: priority companies first, then roles without red flags, best fit first.
2. In the dashboard, turn on **Ready to apply** and sort by **Best fit**.
3. On a role, press `c`. Read the missing requirements and blockers. If it's worth it, run the AI review and copy
   the tailored LaTeX (every claim in it comes from your resume or bullet bank; check it anyway).
4. Press `o` to open the posting, apply, then press `a`. The role moves to your board and a follow-up reminder is
   scheduled.
5. When you hear back, open the application and click its new status. The timeline and reminders update.

## Where the roles come from

Cheapest first, so each scan is a few hundred requests instead of thousands:

1. **13 GitHub lists** (SimplifyJobs, speedyapply, vanshb03, negarprh's Canadian list, the NUFT quant list, and
   others). A handful of requests return thousands of roles, including companies with their own career sites
   (Google, Apple, Meta, Tesla).
2. **About 115 company boards** in `config/companies.yaml`, read straight from their applicant tracking systems:
   Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Oracle, Eightfold (including Microsoft), iCIMS, and
   Amazon's job search. Roles show up here hours or days before any list has them.
3. **Discovered boards.** Every board a list links to gets read in full, not just the roles the list picked:
   up to 300 boards, a third per scan, priority companies every scan.

Priority companies (`priority.companies` in `config/settings.yaml`) also get a **fast scan every 30 minutes**
(`python -m scope run --priority`), skipping the lists entirely so it finishes in seconds. That's the difference
between applying in the first hour a role is posted and applying on day three.

The same role from several sources is merged, and the company's own board wins. Descriptions are fetched for up to
250 new roles per scan (priority companies first) and cached, so fit scores fill in within a day.

Not scraped: LinkedIn, Indeed and Handshake. Their terms forbid it and they require logins, and nearly every
posting there links to one of the boards above anyway.

## Fit score, red flags, deadlines and pay

The fit score is the share of the posting's technical keywords your resume contains. Requirements count double,
"nice to have" counts once, and benefits and legal boilerplate count zero. The keyword list (about 250 terms,
from SystemVerilog and UVM to Kubernetes and stochastic calculus) is in `tools/build_keywords.py`; the scanner and
the dashboard share it through `docs/keywords.json`, so both give the same number.

A low score isn't a no. It tells you what to add if it's true. Red flags are what to check before spending time:
US citizenship or ITAR, security clearance, no visa sponsorship (only flagged on US roles), French required,
a minimum GPA, and a graduation window you fall outside.

Hardware roles also get a sub-type from the title alone (verification, RTL design, physical design, analog,
FPGA, RF, embedded, PCB/electrical, or validation/test), so you can filter "Verification" separately from
"Physical design" instead of one "Hardware" bucket.

When a posting states an application deadline ("Apply by...") or a pay rate, both are pulled out and shown on
the role, with a one-click link to add the deadline to Google Calendar. Neither is guaranteed to be found; most
postings don't state either.

## Interview prep and the weekly digest

Once you move a tracked application to **Assessment** or **Interview**, its drawer shows a Prep section:
technical questions (pulled from your saved AI review if you ran an ATS check with one, otherwise a general
bank for that role's category or hardware sub-type) and your resume's bullets listed as stories worth practicing
in the STAR format. For Assessment-stage applications, set the **Deadline** field to get a reminder the day before
it's due.

Every Monday, `python -m scope digest` posts a Discord message: how many applications you've made this week
against the goal in `config/settings.yaml` (`goals.weekly_applications`, default 5), how many follow-ups are due,
and your five best-fit roles you haven't applied to yet. The scheduled workflow runs this automatically.

## Commands

| Command | What it does |
| --- | --- |
| `python -m scope run` | Scan everything, update `data/`, post new roles to Discord (`--no-notify`, `--dry-run`) |
| `python -m scope run --priority` | Fast scan of priority companies only, skips the lists |
| `python -m scope run --explain "NVIDIA"` | Show why each NVIDIA role was kept or dropped |
| `python -m scope check` | Test every company board and report the broken ones (`--lists` for lists too) |
| `python -m scope add "Company" URL` | Watch a company by pasting its careers page URL |
| `python -m scope suggest --canada` | Boards the lists mention that you don't watch yet |
| `python -m scope nudge` | Post due follow-up reminders |
| `python -m scope digest` | Post this week's application count against your goal, and top unapplied matches |
| `python -m scope serve` | Open the dashboard locally |
| `python -m unittest discover -s tests` | Run the tests |

Setup for local use: `pip install -r requirements.txt` (Python 3.10 or newer).

## Tuning

`config/settings.yaml` holds the terms you want, countries, role categories, title words to exclude, priority
companies and keywords (these jump the queue, get scanned every 30 minutes, and get highlighted), the GitHub
lists, discovery limits, your graduation date, follow-up timing, your weekly application goal, and Discord quiet
hours. After editing keywords in `tools/build_keywords.py`, run `python tools/build_keywords.py`.

## Costs

GitHub Actions is free for public repositories. Private repositories get 2,000 minutes a month free, or more
with the GitHub Student Developer Pack. The full scan (every 4 hours) takes roughly 5 minutes; the priority scan
(every 30 minutes) takes under a minute since it skips the lists. Together that's roughly 1,300 minutes a month.
If you're tight on minutes, widen the priority cron in `.github/workflows/scope.yml` (e.g. every hour instead of
every 30 minutes) or drop it and rely on the 4-hour scan alone.

AI reviews through a Claude chat are free. With an API key, a review costs roughly 10 to 25 cents on Claude
Sonnet 5 and a few cents on Claude Haiku 4.5.

## Privacy

- Keep the repository private. The Pages dashboard is only an empty shell until you give it your token.
- The GitHub token and API key are stored only in your browser. The API key is sent only to api.anthropic.com.
- Nothing is ever submitted to an employer for you. Scope finds, scores and reminds; you apply.

## Files

```
config/settings.yaml      what to look for, lists, discovery, follow-ups
config/companies.yaml     company boards read directly
resume/                   resume.tex and bullets.md (yours)
scope/                    the scanner (python -m scope)
docs/index.html           the dashboard (GitHub Pages serves docs/)
docs/keywords.json        keyword list shared by the scanner and dashboard
data/                     written by each scan: jobs, descriptions, source health; applications.json is your tracker
tests/                    unit tests
```

## When something breaks

- **A source shows as failing** in the Sources tab: run `python -m scope check --only "Company"`. Career sites
  change platforms; `python -m scope add "Company" NEW_URL --force` fixes most of them.
- **A role you expected is missing**: `python -m scope run --explain "Company" --dry-run` prints the reason for
  every one of that company's postings.
- **The first scan found 0 roles from a list**: its format changed. The list is skipped and flagged; everything
  else keeps working.
