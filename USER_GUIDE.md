# internbot user guide

Everything you need to run, tune and trust the bot. If you only read one
section, read [Daily use](#daily-use) and [When it sends you rubbish](#when-it-sends-you-rubbish).

**Contents**

1. [What it actually does](#what-it-actually-does)
2. [First-time setup](#first-time-setup)
3. [Daily use](#daily-use)
4. [Reading the email](#reading-the-email)
5. [Filtering: London only](#filtering-london-only)
6. [Tuning the scores](#tuning-the-scores)
7. [Adding and removing employers](#adding-and-removing-employers)
8. [When it sends you rubbish](#when-it-sends-you-rubbish)
9. [When it sends you nothing](#when-it-sends-you-nothing)
10. [Troubleshooting](#troubleshooting)
11. [Command reference](#command-reference)

---

## What it actually does

Four steps, every run:

1. **Collect.** Calls each employer's applicant tracking system (Greenhouse,
   Lever, Ashby, SmartRecruiters, Workable) plus the Adzuna and Reed job board
   APIs. All official JSON endpoints, no scraping, so nothing breaks when a
   careers page gets redesigned.
2. **Filter.** Throws out anything that is not a London-based, early-careers,
   technical role. Roughly 3,000 postings come in, single digits survive.
3. **Score.** Ranks what is left out of 100 by how likely *you* are to land it.
4. **Send.** Emails you the ranked list, and remembers what it has already
   shown you in `seen.json` so you are never alerted twice for the same job.

It never applies to anything on your behalf. You read every posting yourself.

---

## First-time setup

### Step 1 — install

```bash
pip install -r requirements.txt
```

### Step 2 — get two free API keys

| Service | Sign up at | Cost |
|---|---|---|
| Adzuna | developer.adzuna.com | Free, instant |
| Reed | reed.co.uk/developers | Free, instant |

**Do not skip this.** The employer feeds alone only cover the ~30 companies
with a public ATS. Adzuna and Reed are where most of your London coverage comes
from. Without them a run typically finds 4 or 5 roles instead of 40.

### Step 3 — a Gmail app password

Two-factor must be switched on first. Then Google Account → Security → App
passwords → generate one for "Mail". You get a 16-character string. That is
your `SMTP_PASS`. Your normal Gmail password will not work.

### Step 4 — set the environment variables

```bash
export ADZUNA_APP_ID=...
export ADZUNA_APP_KEY=...
export REED_API_KEY=...
export SMTP_USER=you@gmail.com
export SMTP_PASS=abcd efgh ijkl mnop     # the 16-char app password
export EMAIL_TO=you@gmail.com            # optional, defaults to SMTP_USER
```

On Windows PowerShell use `$env:ADZUNA_APP_ID="..."` instead.

To avoid retyping these, put them in a `.env` file and source it. `.env` is
already gitignored.

### Step 5 — find each employer's job feed

```bash
python fetch.py --discover
```

This probes every company in `config.json` across all five ATS platforms,
works out which one each actually uses, and writes the confirmed slugs back
into the file. Takes a few minutes. You only ever need to run it once, or
again after you add new companies.

Anything reported `no public feed` has no machine-readable board. Do not fight
it — set up that employer's own email alert once and move on. This applies to
Civil Service Jobs and most Workday employers.

### Step 6 — test before going live

```bash
python fetch.py --dry-run --explain
```

Prints the ranked list to your terminal and sends nothing. `--explain` also
shows why everything else was rejected, which is the fastest way to confirm the
filters are behaving. Check the ordering looks sane, then drop `--dry-run`.

### Step 7 — put it on GitHub Actions

Push to a **private** repo. Then Settings → Secrets and variables → Actions,
and add: `SMTP_USER`, `SMTP_PASS`, `EMAIL_TO`, `ADZUNA_APP_ID`,
`ADZUNA_APP_KEY`, `REED_API_KEY`.

The workflow at `.github/workflows/internbot.yml` then runs every 4 hours for
new roles plus an 08:00 weekday digest. Free, no server, nothing to leave
switched on.

---

## Daily use

You do not run anything day to day — GitHub Actions does it. But locally:

```bash
python fetch.py                 # alert on NEW roles only (the default)
python fetch.py --mode digest   # everything still open, not just new
python fetch.py --dry-run       # print to terminal, send no email
```

`new` mode is what you want almost always. It only shows roles the bot has
never shown you before, so an empty result means nothing has changed, not that
something is broken.

`digest` mode re-lists everything found in the last 14 days. Useful on a Sunday
when you want to work through a backlog.

---

## Reading the email

Each role shows a band, a score, and the reasons it scored that way.

| Band | Score | What to do |
|---|---|---|
| **APPLY NOW** | 70+ | Drop what you are doing. Apply this week. |
| **STRONG** | 50–69 | Worth a proper tailored application. |
| **WORTH A LOOK** | 34–49 | Apply if you have spare capacity. |

The reason line (`cloud/DevOps fit, Tier A, placement, London, posted this
week`) tells you exactly which rules fired. If a reason looks wrong, that is
your signal to tune `config.json` — see below.

Everything is also appended to `job_log.csv`, which doubles as an application
tracker. Add your own column for "applied / rejected / interview".

---

## Filtering: London only

The bot only sends London roles. This is enforced by the `location` block in
`config.json`:

```json
"location": {
  "mode": "london",
  "radius_miles": 20,
  "include_commuter_belt": true,
  "allow_remote_uk": false
}
```

| Setting | Effect |
|---|---|
| `mode` | `"london"` drops anything outside London. Set to `"uk"` to widen to the whole country. |
| `radius_miles` | Search radius passed to Adzuna and Reed. 20 covers Greater London. Raise to 30 for a wider net. |
| `include_commuter_belt` | Also accept Watford, St Albans, Reading, Guildford and similar. Set `false` for strictly inside London. |
| `allow_remote_uk` | Accept UK-wide remote roles that never name a city. Off by default because "Remote" usually means remote-from-anywhere, including abroad. |

### How a posting is judged to be in London

Order matters, and it is deliberate:

1. **A foreign city in the location field is final.** `Bengaluru, India` is
   rejected immediately and no description can override it.
2. **A vague location** (`Remote`, `Hybrid`, `In-Office`) proves nothing, so
   the description has to carry the evidence — and has to survive the foreign
   check too.
3. **A specific location** is matched against the London list (all 32 boroughs
   plus districts like Shoreditch and Canary Wharf) and London postcode
   districts (`EC2A`, `SE1`, `N1`).
4. **A UK city that is not London** — Manchester, Belfast — is rejected while
   `mode` is `"london"`.

Foreign evidence is always checked *before* UK evidence. A global posting will
happily list "London, New York, Singapore" in its boilerplate, and reading that
as proof of a London job is exactly how a Bengaluru role once reached the
inbox.

Accents are folded before matching, so `Kraków` and `Zürich` are caught by the
plain-ASCII entries `krakow` and `zurich` in the list.

---

## Tuning the scores

Everything lives in `config.json` under `scoring`. Change the config, not the
code.

**`exclude_title`** — seniority words. Any match in the *title* is an instant
reject: senior, lead, manager, principal, staff.

**`exclude_discipline`** — non-technical fields, also title-only: marketing,
sales, HR, finance, legal. This exists because a "Marketing Placement" whose
company blurb mentions AWS would otherwise score as a cloud role.

**`level_keywords`** — the early-careers words. At least one must appear in the
**title**, not the description. Job descriptions say "we welcome students" on
senior roles constantly; matching those would flood your inbox and you would
stop trusting the alerts within a week.

**`role_cloud` / `role_software` / `role_adjacent`** — what the role actually
is, worth 24 / 15 / 6 points. Judged on the title first. If the title is
generic ("Summer Placement Programme") it falls back to the description, but
only for a real engineering signal and only for 8 points, because that is
weaker evidence.

**`modifiers`** — `["phrase", points, "label"]`. Boosts and penalties from the
description. The total is clamped to between −40 and +20, so three buzzwords
can never outweigh employer tier and role fit.

Examples already in there:

```json
["penultimate",     10, "penultimate year - that is you"],
["aws",             10, "AWS - matches your cert"],
["final year only", -22, "final-year only"],
["unpaid",          -35, "unpaid"]
```

Add your own. If you get a Kubernetes certification, bump its weight. If you
decide you do not want data roles, drop `data` from `role_adjacent`.

All matching is whole-word, so `aws` will not fire on "laws" and `intern` will
not fire on "internal".

---

## Adding and removing employers

Add an entry to `companies` in `config.json`, then re-run `--discover`:

```json
{ "name": "Some Company", "tier": "A" }
```

Tiers, and why they are ordered this way:

- **Tier A (32 pts) — realistically gettable.** Public sector digital agencies,
  cloud MSPs, transport and infrastructure, mid-size London tech. Far lower
  applicant volume per seat than big tech. This is where your effort should go.
- **Tier B (20 pts) — large structured intakes.** Bloomberg, Ocado, Sky, BT,
  Accenture, IBM, Amazon. Big cohorts, online-assessment-first, so preparation
  converts into offers.
- **Tier C (8 pts) — long shots.** Google, Meta, Palantir, Goldman. Worth one
  form each, but do not build your plan around them.

An employer with no tier (anything found via Adzuna or Reed) scores 16.

### Workday employers

Workday cannot be auto-discovered. Open the employer's job search, press F12 →
Network tab, filter for `jobs`, copy the `/wday/cxs/` request URL, then add:

```json
{ "name": "...", "tier": "B", "ats": "workday", "url": "<paste the URL>" }
```

---

## When it sends you rubbish

Run with `--explain` to see the whole picture:

```bash
python fetch.py --dry-run --explain
```

Then, depending on what got through:

| Problem | Fix |
|---|---|
| A non-technical role got in | Add its discipline to `exclude_discipline` |
| A too-senior role got in | Add the word to `exclude_title` |
| A non-London role got in | Add the city to `location.exclude_locations` |
| Something scored too high | Lower the relevant `modifiers` weight |
| A whole employer is noise | Drop their tier to `"C"`, or delete them |

Changes take effect on the next run. Nothing needs rebuilding.

---

## When it sends you nothing

In order of likelihood:

1. **No Adzuna or Reed keys set.** Check the run output — if it says
   `adzuna: 0 postings` you are missing ~90% of your coverage.
2. **Nothing is new.** `new` mode only shows roles you have not been shown
   before. Run `--mode digest` to see everything still open.
3. **Wrong time of year.** Internships for the following summer open roughly
   July to October. Outside that window the market is genuinely quiet.
4. **Filters too tight.** Run `--explain`. If you see a lot of `UK but not
   London`, either accept that or set `location.mode` to `"uk"`. Try
   `--min-score 20` to see what is being scored but suppressed.

---

## Troubleshooting

**`config.json missing`** — you are running from the wrong directory. `cd` into
the repo first.

**`SMTP_USER / SMTP_PASS not set, skipping email`** — the environment variables
are not set in the shell you are running from. Note the bot deliberately does
*not* save state when the email fails, so nothing is lost; those roles will be
retried on the next run.

**SMTP authentication error** — you are using your Gmail password instead of a
16-character app password, or two-factor is not enabled on the account.

**A company shows `no public feed`** — it genuinely has no machine-readable
board. Set up their native email alert instead.

**Same job emailed twice** — the employer posted it under two URLs.
Deduplication is by URL, so this happens occasionally and cannot be fully
avoided.

**Want to reset the memory** — delete `seen.json`. The next run will treat
everything as new, which will produce one very large email.

---

## Command reference

```
python fetch.py                     alert on new roles, send email
python fetch.py --mode digest       everything open from the last 14 days
python fetch.py --dry-run           print to terminal, send nothing
python fetch.py --explain           show rejection reasons and counts
python fetch.py --min-score 20      lower the cutoff (default 34)
python fetch.py --discover          find and verify employer ATS feeds
```

Flags combine: `--dry-run --explain --min-score 20` is the standard tuning run.

### Files

```
fetch.py            adapters, filtering, scoring, email
config.json         employers, location rules, keywords, weights - edit this
requirements.txt    dependencies
seen.json           auto-created state, stops repeat alerts
job_log.csv         running log of every match, doubles as your tracker
.github/workflows/  the scheduled GitHub Actions run
```

---

## The part the bot cannot do

Worth saying plainly, because it matters more than anything above.

**Cold emails** are the highest-return channel available to you and are
invisible to any aggregator. Smaller firms rarely advertise internships but
will take one if a decent student asks. Ten well-researched emails beat a
hundred portal applications.

**Your own university.** City St George's runs its own internship scheme and
lecturers hire summer research assistants directly. You are competing with a
handful of people rather than thousands. This is the most underrated route on
the list.

**Have your CV ready now.** Nearly everything is rolling, so places fill long
before the posted deadline. Applying in week one genuinely beats a polished
application in week four. The bot only buys you the head start — you still have
to use it.
