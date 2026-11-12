# TELL — Textile Ecosystem Living Lab

@author r4nd0m4gent

TELL gives a multi-perspective picture of the Dutch Textile, Clothing, Leather
and Footwear sector: where businesses are and what they specialise in, and how
research institutions and companies work together through funded projects. The
data structure and the site are built so they can be extended to the rest of the EU.

Live site: <https://tell.newtexeco.nl>

## The site

| Path | What it is |
|---|---|
| `/` | Landing page. Introduces TELL and links to the two perspectives. |
| `/dashboard/` | **Business specialization**: dashboard of Dutch textile businesses. |
| `/network/` | **Research network**: research-funding network across the Netherlands and the EU. |
| `/articles/` | **Deep dive**: search the articles of open-access textile journals. Reached by the "Deep dive" button in the page header. |
| `/contribute/` | Contribution form for feedback and suggested edits or additions. |

### Business specialization dashboard (`/dashboard/`)

- **Filters**: region, city, company, consortium membership, product category,
  supply-chain tier and keywords. Every chart, the map and the table follow them.
- **Map** of businesses per city, plus charts by region, category, tier, class
  and founding year (legends are clickable). KPI tiles show active businesses,
  registered websites and public website contacts.
- **Keywords**: each company has a merged list of curated tags and terms
  collected from its public website. Keywords are searchable one at a time, and
  a keyword cloud at the bottom of the page follows the filters.
- **Company profiles**: click a company name to open its profile.
- **Table and export**: server-side company table. The filtered data and
  e-mail contacts can be exported.
- **Custom classification**: define your own classes with keywords and
  classify every company. This runs keyword matching or the semantic
  (sentence-embedding) classifier, and the results download as Excel.

### Research network (`/network/`)

A static page built on project data from **NWO, Erasmus+, Horizon Europe and
Horizon 2020**, limited to textile projects (whole-word keyword matching and a
relevance score), plus the **ECOSYSTEX member projects** (ecosystex.eu), also
those without a Dutch partner or from other EU programmes (Interreg, LIFE,
Digital Europe, …).

- **Network view and map view**, with every organisation, in the Netherlands
  and abroad. Dutch organisations are placed at their main seat, those abroad at
  their CORDIS address, or in the middle of their country when only that is
  known. Clicking an organisation shows the lines to its shared projects.
- **Filters**: programme, years active, Dutch-led only, ECOSYSTEX only, and
  whether to include the organisations abroad. The search box ranks the
  matching projects and highlights them, and pressing Enter turns a search term
  into a keyword filter.
- **Project and organisation profiles**, sortable tables, CSV download, and
  the option to exclude projects that were matched by mistake.

### Journal articles (`/articles/`)

A static page that searches about 5,900 open-access articles from Textiles,
Fibers, Sustainability (textile articles only), Fashion and Textiles and the
Journal of Engineered Fibers and Fabrics, collected from OpenAlex by
`new_tell_scraper/fetch_textile_oa.py`. It is stored in the `journal_articles`
table by `db/mysql/import_articles.py --apply`, which also writes the page's data.

It also searches the public reports of the ECOSYSTEX member projects, collected
from CORDIS by `db/mysql/import_project_reports.py --apply` into the
`project_reports` table and the page's `data/project_reports.json`. That covers
the Horizon Europe and Horizon 2020 projects (CORDIS does not hold the other
programmes): the full text of their periodic and final report summaries, and
their public report deliverables by title, with a link to the document. On the
page they are one more source, "ECOSYSTEX project reports" (`source:ecosystex`).

- **Search like Google Scholar**: results ranked by relevance (title first,
  then authors, keywords and abstract), with the matching words highlighted.
  Supports `"phrases"`, `OR`, `-word`, `author:`, `intitle:`, `allintitle:` and
  `source:` (journal), an advanced-search form, and keyword suggestions.
- **Filters**: date (presets or a custom range), sort by relevance or date,
  sources (journals and project reports, with counts), and open-access copy only.
