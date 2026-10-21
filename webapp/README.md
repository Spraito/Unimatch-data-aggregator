# UniMatch PH website prototype

An undergraduate thesis prototype for **UniMatch PH: A College Admissions
Aggregator for Manila**. It prepares a Metro Manila college directory and exposes
collected evidence without pretending it is complete or verified.

## What works

- Explore university profiles and filter by name, program keyword, city or sector.
- Search 312 indexed PUP/DLSU title entries, filter by browsing topic or university,
  and page through results.
- Open university details, source links, program lists, admissions requirements,
  tuition evidence, application notices and collection notes.
- Compare up to three institutions, with missing data explicitly shown.
- Save schools in browser local storage and revisit the shortlist.
- Import additional scraper JSON into a persistent SQLite database using a CLI.
- Use the interface on desktop or mobile without external fonts, CDNs or APIs.

Scholarships are excluded as requested. The prototype does not include student
accounts, applications, a validated recommendation model or a human approval
workflow. Topics use simple keyword grouping to support browsing. They do not
predict eligibility, fit, admission chances or strand alignment. Browser shortlists
are local to the current browser and origin, not synced between devices.

## Run on CachyOS / Arch Linux

If necessary, install Python first:

```bash
sudo pacman -S --needed python python-pip
```

Download the updated GitHub ZIP and extract it, then open a terminal in the folder
containing `run_web.py`. A previously downloaded ZIP does not automatically update.
If the folder has the default GitHub name, the directory command is:

```bash
builtin cd "$HOME/Downloads/Unimatch-data-aggregator-main"
```

Verify `ls run_web.py requirements-web.txt` shows both files. Then:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-web.txt
.venv/bin/python run_web.py
```

Open the local address printed by Flask in your browser. Keep the terminal open.
Ctrl+C stops the server. If port 8000 is occupied, use
`.venv/bin/python run_web.py --port 8001`.

On macOS, use `python3 -m venv .venv` for the first command. On Windows, replace
`.venv/bin/python` with `.venv\Scripts\python.exe`.

You need an internet connection for the initial package installation. The seeded
website itself uses local assets and saved data; visiting it does not run the
scraper. No researcher email is needed to start the website. The CLI server binds
to the local machine and runs without Flask's debugger; it is a local prototype,
not a public production deployment.

## Backend and frontend

| File | Purpose |
| --- | --- |
| `run_web.py` | Starts the local Flask server |
| `webapp/__init__.py` | Application factory, JSON API and response headers |
| `webapp/database.py` | SQLite schema, initial data and transactional import CLI |
| `webapp/templates/index.html` | Accessible page structure and navigation |
| `webapp/static/app.js` | API calls, filters, dialogs, comparison and browser shortlist |
| `webapp/static/style.css` | Responsive visual design |
| `webapp/static/campus.svg` | Original decorative campus illustration |
| `instance/unimatch.sqlite3` | Generated local database; excluded from Git |

The application has `universities` and `programs` tables. University records also
retain JSON evidence from the collector, including source URLs, retrieval dates,
missing fields and source-conflict notes. SQL query values are parameterized,
rendered data is escaped, and imported clickable URLs must use HTTPS. The app has
no public file-upload endpoint or write API.

On the first startup, the database is initialized from
`config/universities.json`, `data/pup/unimatch_ph_raw_data.json`, and the uploaded
`data/dlsu/unimatch_ph_raw_data.json`. Later startups
reuse the database; they do not overwrite imported work or fetch external pages.
Use the import command below to update it. Back up `instance/unimatch.sqlite3`
before making changes you need to retain.

## Import new university data

1. Run the separate scraper for your desired school, using your real research
   contact and a separate output directory, as described in the repository README.
2. Review its evidence, dates, campus scope, missing fields and source conflicts.
3. From the repository root, import the JSON export. For example:

   ```bash
   .venv/bin/python -m flask --app webapp import-data output/dlsu/unimatch_ph_raw_data.json
   ```

4. Refresh the website to see the updated counts and records.

The importer validates the whole batch before writing. A successful reimport
replaces that university's program list, so removed entries do not linger; it does
not duplicate the school. Other universities are preserved. An invalid batch or
database write failure rolls back the transaction. Files over 10 MiB and batches
over 100 institutions are rejected. There is no browser-based admin upload.

Imports remain marked as needing review. This prototype does not certify data
just because it was imported, and it does not infer academic years or current
application availability. Review source conflicts before publishing admissions
advice.

## JSON API

All paths below are served by the same Flask application:

| Endpoint | Purpose / query parameters |
| --- | --- |
| `GET /api/health` | Check server and SQLite connectivity |
| `GET /api/stats` | University/program totals, cities, topics and latest collection |
| `GET /api/universities` | `search`, `city`, `sector`, `sort=featured` or `sort=name` |
| `GET /api/universities/<id>` | Full university details, programs and source evidence |
| `GET /api/programs` | `search`, `university_id`, `topic`, `page`, `limit` (1–100) |
| `GET /api/compare?ids=pup-sta-mesa,dlsu-manila` | Compare 1–3 universities |

Stable seeded IDs are `pup-sta-mesa`, `up-diliman`, `dlsu-manila`, `ust-manila`.
Unknown schools return 404, and invalid filters/pagination return 400. Sector and
city filters with no matches return an empty list. Page size defaults to 12.
Search is case-insensitive for the supported English names. No ranking or
eligibility score is returned.

## Data limitations

Only PUP was collected live, on October 21, 2026. There are 55 program entries,
three admissions requirement blocks and one Sta. Mesa notice. Tuition amounts,
entrance exam coverage, dated application deadlines and strand alignment were not
found on those two source pages. PUP's notice says "Application is closed", but
its page mixes AY 2026–2027 with eligibility text referring to AY 2024–2025. The
interface preserves these statements and the conflict instead of declaring a
verified current status.

DLSU's user-supplied export contributes 258 raw rows, indexed as 257 titles after
one case-only duplicate is collapsed. Its sources use `old.dlsu.edu.ph`, include
older academic-year references, and contain two fee references without numeric
amounts. Its title entries include combined degrees, campus pathways and some
scraping artifacts. The site retains these as unverified evidence with review
notes; see [the DLSU dataset guide](../data/dlsu/README.md).

UP Diliman and UST have curated directory identities and configured
source links, not collected admissions datasets. A zero collected-program count
does not mean a university offers no programs. Counts measure collection coverage,
not university quality or completeness. The program listing's applicability to a
particular campus must still be checked.

## Tests

From the repository root:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt -r requirements-web.txt
.venv/bin/python -m pytest -q
```

API and database tests cover search, pagination, missing data, source conflicts,
input validation, repeatable imports, persistence and transactional rollback.
Optional browser tests additionally exercise the real frontend, HTTP API and
SQLite together, including search, saving, comparison, detail tabs, program
pagination and mobile navigation. They require Playwright and Chromium:

```bash
.venv/bin/python -m pip install -r requirements-browser.txt
sudo pacman -S --needed chromium
```

Chromium is needed for these automated tests or browser-based scraping, not for
displaying the website. You can open the prototype in a current browser of your
choice. Without Playwright/Chromium, optional browser tests are explicitly skipped.
All tests use synthetic or previously saved data; they do not scrape universities.
