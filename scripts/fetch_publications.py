#!/usr/bin/env python3
"""Pull the publication list from public scholarly APIs and write data/publications.json.

Sources, in order of trust for conflicting fields: ORCID, Crossref, DBLP, OpenAlex, NTRS.
Entries are de-duplicated by DOI, then by normalised title. Manual fixes live in
data/profile.yaml (publication_overrides / publication_exclude).
"""
from __future__ import annotations

import difflib
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
PROFILE = yaml.safe_load((ROOT / "data" / "profile.yaml").read_text())
IDS = PROFILE["ids"]
OUT = ROOT / "data" / "publications.json"
SOURCES = ROOT / "data" / "sources"
UA = {"User-Agent": f"nicolasrouquette.github.io publication fetcher (mailto:{PROFILE['email']})"}

TYPE_MAP = {
    "journal-article": "article", "article": "article", "journal": "article",
    "proceedings-article": "inproceedings", "conference-paper": "inproceedings", "inproceedings": "inproceedings",
    "book-chapter": "incollection", "incollection": "incollection",
    "book": "book", "edited-book": "book", "proceedings": "book", "editor": "book",
    "dissertation": "phdthesis", "phdthesis": "phdthesis",
    "report": "techreport", "report-component": "techreport", "paper-conference": "inproceedings",
    "preprint": "misc", "posted-content": "misc", "other": "misc", "conference-abstract": "misc",
}


