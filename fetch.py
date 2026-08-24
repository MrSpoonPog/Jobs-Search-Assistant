#!/usr/bin/env python3
"""
internbot - UK CS internship / placement aggregator.

No scraping. Pulls only from official JSON APIs and applicant tracking systems,
scores every result by how likely you are to actually land it, and emails a
ranked digest when something new appears.

Usage:
    python fetch.py --discover      # one-off: find + verify each company's ATS
    python fetch.py                 # normal run: alert on NEW jobs only
    python fetch.py --mode digest   # daily 8am: everything still open
    python fetch.py --dry-run       # print to terminal, send no email
"""

import argparse
import csv
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
SEEN_PATH = ROOT / "seen.json"
LOG_PATH = ROOT / "job_log.csv"

UA = {"User-Agent": "internbot/1.0 (personal job alert; contact via GitHub)"}
TIMEOUT = 25
NOW = datetime.now(timezone.utc)


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def load_json(path, default):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get(url, **kw):
    """GET with retry and polite backoff."""
    for attempt in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            return r
        except requests.RequestException:
            if attempt == 2:
                return None
            time.sleep(2)
    return None


def parse_date(value):
    """Best-effort ISO date parse. Returns tz-aware datetime or None."""
    if not value:
        return None
    if isinstance(value, (int, float)):          # epoch millis (Lever)
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    text = str(value).replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.fromisoformat(text) if fmt is None \
                else datetime.strptime(text[:10], fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def clean(html):
    """Strip tags and entities so we can keyword-match on descriptions."""
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(html))
    text = (text.replace("&amp;", "&").replace("&nbsp;", " ")
                .replace("&lt;", "<").replace("&gt;", ">")
                .replace("&#39;", "'").replace("&quot;", '"'))
    return re.sub(r"\s+", " ", text).strip()


def job_key(job):
    return re.sub(r"[?#].*$", "", job["url"]).lower().strip("/")


def mk(title, company, url, location, posted, source, tier, desc=""):
    return {
        "title": clean(title), "company": company, "url": url,
        "location": clean(location) or "Unspecified",
        "posted": posted.isoformat() if posted else None,
        "source": source, "tier": tier, "desc": clean(desc)[:2500],
    }


# ----------------------------------------------------------------------------
# ATS adapters - all official, documented, machine-readable endpoints
# ----------------------------------------------------------------------------

def ats_greenhouse(slug, company, tier):
    r = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    if not r or r.status_code != 200:
        return []
    out = []
    for j in r.json().get("jobs", []):
        out.append(mk(j.get("title"), company, j.get("absolute_url", ""),
                      (j.get("location") or {}).get("name", ""),
                      parse_date(j.get("updated_at")), "greenhouse", tier,
                      j.get("content", "")))
    return out


def ats_lever(slug, company, tier):
    r = get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if not r or r.status_code != 200:
        return []
    out = []
    for j in r.json():
        cat = j.get("categories") or {}
        out.append(mk(j.get("text"), company, j.get("hostedUrl", ""),
                      cat.get("location", ""), parse_date(j.get("createdAt")),
                      "lever", tier, j.get("descriptionPlain", "")))
    return out


def ats_ashby(slug, company, tier):
    r = get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if not r or r.status_code != 200:
        return []
    out = []
    for j in r.json().get("jobs", []):
        out.append(mk(j.get("title"), company,
                      j.get("jobUrl") or j.get("applyUrl", ""),
                      j.get("location", ""), parse_date(j.get("publishedAt")),
                      "ashby", tier, j.get("descriptionPlain", "")))
    return out


def ats_smartrecruiters(slug, company, tier):
    r = get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
    if not r or r.status_code != 200:
        return []
    out = []
    for j in r.json().get("content", []):
        loc = j.get("location") or {}
        where = ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
        out.append(mk(j.get("name"), company,
                      f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                      where, parse_date(j.get("releasedDate")),
                      "smartrecruiters", tier))
    return out


