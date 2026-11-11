# Create missing items from your first ten FLoRA rows

**Ready-to-run update:** the actual six-item plan and simpler publishing launcher are described in [CREATE_NOW.md](CREATE_NOW.md). That guide supersedes the earlier autoconfirmed restriction below for individually reviewed item creation. The source input and remaining candidate metadata have now been reconciled; use `CREATION_REVIEW.md` for the concrete preview.

Use **flora_create.py** for this request. `flora_pilot.py` is the earlier existing-item statement updater.

Selected source: `C:\Users\hamid\Documents\GitHub\flora-validation\output\flora.csv`.
Intended account: **Hb19988**.

The first ten data rows, in file order, have been extracted to `first-ten-flora-validation.review.json`. Read the accompanying `.md` overview. The full source SHA-256 and row numbers are recorded. No rows are sorted, skipped, or replaced by later rows. Missing values remain missing. This is preparation, not an assertion that the metadata has been verified.

## Behavior

For each of the ten rows, the tool searches for the original and replication by DOI or an explicitly reviewed stable identifier. It reads candidates directly to verify exact matches. It reuses an existing match without editing that item's attributes in this mode. Ambiguous matches stop the plan. If both are absent, it proposes creating two separate items with their reviewed attributes. If one is absent, it proposes one creation. Shared works are reused across rows. At most 20 item creations are allowed.

New items carry a title label, description, item type, DOI, title statement, publication year with year precision, author names with author order, and available volume/issue/pages. Author names use P2093; the importer does not invent author QIDs or create people. Journal names are preserved for review; add P1433 only after finding the correct journal QID. Publication types also require review: the first replication is listed as **OSF Registries**, so it must not automatically be classified as a published journal article. Title language is left missing when the source lacks it.

This CSV mode creates bibliographic items only. It does not infer a CiTO outcome, create a nanopublication, or add a relationship from unreviewed outcome text. The earlier relation workflow remains available separately after evidence and modelling review. The optional JSON relation mode in `flora_create.py` retains a ten-save cap; the CSV `items_only` mode has the requested ten-row / twenty-creation cap.

Wikidata items are public. Logging in as Hb19988 attributes edits to that account; it does not put items in private account storage.

## Run in PowerShell

In the extracted package folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The first ten rows have already been prepared. To prepare another snapshot, use a new output filename:

```powershell
.\.venv\Scripts\python.exe flora_create.py prepare-csv --csv 'C:\Users\hamid\Documents\GitHub\flora-validation\output\flora.csv' --out first-ten-new.review.json
```

Review `first-ten-flora-validation.review.json` before planning:

1. Fill the dataset URL and confirm CC0 compatibility only when established.
2. Check each work's identity and metadata. Fill a factual English description, the P31 item type QID, and any missing language code. For a journal, add a reviewed P1433 item attribute if desired. Do not copy example QIDs blindly.
3. Fill `retrieved`, `verified_at`, `verified_by`, and `review_note`; mark `metadata_reviewed` true after review. For a work that may need creating, investigate identifier and title searches, notability and possible indexing lag; then mark `creation_reviewed` true and explain the check in `creation_review_note`. A zero-match search alone does not prove absence.
4. Preserve the original first-ten selection. Do not mark unresolved data as reviewed just to make the script continue.

Generate the read-only creation plan (no login or write):

```powershell
.\.venv\Scripts\python.exe flora_create.py plan --input first-ten-flora-validation.review.json --environment wikidata --out first-ten.create.plan.json --contact 'https://www.wikidata.org/wiki/User:Hb19988' --dry-run
```

The JSON plan contains resolved QIDs, exact creation payloads, property revisions, and ordered operations. Review it before applying. It expires after 24 hours. Existing items are reused. Immediately before every creation the tool searches again; a new match stops the run so a fresh plan can reuse it. Wikidata does not provide a global uniqueness lock, so manual review remains necessary despite these checks.

## Log in as Hb19988 and publish only reviewed creations

Create an owner-only OAuth credential or a bot password as described in `README.md`. **For item creation, grant both editing existing pages and creating pages.** The earlier updater did not need the creation grant. The tool checks account identity and create permission before saving.

For a bot password, visit [Wikidata's BotPasswords page](https://www.wikidata.org/wiki/Special:BotPasswords) while logged in as Hb19988. Name the application `FLoRA`. Keep the generated password local. In PowerShell, set only the nonsecret login name:

```powershell
$env:FLORA_WD_BOT_USERNAME = 'Hb19988@FLoRA'
```

After reviewing the exact plan, this command starts the interactive publishing process. Replace the hash with the value printed by `plan`:

```powershell
.\.venv\Scripts\python.exe flora_create.py apply --plan first-ten.create.plan.json --approved-sha256 'PASTE_PLAN_HASH' --auth bot-password --expected-user 'Hb19988' --contact 'https://www.wikidata.org/wiki/User:Hb19988' --apply --data-verified --policy-reviewed --allow-live
```

It asks for the generated bot password in a hidden terminal prompt, then a batch confirmation, then `SAVE create:work-key` for every proposed creation. Use the generated bot password, never your normal account password. Do not send credentials in chat. OAuth 2 and OAuth 1 remain supported with `--auth oauth2` or `--auth oauth1` and the existing environment-variable conventions.

The inherited account safeguard requires production autoconfirmed rights. Account age/eligibility has not been checked for Hb19988 here. See the policy and test-wiki sections in `README.md`. Test wiki IDs are independent; reviewed test data and a local property model are necessary for rehearsal.

Each save sends metadata together, logs the returned QID/revision, and reads the item back to verify it. Writes are never automatically retried. Keep the plan, `.audit.jsonl`, and `.journal.sqlite3` together. A timeout or unverified creation stops resumption: inspect contributions and the journal to find any item already created before attempting anything else. Successful entries persist and are skipped on resumption.

## What was tested

The selected ten rows contain 19 distinct DOI-identified works; one work occurs twice. The importer creates that work at most once. Existing Wikidata matches may reduce the number of creations further.

The combined suite passed 62 tests, including first-ten file-order selection, a 20-item creation plan, shared-work reuse, existing-item reuse, review gates, date precision, and 20 mocked creations with verification and no replay on resumption. No actual login, real creation, or live metadata verification has been performed in this update.
