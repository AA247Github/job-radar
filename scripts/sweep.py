#!/usr/bin/env python3
"""Nightly sweep of employer ATS boards (Greenhouse, Ashby, Lever).

Writes data/latest.json (today's matches) and data/seen.json (every job key
ever seen, with first-seen date) so the Claude routine can prioritise is_new.
"""
import datetime as dt
import html
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text())
DATA = ROOT / "data"
SEEN_PATH = DATA / "seen.json"
UA = {"User-Agent": "job-radar/1.0 (personal job search; github actions)"}
TODAY = dt.date.today().isoformat()


def get_json(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def strip_html(s):
    s = html.unescape(s or "")
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<li[^>]*>", "- ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def title_ok(title):
    t = title.lower()
    if any(x in t for x in CONFIG["title_exclude"]):
        return False
    return any(k in t for k in CONFIG["title_keywords"])


def loc_ok(loc):
    l = (loc or "").lower()
    return any(re.search(r"\b" + re.escape(k) + r"\b", l) for k in CONFIG["location_keywords"])


# ---------- per-ATS fetchers: each returns list of normalised dicts ----------

def fetch_greenhouse(token):
    d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    out = []
    for j in d.get("jobs", []):
        out.append({
            "ats": "greenhouse", "token": token, "id": str(j["id"]),
            "company": j.get("company_name") or token,
            "role": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url"),
            "posted": (j.get("first_published") or j.get("updated_at") or "")[:10] or None,
            "updated": (j.get("updated_at") or "")[:10] or None,
            "description": strip_html(j.get("content")),
            "salary_text": None,
        })
    return out


def fetch_ashby(token):
    d = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true")
    out = []
    for j in d.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        if j.get("isRemote"):
            locs.append("remote")
        comp = j.get("compensation") or {}
        out.append({
            "ats": "ashby", "token": token, "id": j.get("id"),
            "company": token,
            "role": j.get("title", ""),
            "location": "; ".join(x for x in locs if x),
            "url": j.get("jobUrl"),
            "posted": (j.get("publishedAt") or "")[:10] or None,
            "updated": (j.get("publishedAt") or "")[:10] or None,
            "description": j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")),
            "salary_text": comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary"),
        })
    return out


def _lever(token, host):
    d = get_json(f"https://{host}/v0/postings/{token}?mode=json")
    out = []
    for j in d:
        cats = j.get("categories") or {}
        locs = [cats.get("location") or ""] + list(cats.get("allLocations") or [])
        sal = j.get("salaryRange") or {}
        sal_text = None
        if sal.get("min"):
            sal_text = f"{sal.get('currency','')} {sal.get('min'):,} - {sal.get('max'):,} {sal.get('interval','')}".strip()
        created = j.get("createdAt")
        out.append({
            "ats": "lever", "token": token, "id": j.get("id"),
            "company": token,
            "role": j.get("text", ""),
            "location": "; ".join(dict.fromkeys(x for x in locs if x)) + ("; " + j.get("workplaceType", "") if j.get("workplaceType") else ""),
            "url": j.get("hostedUrl"),
            "posted": dt.datetime.utcfromtimestamp(created / 1000).date().isoformat() if created else None,
            "updated": None,
            "description": j.get("descriptionPlain", "") + "\n\n" + "\n\n".join(
                l.get("text", "") + "\n" + strip_html(l.get("content")) for l in j.get("lists") or []),
            "salary_text": sal_text,
        })
    return out


def fetch_lever(token):
    return _lever(token, "api.lever.co")


def fetch_lever_eu(token):
    return _lever(token, "api.eu.lever.co")


FETCHERS = {"greenhouse": fetch_greenhouse, "ashby": fetch_ashby,
            "lever": fetch_lever, "lever_eu": fetch_lever_eu}


def try_fetch(ats, token):
    try:
        return FETCHERS[ats](token), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return None, type(e).__name__ + ": " + str(e)[:120]


def main():
    seen = json.loads(SEEN_PATH.read_text()) if SEEN_PATH.exists() else {}
    all_jobs, live_tokens, failures, discovered = [], [], [], []

    for ats in ("greenhouse", "ashby", "lever", "lever_eu"):
        for token in CONFIG[ats]:
            jobs, err = try_fetch(ats, token)
            time.sleep(0.5)
            if err:
                failures.append({"ats": ats, "token": token, "error": err})
                continue
            live_tokens.append({"ats": ats, "token": token, "open_jobs": len(jobs)})
            all_jobs.extend(jobs)

    # Probe candidate tokens on every ATS; a 404 just means "not on this ATS".
    for token in CONFIG.get("probe", []):
        for ats in ("greenhouse", "ashby", "lever", "lever_eu"):
            jobs, err = try_fetch(ats, token)
            time.sleep(0.3)
            if err is None and jobs:
                discovered.append({"ats": ats, "token": token, "open_jobs": len(jobs),
                                   "matching": sum(1 for j in jobs if title_ok(j["role"]) and loc_ok(j["location"]))})
                all_jobs.extend(jobs)
                break

    matches, keys = [], set()
    for j in all_jobs:
        if not (title_ok(j["role"]) and loc_ok(j["location"])):
            continue
        key = f"{j['ats']}:{j['token']}:{j['id']}"
        if key in keys:
            continue
        keys.add(key)
        j["key"] = key
        j["is_new"] = key not in seen
        j["first_seen"] = seen.get(key, TODAY)
        seen.setdefault(key, TODAY)
        j["description"] = (j["description"] or "")[:6000]
        matches.append(j)

    matches.sort(key=lambda j: (not j["is_new"], j["posted"] or ""), reverse=False)
    out = {
        "generated_at": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "counts": {"matches": len(matches), "new": sum(j["is_new"] for j in matches),
                   "boards_ok": len(live_tokens), "boards_failed": len(failures)},
        "live_tokens": live_tokens,
        "discovered_tokens": discovered,
        "failures": failures,
        "jobs": matches,
    }
    DATA.mkdir(exist_ok=True)
    (DATA / "latest.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    SEEN_PATH.write_text(json.dumps(seen, indent=0, sort_keys=True))
    print(json.dumps(out["counts"]), "discovered:", [d["token"] for d in discovered])
    return 0


if __name__ == "__main__":
    sys.exit(main())
