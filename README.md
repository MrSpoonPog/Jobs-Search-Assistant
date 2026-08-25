# internbot

Finds **London** CS internships and placements, scores each one by how likely
**you** are to actually land it, and emails you a ranked digest when something
new appears.

No scraping. Every source is an official JSON API or a documented applicant
tracking system feed, so nothing breaks when a careers page gets redesigned.

**New here? Read [USER_GUIDE.md](USER_GUIDE.md).** It covers setup, daily use,
tuning and troubleshooting in full. This README is the short version.

---

## Setup, about 20 minutes

### 1. Get the free API keys

| Service | Where | Cost |
|---|---|---|
| Adzuna | developer.adzuna.com | Free, instant |
| Reed | reed.co.uk/developers | Free, instant |

Both cover most of the London market in one call each. This is where the bulk
of your coverage comes from — without them a run finds single-digit results.

### 2. Gmail app password

Two-factor must be on. Google Account, Security, App passwords, generate one for
"Mail". You get a 16-character string. That is your `SMTP_PASS`, not your normal
password.

### 3. Discover the employer feeds

```bash
pip install -r requirements.txt
python fetch.py --discover
```

This probes all 75 employers across Greenhouse, Lever, Ashby, SmartRecruiters and
Workable, verifies which one each actually uses, and writes the confirmed slugs
straight back into `config.json`. Takes a few minutes.

Anything reported `no public feed` has no machine-readable board. Do not fight
it. Set up their own native email alert once and forget about it. That applies
to Civil Service Jobs and most Workday employers.

### 4. Test before going live

```bash
export ADZUNA_APP_ID=...  ADZUNA_APP_KEY=...  REED_API_KEY=...
python fetch.py --dry-run --explain
```

Prints the ranked list to your terminal and sends nothing. `--explain` also
shows why everything else was rejected, which is the fastest way to confirm the
filters are behaving. Check the ordering looks sane, then drop `--dry-run`.

### 5. Put it on GitHub Actions so it runs itself

Push to a **private** repo. Then Settings, Secrets and variables, Actions, and
add: `SMTP_USER`, `SMTP_PASS`, `EMAIL_TO`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`,
`REED_API_KEY`.

It then runs every 4 hours for new roles, plus an 8am weekday digest. Free, no
server, nothing to leave switched on.

---

## How the scoring works

Every role is filtered, then scored out of 100. Around 3,000 postings come in
per run and single digits survive the filters.

**Dropped outright:** senior/lead/manager titles, non-technical disciplines
(marketing, sales, HR, finance), anything not based in London, roles with no
early-careers word in the *title*, and anything with no technical role match.
Those last two are why an ASOS marketing placement cannot outrank a Sky software
placement.

**Scored on:** employer tier (32/20/8), role fit against your cloud and DevOps
angle (24 for cloud/DevOps, 15 for general software, 6 adjacent), how strong the
level match is, how central the London location is, and freshness. Then boosts
and penalties from the description, clamped so three buzzwords cannot outweigh
employer tier.

| Band | Meaning |
|---|---|
| **APPLY NOW** (70+) | Drop what you are doing. Apply this week. |
| **STRONG** (50-69) | Worth a proper tailored application. |
| **WORTH A LOOK** (34-49) | Apply if you have spare capacity. |

Tune anything in `config.json`. If cloud roles stop mattering to you, change the
weights there rather than the code. See
[USER_GUIDE.md](USER_GUIDE.md#tuning-the-scores) for what each list does.

---

## London only

The `location` block in `config.json` controls this:

```json
"location": {
  "mode": "london",              // "uk" widens it to the whole country
  "radius_miles": 20,            // passed to the Adzuna and Reed searches
  "include_commuter_belt": true, // Watford, St Albans, Reading, Guildford...
  "allow_remote_uk": false       // accept UK-wide remote roles
}
```

A foreign city in the location field is final and no description can override
it. A vague location like `Remote` or `Hybrid` proves nothing, so the
description has to carry the evidence. Foreign evidence is always checked
before UK evidence, because a global posting will happily list "London, New
York, Singapore" in its boilerplate. Accents are folded before matching, so
`Kraków` and `Zürich` are caught too.

---

## Tiers, and why they are ordered this way

**Tier A. Realistically gettable.** Public sector digital agencies (Made Tech,
dxw, Scott Logic, BJSS, Kainos), cloud MSPs and AWS partners (Softcat,
Computacenter, Node4, Rackspace), transport and infrastructure (TfL, Network
Rail, National Grid), and mid-size London tech. These have far lower applicant
volume per seat than big tech, and the cloud consultancies map directly onto your
CLF-C02. This is where your effort should go.

**Tier B. Large structured intakes.** Bloomberg, Ocado, Sky, BT, the Big Four,
Accenture, IBM, Amazon. Big cohorts and an online-assessment-first process, which
means preparation converts into offers. Less prestige competition than FAANG.

**Tier C. Long shots.** Google, Meta, Palantir, Revolut, BlackRock, Goldman.
Worth applying since the marginal cost is one form, but do not build your plan
around them.

Quant firms are almost entirely absent on purpose. For a second-year with no
prior internship and no competitive programming record, the prep time has close
to zero expected return. Two are left in so you can see them. That time is better
spent on Tier A.

---

## What the bot cannot do, and what to do instead

**Cold emails.** The single highest-return channel available to you and it is
invisible to any aggregator. Smaller firms rarely post interns at all, but will
take one if a decent student asks. Ten well-researched emails beats a hundred
portal applications.

**Your own university.** City St George's runs its own internship scheme, and
lecturers hire summer research assistants directly. Competition is a handful of
people rather than thousands. Ask your Algorithms lecturer. This is the most
underrated route on the entire list.

**Workday employers and Civil Service Jobs.** Set up their native alerts once.
For a Workday employer, open their job search, press F12, Network tab, filter for
`jobs`, and copy the `/wday/cxs/` request URL into `config.json` as
`{"name": "...", "tier": "B", "ats": "workday", "url": "<paste>"}`.

---

## Timing

Tech internships for summer 2027 open July to October 2026, so you are building
this at exactly the right moment. Nearly everything is rolling, which means
places fill long before the posted deadline and applying in week one genuinely
beats a polished application in week four.

Which makes the real bottleneck your CV, not detection. Have a base CV and cover
letter ready **now** so that when an alert lands you can apply the same evening.
The bot only buys you the head start. You still have to use it.

---

## Files

```
fetch.py            everything: adapters, filtering, scoring, email
config.json         employers, location rules, keywords, weights - edit this
requirements.txt    dependencies
USER_GUIDE.md       full guide: setup, tuning, troubleshooting
seen.json           auto-created state, stops repeat alerts
job_log.csv         running log of every match, doubles as your tracker
.github/workflows/  the scheduled GitHub Actions run
```

## Ground rules

Official APIs only, polite rate limiting, retry with backoff, and no
auto-applying. A human reads every posting before anything gets submitted.
