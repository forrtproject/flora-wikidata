"""Page-by-page creation: offline tests with a fake Wikidata and a fake DOI registry."""
import copy
import csv
import io
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from urllib.parse import quote

import flora_pilot as p
import flora_create as c
import flora_pages as fp

ATTRIBUTE_TYPES = {"P31": "wikibase-item", "P1476": "monolingualtext", "P577": "time", "P2093": "string",
                   "P1545": "string", "P478": "string", "P433": "string", "P304": "string",
                   "P1433": "wikibase-item", "P236": "external-id"}


class FakeWikidata:
    environment = "wikidata"

    def __init__(self):
        self.endpoint = p.TARGETS["wikidata"]
        self.entities, self.doi_index, self.issn_index, self.labels = {}, {}, {}, []
        self.posts, self.counter = [], 900
        self.rights = ["edit", "createpage", "autoconfirmed"]
        self.lag_on_post, self.fail_next_verify = set(), False
        for role, prop in p.PROPERTIES.items():
            self.entities[prop] = self._entity(prop, prop, datatype=p.DATATYPES[role])
        for prop, kind in ATTRIBUTE_TYPES.items():
            self.entities[prop] = self._entity(prop, prop, datatype=kind)
        for qid, label in fp.CLASSES.values():
            self.entities[qid] = self._entity(qid, label)

    @staticmethod
    def _entity(identifier, label, claims=None, datatype=None):
        e = {"id": identifier, "lastrevid": 5, "labels": {"en": {"language": "en", "value": label}},
             "claims": claims or {}}
        if datatype:
            e["datatype"] = datatype
        return e

    @staticmethod
    def _claim(prop, value, kind="external-id"):
        return {"mainsnak": p.snak(prop, value, kind), "rank": "normal", "type": "statement"}

    def add_work(self, qid, doi, label):
        self.entities[qid] = self._entity(qid, label, {"P356": [self._claim("P356", doi)]})
        self.doi_index.setdefault(doi, []).append(qid)

    def add_journal(self, qid, issn, label, article=False):
        claims = {"P236": [self._claim("P236", issn)]}
        if article:
            claims["P31"] = [self._claim("P31", fp.SCHOLARLY_ARTICLE, "wikibase-item")]
        self.entities[qid] = self._entity(qid, label, claims)
        self.issn_index.setdefault(issn, []).append(qid)

    def entity(self, identifier):
        return copy.deepcopy(self.entities[identifier])

    def call(self, params, post=False, write=False):
        action = params["action"]
        if action == "query" and params.get("list") == "search":
            prop, value = params["srsearch"].removeprefix("haswbstatement:").split("=", 1)
            index = self.doi_index if prop == "P356" else self.issn_index
            return {"query": {"search": [{"title": q} for q in index.get(value, [])]}}
        if action == "wbsearchentities":
            wanted = fp.norm(params["search"])
            return {"search": [{"id": q, "label": l} for q, l in self.labels if fp.norm(l) == wanted]}
        if action == "wbgetentities":
            if self.fail_next_verify:
                self.fail_next_verify = False
                raise p.Stop("Network failure.")
            return {"entities": {params["ids"]: self.entity(params["ids"])}}
        if action == "wbeditentity":
            if len(self.posts) + 1 in self.lag_on_post:
                self.lag_on_post.discard(len(self.posts) + 1)
                raise p.Lagged("maxlag")
            self.posts.append(params)
            self.counter += 1
            qid = f"Q{self.counter}"
            payload = json.loads(params["data"])
            e = {"id": qid, "lastrevid": 7000 + self.counter, "labels": payload["labels"],
                 "descriptions": payload["descriptions"], "claims": {}}
            for i, claim in enumerate(payload["claims"]):
                e["claims"].setdefault(claim["mainsnak"]["property"], []).append({**claim, "id": f"{qid}${i}"})
            self.entities[qid] = e  # deliberately NOT added to doi_index: simulates search-index lag
            return {"success": 1, "entity": e}
        raise AssertionError(params)

    def authenticate(self, method, user, **kwargs):
        return {"name": user, "rights": self.rights}

    def csrf(self):
        return "FAKE-TOKEN"


