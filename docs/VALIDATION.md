# zhanhTRAN 1.0.1 Validation

Validated on 2026-10-12 using synthetic documents and a loopback-only mock provider.
No real model provider, personal paper, or daily Zotero profile was used.

## Results

| Layer | Observed result |
| --- | --- |
| JavaScript unit/integration/browser tests | 99 passed; Node line coverage 91.41%, branch coverage 86.63% |
| Python parsing/layout/package tests | 138 passed; source line coverage 94% |
| Local PDF renderer | 91% line coverage; complete short-box translation and collision refusal covered |
| Isolated Zotero 10.0.6 | 11 PDF/OCR/image/rotation/output cases passed |
| Native reliability | 6 retry/timeout/cancel cases passed; retry limit is six total requests |
| Settings | Independent window and preference pane, icons and both protocol routes passed |
| Output invariants | No added notes, unchanged source bytes, correct file type/parent/library, safe sidecars |
| Packaging | Reproducible XPI; 26 unique members; Git-tracked bytes match tested source |
| Publication boundary | Source and unpacked XPI scanned before publishing; no credentials or private machine configuration |
| Dependency audit | npm: no vulnerabilities; pip-audit: no known vulnerabilities in the seven pinned direct requirements |

Node's coverage report includes the bundled parser and fixture helper but excludes VM-loaded
Zotero adapter code and browser code from its denominator. Python coverage covers the three
production Python modules. Browser/native tests exercise settings and host integration separately.
Dependency audit results are time-specific, not a permanent security guarantee; Python transitive
dependencies were not included in the direct-requirement audit.

## Installable Artifact

- File: `zhanhTRAN-1.0.1.xpi`
- Size: 87,294 bytes
- SHA-256: `cbdbf327192419f39d92cc8732ef70b6a2c755b2ee1e6ad207f57acb4095b07f`

The final package was tested in a fresh isolated Zotero profile and rebuilt with identical bytes.
Raw local logs, screenshots, profiles and generated fixtures are intentionally not published.
Run the checked-in test tools to produce independent evidence.

## Limits

Zotero 9 is declared compatible but has not been tested on a real host. Native tests were on
Windows; Linux CI covers parsing, layout and browser tests, not a native Zotero host.
Group-library import has mocked coverage, not a real group-account acceptance test.
Controlled translations verify plumbing/completeness, not real-provider linguistic quality.
OCR, complex tables and page geometry remain subject to the limits described in the README.
