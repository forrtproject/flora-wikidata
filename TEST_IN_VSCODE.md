# Test this importer in VS Code

**Publishing:** see [CREATE_NOW.md](CREATE_NOW.md) for the prepared six-item creation plan. The account safeguard described in this historical test report was a tool-level restriction, now corrected for the individually reviewed creation workflow. Server edit/create permissions and every per-item confirmation remain enforced.

The package is open in its own VS Code window. Tests run with the existing isolated Python environment for this task.

1. Open **Terminal → Run Task…** in VS Code.
2. Choose **FLoRA: 1 Run safety tests**. Expected: 64 tests pass.
3. Choose **FLoRA: 2 Check first ten rows (read only)**. This queries Wikidata using your exact CSV and writes `online-check.json` and `online-check.json.md`. It never logs in or sends an edit.
4. Open `online-check.json.md` to review the results. `latest-test-results.txt` contains the test run performed during setup.

The VS Code tasks currently use the already-installed Python environment at `../../work/.venv/Scripts/python.exe` relative to this folder. If moving this folder elsewhere, create a local environment using `CREATE_FIRST_TEN.md` and update the task interpreter paths.

## Live read-only test on 11 November 2026

- Exactly the first ten CSV rows were checked, containing 19 distinct works.
- 12 works matched existing Wikidata items by DOI, verified directly against their stored DOI statements.
- Seven had no exact DOI match. One of these has a title candidate: Q38490712, which currently has no DOI statement. Review its bibliographic identity before deciding whether to reuse it. The other six had no result from the implemented title search either; that does not prove absence.
- No actual login, creation, or update was attempted.
- Public account information for Hb19988 showed 12 edits, registration on 10 November 2026, and no autoconfirmed right. The tool's conservative production guard therefore prevents live saves for now. This does not mean Wikidata forbids every manual edit by that account.

The online test exposed and corrected a DOI-search bug: CirrusSearch did not return the expected matches when the identifier value was enclosed in quotation marks. The importer, updater, and checker now use the verified unquoted syntax and reject identifiers requiring unsupported escaping. Regression tests were added. This replaces the initial all-missing result; use the current report.

Publishing still requires reviewed metadata, resolved title candidates, local credentials, and the existing explicit apply confirmations. A missing DOI on an otherwise matching Wikidata item needs manual reconciliation; do not create a duplicate or pretend the DOI is already stored on it.