class FakeRegistry:
    def __init__(self, records):
        self.records = records

    def lookup(self, doi):
        if doi not in self.records:
            raise fp.Hold("DOI not found at Crossref")
        service, data = self.records[doi]
        return service, "https://api.crossref.org/works/" + quote(doi, safe=""), data


def crossref(title, kind="journal-article", authors=("Ann Lee", "Bo Chen"), issn=("1234-5678",), year=2020, **extra):
    return {"type": kind, "title": [title], "ISSN": list(issn), "volume": "3", "issue": "2", "page": "10-20",
            "author": [dict(zip(("given", "family"), name.split(" ", 1))) for name in authors],
            "published": {"date-parts": [[year, 5, 1]]}, **extra}


FIELDS = ["id", "doi_o", "title_o", "language_o", "author_o", "doi_r", "title_r", "language_r", "author_r"]


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({"language_o": "en", "language_r": "en", **row})


def workspace(tmp):
    """A published one-row pilot, a CSV and a fake Wikidata/registry."""
    root = Path(tmp)
    csv_path = root / "flora.csv"
    rows = [
        {"id": "pilot-1", "doi_o": "10.1000/PILOT", "title_o": "Pilot", "doi_r": "10.1000/PILOTR", "title_r": "Pilot r"},
        {"id": "r2", "doi_o": "10.1000/EXIST", "title_o": "Existing original",
         "doi_r": "10.1000/NEW1", "title_r": "Replication of the existing effect"},
        {"id": "r3", "doi_o": "10.1000/EXIST", "title_o": "Existing original", "doi_r": "", "title_r": "No DOI work"},
        {"id": "r4", "doi_o": "10.1000/BOOK", "title_o": "A book chapter", "doi_r": "10.1000/NEW2", "title_r": "Fine"},
        {"id": "r5", "doi_o": "10.1002/(SICI)1234<5::AID-X1>3.0.CO;2-Z", "title_o": "Old", "doi_r": "10.1000/EXIST2", "title_r": "E2"},
        {"id": "r6", "doi_o": "10.1000/MISMATCH", "title_o": "Completely different subject",
         "doi_r": "10.1000/EXIST2", "title_r": "E2"},
        {"id": "r7", "doi_o": "10.1000/DUPTITLE", "title_o": "Known title without doi", "doi_r": "10.1000/EXIST2", "title_r": "E2"},
        {"id": "r8", "doi_o": "10.1000/EXIST2", "title_o": "E2",
         "doi_r": "10.1000/NEW3", "title_r": "Hip replacement and the effect of priming"},
        {"id": "r9", "doi_o": "10.1000/NEW1", "title_o": "Replication of the existing effect",
         "doi_r": "10.1000/NEW4", "title_r": "A second look at the replication"},
        {"id": "r10", "doi_o": "10.1000/NEW5", "title_o": "An original study of attention",
         "doi_r": "10.1000/NEW6", "title_r": "Replicating an original study of attention"},
        {"id": "r11", "doi_o": "10.1000/EXIST", "title_o": "Existing original",
         "doi_r": "10.1000/NOLANG", "title_r": "Untitled language", "language_r": "NA"},
    ]
    write_csv(csv_path, rows)
    (root / "first-ten.ready.json").write_text(json.dumps(
        {"records": [{"record_id": "pilot-1", "csv_data_row": 1}], "source_csv": {"path": str(csv_path)}}), encoding="utf-8")
    (root / "six-items.create.plan.json").write_text(json.dumps({"operations": []}), encoding="utf-8")
    api = FakeWikidata()
    api.add_work("Q500", "10.1000/EXIST", "Existing original")
    api.add_work("Q501", "10.1000/EXIST2", "E2")
    api.add_journal("Q600", "1234-5678", "Journal of Tests")
    api.add_journal("Q601", "1234-5678", "Mistyped article", article=True)
    api.entities["Q700"] = FakeWikidata._entity("Q700", "Known title without doi")
    api.labels.append(("Q700", "Known title without doi"))
    registry = FakeRegistry({
        "10.1000/NEW1": ("crossref", crossref("Replication of the existing effect")),
        "10.1000/NEW2": ("crossref", crossref("Fine")),
        "10.1000/BOOK": ("crossref", crossref("A book chapter", kind="book-chapter")),
        "10.1000/MISMATCH": ("crossref", crossref("Totally unrelated words appear here")),
        "10.1000/DUPTITLE": ("crossref", crossref("Known title without doi")),
        "10.1000/NEW3": ("crossref", crossref("Hip replacement and the effect of priming",
                                              authors=("Alexander Todorov", "Ann Lee"))),
        "10.1000/NEW4": ("crossref", crossref("A second look at the replication")),
        "10.1000/NEW5": ("crossref", crossref("An original study of attention")),
        "10.1000/NEW6": ("crossref", crossref("Replicating an original study of attention")),
        "10.1000/NOLANG": ("crossref", crossref("Untitled language")),
    })
    return root, csv_path, api, registry


