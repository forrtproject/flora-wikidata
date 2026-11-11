import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flora_pilot as p


def example_record(i=1):
    return {
        "record_id": f"synthetic-{i:02d}",
        "replication": {"qid": f"Q{900000000 + i}", "doi": "", "title": f"SYNTHETIC replication {i}"},
        "original": {"qid": f"Q{910000000 + i}", "doi": "", "title": f"SYNTHETIC original {i}"},
        "cito": {"qid": "Q920000001", "label": "SYNTHETIC qualifies"},
        "reference_url": f"https://forrt.org/flora/#offline-demo-nanopub-{i}",
        "evidence_url": f"https://forrt.org/flora/#offline-demo-evidence-{i}",
        "retrieved": "2026-11-11", "verified_at": "2026-11-11",
        "verified_by": "SYNTHETIC FIXTURE - NOT A REAL REVIEW",
        "review_note": "SYNTHETIC data solely for offline software testing.",
        "model_review_note": "SYNTHETIC model solely for offline software testing.",
        "whole_relation_appropriate": True,
    }


def example_data(n=10):
    return {"schema_version": 1, "environment": "test",
            "dataset": {"name": "SYNTHETIC OFFLINE DEMO", "version": "fixture-v1",
                        "url": "https://forrt.org/flora/#synthetic-offline-demo", "cc0_compatible": True},
            "records": [example_record(i) for i in range(1, n + 1)]}


def entity(identifier, label="", claims=None):
    return {"id": identifier, "lastrevid": 100, "labels": {"en": {"language": "en", "value": label}},
            "claims": claims or {}}