def ats_workable(slug, company, tier):
    r = get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
    if not r or r.status_code != 200:
        return []
    try:
        data = r.json()
    except ValueError:
        return []
    out = []
    for j in data.get("jobs", []):
        loc = j.get("location") or {}
        where = ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
        out.append(mk(j.get("title"), company, j.get("url") or j.get("application_url", ""),
                      where, parse_date(j.get("created_at")), "workable", tier,
                      j.get("description", "")))
    return out


def ats_workday(url, company, tier):
    """Workday needs the full /wday/cxs/ endpoint pasted into config - it cannot
    be auto-discovered. See README for how to grab it in 20 seconds."""
    if not url:
        return []
    out = []
    try:
        r = requests.post(url, headers={**UA, "Content-Type": "application/json"},
                          json={"appliedFacets": {}, "limit": 20, "offset": 0,
                                "searchText": "intern"}, timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        base = url.split("/wday/")[0]
        for j in r.json().get("jobPostings", []):
            path = j.get("externalPath", "")
            out.append(mk(j.get("title"), company, base + path,
                          j.get("locationsText", ""),
                          parse_date(j.get("startDate")), "workday", tier))
    except (requests.RequestException, ValueError):
        pass
    return out


ATS = {
    "greenhouse": ats_greenhouse, "lever": ats_lever, "ashby": ats_ashby,
    "smartrecruiters": ats_smartrecruiters, "workable": ats_workable,
}


# ----------------------------------------------------------------------------
# job boards - free API keys, huge coverage, zero maintenance
# ----------------------------------------------------------------------------

def board_adzuna(cfg):
    app_id, key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not (app_id and key):
        return []
    out = []
    for q in cfg["board_queries"]:
        r = get("https://api.adzuna.com/v1/api/jobs/gb/search/1", params={
            "app_id": app_id, "app_key": key, "results_per_page": 50,
            "what": q, "where": "London", "distance": 40,
            "max_days_old": 21, "content-type": "application/json"})
        if not r or r.status_code != 200:
            continue
        for j in r.json().get("results", []):
            out.append(mk(j.get("title"), (j.get("company") or {}).get("display_name", "Unknown"),
                          j.get("redirect_url", ""),
                          (j.get("location") or {}).get("display_name", ""),
                          parse_date(j.get("created")), "adzuna", None,
                          j.get("description", "")))
        time.sleep(1)
    return out


def board_reed(cfg):
    key = os.getenv("REED_API_KEY")
    if not key:
        return []
    out = []
    for q in cfg["board_queries"]:
        try:
            r = requests.get("https://www.reed.co.uk/api/1.0/search",
                             auth=(key, ""), headers=UA, timeout=TIMEOUT,
                             params={"keywords": q, "locationName": "London",
                                     "distanceFromLocation": 30, "resultsToTake": 100})
        except requests.RequestException:
            continue
        if r.status_code != 200:
            continue
        for j in r.json().get("results", []):
            out.append(mk(j.get("jobTitle"), j.get("employerName", "Unknown"),
                          j.get("jobUrl", ""), j.get("locationName", ""),
                          parse_date(j.get("date")), "reed", None,
                          j.get("jobDescription", "")))
        time.sleep(1)
    return out


# ----------------------------------------------------------------------------
# scoring - the whole point. Ranks by realistic chance of landing it.
# ----------------------------------------------------------------------------

def score_job(job, cfg):
    sc = cfg["scoring"]
    title = job["title"].lower()
    blob = (title + " " + job["desc"] + " " + job["location"]).lower()
    points, why = 0, []

    # -- hard excludes -------------------------------------------------------
    for bad in sc["exclude_title"]:
        if re.search(rf"\b{re.escape(bad)}\b", title):
            return None, []

    # must be an early-careers role in the TITLE, not just mentioned in the
    # body text. Descriptions say "we welcome students" on senior roles all the
    # time - matching those would flood the inbox and kill trust in the alerts.
    level_hit = next((k for k in sc["level_keywords"] if k in title), None)
    if not level_hit:
        return None, []

    # UK only
    if any(re.search(rf"\b{re.escape(c)}\b", job["location"].lower())
           for c in sc["exclude_locations"]):
        return None, []

    # -- role fit: no technical match at all means it is not for you ---------
    # (an ASOS "Marketing Placement" must not outrank a Sky software placement)
    if any(k in blob for k in sc["role_cloud"]):
        points += 24
        why.append("cloud/DevOps fit")
    elif any(k in blob for k in sc["role_software"]):
        points += 15
        why.append("software role")
    elif any(k in blob for k in sc["role_adjacent"]):
        points += 6
        why.append("tech-adjacent")
    else:
        return None, []

    # -- employer tier: dominant factor -------------------------------------
    points += {"A": 32, "B": 20, "C": 8}.get(job.get("tier"), 16)
    if job.get("tier"):
        why.append(f"Tier {job['tier']}")

    # -- level quality -------------------------------------------------------
    strong = ("placement", "industrial", "year in industry", "summer analyst",
              "internship", "summer intern")
    points += 18 if any(k in title for k in strong) else 11
    why.append(level_hit)

    # -- location ------------------------------------------------------------
    loc = job["location"].lower()
    if "london" in loc:
        points += 12
        why.append("London")
    elif any(k in loc for k in ("remote", "hybrid", "flexible")):
        points += 7
        why.append("remote/hybrid")
    else:
        points += 2

    # -- freshness: rolling deadlines mean speed wins -------------------------
    posted = parse_date(job.get("posted"))
    if posted:
        age = (NOW - posted).days
        if age <= 3:
            points += 14
            why.append("posted this week")
        elif age <= 7:
            points += 8
        elif age > 45:
            points -= 12
            why.append("stale")

    # -- modifiers: boosts and penalties from the description -----------------
    # clamped, so three cloud buzzwords cannot outweigh employer tier and fit
    bonus = 0
    for phrase, delta, label in sc["modifiers"]:
        if phrase in blob:
            bonus += delta
            why.append(label)
    points += max(-40, min(bonus, 20))

    return max(0, min(points, 100)), why


def band(points):
    if points >= 70:
        return "APPLY NOW", "#0f7b3e"
    if points >= 50:
        return "STRONG", "#b06f00"
    if points >= 34:
        return "WORTH A LOOK", "#5a6270"
    return "LOW", "#8b909a"


# ----------------------------------------------------------------------------
# discovery - verifies which ATS each company uses, writes results into config
# ----------------------------------------------------------------------------

FILLER = {"group", "technology", "technologies", "partnership", "systems",
          "ltd", "limited", "plc", "uk", "the", "for", "and", "digital",
          "intelligence", "company", "inc", "corporation", "bank"}


def slugify(name):
    """Candidate ATS slugs, most specific first.

    Deliberately does NOT emit a bare first word for multi-word names:
    probing 'transport' for Transport for London would happily match some
    unrelated company's board and silently wire up the wrong feed.
    """
    words = re.sub(r"[^a-z0-9 ]+", " ", name.lower()).split()
    core = [w for w in words if w not in FILLER] or words

    variants = ["".join(words), "-".join(words), "".join(core), "-".join(core)]
    if len(words) > 1:
        variants.append("".join(words[:2]))
    if len(core) == 1:
        variants.append(core[0])

    seen, out = set(), []
    for v in variants:
        if len(v) >= 3 and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def discover(cfg):
    probes = {
        "greenhouse": lambda s: f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
        "lever": lambda s: f"https://api.lever.co/v0/postings/{s}?mode=json",
        "ashby": lambda s: f"https://api.ashbyhq.com/posting-api/job-board/{s}",
        "smartrecruiters": lambda s: f"https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=1",
        "workable": lambda s: f"https://apply.workable.com/api/v1/widget/accounts/{s}",
    }
    found = missing = 0
    for c in cfg["companies"]:
        if c.get("ats") or c.get("ats") == "skip":
            continue
        hit = None
        for slug in slugify(c["name"]):
            for platform, mkurl in probes.items():
                r = get(mkurl(slug))
                if r is not None and r.status_code == 200:
                    try:
                        data = r.json()
                    except ValueError:
                        continue
                    n = (len(data) if isinstance(data, list)
                         else len(data.get("jobs", data.get("content", []))))
                    if n:
                        hit = (platform, slug, n)
                        break
                time.sleep(0.15)
            if hit:
                break
        if hit:
            c["ats"], c["slug"] = hit[0], hit[1]
            found += 1
            print(f"  [ok]   {c['name']:<32} {hit[0]}/{hit[1]}  ({hit[2]} live roles)")
        else:
            c["ats"] = "manual"
            missing += 1
            print(f"  [--]   {c['name']:<32} no public feed - use their email alert")
    save_json(CONFIG_PATH, cfg)
    print(f"\nVerified {found} feeds. {missing} need manual alerts. Written to config.json.")


# ----------------------------------------------------------------------------
# collect
# ----------------------------------------------------------------------------

def collect(cfg):
    jobs, live = [], 0
    for c in cfg["companies"]:
        ats, slug = c.get("ats"), c.get("slug")
        if ats in ATS and slug:
            got = ATS[ats](slug, c["name"], c["tier"])
        elif ats == "workday" and c.get("url"):
            got = ats_workday(c["url"], c["name"], c["tier"])
        else:
            continue
        jobs.extend(got)
        live += 1
        time.sleep(0.2)
    print(f"  {live} employer feeds, {len(jobs)} raw postings")

    for fn, name in ((board_adzuna, "adzuna"), (board_reed, "reed")):
        got = fn(cfg)
        print(f"  {name}: {len(got)} postings")
        jobs.extend(got)

    # dedupe on URL, keep the richest record
    best = {}
    for j in jobs:
        if not j.get("url"):
            continue
        k = job_key(j)
        if k not in best or len(j["desc"]) > len(best[k]["desc"]):
            best[k] = j
    return list(best.values())


# ----------------------------------------------------------------------------
# email
# ----------------------------------------------------------------------------

def render(groups, mode):
    heading = ("New roles matched" if mode == "new"
               else "Still open, not yet applied")
    rows = []
    for j in groups:
        label, colour = band(j["score"])
        posted = parse_date(j.get("posted"))
        age = f"{(NOW - posted).days}d ago" if posted else "date unknown"
        why = ", ".join(j["why"][:4])
        rows.append(f"""
<tr><td style="padding:14px 0;border-bottom:1px solid #e6e8eb">
  <div style="font:600 12px system-ui;color:{colour};letter-spacing:.06em">
    {label} &middot; {j['score']}
  </div>
  <div style="font:600 16px system-ui;margin:4px 0 2px">
    <a href="{j['url']}" style="color:#14161a;text-decoration:none">{j['title']}</a>
  </div>
  <div style="font:14px system-ui;color:#42474f">
    {j['company']} &middot; {j['location']} &middot; {age}
  </div>
  <div style="font:13px system-ui;color:#71767e;margin-top:3px">{why}</div>
  <a href="{j['url']}" style="display:inline-block;margin-top:8px;font:600 13px system-ui;
     color:#fff;background:#14161a;padding:7px 14px;border-radius:6px;
     text-decoration:none">Apply</a>
</td></tr>""")

    return f"""<html><body style="margin:0;padding:24px;background:#f6f7f8">
<div style="max-width:620px;margin:0 auto;background:#fff;padding:28px;border-radius:10px">
  <div style="font:700 20px system-ui;color:#14161a">{heading}</div>
  <div style="font:14px system-ui;color:#71767e;margin:4px 0 18px">
    {len(groups)} roles &middot; ranked by how likely you are to land it &middot;
    {NOW.strftime('%a %d %b, %H:%M')}
  </div>
  <table style="width:100%;border-collapse:collapse">{''.join(rows)}</table>
  <div style="font:13px system-ui;color:#8b909a;margin-top:20px;
       border-top:1px solid #e6e8eb;padding-top:14px">
    Rolling deadlines. Applying in week one beats a polished application in week four.
  </div>
</div></body></html>"""


def send(subject, html):
    user, pw = os.getenv("SMTP_USER"), os.getenv("SMTP_PASS")
    to = os.getenv("EMAIL_TO", user)
    if not (user and pw):
        print("  ! SMTP_USER / SMTP_PASS not set, skipping email")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.attach(MIMEText(html, "html"))
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", 587))) as s:
        s.starttls()
        s.login(user, pw)
        s.send_message(msg)
    print(f"  sent to {to}")
    return True


def log_csv(jobs):
    exists = LOG_PATH.exists()
    with open(LOG_PATH, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["found", "score", "band", "title", "company",
                        "location", "posted", "source", "url"])
        for j in jobs:
            w.writerow([NOW.strftime("%Y-%m-%d"), j["score"], band(j["score"])[0],
                        j["title"], j["company"], j["location"],
                        j.get("posted", ""), j["source"], j["url"]])


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--mode", choices=["new", "digest"], default="new")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-score", type=int, default=32)
    args = ap.parse_args()

    cfg = load_json(CONFIG_PATH, None)
    if cfg is None:
        sys.exit("config.json missing")

    if args.discover:
        print("Probing employer ATS feeds...\n")
        discover(cfg)
        return

    print(f"Run: {NOW:%Y-%m-%d %H:%M} UTC  mode={args.mode}")
    jobs = collect(cfg)

    scored = []
    for j in jobs:
        pts, why = score_job(j, cfg)
        if pts is None or pts < args.min_score:
            continue
        j["score"], j["why"] = pts, why
        scored.append(j)
    scored.sort(key=lambda x: -x["score"])
    print(f"  {len(scored)} passed filters and scoring")

    seen = load_json(SEEN_PATH, {})
    new = [j for j in scored if job_key(j) not in seen]

    for j in scored:
        k = job_key(j)
        if k not in seen:
            seen[k] = {"first_seen": NOW.isoformat(), "title": j["title"],
                       "company": j["company"], "score": j["score"]}

    cutoff = NOW - timedelta(days=75)
    seen = {k: v for k, v in seen.items()
            if (parse_date(v.get("first_seen")) or NOW) > cutoff}

    if args.mode == "digest":
        recent = NOW - timedelta(days=14)
        payload = [j for j in scored
                   if (parse_date(seen.get(job_key(j), {}).get("first_seen")) or NOW) > recent]
        subject = f"{len(payload)} open roles - {NOW:%a %d %b}"
    else:
        payload = new
        top = new[0]["company"] if new else ""
        subject = f"{len(new)} new: {top} + {len(new) - 1} more" if len(new) > 1 \
            else (f"New: {top}" if new else "")

    if not payload:
        print("  nothing to send")
        save_json(SEEN_PATH, seen)
        return

    print(f"  {len(payload)} in this {args.mode} email")
    for j in payload[:12]:
        print(f"    {j['score']:>3}  {band(j['score'])[0]:<13} {j['company'][:22]:<24} {j['title'][:50]}")

    if args.dry_run:
        print("\n  (dry run, no email sent, state not saved)")
        return

    # Only bank the state once the email is genuinely out. If SMTP is missing
    # or the send throws, these stay unseen and get retried on the next run
    # rather than vanishing silently.
    try:
        delivered = send(subject, render(payload, args.mode))
    except Exception as e:                              # noqa: BLE001
        print(f"  ! send failed ({e}) - will retry next run")
        return

    if not delivered:
        print("  ! not delivered - state left unsaved so nothing is lost")
        return

    if new:
        log_csv(new)
    save_json(SEEN_PATH, seen)


if __name__ == "__main__":
    main()
