# DLSU user-supplied collection

These JSON and CSV files were uploaded by the project owner. Both originals are
included unchanged. The JSON is imported into the website; the CSV provides the
same 258 program rows for spreadsheet review. This integration did not fetch or
independently verify the university pages.

The export reports collection at `2026-10-21T17:05:44.521174+00:00`, equivalent to
October 22, 2026 in Manila. Its configured identity is De La Salle University,
Taft Avenue, Malate, Manila, Metro Manila. This is not DLSU–Dasmariñas.

## What is usable in the prototype

- 258 raw program title rows become **257 indexed title entries**. Two rows differ
  only in capitalization: "Bachelor of Science in Management Accounting with
  Specialization in Business Analytics" and the version with lowercase
  "specialization". The existing importer indexes one normalized title.
- Program source URLs point to the recorded DLSU degree catalog on
  `old.dlsu.edu.ph`; the frontend retains those links.
- Two tuition evidence blocks contain navigation/reference text, including a
  projected-fees heading for AY 2025–2026. **Neither contains a numeric fee.**
- Requirements, exam coverage, dated application deadlines, application status
  and strand alignment are missing. Their absence is not filled with guesses.

## Review before treating entries as admissions facts

The catalog contains dual degrees, bachelor/master pathways, and names explicitly
referring to Laguna, Rufino or multiple campuses. The record's university address
does not prove each entry is offered entirely in Manila.

Some names also retain extraction artifacts, including a trailing `>` in a title
and a possibly concatenated computer-science/information-security label. They are
retained as unverified source text. The importer neither invents a corrected title
nor automatically splits legitimate combined degrees.

The importer adds review notes to SQLite for legacy source hosts, duplicate
titles, missing numeric fees, campus scope, and title quality. It preserves the
uploaded files and existing source notes. The website's count describes indexed
source titles, not a certified number of distinct current degree offerings.

## Load into an existing prototype database

From the repository root, after installing `requirements-web.txt`:

```bash
.venv/bin/python -m flask --app webapp import-data data/dlsu/unimatch_ph_raw_data.json
```

Then refresh the website. Reimporting is repeatable and replaces only DLSU's
program list; PUP's 55 entries remain intact. A new database includes both bundled
samples automatically: 4 university profiles, 312 indexed titles, and 2 schools
with collected records. Existing databases are not automatically overwritten.