def run_apply(api, page_dir, confirm="page"):
    saved = p.read_json(page_dir / "plan.json")
    args = SimpleNamespace(plan=str(page_dir / "plan.json"), approved_sha256=p.digest(saved), apply=True,
                           data_verified=True, policy_reviewed=True, allow_live=True, auth="bot-password",
                           expected_user="Hb19988", contact="test", confirm=confirm)
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return prompt.removeprefix("Type ").removesuffix(": ")
    with patch("flora_pilot.API", return_value=api), patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", side_effect=answer), patch("flora_create.time.sleep"), \
         patch("sys.stdout", new=io.StringIO()):
        c.apply(args)
    return prompts


def quiet(fn, *a, **kw):
    with patch("sys.stdout", new=io.StringIO()):
        return fn(*a, **kw)


def journal_states(page_dir):
    db = sqlite3.connect(page_dir / "plan.json.journal.sqlite3")
    try:
        return dict(db.execute("SELECT record_id,status FROM attempts"))
    finally:
        db.close()


class PageTests(unittest.TestCase):
    def prepared(self, tmp, count=7):
        root, csv_path, api, registry = workspace(tmp)
        page = quiet(fp.prepare, count, csv_path, root=root, api=api, registry=registry)
        return root, csv_path, api, registry, page

    def test_page_reuses_creates_and_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _, api, _, page = self.prepared(tmp)
            manifest = p.read_json(page / "page.json")
            self.assertEqual(page.name, "page-002")
            self.assertEqual(manifest["ready_ids"], ["r2", "r8"])
            held = {h["record_id"]: " ".join(h["reasons"]) for h in manifest["held"]}
            self.assertEqual(set(held), {"r3", "r4", "r5", "r6", "r7"})
            self.assertIn("no DOI", held["r3"])
            self.assertIn("book-chapter", held["r4"])
            self.assertIn("characters", held["r5"])
            self.assertIn("differs", held["r6"])
            self.assertIn("Q700", held["r7"])
            saved = p.read_json(page / "plan.json")
            created = {op["key"]: op["data"] for op in saved["operations"]}
            self.assertEqual(set(created), {"10.1000/NEW1", "10.1000/NEW3"})  # NEW2 held with its row
            claims = {cl["mainsnak"]["property"]: cl for cl in created["10.1000/NEW1"]["claims"]}
            self.assertEqual(claims["P1433"]["mainsnak"]["datavalue"]["value"]["id"], "Q600")  # not mistyped Q601
            self.assertEqual(claims["P31"]["mainsnak"]["datavalue"]["value"]["id"], "Q13442814")
            names = [cl["mainsnak"]["datavalue"]["value"] for cl in created["10.1000/NEW3"]["claims"]
                     if cl["mainsnak"]["property"] == "P2093"]
            self.assertEqual(names, ["Alexander Todorov", "Ann Lee"])  # placeholder guard no longer bites
            self.assertEqual(saved["works"]["10.1000/EXIST"]["qid"], "Q500")
            review = (page / "REVIEW.md").read_text(encoding="utf-8")
            self.assertIn("Held rows", review)
            self.assertIn("editgroups.toolforge.org/b/CB/", review)

    def test_next_page_waits_for_publish_and_discard_frees_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, csv_path, api, registry, page = self.prepared(tmp)
            with self.assertRaisesRegex(p.Stop, "not published"):
                quiet(fp.prepare, 2, csv_path, root=root, api=api, registry=registry)
            quiet(fp.discard, "page-002", root=root)
            again = quiet(fp.prepare, 7, csv_path, root=root, api=api, registry=registry)
            self.assertEqual(p.read_json(again / "page.json")["ready_ids"], ["r2", "r8"])

    def test_page_publish_one_confirmation_editgroups_and_ledger_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, csv_path, api, registry, page = self.prepared(tmp)
            prompts = run_apply(api, page)
            self.assertEqual(len(prompts), 1)  # only replications are new here: one group, one confirmation
            self.assertEqual(len(api.posts), 2)
            group = c.editgroup_id(p.read_json(page / "plan.json"), "replications")
            for post in api.posts:
                self.assertRegex(post["summary"], r"^FLoRA page 2 \(CSV rows 2-8\), replications: create work item .*"
                                                  r"\(\[\[:toolforge:editgroups/b/CB/[0-9a-f]{32}\|details\]\]\)$")
                self.assertIn(group, post["summary"])
            self.assertEqual(fp.list_pages(root)[-1]["state"], "published")
            # Row r9 cites NEW1, created above but not yet in the (lagging) search index.
            nxt = quiet(fp.prepare, 5, csv_path, root=root, api=api, registry=registry)
            saved = p.read_json(nxt / "plan.json")
            self.assertEqual({op["key"] for op in saved["operations"]}, {"10.1000/NEW4", "10.1000/NEW5", "10.1000/NEW6"})
            self.assertEqual(saved["works"]["10.1000/NEW1"]["qid"], "Q901")
            self.assertEqual(nxt.name, "page-003")
            held = p.read_json(nxt / "page.json")["held"]
            self.assertEqual([(h["record_id"], "language" in h["reasons"][0]) for h in held], [("r11", True)])

    def next_page(self, tmp):
        root, csv_path, api, registry, page = self.prepared(tmp)
        run_apply(api, page)
        nxt = quiet(fp.prepare, 5, csv_path, root=root, api=api, registry=registry)
        api.posts.clear()
        return root, api, nxt

    def test_originals_and_replications_are_separate_groups_originals_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, api, page = self.next_page(tmp)
            prompts = run_apply(api, page)
            self.assertEqual(len(prompts), 2)
            self.assertTrue(prompts[0].endswith("originals: ") and prompts[1].endswith("replications: "))
            saved = p.read_json(page / "plan.json")
            ids = {g: c.editgroup_id(saved, g) for g in ("originals", "replications")}
            self.assertNotEqual(ids["originals"], ids["replications"])
            labels = [json.loads(x["data"])["labels"]["en"]["value"] for x in api.posts]
            self.assertEqual(labels[0], "An original study of attention")
            self.assertIn(ids["originals"], api.posts[0]["summary"])
            self.assertTrue(all(ids["replications"] in x["summary"] for x in api.posts[1:]))
            review = (page / "REVIEW.md").read_text(encoding="utf-8")
            self.assertIn("New items: original studies", review)
            self.assertIn(ids["originals"], review)
            self.assertIn(ids["replications"], review)

    def test_stop_after_originals_then_continue_with_replications(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, api, page = self.next_page(tmp)
            saved = p.read_json(page / "plan.json")
            args = SimpleNamespace(plan=str(page / "plan.json"), approved_sha256=p.digest(saved), apply=True,
                                   data_verified=True, policy_reviewed=True, allow_live=True, auth="bot-password",
                                   expected_user="Hb19988", contact="test", confirm="page")
            answers = iter([f"APPLY wikidata {p.digest(saved)[:12]} originals", "no"])
            with patch("flora_pilot.API", return_value=api), patch("sys.stdin.isatty", return_value=True), \
                 patch("builtins.input", side_effect=lambda _: next(answers)), patch("flora_create.time.sleep"), \
                 patch("sys.stdout", new=io.StringIO()):
                with self.assertRaisesRegex(p.Stop, "Stopped before the replications group"):
                    c.apply(args)
            self.assertEqual(len(api.posts), 1)
            self.assertEqual(fp.list_pages(root)[-1]["state"], "partly published (1/3)")
            prompts = run_apply(api, page)
            self.assertEqual(len(prompts), 1)
            self.assertTrue(prompts[0].endswith("replications: "))
            self.assertEqual(len(api.posts), 3)

    def test_page_mode_needs_autoconfirmed_but_each_mode_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _, api, _, page = self.prepared(tmp)
            api.rights = ["edit", "createpage"]
            with self.assertRaisesRegex(p.Stop, "autoconfirmed"):
                run_apply(api, page)
            self.assertEqual(api.posts, [])
            prompts = run_apply(api, page, confirm="each")
            self.assertEqual(len(api.posts), 2)
            self.assertEqual(len(prompts), 3)  # APPLY + one SAVE per item

    def test_maxlag_refusal_mid_page_is_released_and_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _, api, _, page = self.prepared(tmp)
            api.lag_on_post = {2}
            with self.assertRaisesRegex(p.Stop, "NOT saved"):
                run_apply(api, page)
            self.assertEqual(list(journal_states(page).values()), ["verified"])
            self.assertEqual(fp.list_pages(root)[-1]["state"], "partly published (1/2)")
            run_apply(api, page)
            self.assertEqual(len(api.posts), 2)
            self.assertEqual(len({json.loads(x["data"])["labels"]["en"]["value"] for x in api.posts}), 2)

    def test_saved_but_unverified_item_is_rechecked_not_recreated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _, api, _, page = self.prepared(tmp)
            real_call = api.call

            def flaky(params, **kw):
                if params["action"] == "wbgetentities" and params.get("props") == "info|claims|labels|descriptions" \
                        and len(api.posts) == 1 and not getattr(api, "failed_once", False):
                    api.failed_once = True
                    raise p.Stop("Network failure.")
                return real_call(params, **kw)
            api.call = flaky
            with self.assertRaises(p.Stop):
                run_apply(api, page)
            self.assertIn("written_unverified", journal_states(page).values())
            self.assertEqual(fp.list_pages(root)[-1]["state"], "saved, needs re-check")
            run_apply(api, page)
            self.assertEqual(len(api.posts), 2)
            self.assertEqual(set(journal_states(page).values()), {"verified"})

    def test_unrelated_edit_to_reused_item_does_not_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _, api, _, page = self.prepared(tmp)
            api.entities["Q500"]["lastrevid"] += 1
            run_apply(api, page)
            self.assertEqual(len(api.posts), 2)

    def test_new_duplicate_since_preparation_still_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _, api, _, page = self.prepared(tmp)
            api.add_work("Q777", "10.1000/NEW1", "Someone else created it")
            with self.assertRaisesRegex(p.Stop, "differs"):
                run_apply(api, page)
            self.assertEqual(api.posts, [])


class RefreshPilotTests(unittest.TestCase):
    def pilot(self, tmp):
        root, csv_path, api, registry = workspace(tmp)
        rows = c.csv_rows(csv_path, 2, 1)
        data, _, _ = fp.build_page(rows, api, registry, {}, 1, csv_path, "0" * 64)
        saved = c.plan(data, api, p.model_for("wikidata"))
        plan_path = root / "six-items.create.plan.json"
        plan_path.unlink()
        p.write_new(root / "first-ten.ready.json.tmp", data)
        (root / "first-ten.ready.json").unlink()
        (root / "first-ten.ready.json.tmp").rename(root / "first-ten.ready.json")
        p.write_new(plan_path, saved)
        (root / "six-items.create.plan.sha256").write_text(p.digest(saved) + "\n", encoding="utf-8")
        (root / "CREATION_REVIEW.md").write_text(f"Plan SHA-256: {p.digest(saved)}\n", encoding="utf-8")
        return root, api, saved

    def test_refresh_gives_new_window_same_creations_and_archives_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, api, old = self.pilot(tmp)
            same = quiet(fp.refresh_pilot, root=root, api=api)
            new = p.read_json(root / "six-items.create.plan.json")
            self.assertTrue(same)
            self.assertNotEqual(new["batch_id"], old["batch_id"])
            self.assertEqual(new["operations"], old["operations"])
            digest = (root / "six-items.create.plan.sha256").read_text().strip()
            self.assertEqual(digest, p.digest(new))
            self.assertIn(digest, (root / "CREATION_REVIEW.md").read_text())
            self.assertEqual(len(list((root / "superseded").glob("pilot-plan-*/six-items.create.plan.json"))), 1)

    def test_refresh_refused_after_any_save_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, api, old = self.pilot(tmp)
            journal = p.Journal(str(root / "six-items.create.plan.json") + ".journal.sqlite3", old, max_attempts=2)
            journal.reserve("create:x", {"work_key": "x"})
            journal.close()
            with self.assertRaisesRegex(p.Stop, "save attempts"):
                quiet(fp.refresh_pilot, root=root, api=api)


class MetadataTests(unittest.TestCase):
    def test_datacite_registration_and_personal_names(self):
        meta = fp.from_datacite({"types": {"resourceTypeGeneral": "StudyRegistration"}, "publicationYear": "2021",
                                 "titles": [{"title": "Plan <i>A</i>"}],
                                 "creators": [{"name": "Doe, Jane", "nameType": "Personal"}, {"name": "Lab X", "nameType": "Organizational"}]})
        self.assertEqual((meta["cls"], meta["title"], meta["year"]), ("preregistration", "Plan A", 2021))
        self.assertEqual(meta["authors"], ["Jane Doe", "Lab X"])
        self.assertEqual(fp.description(meta, "10.17605/OSF.IO/ABC"), "research preregistration deposited on OSF in 2021")

    def test_crossref_preprint_and_unsupported_type(self):
        self.assertEqual(fp.from_crossref(crossref("T", kind="posted-content", subtype="preprint"))["cls"], "preprint")
        with self.assertRaises(fp.Hold):
            fp.from_crossref(crossref("T", kind="proceedings-article"))

    def test_titles_and_language(self):
        self.assertTrue(fp.titles_agree("Ego depletion", "Ego depletion: a preregistered replication"))
        self.assertFalse(fp.titles_agree("Completely different subject", "Totally unrelated words appear here"))

    def test_duplicate_author_names_are_held(self):
        meta = fp.from_crossref(crossref("T", authors=("Ann Lee", "Ann Lee")))
        with self.assertRaises(fp.Hold):
            fp.creation_attributes(meta, "https://api.crossref.org/works/x", None)

    def test_bibliographic_text_is_not_placeholder_checked(self):
        self.assertEqual(p.data_text("Alexander Todorov", "author"), "Alexander Todorov")
        self.assertEqual(p.data_text("Total hip replacement", "title"), "Total hip replacement")
        with self.assertRaises(p.Stop):
            p.required_text("TODO", "review note")
        with self.assertRaises(p.Stop):
            p.data_text("bad\x07value", "title")

    def test_page_inputs_up_to_fifty_rows(self):
        base = {"schema_version": 1, "environment": "wikidata", "items_only": True,
                "dataset": {"name": "FLoRA", "version": "v", "url": "https://forrt.org/flora/"},
                "page": {"number": 2, "first_row": 1, "last_row": 50, "rows": 50}}
        p.validate_dataset({**base, "records": [{"record_id": str(i)} for i in range(50)]}, "wikidata")
        with self.assertRaises(p.Stop):
            p.validate_dataset({**base, "records": [{"record_id": str(i)} for i in range(51)]}, "wikidata")
        with self.assertRaises(p.Stop):  # without a page block the ten-row pilot limit stays
            p.validate_dataset({**base, "page": None, "records": [{"record_id": str(i)} for i in range(11)]}, "wikidata")

    def test_csv_rows_selects_a_range_with_row_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "f.csv"
            write_csv(path, [{"id": str(i)} for i in range(1, 8)])
            self.assertEqual([(n, r["id"]) for n, r in c.csv_rows(path, 3, 2)], [(3, "3"), (4, "4")])


if __name__ == "__main__":
    unittest.main()