def fixture(n=10):
    model = {"environment": "test", "properties": {k: f"P{9001 + i}" for i, k in enumerate(p.PROPERTIES)},
             "expected_labels": {k: "SYNTHETIC " + k for k in p.PROPERTIES}}
    entities = {}
    for role, prop in model["properties"].items():
        entities[prop] = {**entity(prop, model["expected_labels"][role]), "datatype": p.DATATYPES[role]}
    data = example_data(n)
    for r in data["records"]:
        for role in ("replication", "original"):
            entities[r[role]["qid"]] = entity(r[role]["qid"], r[role]["title"])
        entities[r["cito"]["qid"]] = entity(r["cito"]["qid"], r["cito"]["label"])
    return data, model, {"entities": entities}


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.r = example_record()
        self.e = entity(self.r["replication"]["qid"])
        self.props = p.PROPERTIES

    def propose(self):
        return p.proposal(self.e, self.r["original"]["qid"], self.r["cito"]["qid"], self.r, self.props)

    def install(self, claim):
        claim["id"] = self.e["id"] + "$fixture-guid"
        self.e["claims"]["P2860"] = [claim]

    def test_add_is_one_atomic_statement_with_qualifier_and_two_references(self):
        result = self.propose()
        self.assertEqual(result["status"], "add")
        self.assertEqual(len(result["after"]["references"]), 2)
        self.assertEqual(p.item_value(result["after"]["qualifiers"]["P3712"][0]), self.r["cito"]["qid"])

    def test_idempotent_even_if_retrieved_date_changes(self):
        self.install(self.propose()["after"])
        self.r["retrieved"] = "2026-11-10"
        self.assertEqual(self.propose()["status"], "skip")

    def test_adds_to_plain_citation_preserving_other_reference(self):
        claim = self.propose()["after"]
        del claim["qualifiers"]
        del claim["qualifiers-order"]
        existing_ref = {"hash": "keep", "snaks": {"P854": [p.snak("P854", "https://doi.org/10.1234/other", "url")]}}
        claim["references"] = [existing_ref]
        self.install(claim)
        before = copy.deepcopy(self.e)
        result = self.propose()
        self.assertEqual(result["status"], "update")
        self.assertEqual(result["after"]["references"][0], existing_ref)
        self.assertEqual(before, self.e)

    def test_conflicting_goal_stops(self):
        claim = self.propose()["after"]
        claim["qualifiers"]["P3712"] = [p.snak("P3712", "Q2")]
        self.install(claim)
        with self.assertRaises(p.Stop): self.propose()

    def test_scoped_qualifier_stops(self):
        claim = self.propose()["after"]
        claim["qualifiers"]["P304"] = [p.snak("P304", "3", "string")]
        self.install(claim)
        with self.assertRaises(p.Stop): self.propose()

    def test_deprecated_stops(self):
        claim = self.propose()["after"]
        claim["rank"] = "deprecated"
        self.install(claim)
        with self.assertRaises(p.Stop): self.propose()

    def test_multiple_citations_stops(self):
        self.install(self.propose()["after"])
        self.e["claims"]["P2860"] *= 2
        with self.assertRaises(p.Stop): self.propose()

    def test_unchanged_preferred_rank_preserved(self):
        claim = self.propose()["after"]
        claim["rank"] = "preferred"
        claim["references"] = []
        self.install(claim)
        self.assertEqual(self.propose()["after"]["rank"], "preferred")

    def test_equal_reference_urls_not_duplicated(self):
        self.r["evidence_url"] = self.r["reference_url"]
        self.assertEqual(len(self.propose()["after"]["references"]), 1)

    def test_nanopublication_in_generic_reference_not_duplicated(self):
        claim = self.propose()["after"]
        ref = claim["references"][0]
        ref["snaks"]["P854"] = ref["snaks"].pop("P12545")
        ref["snaks"]["P854"][0]["property"] = "P854"
        ref["snaks-order"] = ["P854", "P813"]
        self.install(claim)
        self.assertEqual(self.propose()["status"], "skip")


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.data, self.model, self.fixture = fixture()
        self.api = p.FixtureAPI(self.fixture, "test")

    def plan(self, offline=True):
        return p.make_plan(self.data, self.api, self.model, offline=offline)

    def test_ten_edit_preview(self):
        self.assertEqual([r["status"] for r in self.plan()["rows"]], ["add"] * 10)

    def test_eleven_rejected(self):
        self.data["records"].append(example_record(11))
        with self.assertRaises(p.Stop): self.plan()

    def test_offline_plan_cannot_apply(self):
        plan = self.plan()
        with self.assertRaisesRegex(p.Stop, "NEVER"): p.validate_plan(plan, p.digest(plan))

    def test_hash_detects_mutation(self):
        plan = self.plan(False)
        original_hash = p.digest(plan)
        plan["rows"][0]["goal"] = "Q1"
        with self.assertRaises(p.Stop): p.validate_plan(plan, original_hash)

    def test_expired_plan_rejected(self):
        plan = self.plan(False)
        plan["created_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        with self.assertRaises(p.Stop): p.validate_plan(plan, p.digest(plan))

    def test_endpoint_substitution_rejected(self):
        plan = self.plan(False)
        plan["endpoint"] = p.TARGETS["wikidata"]
        with self.assertRaises(p.Stop): p.validate_plan(plan, p.digest(plan))

    def test_unverified_data_blocks(self):
        self.data["dataset"]["cc0_compatible"] = False
        self.assertTrue(all(r["status"] == "blocked" for r in self.plan()["rows"]))

    def test_finding_scope_blocks(self):
        self.data["records"][0]["whole_relation_appropriate"] = False
        self.assertEqual(self.plan()["rows"][0]["status"], "blocked")

    def test_duplicate_record_id_rejected(self):
        self.data["records"][1]["record_id"] = self.data["records"][0]["record_id"]
        with self.assertRaises(p.Stop): self.plan()

    def test_repeated_subject_blocks(self):
        self.data["records"][1]["replication"] = self.data["records"][0]["replication"]
        self.assertEqual(self.plan()["rows"][1]["status"], "blocked")

    def test_changed_revision_stops(self):
        plan = self.plan(False)
        self.fixture["entities"][plan["rows"][0]["subject"]]["lastrevid"] += 1
        with self.assertRaises(p.Stop): p.recheck(self.api, plan, plan["rows"][0])

    def test_recomputed_payload_stops_tampering(self):
        plan = self.plan(False)
        plan["rows"][0]["after"]["rank"] = "preferred"
        with self.assertRaises(p.Stop): p.recheck(self.api, plan, plan["rows"][0])

    def test_same_item_pair_blocks(self):
        self.data["records"][0]["original"] = self.data["records"][0]["replication"]
        self.assertEqual(self.plan()["rows"][0]["status"], "blocked")

    def test_wrong_test_property_type_stops(self):
        self.fixture["entities"][self.model["properties"]["goal"]]["datatype"] = "string"
        with self.assertRaises(p.Stop): self.plan()

    def test_wrong_cito_label_blocks(self):
        self.data["records"][0]["cito"]["label"] = "different"
        self.assertEqual(self.plan()["rows"][0]["status"], "blocked")


class TransportTests(unittest.TestCase):
    def api(self, responses):
        session = Mock()
        session.headers = {}
        session.request.side_effect = responses
        return p.API("wikidata", "test contact", session), session

    @staticmethod
    def response(data, status=200, headers=None):
        r = Mock(status_code=status, headers=headers or {})
        r.json.return_value = data
        return r

    @patch("flora_pilot.time.sleep")
    def test_no_write_retry_on_timeout(self, sleep):
        api, session = self.api([p.requests.Timeout()])
        api.username = "Account"
        with self.assertRaises(p.Stop):
            api.call({"action": "wbeditentity"}, post=True, write=True)
        self.assertEqual(session.request.call_count, 1)

    @patch("flora_pilot.time.sleep")
    def test_no_write_retry_on_maxlag(self, sleep):
        api, session = self.api([self.response({"error": {"code": "maxlag"}})])
        api.username = "Account"
        with self.assertRaises(p.Stop): api.call({"action": "wbeditentity"}, post=True, write=True)
        self.assertEqual(session.request.call_count, 1)

    @patch("flora_pilot.time.sleep")
    def test_maxlag_read_waits_at_least_five_seconds(self, sleep):
        api, session = self.api([self.response({"error": {"code": "maxlag"}}), self.response({"query": {}})])
        api.call({"action": "query"})
        self.assertEqual(session.request.call_count, 2)
        sleep.assert_any_call(5)

    @patch("flora_pilot.time.sleep")
    def test_persistent_maxlag_has_retry_guidance(self, sleep):
        api, session = self.api([self.response({"error": {"code": "maxlag"}})] * 4)
        with self.assertRaisesRegex(p.Stop, "run the launcher again"):
            api.call({"action": "query"})
        self.assertEqual(session.request.call_count, 4)

    @patch("flora_pilot.time.sleep")
    def test_login_retries_only_explicit_maxlag(self, sleep):
        api, session = self.api([self.response({"error": {"code": "maxlag"}}, headers={"Retry-After":"45"}),
                                 self.response({"login":{"result":"Success"}})])
        self.assertEqual(api.call({"action": "login"}, post=True)["login"]["result"], "Success")
        self.assertEqual(session.request.call_count, 2)
        sleep.assert_any_call(45)

    @patch("flora_pilot.time.sleep")
    def test_login_maxlag_retry_budget(self, sleep):
        api, session = self.api([self.response({"error": {"code": "maxlag"}})] * 6)
        with self.assertRaisesRegex(p.Stop, "not a password rejection"):
            api.call({"action": "login"}, post=True)
        self.assertEqual(session.request.call_count, 6)

    @patch("flora_pilot.time.sleep")
    def test_login_does_not_retry_ambiguous_or_failed_responses(self, sleep):
        for response in [p.requests.Timeout(), self.response({},503), self.response({},429),
                         self.response({"error":{"code":"badtoken"}})]:
            api, session = self.api([response])
            with self.assertRaises(p.Stop): api.call({"action":"login"}, post=True)
            self.assertEqual(session.request.call_count,1)
        api, session = self.api([self.response({"login":{"result":"Failed"}})])
        self.assertEqual(api.call({"action":"login"},post=True)["login"]["result"],"Failed")
        self.assertEqual(session.request.call_count,1)

    @patch("flora_pilot.time.sleep")
    def test_lag_diagnostics_do_not_expose_error_info(self, sleep):
        api, session = self.api([self.response({"error":{"code":"maxlag","info":"SECRET",
            "type":"wikibase-queryservice","lag":24.7,"queryserviceLag":1483}})])
        with self.assertRaises(p.Stop) as exc: api.call({"action":"query"},post=True)
        self.assertIn("24.7 minutes behind",str(exc.exception))
        self.assertNotIn("SECRET",str(exc.exception))

    @patch("flora_pilot.time.sleep")
    def test_login_long_retry_after_stops(self, sleep):
        api, session = self.api([self.response({"error":{"code":"maxlag"}},headers={"Retry-After":"120"})])
        with self.assertRaisesRegex(p.Stop,"long pause"):
            api.call({"action":"login"},post=True)
        self.assertEqual(session.request.call_count,1)

    @patch("flora_pilot.time.sleep")
    def test_read_retries_respect_retry_after(self, sleep):
        api, session = self.api([self.response({}, 429, {"Retry-After": "12"}), self.response({"query": {}})])
        api.call({"action": "query"})
        self.assertEqual(session.request.call_count, 2)
        sleep.assert_any_call(12)

    @patch("flora_pilot.time.sleep")
    def test_write_parameters_and_no_redirects(self, sleep):
        api, session = self.api([self.response({"success": 1})])
        api.username = "Account"
        row = {"subject": "Q1", "after": {"type": "statement"}, "revisions": {"Q1": 123}, "record_id": "r1"}
        p.write_one(api, row, {"batch_id": "batch"}, "SECRET")
        kwargs = session.request.call_args.kwargs
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["data"]["baserevid"], 123)
        self.assertEqual(kwargs["data"]["assertuser"], "Account")
        self.assertNotIn("bot", kwargs["data"])
        self.assertEqual(json.loads(kwargs["data"]["data"]), {"claims": [row["after"]]})

    @patch("flora_pilot.time.sleep")
    def test_sensitive_api_error_text_not_exposed(self, sleep):
        api, _ = self.api([self.response({"error": {"code": "badtoken", "info": "SECRET"}})])
        with self.assertRaises(p.Stop) as exc: api.call({"action": "query"})
        self.assertNotIn("SECRET", str(exc.exception))

    def test_oauth2_account_check(self):
        api, _ = self.api([])
        api.call = Mock(return_value={"query": {"userinfo": {"name": "Account", "id": 1, "rights": ["edit", "autoconfirmed"]}}})
        with patch.dict(os.environ, {"FLORA_WD_OAUTH2_TOKEN": "SECRET"}):
            api.authenticate("oauth2", "Account")
        self.assertEqual(api.username, "Account")
        self.assertEqual(api.session.headers["Authorization"], "Bearer SECRET")

    def test_wrong_account_stops(self):
        api, _ = self.api([])
        api.call = Mock(return_value={"query": {"userinfo": {"name": "Wrong", "id": 1, "rights": ["edit", "autoconfirmed"]}}})
        with patch.dict(os.environ, {"FLORA_WD_OAUTH2_TOKEN": "SECRET"}):
            with self.assertRaises(p.Stop): api.authenticate("oauth2", "Account")

    def test_unconfirmed_production_stops(self):
        api, _ = self.api([])
        api.call = Mock(return_value={"query": {"userinfo": {"name": "Account", "id": 1, "rights": ["edit"]}}})
        with patch.dict(os.environ, {"FLORA_WD_OAUTH2_TOKEN": "SECRET"}):
            with self.assertRaises(p.Stop): api.authenticate("oauth2", "Account")

    def test_individually_reviewed_mode_checks_edit_right_without_autoconfirmed(self):
        api, _ = self.api([])
        api.call = Mock(return_value={"query": {"userinfo": {"name": "Account", "id": 1, "rights": ["edit", "createpage"]}}})
        with patch.dict(os.environ, {"FLORA_WD_OAUTH2_TOKEN": "SECRET"}):
            api.authenticate("oauth2", "Account", require_autoconfirmed=False)
        self.assertEqual(api.username, "Account")

    def test_bot_password_flow_uses_login_token_and_checks_real_user(self):
        api, _ = self.api([])
        api.call = Mock(side_effect=[
            {"query": {"tokens": {"logintoken": "LOGIN-TOKEN"}}},
            {"login": {"result": "Success"}},
            {"query": {"userinfo": {"name": "Account", "id": 1, "rights": ["edit", "autoconfirmed"]}}},
        ])
        with patch.dict(os.environ, {"FLORA_WD_BOT_USERNAME": "Account@FLoRA", "FLORA_WD_BOT_PASSWORD": "SECRET"}):
            api.authenticate("bot-password", "Account")
        self.assertEqual(api.call.call_args_list[1].args[0]["lgtoken"], "LOGIN-TOKEN")
        self.assertTrue(api.call.call_args_list[1].kwargs["post"])
        self.assertEqual(api.username, "Account")

    def test_bot_password_rejects_main_account_login_name(self):
        api, _ = self.api([])
        api.call = Mock()
        with patch.dict(os.environ, {"FLORA_WD_BOT_USERNAME": "Account"}):
            with self.assertRaises(p.Stop): api.authenticate("bot-password", "Account")
        api.call.assert_not_called()

    def test_oauth1_signed_session_setup(self):
        api, _ = self.api([])
        api.call = Mock(return_value={"query": {"userinfo": {"name": "Account", "id": 1, "rights": ["edit", "autoconfirmed"]}}})
        values = {"FLORA_WD_" + name: "SYNTHETIC" for name in ("CONSUMER_KEY", "CONSUMER_SECRET", "ACCESS_TOKEN", "ACCESS_SECRET")}
        with patch.dict(os.environ, values):
            api.authenticate("oauth1", "Account")
        from requests_oauthlib import OAuth1
        self.assertIsInstance(api.session.auth, OAuth1)

    def test_blocked_account_stops(self):
        api, _ = self.api([])
        api.call = Mock(return_value={"query": {"userinfo": {"name": "Account", "id": 1,
            "rights": ["edit", "autoconfirmed"], "blockid": 123}}})
        with patch.dict(os.environ, {"FLORA_WD_OAUTH2_TOKEN": "SECRET"}):
            with self.assertRaises(p.Stop): api.authenticate("oauth2", "Account")

    @patch("flora_pilot.time.sleep")
    def test_redirect_rejected(self, sleep):
        api, session = self.api([self.response({}, 302, {"Location": "https://example.org"})])
        with self.assertRaises(p.Stop): api.call({"action": "query"})
        self.assertEqual(session.request.call_count, 1)


