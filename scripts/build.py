#!/usr/bin/env python3
"""Render the site into _site/ from data/, content/ and templates/."""
from __future__ import annotations

import json
import re
import shutil
import sys
from itertools import groupby
from pathlib import Path

import markdown
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "_site"


def load() -> tuple[dict, dict]:
    profile = yaml.safe_load((ROOT / "data" / "profile.yaml").read_text())
    pubs_file = ROOT / "data" / "publications.json"
    pubs = json.loads(pubs_file.read_text()) if pubs_file.exists() else {"generated": "never", "publications": []}
    return profile, pubs


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


def main() -> int:
    profile, pubs_doc = load()
    pubs = decorate(pubs_doc["publications"])
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html"]))
    md = markdown.Markdown(extensions=["extra", "smarty"])
    common = dict(p=profile, pubs=pubs, pubs_generated=pubs_doc["generated"], jsonld=jsonld(profile))

    if SITE.exists():
        shutil.rmtree(SITE)
    shutil.copytree(ROOT / "static", SITE / "static")

    def render(template: str, out: str, root: str, **ctx) -> None:
        path = SITE / out
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(env.get_template(template).render(root=root, **common, **ctx))

    body = md.convert((ROOT / "content" / "index.md").read_text()); md.reset()
    render("index.html", "index.html", "./", active="index", body=body, recent=pubs[:6])

    by_year = [(y, list(g)) for y, g in groupby(pubs, key=lambda x: x.get("year"))]
    render("publications.html", "publications/index.html", "../", active="publications", page_title="Publications", by_year=by_year)

    body = md.convert((ROOT / "content" / "software.md").read_text()); md.reset()
    render("software.html", "software/index.html", "../", active="software", page_title="Software", body=body)

    (SITE / "publications.bib").write_text(bibtex(pubs))
    shutil.copy(ROOT / "data" / "publications.json", SITE / "publications.json")
    (SITE / ".nojekyll").write_text("")
    (SITE / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {profile['site_url']}/sitemap.xml\n")
    (SITE / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{profile['site_url']}/{u}</loc></url>\n" for u in ["", "publications/", "software/"])
        + "</urlset>\n")
    print(f"built {SITE.relative_to(ROOT)}: {len(pubs)} publications")
    return 0


if __name__ == "__main__":
    sys.exit(main())
