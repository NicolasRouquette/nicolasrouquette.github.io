#!/usr/bin/env python3
"""Collect public GitHub contribution data for the profile's GitHub login into data/activity/github.com.json.

Uses the GraphQL `contributionsCollection` API, one call per year since the account was created
(the API caps a collection at one year), so the per-repository breakdown covers the whole account
history, not just the last twelve months. Needs a token in GITHUB_TOKEN/GH_TOKEN (any scope;
`gh auth token` is used as a fallback). Only public contributions are visible to a token that is
not the user's own; the count of hidden ones is reported as `restricted`.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "activity" / "github.com.json"
API = "https://api.github.com/graphql"

BY_REPO = """
  {field}(maxRepositories: 100) {{
    repository {{ nameWithOwner url description isFork isArchived stargazerCount
                 owner {{ login }} primaryLanguage {{ name color }} }}
    contributions {{ totalCount }}
  }}"""
KINDS = {"commits": "commitContributionsByRepository", "prs": "pullRequestContributionsByRepository",
         "reviews": "pullRequestReviewContributionsByRepository", "issues": "issueContributionsByRepository"}
YEAR_QUERY = "query($login:String!,$from:DateTime!,$to:DateTime!){ user(login:$login){ contributionsCollection(from:$from,to:$to){" \
    " totalCommitContributions totalPullRequestContributions totalPullRequestReviewContributions totalIssueContributions" \
    " restrictedContributionsCount" \
    + "".join(BY_REPO.format(field=f) for f in KINDS.values()) + "} } }"
CAL_QUERY = "query($login:String!,$from:DateTime!,$to:DateTime!){ user(login:$login){ contributionsCollection(from:$from,to:$to){" \
    " contributionCalendar{ weeks{ contributionDays{ date contributionCount } } } } } }"


def token() -> str:
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not t:
        try:
            t = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            t = ""
    if not t:
        sys.exit("fetch_github: set GITHUB_TOKEN (or log in with gh)")
    return t


def gql(session: requests.Session, query: str, **variables) -> dict:
    r = session.post(API, json={"query": query, "variables": variables}, timeout=60)
    r.raise_for_status()
    doc = r.json()
    if doc.get("errors"):
        sys.exit(f"fetch_github: GraphQL error: {doc['errors'][0].get('message')}")
    return doc["data"]


def main() -> int:
    login = yaml.safe_load((ROOT / "data" / "profile.yaml").read_text())["ids"]["github"]
    s = requests.Session()
    s.headers["Authorization"] = f"bearer {token()}"
    created = gql(s, "query($login:String!){ user(login:$login){ createdAt } }", login=login)["user"]["createdAt"]
    first_year = int(created[:4])
    now = dt.datetime.now(dt.timezone.utc)

    repos: dict[str, dict] = {}
    years: dict[int, dict] = {}
    totals = {k: 0 for k in KINDS} | {"restricted": 0}
    for year in range(first_year, now.year + 1):
        start = dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc)
        end = min(dt.datetime(year + 1, 1, 1, tzinfo=dt.timezone.utc), now)
        c = gql(s, YEAR_QUERY, login=login, **{"from": start.isoformat(), "to": end.isoformat()})["user"]["contributionsCollection"]
        years[year] = {"commits": c["totalCommitContributions"], "prs": c["totalPullRequestContributions"],
                       "reviews": c["totalPullRequestReviewContributions"], "issues": c["totalIssueContributions"]}
        for k in KINDS:
            totals[k] += years[year][k]
        totals["restricted"] += c["restrictedContributionsCount"]
        for kind, field in KINDS.items():
            for row in c[field]:
                r = row["repository"]
                e = repos.setdefault(r["nameWithOwner"], {
                    "repo": r["nameWithOwner"], "owner": r["owner"]["login"], "url": r["url"],
                    "description": r["description"], "fork": r["isFork"], "archived": r["isArchived"],
                    "stars": r["stargazerCount"],
                    "language": (r["primaryLanguage"] or {}).get("name"), "language_color": (r["primaryLanguage"] or {}).get("color"),
                    "commits": 0, "prs": 0, "reviews": 0, "issues": 0, "years": set()})
                e[kind] += row["contributions"]["totalCount"]
                e["years"].add(year)
    # The heatmap is the trailing twelve months, which straddles two calendar years.
    cal = gql(s, CAL_QUERY, login=login, **{"from": (now - dt.timedelta(days=365)).isoformat(), "to": now.isoformat()})
    calendar = [d for w in cal["user"]["contributionsCollection"]["contributionCalendar"]["weeks"] for d in w["contributionDays"]]

    rows = []
    for e in repos.values():
        e["total"] = e["commits"] + e["prs"] + e["reviews"] + e["issues"]
        e["years"] = sorted(e["years"])
        rows.append(e)
    rows.sort(key=lambda e: (-e["total"], e["repo"].lower()))
    orgs: dict[str, dict] = {}
    for e in rows:
        o = orgs.setdefault(e["owner"], {"owner": e["owner"], "repos": 0, "commits": 0, "prs": 0, "reviews": 0, "issues": 0, "total": 0})
        o["repos"] += 1
        for k in ("commits", "prs", "reviews", "issues", "total"):
            o[k] += e[k]
    doc = {
        "host": "github.com", "label": "github.com", "profile_url": f"https://github.com/{login}", "kind": "github", "pr_label": "pull requests",
        "scope_note": f"Public contributions only ({totals['restricted']:,} further contributions are in private repositories)",
        "generated": now.strftime("%Y-%m-%d"), "login": login, "since": first_year,
        "totals": totals, "years": {str(y): years[y] for y in sorted(years)},
        "orgs": sorted(orgs.values(), key=lambda o: -o["total"]), "repos": rows,
        "calendar": calendar,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(rows)} repositories across {len(orgs)} owners, "
          f"{totals['commits']} commits / {totals['prs']} PRs / {totals['reviews']} reviews / {totals['issues']} issues "
          f"since {first_year} ({totals['restricted']} private, not shown)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
