#!/usr/bin/env python3
"""Human-reviewed, existing-item-only FLoRA pilot. No command defaults to writing."""
from __future__ import annotations

import argparse
import copy
from contextlib import closing
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timezone
from urllib.parse import unquote, urlsplit
import uuid

import requests

VERSION = "1.0.0"
MAX_EDITS = 10
# Page mode (item creation only): at most this many CSV rows per page/edit group.
PAGE_MAX_ROWS = 50
TARGETS = {
    "wikidata": "https://www.wikidata.org/w/api.php",
    "test": "https://test.wikidata.org/w/api.php",
}
PROPERTIES = {"relation": "P2860", "goal": "P3712", "reference": "P854", "nanopublication": "P12545",
              "retrieved": "P813", "doi": "P356"}
DATATYPES = {"relation": "wikibase-item", "goal": "wikibase-item",
             "reference": "url", "nanopublication": "url", "retrieved": "time", "doi": "external-id"}


class Stop(Exception):
    """A deliberate fail-closed stop; messages must contain no credentials."""


class Lagged(Stop):
    """The server answered with an explicit maxlag error: the request was NOT processed."""


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def load_local_credentials(prefix, path=None):
    """Read only this tool's credential keys, only when authentication is requested."""
    if prefix not in ("FLORA_WD_", "FLORA_TEST_"):
        raise Stop("Unknown credential environment.")
    credential_path = Path(path) if path is not None else Path(__file__).resolve().with_name(".env")
    if not credential_path.exists():
        return {}
    names = {prefix + key for key in ("BOT_USERNAME", "BOT_PASSWORD", "OAUTH2_TOKEN",
             "CONSUMER_KEY", "CONSUMER_SECRET", "ACCESS_TOKEN", "ACCESS_SECRET")}
    values = {}
    for line in credential_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key not in names:
            continue
        if not separator or key in values:
            raise Stop("Invalid or repeated credential key in .env; inspect it locally.")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def write_new(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def required_text(value, name):
    if not isinstance(value, str) or not value.strip() or re.search(
            r"REPLACE|YOUR_|TODO|<[^>]+>", value, re.I):
        raise Stop(f"{name}: provide a real reviewed value, not a placeholder.")
    if any(ord(c) < 32 for c in value):
        raise Stop(f"{name}: control characters are not allowed.")
    return value.strip()


def data_text(value, name):
    """Bibliographic values (titles, names, pages): non-empty, no control characters.

    Unlike required_text, no placeholder-word check: real titles and surnames
    contain words such as "replacement" or "Todorov".
    """
    if not isinstance(value, str) or not value.strip():
        raise Stop(f"{name}: empty value.")
    if any(ord(c) < 32 for c in value):
        raise Stop(f"{name}: control characters are not allowed.")
    return value.strip()


def qid(value):
    if not isinstance(value, str) or not re.fullmatch(r"Q[1-9][0-9]*", value):
        raise Stop("An item ID must be Q followed by a positive integer.")
    return value


def url(value):
    required_text(value, "URL")
    u = urlsplit(value)
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        raise Stop("Evidence URLs must be public HTTP(S) URLs without credentials.")
    if u.hostname in ("example.com", "example.org", "localhost") or u.hostname.endswith(".invalid"):
        raise Stop("Replace example evidence URLs with actual public sources.")
    return value


def iso_date(value):
    try:
        d = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise Stop("Dates must be YYYY-MM-DD.") from None
    if d > datetime.now(timezone.utc).date():
        raise Stop("Review/retrieval dates cannot be in the future.")
    return d.isoformat()


def doi(value):
    if not value:
        return ""
    value = value.strip()
    if re.match(r"https?://(?:dx\.)?doi\.org/", value, re.I):
        value = unquote(re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.I))
    value = re.sub(r"^doi:\s*", "", value, flags=re.I).upper()
    if not re.fullmatch(r"10\.[0-9]{4,9}/\S+", value) or any(ord(c) < 32 for c in value):
        raise Stop("Invalid DOI; use a bare DOI or doi.org URL.")
    return value


def identifier_search(prop, value):
    # CirrusSearch expects haswbstatement:P356=VALUE, not P356="VALUE".
    # Reject values requiring search escaping; never misread a syntax failure as absence.
    if not re.fullmatch(r"P[1-9][0-9]*", prop) or not isinstance(value, str) or not value or re.search(r'[\s"\\|]', value):
        raise Stop("Identifier cannot safely be searched; reconcile it manually using an explicit QID.")
    return f"haswbstatement:{prop}={value}"


