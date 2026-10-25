# nicolasrouquette.github.io

Personal research site for Nicolas Rouquette, published at <https://nicolasrouquette.github.io>.

A small Python build (Jinja2 + Markdown) renders `content/`, `data/` and `templates/` into `_site/`.
The publication list is not hand-maintained: `scripts/fetch_publications.py` merges ORCID, Crossref,
DBLP, OpenAlex and NTRS into `data/publications.json`, and a weekly GitHub Action commits the refresh.

```
data/profile.yaml        who you are: identifiers, interests, projects, manual publication fixes
data/publications.json   generated — do not edit by hand (use publication_overrides in profile.yaml)
data/sources/dblp.xml    cached DBLP export, used when dblp.org refuses non-browser clients
content/*.md             prose for the About and Software pages
templates/*.html         Jinja2 templates; static/style.css is the only stylesheet
scripts/fetch_publications.py, scripts/build.py
.github/workflows/deploy.yml               build + deploy to GitHub Pages on every push to main
.github/workflows/refresh-publications.yml weekly publication refresh (also runnable by hand)
```

## Recipes

### Preview locally
```sh
pip install -r requirements.txt
python scripts/build.py && python -m http.server -d _site 8000
```

### Refresh the publication list now
```sh
python scripts/fetch_publications.py && python scripts/build.py
```
or on GitHub: *Actions → Refresh publications → Run workflow*.

### Fix a wrong or missing publication
Edit `data/profile.yaml`. Overrides win over every fetched field and are matched by DOI (preferred) or exact title:
```yaml
publication_overrides:
  - doi: 10.1145/3704736
    venue: ACM Transactions on Cyber-Physical Systems
    year: 2025
  - title: A paper the indexes do not know about
    authors: [Nicolas Rouquette]
    year: 2026
    venue: Some Workshop
    url: https://example.org/paper.pdf
publication_exclude:
  - 10.1000/not-mine
```
Then run the refresh recipe (or wait for Monday's job).

### Add a project to the Software page
Append to `projects:` in `data/profile.yaml` (`name`, `repo`, `role`, `blurb`).

### Change the About text
Edit `content/index.md`; headline, title and tagline come from `data/profile.yaml`.

### First-time GitHub Pages setup
Repository *Settings → Pages → Build and deployment → Source: GitHub Actions*. The deploy workflow does the rest.
