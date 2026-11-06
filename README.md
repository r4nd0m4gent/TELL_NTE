# TELL — Textile Ecosystem Living Lab

@author r4nd0m4gent

TELL gives a multi-perspective picture of the Dutch Textile, Clothing, Leather
and Footwear sector in NL: where businesses are and what they specialise in, and how
research institutions and companies work together through funded projects.

Live site: <https://tell.newtexeco.nl>

## The site

| Path | What it is |
|---|---|
| `/` | Landing page. Introduces TELL and links to the two perspectives. |
| `/dashboard/` | **Business specialization**: dashboard of Dutch textile businesses. |
| `/network/` | **Research network**: research-funding network across the Netherlands and the EU. |
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
relevance score).

- **Network view and map view**. Dutch organisations are placed at their main
  seat and European research partners appear under their short names. Clicking
  an organisation shows the lines to its shared projects.
- **Filters**: programme, years active, Dutch-led only, and whether to include
  European partners. The search box ranks the matching projects and highlights
  them, and pressing Enter turns a search term into a keyword filter.
- **Project and organisation profiles**, sortable tables, CSV download, and
  the option to exclude projects that were matched by mistake.

### Usage tracking

The dashboard and the funding network both log anonymous usage events (session
starts, clicks, filter changes). A visitor who moves between the two pages stays
one session. Events are labelled by source, so test and development traffic can
be told apart from real visitors.

## Repository layout

```
textile_companies_NL.py     Dashboard (Dash app); also serves the tracking endpoint
contribution_form.py        Contribution form (Dash app)
wsgi.py                     Mounts both apps for Gunicorn (form at /contribute)
classification.py           Custom-classification feature of the dashboard
semantic_classifier.py      Sentence-embedding classifier
build_embeddings.py         Precomputes company embeddings so classification runs take seconds
build_network_locations.py  Places funding-network organisations on the map (CORDIS + GeoNames)
assets/                     Dashboard CSS/JS
site/                       Static pages served by Nginx: landing page, network/ and its data
db/                         Data-preparation and maintenance scripts (keywords, classes,
                            tiers, categories, funding import, city names, tracking)
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
