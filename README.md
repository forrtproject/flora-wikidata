# FLoRA: a reviewed ten-edit Wikidata pilot

**After the pilot: the rest of FLoRA, page by page.** See [PAGES.md](PAGES.md). `PREPARE_PAGE.cmd` prepares the next 25–50 rows read-only, `PUBLISH_PAGE.cmd` publishes a reviewed page with one confirmation, and every save (including the pilot's) carries an EditGroups tag so a page can be undone as a group.

**To create the actual missing items now:** open [CREATE_NOW.md](CREATE_NOW.md), review [CREATION_REVIEW.md](CREATION_REVIEW.md), then run `CREATE_MISSING_ITEMS.cmd` in your terminal. A verified six-item creation plan is included; thirteen existing works are reused. The creation workflow now checks edit/create permissions and requires individual save confirmations rather than imposing a blanket autoconfirmed requirement.

**Update for the requested CSV workflow:** use [CREATE_FIRST_TEN.md](CREATE_FIRST_TEN.md) and `flora_create.py` to process the first ten rows of `flora-validation/output/flora.csv` and create up to twenty missing original/replication items as Hb19988. The sections below describe the earlier existing-item relation updater and its separate ten-edit cap.

Prepared on 11 November 2026. **No account login, test-site write, or live edit was performed while building this package.**

This Python tool prepares and applies **at most ten individually reviewed statement changes to existing items**, using your own account. One change saves the citation, its qualifier, and its references together in one `wbeditentity` request. It never creates items. If some records already exist, the pilot makes fewer than ten edits; it does not invent replacement edits. The tests and full offline demonstration are included; real writes still need a supervised rehearsal.

Start with the offline demonstration. For real data, prepare a read-only plan, inspect it, and rehearse on Test Wikidata before deciding whether to publish. The `apply` command requires explicit flags, the exact plan's SHA-256, the expected username, a typed batch confirmation, and a typed confirmation for **every individual edit**. There is no unattended or `--yes` mode.

## What was learned from CODECHECK

[CODECHECK's workflow](https://codecheck.org.uk/codecheck/articles/codecheck_wikidata.html), dated 27 October 2026, separates a rebuildable Wikibase rehearsal from permanent Wikidata publication. It resolves identifiers, previews payloads, logs submitted batches, and verifies results. Its production path is human-run QuickStatements; its R package writes to the staging Wikibase.

This pilot adopts that sequence in Python. It avoids item creation, so it does not need CODECHECK's works-before-certificates creation batches. It uses the Action API for identifier discovery and current entity data for final checks. Search indexes can lag; a missing match blocks a row. A missing search result is never permission to create a duplicate item.

CODECHECK certificates use `review of (P6977)`. That does not automatically establish a model for replication findings. Its quoted editing speed is not a universal permission or limit; this tool checks account information, spaces writes by at least ten seconds, and stops on server refusals.

## Proposed FLoRA representation

| Part | Production mapping |
|---|---|
| Citation | Replication item → `cites work (P2860)` → original item |
| Citation classification | `has goal (P3712)` qualifier → explicitly reviewed CiTO item |
| Nanopublication reference | `nanopublication identifier (P12545)` → persistent nanopublication URI |
| Supporting source | Separate reference with `reference URL (P854)` → the evidence page |
| Retrieval date | `retrieved (P813)` on newly added references |
| Work reconciliation | Existing `DOI (P356)` or explicitly reviewed QID |

The mapping follows the example in the earlier chat. It is **not a claim that every FLoRA outcome maps automatically to a CiTO term**. A reviewer must check the exact relation, direction, evidence, and scope. A paper may test multiple findings with different outcomes; those cannot simply be collapsed into one paper-level judgment. `whole_relation_appropriate` must be true only after that review. Finding-level modelling is outside this pilot.

The source property and CiTO item are checked against current entity data. The verified example, [Q142111752](https://www.wikidata.org/wiki/Q142111752), already cites [Q142111820](https://www.wikidata.org/wiki/Q142111820), qualified by [qualifies (Q117121930)](https://www.wikidata.org/wiki/Q117121930), with a [P12545](https://www.wikidata.org/wiki/Property:P12545) nanopublication reference. Its existing relation/reference was checked without editing. See `verification.json`. This does not independently verify the scientific assertion.

The tool preserves references already stored under P12545 or P854. It does not re-add the same exact URL under another of those properties or refresh an existing retrieval date. Equivalent URLs with different spellings are not automatically equated: use canonical persistent URIs and inspect the preview. It does not publish, sign, supersede, or change attribution inside nanopublications.

## Prerequisites and policy

1. **Reviewed data:** select 1–10 curated records from a named FLoRA export/version. Verify both works, every DOI/QID, the citation direction, the CiTO term, the nanopublication, the evidence, and the scope. Do not treat an unreviewed source-row export as approved FLoRA assertions. The tool records human attestations; it cannot judge scientific evidence or validate nanopublication signatures.
2. **Reuse permission and modelling:** establish that the submitted data can be contributed under CC0 and fits Wikidata's conventions. A linked nanopublication is not, by itself, proof that an assessment is endorsed by FLoRA. Keep supporting quotations out of this pilot; no paper text is copied.
3. **Your account:** use its exact on-wiki username. This pilot conservatively requires the `autoconfirmed` right for production and checks it after authentication. This is a safeguard chosen for this tool, not a claim that every small API edit requires that right. The [current autoconfirmed guidance](https://www.wikidata.org/wiki/Wikidata:Autoconfirmed_users) generally specifies 50 edits and a first edit at least four days old. The earlier chat's account-age statement was not independently verified; account rights are checked directly. Do not make filler edits to reach a threshold.
4. **Current community rules:** the [mass-editing policy](https://www.wikidata.org/wiki/Wikidata:Mass-editing_policy) describes its scope in terms of individual review and whether edits could reasonably be done manually. It permits expected uncontroversial work to skip the proposal stage. Potentially controversial imports should be proposed, relevant WikiProjects notified, and disagreement resolved; the stated discussion period is one week without major disagreement. This pilot requires you to review applicability, rather than asserting that ten edits are always exempt or always require a proposal.
5. **Human control:** this tool is intentionally for individual decisions under your account. For unattended bot operation, [Wikidata's bot policy](https://www.wikidata.org/wiki/Wikidata:Bots) requires a separate bot account and the applicable approval process; the request must precede the bot test run. A “bot password” is an authentication credential, not bot status or community approval. Do not remove the prompts to turn this pilot into an unattended importer.
6. **Local setup:** Python 3.10 or later, internet access to the selected wiki, and the dependencies below. Tested locally with Python 3.14.7, Requests 2.34.2 and requests-oauthlib 2.0.0. Authenticated requests and real saves still require rehearsal; they were not exercised with your credentials.

## 1. Install in PowerShell

Open PowerShell in the extracted `flora-wikidata-pilot` folder. No PowerShell activation or execution-policy change is necessary:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

If your Python launcher is named `python`, use `python -m venv .venv` for the first command.

## 2. Run the offline demonstration

```powershell
.\.venv\Scripts\python.exe flora_pilot.py plan --environment test --model demo\model.json --input demo\records.json --offline-fixture demo\entities.json --out demo-run.plan.json --dry-run
```

Expected result: `add: 10`, `update: 0`, `skip: 0`, `blocked: 0`. Open `demo-run.plan.json.md` to read the before/after preview. A ready-made preview is also included under `demo/preview.plan.json.md`.

These ten records, identifiers, review attestations, and evidence links are **synthetic software fixtures**, not scientific data. Offline plans contain `offline: true` and are rejected by `apply` regardless of flags. Never copy the demo data into a real pilot. Use a new output name if you rerun: the tool refuses to overwrite an existing plan or review.

## 3. Prepare real input

Copy `records.template.json` to `records.json`. Fill it with 1–10 records, using one distinct replication item per record. The template is deliberately incomplete and cannot pass validation unchanged.

- `dataset.version`: record the exact export release, date, or commit; `dataset.url` should identify that source. Set `cc0_compatible` only after reviewing reuse conditions.
- `replication` and `original`: provide their verified titles and either a QID, a DOI, or both. If both are supplied, they must agree. DOI strings are normalized; title similarity never automatically chooses an item. A non-DOI report needs an explicit QID.
- `cito`: provide the exact QID and current English label. The script checks that label but does not infer the scientific meaning from it.
- `reference_url`: the persistent URI of the relevant nanopublication, for example its `w3id.org/np/…` URI, not a Nanodash viewer URL. `evidence_url`: the specific report or source supporting the assertion.
- `retrieved`: the date you actually accessed the sources. `verified_by`, `verified_at`, `review_note`, and `model_review_note` document the human review. Include the relevant modelling discussion link in the note when applicable.
- `whole_relation_appropriate`: leave false until you have checked that this citation-level classification preserves the meaning of the source findings.

There is no built-in connector to your changing FLoRA database schema. Export only the reviewed fields into this small JSON input. Nothing in this package marks ten real records as verified.

## 4. Make a production dry run — no login

```powershell
.\.venv\Scripts\python.exe flora_pilot.py plan --environment wikidata --input records.json --out pilot.plan.json --contact 'https://www.wikidata.org/wiki/User:YourWikidataName' --dry-run
```

Replace the contact with your actual user page or email. This sends read-only requests and does not load credentials. Review both `pilot.plan.json.md` and the corresponding item/source pages. The JSON contains the exact proposed statement, existing statement, item revisions, source input, target, and a batch identifier.

`add` means a missing citation will be added. `update` means a uniquely matching citation can be extended without replacing its rank, existing references, or unrelated statements. `skip` means the desired statement and references already exist. `blocked` means the record needs intervention. Any blocked row prevents applying the entire plan; no silent best-effort partial batch is allowed.

The planner checks current DOI statements against Action API search candidates, detects multiple exact matches and mismatches, and rejects repeated replication targets. An explicit QID without a DOI relies on the human identity review. Search is not an exhaustive proof of global uniqueness, and the tool does not merge existing duplicate items.

Plans expire after 24 hours. Property or item changes after preview also stop application. Regenerate under a new filename and review again when that happens.

## 5. Rehearse on Test Wikidata

[Test Wikidata](https://test.wikidata.org/wiki/Wikidata:Main_Page) is a separate public wiki, not a private copy of production. Its API was reachable during preparation. Its QIDs and property IDs have separate meanings: never reuse production numbers by assumption.

Use existing test entities you are permitted to edit, or prepare a small, clearly labelled test dataset manually. Copy `test-model.template.json` to `test-model.json` and supply the local IDs and exact English labels for six property roles. Expected datatypes are item for `relation` and `goal`, URL for `reference` and `nanopublication`, time for `retrieved`, and external identifier for `doi`. The tool checks all six; the DOI role is checked for model consistency even though test reconciliation uses explicit local QIDs only.

Copy your input to `test-records.json`, set `environment` to `test`, use local replication/original/CiTO QIDs, and leave DOI strings empty. Clearly describe the records as test fixtures. Keep any invented evidence in the offline demo; use reviewed sources for a public test rehearsal. The script does not bootstrap a wiki or create properties/items for you.

```powershell
.\.venv\Scripts\python.exe flora_pilot.py plan --environment test --model test-model.json --input test-records.json --out rehearsal.plan.json --contact 'Your actual contact' --dry-run
```

After reviewing that plan, use the apply command in step 7 with `--plan rehearsal.plan.json`, its own hash, the test credentials below, and **omit `--allow-live`**. The test target is fixed in the saved plan; changing it invalidates the plan. Successful rehearsal does not authorize production: create and review a separate production plan.

## 6. Set up authentication locally

Never send credentials in this chat or put your normal Wikidata password in a script, JSON file, command argument, or repository. Leave secret environment variables unset to receive hidden terminal prompts. The code uses a persistent session for cookies, rejects redirects, and checks the authenticated username and edit rights. It never writes credentials to the plan, audit log, or journal. It intentionally ignores `.netrc` and environment proxy settings; a mandatory proxy needs a separately reviewed configuration change.

**Preferred: owner-only OAuth 2.** [MediaWiki's current guidance](https://www.mediawiki.org/wiki/OAuth/Owner-only_consumers) recommends OAuth 2 when unsure. Register an [owner-only consumer on Meta](https://meta.wikimedia.org/wiki/Special:OAuthConsumerRegistration/propose/oauth2) while logged into the account you intend to use. Select the owner-only option and the minimum grants needed to edit existing pages. No high-volume or item-creation grant is needed for this pilot. Store the access token securely. Use `--auth oauth2`; only the access token is needed by this implementation. No automatic token refresh is attempted: an expired/revoked credential stops the run.

**OAuth 1 alternative.** Use an owner-only OAuth 1 consumer and `--auth oauth1`. The tool supports consumer key, consumer secret, access token, and access secret via `requests-oauthlib`.

**Bot-password alternative.** Create a dedicated credential at [Wikidata Special:BotPasswords](https://www.wikidata.org/wiki/Special:BotPasswords), with permission to edit existing pages and the basic access required by the form. Use the generated `YourName@FLoRAPilot` login and generated password, with `--auth bot-password`. For the test wiki, create/verify the credential on [its own BotPasswords page](https://test.wikidata.org/wiki/Special:BotPasswords). The code first gets a login token, logs in by POST, then obtains a CSRF token for writes. It never uses `action=login` with your main-account password. See [API:Login](https://www.mediawiki.org/wiki/API:Login) and [bot passwords](https://www.mediawiki.org/wiki/Manual:Bot_passwords).

If you use environment variables, their names are:

| Method | Production | Test wiki |
|---|---|---|
| OAuth 2 | `FLORA_WD_OAUTH2_TOKEN` | `FLORA_TEST_OAUTH2_TOKEN` |
| OAuth 1 | `FLORA_WD_CONSUMER_KEY`, `FLORA_WD_CONSUMER_SECRET`, `FLORA_WD_ACCESS_TOKEN`, `FLORA_WD_ACCESS_SECRET` | Same suffixes with `FLORA_TEST_` |
| Bot password | `FLORA_WD_BOT_USERNAME`, `FLORA_WD_BOT_PASSWORD` | `FLORA_TEST_BOT_USERNAME`, `FLORA_TEST_BOT_PASSWORD` |

The different prefixes prevent accidentally selecting production credentials for a rehearsal. Bot usernames may be entered visibly; all secret values use hidden prompts if unset. `--expected-user` is your actual account name without `@FLoRAPilot`.

## 7. Apply only after your explicit approval

**The command below writes to public Wikidata. Do not run it until data, modelling, policies, account rights, rehearsal, and the exact plan have been reviewed.** Replace the username, contact, and hash; this example is not pre-authorized.

```powershell
.\.venv\Scripts\python.exe flora_pilot.py apply --plan pilot.plan.json --approved-sha256 'PASTE_THE_64_CHARACTER_PLAN_HASH' --auth oauth2 --expected-user 'YourWikidataName' --contact 'https://www.wikidata.org/wiki/User:YourWikidataName' --apply --data-verified --policy-reviewed --allow-live
```

Use the **plan hash printed by `plan`**, not PowerShell's hash of the file bytes; the tool hashes canonical JSON. The hash binds the review to the plan contents; it is not a digital signature from Wikidata.

After account checks, type the requested `APPLY wikidata …` phrase. Before each edit, the tool prints its before/after statement and requires `EDIT record-id`. Anything else stops. Press Ctrl+C to stop at any time. A cancelled or failed run may already have completed earlier edits; the journal records those.

Every write includes `assert=user`, `assertuser`, a CSRF token, `maxlag=5`, a descriptive batch summary, and the preview's base revision. One minimal entity request carries the complete changed statement. No labels, other statements, or other items are replaced. Requests are serial. Read requests have bounded backoff; writes are never automatically retried, including on maxlag, rate limits, edit conflicts, a bad token, HTTP errors, or uncertain responses. That intentionally conservative behavior follows the [API guidance](https://www.mediawiki.org/wiki/API:Etiquette) without treating any failure as permission to replay an edit.

## Logs, interruption, and cleanup

Keep these files together:

- `pilot.plan.json` and its `.md` preview: reviewed intent, source records, before/after statements, revisions, hash and batch ID.
- `pilot.plan.json.journal.sqlite3`: durable reservation and status for each write attempt. There are at most ten attempts for the plan, not just ten successes.
- `pilot.plan.json.audit.jsonl`: timestamps, target account/wiki, attempts, acknowledged revisions, statement IDs, verification results and diff links. No secrets are recorded.

```powershell
.\.venv\Scripts\python.exe flora_pilot.py inspect --plan pilot.plan.json
```

Verified attempts are skipped when the same plan resumes. A `pending` or `written_unverified` entry blocks resumption: the server may have saved the edit even if the client did not get a response. Inspect the exact item history, batch summary, and intended statement before doing anything else. Do not delete the journal or rerun blindly. If an edit is confirmed present, leave it alone; if it is confirmed absent, prepare a new reviewed plan containing only the genuinely remaining changes. Keep the old records, and keep the overall pilot within ten intended changes across recovery plans.

After each successful response, the tool reads the item back and checks the saved statement. This is structural verification, not a full constraint engine: inspect Wikidata's constraint warnings, the evidence, and the resulting diff manually before approving the next edit. Query-service visibility can lag behind direct entity reads.

This Python batch is **not automatically registered with EditGroups**. Its summary and local revision/diff records provide traceability. If a change is wrong, stop and inspect the saved diff. Use the item's history/undo interface for that specific revision when it remains safe, or manually remove/correct only the pilot's addition while preserving intervening edits. There is no whole-batch transaction, automatic rollback, destructive reset, or deletion feature.

## Verification scope

The included tests cover duplicate DOI matches, existing statements and references, conflicting qualifiers, deprecated statements, self-links, repeated targets, unverified data, finding scope, changed revisions, changed plans, endpoint separation, missing confirmations, account identity/rights, read backoff, no write retry, durable unknown outcomes, and a complete mocked ten-edit run with safe resumption. See `TEST_RESULTS.md` for the recorded result.

Actual authentication and actual test/production saves were not performed. Ten real FLoRA records have not been selected or scientifically verified. Those are prerequisites for running a real pilot, not work silently completed by the demo.
