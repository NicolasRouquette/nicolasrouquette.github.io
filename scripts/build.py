#!/usr/bin/env python3
"""Render the site into _site/ from data/, content/ and templates/."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from itertools import groupby
from pathlib import Path

import markdown
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "_site"


def detect_variant() -> str | None:
    """Pick the variant whose `repos:` list names the repository being built ($GITHUB_REPOSITORY or origin)."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        try:
            url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
            repo = re.sub(r"\.git$", "", re.sub(r"^.*[:/]([^/]+/[^/]+?)(\.git)?$", r"\1", url))
        except (OSError, subprocess.CalledProcessError):
            return None
    for f in sorted((ROOT / "data" / "variants").glob("*.yaml")):
        if repo in (yaml.safe_load(f.read_text()) or {}).get("repos", []):
            return f.stem
    return None


def load(variant: str | None) -> tuple[dict, dict, list[dict]]:
    profile = yaml.safe_load((ROOT / "data" / "profile.yaml").read_text())
    if variant:
        profile.update(yaml.safe_load((ROOT / "data" / "variants" / f"{variant}.yaml").read_text()) or {})
    pubs_file = ROOT / "data" / "publications.json"
    pubs = json.loads(pubs_file.read_text()) if pubs_file.exists() else {"generated": "never", "publications": []}
    activity = [json.loads(f.read_text()) for f in sorted((ROOT / "data" / "activity").glob("*.json"))]
    activity.sort(key=lambda a: (a.get("host") != "github.com", a.get("host", "")))
    return profile, pubs, activity


CELL = 14      # heatmap grid pitch in SVG units (11px cell + 3px gap)
GUTTER = 28    # room for the weekday labels


def heatmap(calendar: list[dict]) -> dict:
    """Lay out one year of daily counts as a GitHub-style 7-row grid, Sundays on top."""
    days = [(dt.date.fromisoformat(d["date"]), d["contributionCount"]) for d in calendar]
    mx = max((n for _, n in days), default=0)
    col, cells, months, last_month = 0, [], [], None
    for i, (date, n) in enumerate(days):
        dow = (date.weekday() + 1) % 7
        if i and dow == 0:
            col += 1
        if date.month != last_month and date.day <= 7:
            months.append({"label": date.strftime("%b"), "x": GUTTER + col * CELL})
            last_month = date.month
        level = 0 if n == 0 else 1 + min(3, int(3 * (n - 1) / max(mx - 1, 1)))
        cells.append({"x": GUTTER + col * CELL, "y": 14 + dow * CELL, "level": level, "n": n, "date": date.isoformat()})
    months = [m for m in months if m["x"] + 24 < GUTTER + (col + 1) * CELL]
    dows = [{"label": l, "y": 14 + r * CELL + 9} for l, r in (("Mon", 1), ("Wed", 3), ("Fri", 5))]
    return {"cells": cells, "months": months, "dows": dows, "width": GUTTER + (col + 1) * CELL}


def github_view(gh: dict | None, max_orgs: int = 12, max_repos: int = 25) -> dict | None:
    if not gh or not gh.get("repos"):
        return None
    kinds = ("commits", "prs", "issues", "reviews")
    years = [{"year": y, **v} for y, v in gh["years"].items()]
    top = max((y["commits"] for y in years), default=1) or 1
    for y in years:
        y["pct"] = round(100 * y["commits"] / top, 1)
    orgs = gh["orgs"][:max_orgs]
    rest = gh["orgs"][max_orgs:]
    if rest:
        orgs = orgs + [{"owner": f"{len(rest)} other organisations and people", "url": None, "repos": sum(o["repos"] for o in rest),
                        **{k: sum(o[k] for o in rest) for k in kinds}, "total": sum(o["total"] for o in rest)}]
    top = max(o["total"] for o in orgs) or 1
    for o in orgs:
        o.setdefault("url", f"https://github.com/{o['owner']}")
        o["segments"] = [{"kind": k, "n": o[k], "pct": round(100 * o[k] / top, 2)} for k in kinds if o[k]]
    repos = []
    for r in gh["repos"][:max_repos]:
        ys = r["years"]
        r["span"] = f"{ys[0]}" if ys[0] == ys[-1] else f"{ys[0]}–{ys[-1]}"
        repos.append(r)
    return {**gh, "years": years, "org_rows": orgs, "top_repos": repos, "heat": heatmap(gh["calendar"])}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80]


def decorate(pubs: list[dict]) -> list[dict]:
    seen: set[str] = set()
    for p in pubs:
        a = f"{p.get('year') or 'nd'}-{slug(p['title'])}"
        while a in seen:
            a += "-x"
        seen.add(a)
        p["anchor"] = a
        p["link"] = (f"https://doi.org/{p['doi']}" if p.get("doi") else None) or p.get("open_access_url") or p.get("url")
    return pubs