def norm_title(t: str) -> str:
    t = re.sub(r"/sup (\w+)/", r"\1", (t or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def same_work(a: dict, b: dict) -> bool:
    """Near-identical titles in the same year are one work indexed twice (NTRS vs publisher spelling)."""
    if a.get("year") != b.get("year"):
        return False
    return difflib.SequenceMatcher(None, norm_title(a["title"]), norm_title(b["title"])).ratio() >= 0.97


def norm_doi(d: str | None) -> str | None:
    if not d:
        return None
    d = d.strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    return d or None


def get(url: str, retries: int = 3, headers: dict | None = None, **kw) -> requests.Response | None:
    for attempt in range(retries):
        try:
            r = requests.get(url, headers={**UA, **(headers or {})}, timeout=60, **kw)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            if r.ok:
                return r
            print(f"  ! {r.status_code} {url}", file=sys.stderr)
            return None
        except requests.RequestException as e:
            print(f"  ! {e} {url}", file=sys.stderr)
            time.sleep(2)
    return None


def entry(**kw) -> dict:
    e = {"title": None, "authors": [], "year": None, "venue": None, "type": "misc",
         "doi": None, "url": None, "sources": [], "citations": None, "open_access_url": None}
    e.update({k: v for k, v in kw.items() if v is not None})
    return e


# ---------------------------------------------------------------- ORCID
def fetch_orcid() -> list[dict]:
    orcid = IDS["orcid"]
    r = get(f"https://pub.orcid.org/v3.0/{orcid}/works", headers={"Accept": "application/json"})
    if not r:
        return []
    out = []
    for g in r.json().get("group", []):
        ws = g["work-summary"][0]
        doi = next((x["external-id-value"] for x in g["external-ids"]["external-id"] if x["external-id-type"] == "doi"), None)
        pd = ws.get("publication-date") or {}
        out.append(entry(
            title=ws["title"]["title"]["value"],
            year=int(pd["year"]["value"]) if pd.get("year") else None,
            venue=(ws.get("journal-title") or {}).get("value"),
            type=TYPE_MAP.get(ws.get("type"), "misc"),
            doi=norm_doi(doi), url=(ws.get("url") or {}).get("value"),
            sources=["orcid"]))
    print(f"ORCID: {len(out)}")
    return out


# ---------------------------------------------------------------- Crossref
def fetch_crossref() -> list[dict]:
    r = get(f"https://api.crossref.org/works?filter=orcid:{IDS['orcid']}&rows=200")
    if not r:
        return []
    out = []
    for it in r.json()["message"]["items"]:
        issued = (it.get("issued") or {}).get("date-parts", [[None]])[0][0]
        out.append(entry(
            title=(it.get("title") or [None])[0],
            authors=[f"{a.get('given', '')} {a.get('family', '')}".strip() for a in it.get("author", [])],
            year=issued, venue=(it.get("container-title") or [None])[0],
            type=TYPE_MAP.get(it.get("type"), "misc"), doi=norm_doi(it.get("DOI")),
            url=it.get("URL"), sources=["crossref"]))
    print(f"Crossref: {len(out)}")
    return out


# ---------------------------------------------------------------- DBLP
def fetch_dblp() -> list[dict]:
    pid = IDS["dblp"]
    cache = SOURCES / "dblp.xml"
    r = get(f"https://dblp.org/pid/{pid}.xml")
    if r and r.text.lstrip().startswith("<?xml"):
        cache.write_text(r.text)
        xml = r.text
    elif cache.exists():
        print("DBLP: live fetch blocked, using cached data/sources/dblp.xml", file=sys.stderr)
        xml = cache.read_text()
    else:
        return []
    out = []
    for rec in ET.fromstring(xml).findall("r"):
        pub = rec[0]
        ee = pub.findtext("ee")
        doi = norm_doi(ee) if ee and "doi.org" in ee else None
        venue = pub.findtext("journal") or pub.findtext("booktitle") or pub.findtext("publisher")
        year = pub.findtext("year")
        out.append(entry(
            title=(pub.findtext("title") or "").rstrip("."),
            authors=[re.sub(r" \d{4}$", "", a.text) for a in pub.findall("author") or pub.findall("editor")],
            year=int(year) if year else None, venue=venue,
            type=TYPE_MAP.get(pub.tag, "misc"), doi=doi,
            url=f"https://dblp.org/rec/{pub.get('key')}", sources=["dblp"],
            dblp_key=pub.get("key")))
    print(f"DBLP: {len(out)}")
    return out


# ---------------------------------------------------------------- OpenAlex
def fetch_openalex() -> list[dict]:
    out = []
    for aid in IDS.get("openalex", []):
        cursor = "*"
        while cursor:
            r = get("https://api.openalex.org/works", params={
                "filter": f"authorships.author.id:{aid}", "per-page": 200, "cursor": cursor,
                "select": "id,doi,title,publication_year,type,authorships,primary_location,cited_by_count,open_access,ids"})
            if not r:
                break
            d = r.json()
            for w in d["results"]:
                loc = w.get("primary_location") or {}
                out.append(entry(
                    title=w.get("title"),
                    authors=[a["author"]["display_name"] for a in w.get("authorships", [])],
                    year=w.get("publication_year"),
                    venue=((loc.get("source") or {}).get("display_name")),
                    type=TYPE_MAP.get(w.get("type"), "misc"), doi=norm_doi(w.get("doi")),
                    url=loc.get("landing_page_url") or w.get("id"),
                    citations=w.get("cited_by_count"),
                    open_access_url=(w.get("open_access") or {}).get("oa_url"),
                    openalex_id=w["id"].rsplit("/", 1)[-1], sources=["openalex"]))
            cursor = d["meta"].get("next_cursor")
    print(f"OpenAlex: {len(out)}")
    return out


# ---------------------------------------------------------------- NTRS
def fetch_ntrs() -> list[dict]:
    q = IDS.get("ntrs_query")
    if not q:
        return []
    r = get("https://ntrs.nasa.gov/api/citations/search", params={"q": q, "page.size": 100})
    if not r:
        return []
    out = []
    for rec in r.json().get("results", []):
        pubs = rec.get("publications") or [{}]
        date = pubs[0].get("publicationDate") or rec.get("distributionDate") or ""
        out.append(entry(
            title=rec.get("title"),
            authors=[a["meta"]["author"]["name"] for a in rec.get("authorAffiliations", []) if a.get("meta", {}).get("author")],
            year=int(date[:4]) if date[:4].isdigit() else None,
            venue=pubs[0].get("publicationName") or "NASA Technical Reports Server",
            type="techreport", doi=norm_doi(pubs[0].get("doi")),
            url=f"https://ntrs.nasa.gov/citations/{rec['id']}", sources=["ntrs"]))
    print(f"NTRS: {len(out)}")
    return out


# ---------------------------------------------------------------- merge
def merge(lists: list[list[dict]]) -> list[dict]:
    by_doi: dict[str, dict] = {}
    by_title: dict[str, dict] = {}

    def absorb(dst: dict, src: dict) -> None:
        for k, v in src.items():
            if k == "sources":
                dst["sources"] = sorted(set(dst["sources"]) | set(v))
            elif k == "citations":
                dst["citations"] = max(dst.get("citations") or 0, v or 0) or None
            elif k == "authors":
                if len(v) > len(dst.get("authors") or []):
                    dst["authors"] = v
            elif dst.get(k) in (None, "", [], "misc") and v not in (None, "", []):
                dst[k] = v

    for lst in lists:
        for e in lst:
            if not e.get("title"):
                continue
            key_t = norm_title(e["title"])
            hit = (by_doi.get(e["doi"]) if e.get("doi") else None) or by_title.get(key_t)
            if hit is None:
                hit = next((h for h in by_title.values()
                            if same_work(h, e) and (not e.get("doi") or h.get("doi") in (None, e["doi"]))), None)
            if hit:
                absorb(hit, e)
            else:
                hit = dict(e)
                by_title[key_t] = hit
            if hit.get("doi"):
                by_doi[hit["doi"]] = hit
            by_title[key_t] = hit
    return list({id(v): v for v in by_title.values()}.values())


def apply_manual(pubs: list[dict]) -> list[dict]:
    excl = {norm_doi(x) or norm_title(x) for x in PROFILE.get("publication_exclude") or []}
    pubs = [p for p in pubs if (p.get("doi") not in excl) and (norm_title(p["title"]) not in excl)]
    for ov in PROFILE.get("publication_overrides") or []:
        key_doi, key_t = norm_doi(ov.get("doi")), norm_title(ov.get("title", ""))
        tgt = next((p for p in pubs if (key_doi and p.get("doi") == key_doi) or (key_t and norm_title(p["title"]) == key_t)), None)
        if tgt is None:
            tgt = entry(sources=["manual"])
            pubs.append(tgt)
        tgt.update({k: v for k, v in ov.items()})
        tgt["sources"] = sorted(set(tgt["sources"]) | {"manual"})
        if tgt.get("doi"):
            tgt["doi"] = norm_doi(tgt["doi"])
    return pubs


def main() -> int:
    SOURCES.mkdir(parents=True, exist_ok=True)
    pubs = merge([fetch_orcid(), fetch_crossref(), fetch_dblp(), fetch_openalex(), fetch_ntrs()])
    pubs = apply_manual(pubs)
    pubs.sort(key=lambda p: (-(p.get("year") or 0), (p.get("citations") or 0) * -1, p["title"]))
    OUT.write_text(json.dumps({"generated": time.strftime("%Y-%m-%d"), "count": len(pubs), "publications": pubs},
                              indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(pubs)} publications")
    return 0


if __name__ == "__main__":
    sys.exit(main())
