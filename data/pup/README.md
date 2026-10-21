# PUP collection sample

Collected from these public pages on October 21, 2026:

- https://www.pup.edu.ph/iapply/pupcet
- https://www.pup.edu.ph/academic/undergrad

`unimatch_ph_raw_data.json` contains one institution record. The CSV has 55 rows,
one per extracted program entry, with repeated institution-level evidence encoded
as JSON strings. These files contain the actual partial collection, not test data.

The collection includes 55 program entries, three admissions requirement evidence
blocks, and the Sta. Mesa application status row. Program availability on a
specific campus and intake still requires confirmation. Tuition amounts, entrance
exam coverage, dated application deadlines and strand alignment were not found
on these pages and have not been invented.

The admissions page labels AY 2026–2027 but references AY 2024–2025 in its
qualification text. The reported "Application is closed" status is a quoted source
statement, not a verified current admissions decision. Preserve `source_notes`,
`source_context`, source URLs and retrieval timestamps when importing this sample.

The scraper returned exit code 2 to flag incomplete data, with zero page-fetch
failures. All records require human review before being used in UniMatch PH.

No researcher contact email, credentials or applicant records are included in
these exports. Scholarships are outside the requested collection scope.