- **Per article**: PDF/HTML link, citations in APA, MLA, Chicago, Harvard and
  Vancouver plus BibTeX/RIS download, related articles, the full abstract, and
  a personal library saved in the browser.
- Searches are in the URL, so they can be shared and the back button works.
- **Summarize these results**: Claude Haiku writes an overview of the whole
  result set (up to 1,000 results), citing them by number; a click on a number
  goes to that result. It reads the abstracts and report texts of the top 50,
  the titles of the rest, and exact counts by year, source and keyword. The
  page posts the result IDs to `/dashboard/summarize`
  ([article_summary.py](article_summary.py)), which reads the texts from
  `journal_articles` and `project_reports` and streams the summary back. It
  needs `ANTHROPIC_API_KEY` in the environment or in `db/mysql/.env`; without
  one the page says the summary is not available. Each visitor gets 8
  summaries an hour, each worker writes at most 200 a day, and a repeated
  search is answered from a cache. These limits are per process, so also set a
  spend limit in the Anthropic Console.

### Usage tracking

The dashboard, the funding network and the articles page log anonymous usage
events (session starts, searches, clicks, filter changes). A visitor who moves
between the pages stays one session. Events are labelled by source, so test and development traffic can
be told apart from real visitors.

## Repository layout

```
textile_companies_NL.py     Dashboard (Dash app); also serves the tracking and summary endpoints
article_summary.py          "Summarize these results" for the articles page (Claude Haiku)
contribution_form.py        Contribution form (Dash app)
wsgi.py                     Mounts both apps for Gunicorn (form at /contribute)
classification.py           Custom-classification feature of the dashboard
semantic_classifier.py      Sentence-embedding classifier
build_embeddings.py         Precomputes company embeddings so classification runs take seconds
build_network_locations.py  Places funding-network organisations on the map (CORDIS + GeoNames)
assets/                     Dashboard CSS/JS
site/                       Static pages served by Nginx: landing page, network/ and
                            articles/, each with its data
db/                         Data-preparation and maintenance scripts (keywords, classes,
                            tiers, categories, funding, ECOSYSTEX and article imports,
                            city names, tracking)
deploy/                     Server setup: Nginx, systemd units, Gunicorn, deploy scripts
```

## Running locally

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; on Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python textile_companies_NL.py
```

The apps read their data from a MySQL database. The connection settings come
from a local `.env` file in `db/mysql/`. This file is git-ignored and must
**never be committed**; ask the maintainer for access. If the database can't be
reached, the apps fall back to a local Excel export in `data/` (also
git-ignored).

`sentence-transformers` pulls in PyTorch, so the first install is large. It is
only needed for semantic classification.

## Deployment

Production runs on an Ubuntu server: Nginx serves HTTPS and the static pages in
`site/`, and proxies the Dash apps, which run under Gunicorn as systemd services.
See [deploy/DEPLOY.md](deploy/DEPLOY.md) for the full architecture.

### Redeploy production (only when the changes are approved)

```bash
# 1. Locally: commit and push to main
git push origin main

# 2. On the server
sudo bash /home/tell/app/deploy/redeploy.sh
```

`redeploy.sh` pulls the code, updates dependencies and the Nginx config, and
restarts the services.

### Dev preview (`/dev/`)

Feature branches can be previewed at <https://tell.newtexeco.nl/dev/> without
touching production:

```bash
sudo bash /home/tell/app-dev/deploy/setup_dev.sh            # set up / refresh
sudo bash /home/tell/app-dev/deploy/setup_dev.sh --remove   # take it down
```

`redeploy.sh` overwrites the production Nginx config, which removes the dev
preview. **Rerun `setup_dev.sh` after every production redeploy.**

### HTTPS certificate

A Let's Encrypt certificate is renewed automatically by certbot. To install or
repair it (safe to re-run):

```bash
sudo bash /home/tell/app/deploy/setup_ssl.sh
certbot certificates                       # check expiry
systemctl list-timers | grep certbot       # check the renewal timer
```

## Secrets

Credentials (database, server) are never written in this repository: not in
code, docs or commit messages. Keep them in the git-ignored `.env` files and
share them outside of git.