class ReconciliationTests(unittest.TestCase):
    def test_doi_normalization(self):
        self.assertEqual(p.doi("https://doi.org/10.1234%2FabC"), "10.1234/ABC")
        self.assertEqual(p.doi("doi:10.1234/ABC"), "10.1234/ABC")

    def make_api(self, candidates):
        api = p.API("wikidata", "test contact")
        api.call = Mock(return_value={"query": {"search": [{"title": x} for x in candidates]}})
        api.entity = lambda x: entity(x, claims={"P356": [{"rank": "normal", "mainsnak": p.snak("P356", "10.1234/ABC", "external-id")}]})
        return api

    def test_unique_exact_doi(self):
        api = self.make_api(["Q1"])
        self.assertEqual(api.resolve({"doi": "10.1234/abc"}, p.PROPERTIES)["id"], "Q1")
        self.assertEqual(api.call.call_args.args[0]['srsearch'], 'haswbstatement:P356=10.1234/ABC')

    def test_identifier_query_uses_unquoted_cirrus_syntax(self):
        self.assertEqual(p.identifier_search('P356', '10.1016/0010-0285(72)90003-5'),
                         'haswbstatement:P356=10.1016/0010-0285(72)90003-5')

    def test_unsafe_identifier_query_is_rejected(self):
        for value in ['10.1234/A B', '10.1234/"A"', '10.1234/A|B']:
            with self.assertRaises(p.Stop): p.identifier_search('P356', value)

    def test_multiple_doi_matches_stops(self):
        with self.assertRaises(p.Stop): self.make_api(["Q1", "Q2"]).resolve({"doi": "10.1234/abc"}, p.PROPERTIES)

    def test_missing_doi_does_not_create(self):
        with self.assertRaises(p.Stop): self.make_api([]).resolve({"doi": "10.1234/abc"}, p.PROPERTIES)

    def test_explicit_qid_doi_disagreement_stops(self):
        with self.assertRaises(p.Stop): self.make_api(["Q1"]).resolve({"qid": "Q2", "doi": "10.1234/abc"}, p.PROPERTIES)

    def test_test_dois_rejected(self):
        api = p.API("test", "test contact")
        with self.assertRaises(p.Stop): api.resolve({"qid": "Q1", "doi": "10.1234/abc"}, p.PROPERTIES)