def bibtex(pubs: list[dict]) -> str:
    def esc(s: str) -> str:
        return (s or "").replace("{", "\\{").replace("}", "\\}").replace("&", "\\&")
    out = []
    for p in pubs:
        key = f"rouquette{p.get('year') or 'nd'}{slug(p['title']).split('-')[0]}"
        fields = {"title": esc(p["title"]), "author": " and ".join(p.get("authors") or []), "year": p.get("year")}
        venue_field = {"article": "journal", "inproceedings": "booktitle", "incollection": "booktitle"}.get(p["type"], "howpublished")
        if p.get("venue"):
            fields[venue_field] = esc(p["venue"])
        if p.get("doi"):
            fields["doi"] = p["doi"]
        if p.get("url") and not p.get("doi"):
            fields["url"] = p["url"]
        body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields.items() if v)
        out.append(f"@{p['type']}{{{key},\n{body}\n}}\n")
    return "\n".join(out)


def jsonld(p: dict) -> str:
    return json.dumps({
        "@context": "https://schema.org", "@type": "Person",
        "name": p["name"], "jobTitle": p["title"], "url": p["site_url"],
        "affiliation": {"@type": "Organization", "name": p["affiliation"]},
        "sameAs": [f"https://orcid.org/{p['ids']['orcid']}",
                   f"https://scholar.google.com/citations?user={p['ids']['google_scholar']}",
                   f"https://dblp.org/pid/{p['ids']['dblp']}.html",
                   f"https://openalex.org/{p['ids']['openalex'][0]}",
                   f"https://github.com/{p['ids']['github']}",
                   f"https://www.linkedin.com/in/{p['ids']['linkedin']}/"],
        "knowsAbout": p["interests"],
    }, indent=1)


def content(name: str, variant: str | None) -> Path | None:
    """content/<variant>/<name>.md overrides content/<name>.md; None if neither exists."""
    for c in ([ROOT / "content" / variant / f"{name}.md"] if variant else []) + [ROOT / "content" / f"{name}.md"]:
        if c.exists():
            return c
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Render the site into _site/.")
    ap.add_argument("--variant", "-v", help="data/variants/<variant>.yaml profile overlay, or 'auto' to pick by repository")
    variant = ap.parse_args().variant
    if variant == "auto":
        variant = detect_variant()
    profile, pubs_doc, activity_docs = load(variant)
    pubs = decorate(pubs_doc["publications"])
    activity = [v for v in (github_view(a) for a in activity_docs) if v]
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html"]))
    md = markdown.Markdown(extensions=["extra", "smarty"])
    focus = content("focus", variant)
    common = dict(p=profile, pubs=pubs, pubs_generated=pubs_doc["generated"], activity=activity, jsonld=jsonld(profile), has_focus=bool(focus))
    pages = ["", "publications/", "software/"] + (["focus/"] if focus else [])

    if SITE.exists():
        shutil.rmtree(SITE)
    shutil.copytree(ROOT / "static", SITE / "static")

    def render(template: str, out: str, root: str, **ctx) -> None:
        path = SITE / out
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(env.get_template(template).render(root=root, **common, **ctx))

    body = md.convert(content("index", variant).read_text()); md.reset()
    render("index.html", "index.html", "./", active="index", body=body, recent=pubs[:6])
    if focus:
        body = md.convert(focus.read_text()); md.reset()
        render("page.html", "focus/index.html", "../", active="focus", page_title="Current focus", body=body)

    by_year = [(y, list(g)) for y, g in groupby(pubs, key=lambda x: x.get("year"))]
    render("publications.html", "publications/index.html", "../", active="publications", page_title="Publications", by_year=by_year)

    body = md.convert(content("software", variant).read_text()); md.reset()
    render("software.html", "software/index.html", "../", active="software", page_title="Software", body=body)

    (SITE / "publications.bib").write_text(bibtex(pubs))
    shutil.copy(ROOT / "data" / "publications.json", SITE / "publications.json")
    if activity_docs:
        shutil.copytree(ROOT / "data" / "activity", SITE / "activity")
    (SITE / ".nojekyll").write_text("")
    (SITE / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {profile['site_url']}/sitemap.xml\n")
    (SITE / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{profile['site_url']}/{u}</loc></url>\n" for u in pages)
        + "</urlset>\n")
    print(f"built {SITE.relative_to(ROOT)}{' (variant ' + variant + ')' if variant else ''}: {len(pubs)} publications, {sum(len(a['repos']) for a in activity)} repositories on {len(activity)} host(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
