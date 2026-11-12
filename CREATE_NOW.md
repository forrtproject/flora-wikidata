# Create the six missing items

Local credential support: the login step reads `.env` beside the scripts. Use `FLORA_WD_BOT_USERNAME` and `FLORA_WD_BOT_PASSWORD`. Existing environment variables take precedence; otherwise hidden prompts remain available. `.env` and its backups are excluded from Git and the downloadable package. Read-only commands do not load credentials. Keep the file private and replace any password that has been exposed in chat.

The creation plan is prepared in `six-items.create.plan.json`. Open `CREATION_REVIEW.md` to review the six complete proposed items. The first ten FLoRA rows contain 19 distinct works: thirteen are reused, six are proposed for creation. No items have yet been created by this tool.

## 1. Create a password specifically for this script

While logged in as **Hb19988**, visit [Special:BotPasswords](https://www.wikidata.org/wiki/Special:BotPasswords).

- Use the application name **FLoRA**.
- Grant editing existing pages and creating/editing/moving pages, as offered by the form. Creating items requires the creation grant; high-volume editing is unnecessary.
- Save the generated login name and generated password locally. It should give a login name such as `Hb19988@FLoRA`.
- Do not send the password in chat and do not use your normal account password in the script.

An existing bot password with the correct grants also works. Enter its exact generated login name when prompted.

## 2. Run the creation launcher

In the VS Code terminal, from this project folder, run:

```powershell
.\CREATE_MISSING_ITEMS.cmd
```

The launcher displays the six titles. After reading `CREATION_REVIEW.md`, type **REVIEWED**. Enter the generated bot-password login name, then the generated password at the hidden prompt. Password characters will not appear as you type.

The tool first repeats reconciliation and checks that the plan still matches current data. This may take a minute or two before the password prompt. It logs in, verifies the account is Hb19988 and has edit/create permissions, and asks for the displayed **APPLY wikidata …** phrase. It then displays each payload and asks for **SAVE create:…** before that individual creation. Anything else stops. It prints each new QID and saves the revision in the journal.

This command publishes public Wikidata items under Hb19988. It is not a dry run. Read-only testing remains available through `RUN_READ_ONLY_TEST.cmd`.

The saved plan expires 24 hours after preparation. A changed property/item or expired plan stops the run; never edit the timestamp. If the plan expired before anything was saved, run `.\REFRESH_PILOT.cmd`. It is read-only and needs the server to be out of lag. It re-checks the six creations against current Wikidata and starts a new 24-hour window. The old plan is kept under `superseded\`. If the creations changed, it warns you to review again. `CHECK_SERVER_STATUS.cmd` shows when the plan expires. A failed or uncertain save also stops the run. Preserve the journal and inspect account contributions before resuming so a creation is never blindly repeated.

## Undo as a group, and continuing after lag

The pilot is saved as two EditGroups, each with its own undo link:
- **originals**, 1 item: *Does darkness lead to happiness?…*, saved first;
- **replications**, the other 5 items.

Each save's edit summary names the batch and side, for example *FLoRA pilot (first 10 CSV rows), originals*. The launcher asks the displayed `APPLY wikidata … originals` phrase, then `APPLY wikidata … replications`. Type anything else at the second prompt to stop after the original and continue later. Either side can be undone without the other at:
- originals: https://editgroups.toolforge.org/b/CB/e581b817dc3cda6c51dc457bc2b945c9/
- replications: https://editgroups.toolforge.org/b/CB/81ef3e9d860b0d33f2f7bb064ee668e6/

These links change if the plan is refreshed; the launcher always prints the current ones. Deleting created items needs an administrator; EditGroups files that request.

If lag rises during the run, the refused save is recorded as not processed and the run stops cleanly. Start the launcher again later and it continues with the next item. An item that was saved but could not be read back is re-checked on the next run, not created again. An unrelated edit by someone else to one of the thirteen reused items no longer blocks the run.

After the pilot, continue with [PAGES.md](PAGES.md).

## Server lag troubleshooting

Run `CHECK_SERVER_STATUS.cmd` to check current server health with one credential-free, read-only request. If it says NOT READY, wait several minutes before checking again. When it says READY NOW, run the creation launcher. Readiness can change; each later request still enforces `maxlag=5`.

On 11 November 2026, a credential-free request reproduced a `maxlag` response from Wikidata Query Service: `queryserviceLag=1483` seconds (24.7 minutes), with a scaled API lag value of 24.7 against the configured limit of 5. HTTP 429 rate limiting was also observed. This did not test whether the password is valid. The earlier generic error hid this diagnostic detail.

The login now retries only explicit maxlag rejections, up to six attempts, waiting at least 30 seconds and respecting Retry-After. It does not retry incorrect credentials, ambiguous connection failures, or item saves. Requests are spaced at least one second apart. Do not increase maxlag to force this pilot through a backlog. See [MediaWiki maxlag guidance](https://www.mediawiki.org/wiki/Manual:Maxlag_parameter).

## Account restriction details

Earlier instructions described an autoconfirmed gate added by this tool. **The individually reviewed creation command now checks actual edit/create permissions instead.** It always requires a human decision for each public save and is not an unattended bot. This interpretation follows the individual-review scope in [Wikidata's mass-editing policy](https://www.wikidata.org/wiki/Wikidata:Mass-editing_policy). Wikidata can still refuse requests, require CAPTCHA, or apply limits; the tool stops and does not bypass those controls. The older relation updater retains its separate conservative gate.

## Data review

Creation metadata was checked against Crossref and DataCite. Factual bibliographic fields, not abstracts or quotations, are imported. See [Crossref's metadata retrieval guidance](https://www.crossref.org/documentation/retrieve-metadata/) and [DataCite's CC0 metadata guidance](https://support.datacite.org/docs/harvesting-datacite-doi-metadata).

- The OSF work is a preregistration. Its proposed broad class is **written work**, with a specific preregistration description, not scholarly article.
- The multilingual CSV title for the Asch replication was separated; the primary English title is used rather than a concatenated bilingual label.
- Journal items were reconciled through ISSNs. A spurious ISSN hit typed as a scholarly article was excluded from the PLOS journal match.
- Q38490712 is reused after matching title, authors, publication date, volume, issue and pages; it is not recreated merely because it lacks a DOI statement.
- Creation candidates were checked through DOI, exact title and entity-label searches. Search indexes can lag, so review their linked sources before approving.

This run creates bibliographic items. It does not create people, nanopublications, or scientific outcome classifications.