def snak(prop, value, kind="wikibase-item"):
    if kind == "wikibase-item":
        data = {"type": "wikibase-entityid", "value": {
            "entity-type": "item", "numeric-id": int(qid(value)[1:]), "id": value}}
    elif kind == "time":
        data = {"type": "time", "value": {
            "time": f"+{iso_date(value)}T00:00:00Z", "timezone": 0, "before": 0,
            "after": 0, "precision": 11,
            "calendarmodel": "http://www.wikidata.org/entity/Q1985727"}}
    else:
        data = {"type": "string", "value": value}
    return {"snaktype": "value", "property": prop, "datatype": kind, "datavalue": data}


def item_value(s):
    if s.get("snaktype") != "value":
        return None
    v = s.get("datavalue", {}).get("value")
    return v.get("id", f"Q{v.get('numeric-id')}") if isinstance(v, dict) else None


def without_hashes(obj):
    if isinstance(obj, dict):
        return {k: without_hashes(v) for k, v in obj.items() if k != "hash"}
    if isinstance(obj, list):
        return [without_hashes(v) for v in obj]
    return obj


class API:
    def __init__(self, environment, contact, session=None):
        self.environment = environment
        self.endpoint = TARGETS[environment]
        self.session = session or requests.Session()
        # Prevent implicit .netrc credentials; OAuth and bot passwords are explicit.
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": f"FLoRA-reviewed-pilot/{VERSION} ({required_text(contact, 'contact')})"})
        self.last_request = 0.0
        self.username = None

    def call(self, params, *, post=False, write=False):
        p = {"format": "json", "formatversion": 2, "maxlag": 5, **params}
        if write and (not post or p.get("action") != "wbeditentity" or not self.username):
            raise Stop("Invalid write request.")
        if write:
            p.update({"assert": "user", "assertuser": self.username})
        # A login may retry only an explicit maxlag rejection (not processed).
        # Writes and ambiguous POST results are never automatically retried.
        login = post and not write and p.get("action") == "login"
        attempts = 6 if login else (4 if not post else 1)
        for attempt in range(attempts):
            time.sleep(max(0, 1.0 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                r = self.session.request("POST" if post else "GET", self.endpoint,
                    data=p if post else None, params=None if post else p,
                    timeout=(10, 45), allow_redirects=False)
            except requests.RequestException:
                if not post and attempt < 3:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise Stop("Network failure. A POST result may be unknown; inspect the journal and item history before restarting.") from None
            try:
                data = r.json()
            except ValueError:
                data = {}
            code = data.get("error", {}).get("code")
            lag_detail = ""
            if code == "maxlag":
                error = data["error"]
                lag = error.get("lag")
                if isinstance(lag, (int, float)):
                    lag_detail = f" Reported lag value: {lag:.1f}; configured limit: 5."
                if error.get("type") == "wikibase-queryservice":
                    lag_detail += " Source: Wikidata Query Service."
                    actual = error.get("queryserviceLag")
                    if isinstance(actual, (int, float)):
                        lag_detail += f" Query service is {actual / 60:.1f} minutes behind."
                if login and r.status_code in (200, 503) and attempt < attempts - 1:
                    header = r.headers.get("Retry-After", "")
                    delay = max(30, int(header) if header.isdigit() else 0)
                    if delay > 60:
                        raise Stop("Server requested a long pause before login. Retry later." + lag_detail)
                    print(f"Login refused due to server lag.{lag_detail} Waiting {delay} seconds; login attempt {attempt + 2}/{attempts} next. No items saved.")
                    time.sleep(delay)
                    continue
            retryable = r.status_code in (429, 502, 503, 504) or code in ("maxlag", "ratelimited", "readonly")
            if retryable and not post and attempt < 3:
                retry_after = r.headers.get("Retry-After", "")
                delay = max(5, 2 ** (attempt + 1),
                            int(retry_after) if retry_after.isdigit() else 0)
                if delay > 60:
                    raise Stop("Server requested a long pause. Stop and retry later.")
                if code == "maxlag":
                    print(f"Wikidata is temporarily lagging.{lag_detail} Waiting {delay} seconds before retrying this read-only check.")
                elif r.status_code == 429:
                    print(f"Wikidata requested fewer requests. Waiting {delay} seconds before retrying this read-only check.")
                time.sleep(delay)
                continue
            if code == "maxlag":
                guidance = ("A save was refused; inspect the journal before resuming. Do not delete it."
                            if write else "Wait a minute, then run the launcher again. If the plan has expired, reconcile and review it again.")
                raise Lagged("Wikidata is temporarily lagging (maxlag); this request was not processed. "
                             "This is not a password rejection." + lag_detail + " " + guidance)
            if r.status_code != 200:
                raise Stop(f"HTTP {r.status_code}; stopped. Writes are never automatically retried.")
            if code:
                safe_code = re.sub(r"[^a-zA-Z0-9_-]", "", str(code))[:80]
                raise Stop(f"API error {safe_code}; stopped. Resolve the cause and prepare/review again.")
            if not data or "warnings" in data:
                raise Stop("Empty response or API warning; stopped for inspection.")
            return data
        raise Stop("Read retry budget exhausted.")

    def entity(self, identifier):
        data = self.call({"action": "wbgetentities", "ids": identifier,
                          "props": "info|claims|labels|datatype", "languages": "en"})
        e = data.get("entities", {}).get(identifier)
        if not e or "missing" in e or e.get("id") != identifier or "redirect" in e:
            raise Stop(f"{identifier}: missing or redirected item; reconcile it manually.")
        if not isinstance(e.get("lastrevid"), int) or e["lastrevid"] <= 0:
            raise Stop(f"{identifier}: no revision ID available.")
        return e

    def resolve(self, identity, props):
        wanted_doi = doi(identity.get("doi", ""))
        explicit = identity.get("qid", "")
        if self.environment == "test":
            if wanted_doi:
                raise Stop("Test input uses local QIDs only; omit production DOIs.")
            explicit = qid(explicit)
        if not explicit and not wanted_doi:
            raise Stop("Each work needs an explicit QID or a DOI.")
        if explicit:
            qid(explicit)
        matches = set()
        if wanted_doi:
            data = self.call({"action": "query", "list": "search", "srnamespace": 0,
                "srsearch": identifier_search(props['doi'], wanted_doi),
                "srlimit": 50, "srprop": ""})
            if "continue" in data:
                raise Stop("Too many DOI candidates; reconcile manually.")
            for hit in data["query"]["search"]:
                candidate = hit["title"]
                e = self.entity(qid(candidate))
                if wanted_doi in entity_dois(e, props):
                    matches.add(candidate)
            if len(matches) > 1:
                raise Stop(f"DOI resolves to multiple items: {', '.join(sorted(matches))}.")
            if explicit and matches and matches != {explicit}:
                raise Stop("The explicit QID disagrees with DOI reconciliation.")
            if not explicit:
                if len(matches) != 1:
                    raise Stop("No exact DOI match. Indexing may lag; no item will be created.")
                explicit = next(iter(matches))
        e = self.entity(explicit)
        if wanted_doi and wanted_doi not in entity_dois(e, props):
            raise Stop("The item's current DOI does not match the input DOI.")
        return e

    def authenticate(self, method, expected_user, *, require_autoconfirmed=True):
        prefix = "FLORA_WD_" if self.environment == "wikidata" else "FLORA_TEST_"
        credentials = load_local_credentials(prefix)

        def secret(name):
            value = os.environ.get(prefix + name) or credentials.get(prefix + name)
            if not value:
                if not sys.stdin.isatty():
                    raise Stop(f"Set {prefix + name} locally or run in an interactive terminal.")
                value = getpass.getpass(prefix + name + ": ")
            if not value:
                raise Stop("Empty credential.")
            return value

        if method == "oauth2":
            self.session.headers["Authorization"] = "Bearer " + secret("OAUTH2_TOKEN")
        elif method == "oauth1":
            from requests_oauthlib import OAuth1
            self.session.auth = OAuth1(secret("CONSUMER_KEY"), secret("CONSUMER_SECRET"),
                                       secret("ACCESS_TOKEN"), secret("ACCESS_SECRET"))
        else:
            login_name = (os.environ.get(prefix + "BOT_USERNAME") or credentials.get(prefix + "BOT_USERNAME")
                          or input("Bot-password username (User@App): ").strip())
            if "@" not in login_name or not login_name.rsplit("@", 1)[1]:
                raise Stop("Use the generated bot-password username User@App, not a main-account password.")
            print("Requesting a login token...")
            token = self.call({"action": "query", "meta": "tokens", "type": "login"})["query"]["tokens"]["logintoken"]
            print("Submitting bot-password login...")
            result = self.call({"action": "login", "lgname": login_name,
                "lgpassword": secret("BOT_PASSWORD"), "lgtoken": token}, post=True)
            if result.get("login", {}).get("result") != "Success":
                raise Stop("Bot-password login failed; check the credential and grants locally.")
        print("Verifying the signed-in account and permissions...")
        info = self.call({"action": "query", "meta": "userinfo",
                          "uiprop": "rights|groups|blockinfo|ratelimits|editcount", "assert": "user"})["query"]["userinfo"]
        if info.get("name") != expected_user or info.get("id", 0) <= 0 or "anon" in info:
            raise Stop("Authenticated account does not match --expected-user.")
        if any(k in info for k in ("blockid", "blockedby", "blockreason")):
            raise Stop("The account is blocked; do not proceed.")
        if "edit" not in info.get("rights", []):
            raise Stop("The account or credential lacks edit permission.")
        if require_autoconfirmed and self.environment == "wikidata" and "autoconfirmed" not in info.get("rights", []):
            raise Stop("This pilot conservatively requires the autoconfirmed right for production. Do not make filler edits to obtain it.")
        self.username = expected_user
        return info

    def csrf(self):
        return self.call({"action": "query", "meta": "tokens", "type": "csrf",
                          "assert": "user", "assertuser": self.username})["query"]["tokens"]["csrftoken"]


def entity_dois(entity, props):
    values = set()
    for c in entity.get("claims", {}).get(props["doi"], []):
        if c.get("rank") != "deprecated" and c["mainsnak"].get("snaktype") == "value":
            values.add(doi(c["mainsnak"]["datavalue"]["value"]))
    return values


def model_for(environment, path=None):
    if environment == "wikidata":
        if path:
            raise Stop("Production property mappings are fixed; no --model allowed.")
        return {"environment": environment, "properties": PROPERTIES}
    if not path:
        raise Stop("Test Wikidata has separate IDs. Supply --model with reviewed local property mappings.")
    model = read_json(path)
    validate_model(model, environment)
    return model


def validate_model(model, environment):
    if model.get("environment") != environment or set(model.get("properties", {})) != set(PROPERTIES):
        raise Stop("Incorrect model environment/property keys.")
    if environment == "wikidata" and model["properties"] != PROPERTIES:
        raise Stop("Production property mapping has changed.")
    if len(set(model["properties"].values())) != len(PROPERTIES):
        raise Stop("Property roles must have distinct IDs.")
    for value in model["properties"].values():
        if not re.fullmatch(r"P[1-9][0-9]*", value):
            raise Stop("Fill every property ID in the test model.")


def check_model(api, model):
    validate_model(model, api.environment)
    snapshot = {}
    for role, prop in model["properties"].items():
        e = api.entity(prop)
        if e.get("datatype") != DATATYPES[role]:
            raise Stop(f"{prop}: incorrect datatype for {role}.")
        label = e.get("labels", {}).get("en", {}).get("value", "")
        if api.environment == "test":
            if label != required_text(model.get("expected_labels", {}).get(role), f"test {role} label"):
                raise Stop(f"{prop}: test label disagrees with the reviewed mapping.")
        snapshot[prop] = {"label": label, "revision": e["lastrevid"]}
    return snapshot


def validate_dataset(data, environment):
    if data.get("schema_version") != 1 or data.get("environment") != environment:
        raise Stop("Input schema/environment does not match the command.")
    dataset = data.get("dataset", {})
    for key in ("name", "version"):
        required_text(dataset.get(key), f"dataset.{key}")
    url(dataset.get("url"))
    records = data.get("records")
    limit = MAX_EDITS
    page = data.get("page")
    if page is not None:
        if data.get("items_only") is not True or not isinstance(page, dict):
            raise Stop("Pages are supported for item creation only.")
        limit = PAGE_MAX_ROWS
    if not isinstance(records, list) or not 1 <= len(records) <= limit:
        raise Stop(f"Provide 1 to {limit} records. Larger inputs are rejected, never silently truncated.")
    ids = [required_text(r.get("record_id"), "record_id") for r in records]
    if len(set(ids)) != len(ids):
        raise Stop("Duplicate record_id in input.")


def validate_review(record, dataset):
    if dataset.get("cc0_compatible") is not True:
        raise Stop("Dataset CC0 compatibility has not been reviewed.")
    for field in ("verified_by", "review_note", "model_review_note"):
        required_text(record.get(field), field)
    iso_date(record.get("verified_at"))
    iso_date(record.get("retrieved"))
    if record.get("whole_relation_appropriate") is not True:
        raise Stop("Finding-level results must not be flattened to a whole citation. Review scope first.")
    for field in ("reference_url", "evidence_url"):
        url(record.get(field))
    for role in ("replication", "original"):
        required_text(record.get(role, {}).get("title"), role + ".title")
    qid(record.get("cito", {}).get("qid"))
    required_text(record["cito"].get("label"), "cito.label")


def proposal(entity, original, goal, record, props):
    """Return one full statement; preserve unrelated data; conflict rather than guess."""
    matches = [c for c in entity.get("claims", {}).get(props["relation"], [])
               if item_value(c["mainsnak"]) == original]
    if len(matches) > 1:
        raise Stop("Multiple statements cite this original; resolve the ambiguity manually.")
    before = copy.deepcopy(matches[0]) if matches else None
    if before:
        if before.get("rank", "normal") == "deprecated":
            raise Stop("Existing citation is deprecated; manual review required.")
        quals = before.get("qualifiers", {})
        if quals and (set(quals) != {props["goal"]} or len(quals[props["goal"]]) != 1
                      or item_value(quals[props["goal"]][0]) != goal):
            raise Stop("Existing citation has different or scoped qualifiers; no automatic merge.")
        after = copy.deepcopy(before)
    else:
        after = {"type": "statement", "rank": "normal", "mainsnak": snak(props["relation"], original)}
    if not after.get("qualifiers"):
        after["qualifiers"] = {props["goal"]: [snak(props["goal"], goal)]}
        after["qualifiers-order"] = [props["goal"]]
    refs = after.setdefault("references", [])
    # Same supporting URL already present: retain its reference and retrieval date.
    for ref_property, source in ((props["nanopublication"], record["reference_url"]),
                                 (props["reference"], record["evidence_url"])):
        if not any(s.get("datavalue", {}).get("value") == source for ref in refs
                   for prop in (props["reference"], props["nanopublication"])
                   for s in ref.get("snaks", {}).get(prop, [])):
            refs.append({"snaks": {
                ref_property: [snak(ref_property, source, "url")],
                props["retrieved"]: [snak(props["retrieved"], record["retrieved"], "time")]},
                "snaks-order": [ref_property, props["retrieved"]]})
    status = "skip" if before == after else ("update" if before else "add")
    return {"status": status, "before": before, "after": after}


def make_plan(data, api, model, *, offline=False):
    validate_dataset(data, api.environment)
    property_snapshot = check_model(api, model)
    props = model["properties"]
    rows = []
    subjects = set()
    for r in data["records"]:
        row = {"record_id": r["record_id"], "record": copy.deepcopy(r)}
        try:
            validate_review(r, data["dataset"])
            subject = api.resolve(r["replication"], props)
            original = api.resolve(r["original"], props)
            goal = api.entity(r["cito"]["qid"])
            if subject["id"] == original["id"]:
                raise Stop("Replication and original resolved to the same item.")
            if subject["id"] in subjects:
                raise Stop("This pilot allows one record per replication item; deduplicate or review a later pilot separately.")
            subjects.add(subject["id"])
            if goal.get("labels", {}).get("en", {}).get("value") != r["cito"]["label"]:
                raise Stop("CiTO item label does not match the reviewed label.")
            row.update({"subject": subject["id"], "original": original["id"], "goal": goal["id"],
                "revisions": {e["id"]: e["lastrevid"] for e in (subject, original, goal)},
                "labels": {e["id"]: e.get("labels", {}).get("en", {}).get("value", "")
                           for e in (subject, original, goal)}})
            row.update(proposal(subject, original["id"], goal["id"], r, props))
        except Stop as exc:
            row.update({"status": "blocked", "reason": str(exc)})
        rows.append(row)
    return {"schema_version": 1, "tool_version": VERSION, "created_at": now(),
        "batch_id": str(uuid.uuid4()), "environment": api.environment,
        "endpoint": TARGETS[api.environment], "offline": offline,
        "dataset": data["dataset"], "model": model, "property_snapshot": property_snapshot,
        "input_sha256": digest(data), "rows": rows}


def report(plan, rows=None):
    lines = ["# FLoRA pilot review", "", f"Plan SHA-256: `{digest(plan)}`",
             f"Target: {plan['endpoint']}", f"Offline fixture: {plan['offline']}",
             "", "This report is a preview. No edits have been sent.",
             "Review both the identity of each work and the evidence for the exact relation.", ""]
    for row in plan["rows"] if rows is None else rows:
        lines += [f"## {row['record_id']}: {row['status'].upper()}", ""]
        if row["status"] == "blocked":
            lines += [row["reason"], ""]
            continue
        r = row["record"]
        base = plan["endpoint"].split("/w/")[0]
        lines += [f"Replication: [{row['subject']}]({base}/wiki/{row['subject']}) — {r['replication']['title']}",
                  f"Original: [{row['original']}]({base}/wiki/{row['original']}) — {r['original']['title']}",
                  f"CiTO: [{row['goal']}]({base}/wiki/{row['goal']}) — {r['cito']['label']}",
                  f"Current labels: {canonical(row['labels'])}",
                  f"Evidence: {r['evidence_url']}", f"Nanopublication/reference: {r['reference_url']}",
                  f"Reviewed by: {r['verified_by']} on {r['verified_at']}",
                  f"Review: {r['review_note']}", f"Model review: {r['model_review_note']}",
                  "", "Before:", "```json", json.dumps(row["before"], ensure_ascii=False, indent=2), "```",
                  "After:", "```json", json.dumps(row["after"], ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


class Journal:
    """Durable reservation before POST: an interrupted/unknown attempt never replays."""
    def __init__(self, path, plan, max_attempts=MAX_EDITS):
        self.max_attempts = max_attempts
        self.db = sqlite3.connect(path, timeout=1)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS meta (hash TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS attempts (record_id TEXT PRIMARY KEY, status TEXT, details TEXT)")
        existing = self.db.execute("SELECT hash FROM meta").fetchall()
        if existing and existing != [(digest(plan),)]:
            self.db.close()
            raise Stop("Journal belongs to a different plan. Keep it with its original plan.")
        if not existing:
            self.db.execute("INSERT INTO meta VALUES (?)", (digest(plan),))
        self.db.commit()

    def close(self):
        self.db.close()

    def states(self):
        return dict(self.db.execute("SELECT record_id, status FROM attempts"))

    def reserve(self, record_id, details):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.db.execute("SELECT count(*) FROM attempts").fetchone()[0] >= self.max_attempts:
                raise Stop("The write-attempt limit for this pilot has been reached.")
            self.db.execute("INSERT INTO attempts VALUES (?, ?, ?)", (record_id, "pending", canonical(details)))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def release(self, record_id):
        """Drop a reservation ONLY after the server explicitly refused it unprocessed (maxlag)."""
        cursor = self.db.execute("DELETE FROM attempts WHERE record_id=? AND status='pending'", (record_id,))
        self.db.commit()
        if cursor.rowcount != 1:
            raise Stop("Reservation to release was not pending; inspect the journal.")

    def finish(self, record_id, status, details):
        self.db.execute("UPDATE attempts SET status=?, details=? WHERE record_id=?",
                        (status, canonical(details), record_id))
        self.db.commit()


def audit(path, event, **fields):
    # Callers supply only public metadata, never HTTP objects, headers or tokens.
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(canonical({"time": now(), "event": event, **fields}) + "\n")
        f.flush()
        os.fsync(f.fileno())


def validate_plan(plan, approved_hash):
    if digest(plan) != approved_hash:
        raise Stop("Plan hash differs from --approved-sha256. Review the exact saved plan.")
    if plan.get("schema_version") != 1 or plan.get("tool_version") != VERSION:
        raise Stop("Unsupported plan version.")
    if plan.get("offline") is not False:
        raise Stop("Offline fixture plans can NEVER be applied.")
    env = plan.get("environment")
    if env not in TARGETS or plan.get("endpoint") != TARGETS[env]:
        raise Stop("Invalid or changed plan endpoint.")
    age = datetime.now(timezone.utc) - datetime.fromisoformat(plan["created_at"])
    if not 0 <= age.total_seconds() <= 86400:
        raise Stop("Plan expired (24 hours) or has a future timestamp; regenerate and review.")
    validate_model(plan["model"], env)
    rows = plan["rows"]
    data = {"schema_version": 1, "environment": env, "dataset": plan["dataset"],
            "records": [r["record"] for r in rows]}
    validate_dataset(data, env)
    if digest(data) != plan["input_sha256"]:
        raise Stop("Input records do not match the plan input hash.")
    if any(r["status"] not in ("add", "update", "skip") for r in rows):
        raise Stop("The plan contains blocked/invalid rows. Resolve them and create a new plan.")
    if len({r["subject"] for r in rows}) != len(rows):
        raise Stop("Multiple records target the same replication item.")
    for r in rows:
        if r["record_id"] != r["record"]["record_id"]:
            raise Stop("Record ID mismatch.")
        validate_review(r["record"], plan["dataset"])


def recheck(api, plan, row):
    props = plan["model"]["properties"]
    subject = api.resolve(row["record"]["replication"], props)
    original = api.resolve(row["record"]["original"], props)
    goal = api.entity(row["record"]["cito"]["qid"])
    for role, entity in (("subject", subject), ("original", original), ("goal", goal)):
        if entity["id"] != row[role] or entity["lastrevid"] != row["revisions"][entity["id"]]:
            raise Stop("An item changed since the preview. Regenerate and review before editing.")
    fresh = proposal(subject, original["id"], goal["id"], row["record"], props)
    if any(fresh[key] != row[key] for key in ("status", "before", "after")):
        raise Stop("Recomputed edit differs from the approved plan.")
    return fresh


def write_one(api, row, plan, csrf):
    return api.call({"action": "wbeditentity", "id": row["subject"],
        "data": canonical({"claims": [row["after"]]}),
        "baserevid": row["revisions"][row["subject"]], "token": csrf,
        "summary": f"FLoRA reviewed pilot {plan['batch_id'][:8]}: citation evidence ({row['record_id'][:32]})",
        "maxlag": 5}, post=True, write=True)


def verify_result(api, row, props):
    current = api.entity(row["subject"])
    desired = copy.deepcopy(row["after"])
    desired.pop("id", None)
    for claim in current.get("claims", {}).get(props["relation"], []):
        value = copy.deepcopy(claim)
        value.pop("id", None)
        if without_hashes(value) == without_hashes(desired):
            return {"statement_id": claim["id"], "observed_revision": current["lastrevid"]}
    raise Stop("Post-write verification failed. Stop and inspect the item and journal.")


def apply_plan(args):
    plan = read_json(args.plan)
    validate_plan(plan, args.approved_sha256)
    if not args.apply or not args.data_verified or not args.policy_reviewed:
        raise Stop("Writing requires --apply --data-verified --policy-reviewed and a reviewed plan hash.")
    if plan["environment"] == "wikidata" and not args.allow_live:
        raise Stop("Production additionally requires --allow-live.")
    if not sys.stdin.isatty():
        raise Stop("Apply requires an interactive terminal; no unattended/--yes mode exists.")
    log_path = Path(str(args.plan) + ".audit.jsonl")
    with closing(Journal(str(args.plan) + ".journal.sqlite3", plan)) as journal:
        execute_reviewed_plan(args, plan, journal, log_path)


def execute_reviewed_plan(args, plan, journal, log_path):
    states = journal.states()
    if any(s != "verified" for s in states.values()):
        raise Stop("An earlier write attempt is pending/uncertain. Use inspect and item history; it will not be replayed.")
    remaining = [r for r in plan["rows"] if r["status"] != "skip" and r["record_id"] not in states]
    if not remaining:
        print("Nothing to write; all statements were already present or verified.")
        return
    api = API(plan["environment"], args.contact)
    # Preflight all remaining data before login or any edit.
    if check_model(api, plan["model"]) != plan["property_snapshot"]:
        raise Stop("Property definitions changed; regenerate the plan.")
    for row in remaining:
        recheck(api, plan, row)
    info = api.authenticate(args.auth, args.expected_user)
    print(f"Account: {info['name']}; target: {api.endpoint}; at most {len(remaining)} edits.")
    print("Account rate limits:", canonical(info.get("ratelimits", {})))
    phrase = f"APPLY {plan['environment']} {digest(plan)[:12]}"
    if input(f"Type {phrase} to continue: ").strip() != phrase:
        raise Stop("Cancelled; no edit was sent.")
    token = api.csrf()
    audit(log_path, "authorized", batch_id=plan["batch_id"], plan_sha256=digest(plan),
          username=info["name"], endpoint=api.endpoint)
    for index, row in enumerate(remaining):
        if index:
            time.sleep(10)
        print(report(plan, [row]))
        if input(f"Type EDIT {row['record_id']} to send this one edit (anything else stops): ").strip() != f"EDIT {row['record_id']}":
            raise Stop("Stopped by operator. Earlier verified edits remain recorded.")
        recheck(api, plan, row)
        details = {"batch_id": plan["batch_id"], "record_id": row["record_id"],
            "subject": row["subject"], "base_revision": row["revisions"][row["subject"]],
            "before": row["before"], "after": row["after"], "time": now()}
        journal.reserve(row["record_id"], details)
        audit(log_path, "write_attempt", **details)
        try:
            response = write_one(api, row, plan, token)
            revision = response.get("entity", {}).get("lastrevid")
            if response.get("success") != 1 or not isinstance(revision, int):
                raise Stop("No confirmed success/revision returned; inspect history before proceeding.")
            details.update({"new_revision": revision,
                "diff_url": api.endpoint.split("/w/")[0] + f"/w/index.php?diff={revision}&oldid={details['base_revision']}"})
            journal.finish(row["record_id"], "written_unverified", details)
            audit(log_path, "write_acknowledged", **details)
            details.update(verify_result(api, row, plan["model"]["properties"]))
            journal.finish(row["record_id"], "verified", details)
            audit(log_path, "verified", **details)
            print("Verified:", details["diff_url"])
        except (Stop, KeyboardInterrupt):
            audit(log_path, "stopped_for_inspection", record_id=row["record_id"])
            raise
    print(f"Finished: {len(remaining)} edits verified. Inspect constraints and the saved diffs.")


class FixtureAPI:
    """Read-only offline adapter. Never instantiated by apply."""
    def __init__(self, fixture, environment):
        self.environment = environment
        self.fixture = fixture

    def entity(self, identifier):
        if identifier not in self.fixture["entities"]:
            raise Stop("Fixture entity missing.")
        return copy.deepcopy(self.fixture["entities"][identifier])

    def resolve(self, identity, props):
        return self.entity(qid(identity["qid"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan", help="Read-only dry run; does not authenticate or write")
    p.add_argument("--input", required=True)
    p.add_argument("--environment", choices=TARGETS, required=True)
    p.add_argument("--model")
    p.add_argument("--out", required=True)
    p.add_argument("--contact", default="")
    p.add_argument("--offline-fixture")
    p.add_argument("--dry-run", action="store_true", help="Explicit alias; plan is always a dry run")
    p = sub.add_parser("apply", help="Interactive, individually authorized edits")
    p.add_argument("--plan", required=True)
    p.add_argument("--approved-sha256", required=True)
    p.add_argument("--auth", choices=("oauth2", "oauth1", "bot-password"), default="oauth2")
    p.add_argument("--expected-user", required=True)
    p.add_argument("--contact", required=True)
    for flag in ("apply", "data-verified", "policy-reviewed", "allow-live"):
        p.add_argument("--" + flag, action="store_true")
    p = sub.add_parser("inspect", help="Read the local journal; sends no requests")
    p.add_argument("--plan", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            data = read_json(args.input)
            model = model_for(args.environment, args.model)
            api = (FixtureAPI(read_json(args.offline_fixture), args.environment) if args.offline_fixture
                   else API(args.environment, args.contact))
            plan = make_plan(data, api, model, offline=bool(args.offline_fixture))
            out = Path(args.out)
            review_path = Path(str(out) + ".md")
            if out.exists() or review_path.exists():
                raise Stop("Output exists; choose a new plan filename to preserve prior reviews.")
            write_new(out, plan)
            review_path.write_text(report(plan), encoding="utf-8")
            counts = {s: sum(r["status"] == s for r in plan["rows"]) for s in ("add", "update", "skip", "blocked")}
            print(canonical(counts))
            print("Plan SHA-256:", digest(plan))
            print("Review:", review_path)
            return 2 if counts["blocked"] else 0
        if args.command == "apply":
            apply_plan(args)
        else:
            path = Path(str(args.plan) + ".journal.sqlite3").resolve()
            if not path.exists():
                raise Stop("No journal exists for this plan.")
            db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
            for record_id, status, details in db.execute("SELECT record_id,status,details FROM attempts"):
                print(record_id, status, details)
            db.close()
        return 0
    except (Stop, ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        # Unexpected API/OS errors are sanitized: they could otherwise include URLs/credentials.
        print("STOP:", str(exc) if isinstance(exc, Stop) else f"{type(exc).__name__}; check input/local files.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Stopped. Inspect the journal before resuming any interrupted write.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