class JournalTests(unittest.TestCase):
    def test_ten_reservations_cap_and_duplicate_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = p.Journal(Path(tmp) / "audit.sqlite3", {"batch": "one"})
            for i in range(10): journal.reserve(str(i), {})
            with self.assertRaises(p.Stop): journal.reserve("11", {})
            self.assertEqual(len(journal.states()), 10)
            journal.db.close()

    def test_pending_persists_across_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.sqlite3"
            j = p.Journal(path, {"batch": "one"})
            j.reserve("1", {})
            j.db.close()
            j = p.Journal(path, {"batch": "one"})
            self.assertEqual(j.states(), {"1": "pending"})
            with self.assertRaises(sqlite3.IntegrityError): j.reserve("1", {})
            j.db.close()

    def test_other_plan_cannot_reuse_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.sqlite3"
            j = p.Journal(path, {"batch": "one"})
            j.db.close()
            with self.assertRaises(p.Stop): p.Journal(path, {"batch": "two"})


class FakeWritingAPI(p.FixtureAPI):
    def __init__(self, values):
        super().__init__(values, "test")
        self.endpoint = p.TARGETS["test"]
        self.writes = []

    def authenticate(self, method, expected_user):
        return {"name": expected_user, "ratelimits": {}}

    def csrf(self):
        return "SECRET-TEST-TOKEN"

    def call(self, params, **kwargs):
        self.writes.append(copy.deepcopy(params))
        subject = self.fixture["entities"][params["id"]]
        claim = json.loads(params["data"])["claims"][0]
        claim["id"] = params["id"] + "$synthetic-guid"
        subject["claims"].setdefault(claim["mainsnak"]["property"], []).append(claim)
        subject["lastrevid"] += 1
        return {"success": 1, "entity": copy.deepcopy(subject)}


class ApplyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.data, self.model, self.values = fixture()
        self.api = FakeWritingAPI(self.values)
        self.plan = p.make_plan(self.data, self.api, self.model, offline=False)

    def args(self, plan_path):
        return SimpleNamespace(plan=str(plan_path), approved_sha256=p.digest(self.plan),
            apply=True, data_verified=True, policy_reviewed=True, allow_live=False,
            contact="test contact", auth="oauth2", expected_user="Test account")

    def consent(self, prompt):
        if prompt.startswith("Type APPLY"):
            return f"APPLY test {p.digest(self.plan)[:12]}"
        if prompt.startswith("Type EDIT"):
            return " ".join(prompt.split()[1:3])
        raise AssertionError("Unexpected prompt")

    def test_ten_mock_writes_verify_and_resume_without_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            p.write_new(path, self.plan)
            with patch("flora_pilot.API", return_value=self.api), patch("sys.stdin.isatty", return_value=True), \
                 patch("builtins.input", side_effect=self.consent), patch("flora_pilot.time.sleep"), \
                 patch("sys.stdout", new=io.StringIO()):
                p.apply_plan(self.args(path))
                p.apply_plan(self.args(path))
            self.assertEqual(len(self.api.writes), 10)
            log = Path(str(path) + ".audit.jsonl").read_text()
            self.assertNotIn("SECRET-TEST-TOKEN", log)
            events = [json.loads(line) for line in log.splitlines()]
            self.assertEqual(sum(e["event"] == "verified" for e in events), 10)
            self.assertTrue(all(e["diff_url"] for e in events if e["event"] == "verified"))

    def test_no_flags_means_no_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            p.write_new(path, self.plan)
            args = self.args(path)
            args.data_verified = False
            with patch("flora_pilot.API") as api:
                with self.assertRaises(p.Stop): p.apply_plan(args)
                api.assert_not_called()

    def test_denied_individual_confirmation_makes_no_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            p.write_new(path, self.plan)
            with patch("flora_pilot.API", return_value=self.api), patch("sys.stdin.isatty", return_value=True), \
                 patch("builtins.input", side_effect=[f"APPLY test {p.digest(self.plan)[:12]}", "no"]), \
                 patch("sys.stdout", new=io.StringIO()):
                with self.assertRaises(p.Stop): p.apply_plan(self.args(path))
            self.assertEqual(len(self.api.writes), 0)

    def test_uncertain_attempt_blocks_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            p.write_new(path, self.plan)
            self.api.call = Mock(side_effect=p.Stop("Unknown write result"))
            with patch("flora_pilot.API", return_value=self.api), patch("sys.stdin.isatty", return_value=True), \
                 patch("builtins.input", side_effect=self.consent), patch("sys.stdout", new=io.StringIO()):
                with self.assertRaises(p.Stop): p.apply_plan(self.args(path))
                with self.assertRaisesRegex(p.Stop, "earlier write attempt"): p.apply_plan(self.args(path))
            self.assertEqual(self.api.call.call_count, 1)

    def test_no_terminal_blocks_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            p.write_new(path, self.plan)
            with patch("sys.stdin.isatty", return_value=False):
                with self.assertRaises(p.Stop): p.apply_plan(self.args(path))

    def test_production_requires_separate_live_flag(self):
        self.plan["environment"] = "wikidata"
        self.plan["endpoint"] = p.TARGETS["wikidata"]
        self.plan["model"] = {"environment": "wikidata", "properties": p.PROPERTIES}
        self.data["environment"] = "wikidata"
        self.plan["input_sha256"] = p.digest(self.data)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            p.write_new(path, self.plan)
            with patch("flora_pilot.API") as api:
                with self.assertRaisesRegex(p.Stop, "allow-live"): p.apply_plan(self.args(path))
                api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
