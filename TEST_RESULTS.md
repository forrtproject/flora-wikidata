# Verification record

Latest server-lag fix: **75 automated tests passed**. Tests cover bounded login retries after explicit maxlag rejection, Retry-After, no retries on failed credentials/ambiguous POST results, safe lag diagnostics, and unchanged no-retry behavior for item saves. A credential-free read-only POST reproduced Query Service lag; no real login or item save was attempted by the diagnostic.

Prepared creation update: **65 tests passed**. The saved plan proposes six new items and reuses thirteen existing works. Bibliographic metadata was verified from Crossref/DataCite and journal matches checked via ISSNs. Q38490712 was matched using title, authors, date, volume, issue and pages. `flora_create.py` now checks actual edit/create rights for individually confirmed saves; the older updater retains its separate autoconfirmed guard. No real authentication or save has been performed.

Latest VS Code test session: **64 tests passed**. A live read-only scan found and fixed quoted DOI query syntax, then found 12 exact DOI matches, one title candidate without a DOI, and six works needing further reconciliation. See `TEST_IN_VSCODE.md` and `online-check.json.md`. No account login or write was attempted.

Creation update: the combined suite now passes **62 tests**, including CSV first-ten selection, shared-work deduplication and 20 mocked creations with verification/resumption. The selected real CSV contains 19 distinct works in its first ten rows. No real login or save was made. The original 53-test results below describe the existing-item updater.

Date: 11 November 2026.

Latest check: 68 automated tests passed, including local credential-file parsing, missing files, and duplicate-key rejection without revealing values. No real login or save was performed. The earlier results below describe the initial validation.

**53 automated tests passed. No live or test-wiki edits were made. No real credentials were used.**

## Local checks

- Python 3.14.7; Requests 2.34.2; requests-oauthlib 2.0.0.
- `python -m unittest discover -s tests -v`: 53 passed.
- The command-line offline demo produced ten additions, zero updates, zero skips, and zero blocked rows, plus a JSON plan and Markdown before/after review.
- A complete mocked application made ten simulated writes, verified each, and made no further writes when resumed.
- Failure tests covered timeouts, maxlag, redirects, account mismatch/block/missing rights, missing approval, plan tampering/expiration, revision changes, ambiguous DOIs, duplicate targets, conflicting qualifiers, and interrupted journals.
- Authentication setup was mocked for owner-only OAuth 2, OAuth 1, and bot passwords. These are not evidence of a successful real account login.

## Read-only checks against Wikimedia

- The live API advertised `wbeditentity`, including `baserevid`, `data`, and token support, and the requested `wbgetentities` fields.
- The six production properties existed and had the expected datatypes.
- Test Wikidata's read-only site information endpoint responded.
- DOI `10.1177/0022243720932532` reconciled to `Q142111820` using the implemented search plus direct entity verification.
- `Q142111752`, revision `2567952448`, already contained the cited original, the `qualifies` qualifier, and a P12545 nanopublication reference. Supplying the same relation and reference to the proposal builder produced `skip`. This was a structural duplicate check, not a scientific review of the underlying claim.

## Not performed

- Real authentication with the user's account.
- Any actual save on Test Wikidata or production Wikidata.
- Selection or scientific verification of ten real FLoRA records.
- Confirmation of FLoRA data reuse permission or community acceptance of the mapping.
- Automated Wikidata constraint validation or nanopublication signature verification.

The remaining checks belong to the reviewed rehearsal and publication procedure in `README.md`.
