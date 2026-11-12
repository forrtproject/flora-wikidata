# Page-by-page import of all FLoRA rows

After the 10-row pilot is published, the rest of `flora.csv` is processed in **pages** of up to 50 rows (default 25). Each page is one batch:

- it is prepared read-only and reviewed in `REVIEW.md` before anything is saved;
- only rows whose original **and** replication are both clean are included. If either side is held, nothing is created for that row;
- the saves are split into two [EditGroups](https://www.wikidata.org/wiki/Wikidata:Edit_groups):
  - **originals** (the studies being replicated), saved first;
  - **replications** (replication and reproduction studies), saved after.

  Each group has its own undo link, so one side can be undone without touching the other. A work that is the original in any row of the page goes into the originals group;
- it is published with one login and one typed confirmation per group. You can stop after the originals, check them on Wikidata, and run the publish again for the replications.

This follows Wikidata's [mass-editing policy](https://www.wikidata.org/wiki/Wikidata:Mass-editing_policy), which allows batch editing from a normal account when the batches are traceable and revertable. No bot account is needed for this workflow. Pages only **create missing work items** (and reuse existing ones). They do not add FLoRA outcome relations.

## Commands

| Step | Command | What happens |
|---|---|---|
| 1 | `.\PREPARE_PAGE.cmd` (or `.\PREPARE_PAGE.cmd 50`) | Read-only. Takes the next 25 (or 50) rows that no earlier page handled and writes `pages\page-NNN\REVIEW.md`. |
| 2 | Open `pages\page-NNN\REVIEW.md` | Check the new items, reused items and held rows. |
| 3 | `.\PUBLISH_PAGE.cmd` | Re-checks everything against current Wikidata and logs in. Asks `APPLY … originals` and saves the originals, then asks `APPLY … replications` and saves those. Type anything else at the second prompt to stop after the originals; run it again later to continue. |
| – | `.\PAGE_STATUS.cmd` | Lists the pilot and every page: state, created count per group, held rows and both edit-group links. |

`PUBLISH_PAGE.cmd --each` asks for confirmation before every single save instead. Use it while the account is not yet autoconfirmed (see below).

Pages are strictly sequential. The next page can be prepared only after the previous one, starting with the pilot, is fully published. If a prepared page is not wanted, or its plan expired (24 hours), set it aside with `.venv\Scripts\python.exe flora_pages.py discard page-NNN`. The folder is moved to `pages\_discarded`, and its rows are offered again by the next prepare.

## What is decided automatically, and what is held

For each work in a row:

- **Reuse:** a Wikidata item already has the DOI, or an earlier page created it. The earlier-page check uses this tool's journals, because Wikidata's search index can lag behind new items.
- **Create:** no item has the DOI, the DOI is registered at Crossref or DataCite, and the registry record is one of these types:
  - a journal article, created as *scholarly article*;
  - a preprint, created as *preprint*;
  - a study registration, created as *written work*, described as a preregistration.

  The new item gets its label, description, title, year, author name strings with their order, volume, issue and pages from the registry record. It gets *published in* only when exactly one journal item matches the ISSN. Every statement cites the registry record.
- **Hold:** the row is listed in `REVIEW.md` and nothing is created for either of its works. This happens when:
  - a work has no DOI or a malformed one;
  - the DOI contains characters that Wikidata search may misread (`< > ; % [ ]`);
  - several items already carry the DOI;
  - the registry type needs a modelling decision (books, chapters, conference papers, theses, …);
  - the CSV title differs from the registry title, which may mean a wrong DOI;
  - an existing item has the same English label but no DOI, which is a possible duplicate;
  - neither the CSV nor the registry records the title language as English (it is never guessed);
  - the same author name appears twice;
  - more than 300 authors are listed;
  - the label would be longer than 250 characters.

Held rows are never offered again automatically. Handle them by hand later.

## Undoing a page

`REVIEW.md`, the publish output and `PAGE_STATUS.cmd` show two edit-group links per page (`https://editgroups.toolforge.org/b/CB/<id>/`), one for originals and one for replications. Open the side you want to undo, log in with your Wikidata account and choose **Undo**. The other side stays as it is.

- Edits to existing items are reverted directly. Pages currently make no such edits.
- **Created items can only be deleted by an administrator.** EditGroups files the deletion request for you. Undo a page soon after publishing if something is wrong, before others build on the new items.

## Server lag and interruptions

- Every save uses `maxlag=5`. If Wikidata starts lagging mid-page, the refused save is recorded as *not processed* and the page stops cleanly. Run `PUBLISH_PAGE.cmd` again later and it continues with the next item.
- If an item was saved but could not be read back, the next run re-checks that item instead of creating it again.
- Only a truly unknown outcome, such as a network failure during a save, blocks the page. Inspect your contributions before continuing.

## Policy checklist (your part)

1. **Autoconfirmed account first.** The mass-editing policy expects mass editors to be at least autoconfirmed through ordinary manual editing: about 50 edits and an account at least 4 days old. Page mode checks this and refuses until then. Do not make filler edits. Until then, publish with `--each`, or do real manual improvements on Wikidata.
2. **Proposal if it could be controversial.** About 1,500 new scholarly items is a sizeable import. The policy asks for a short description at [Wikidata:Mass editing proposals](https://www.wikidata.org/wiki/Wikidata:Mass_editing_proposals) when quantity, quality or modelling could be questioned. Notify a relevant WikiProject (for example WikiProject Source Metadata), link the pilot and the first pages as test edits, and wait one week without major disagreement before continuing in bulk.
3. **Batches stay meaningful.** Every edit summary names the page, its CSV rows and the side, for example *FLoRA page 2 (CSV rows 11-35), originals*.
4. **Respond to feedback.** If someone raises a problem on your talk page, stop publishing pages until it is resolved. The policy allows quick reverts when more than 100 edits, or 5% of edits, are problematic.

## Files in a page folder

| File | Meaning |
|---|---|
| `REVIEW.md` | Human review: new items, reused items, held rows with reasons, edit-group link. |
| `page.json` | Rows and record IDs handled by this page (ready and held). |
| `input.json` / `plan.json` / `plan.sha256` | Exact reviewed input and plan. Publishing refuses if the plan changed. |
| `plan.json.journal.sqlite3` | Durable record of every save. Never delete it. |
| `plan.json.audit.jsonl` | Append-only log of authorisations and saves. |
