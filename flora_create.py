#!/usr/bin/env python3
"""First ten CSV rows: create missing original/replication items, up to twenty saves."""
import argparse
import copy
import csv
import hashlib
from itertools import islice
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time
import uuid

import flora_pilot as p


def reference(source, props, retrieved):
    return {"snaks": {props["reference"]: [p.snak(props["reference"], p.url(source), "url")],
                      props["retrieved"]: [p.snak(props["retrieved"], retrieved, "time")]},
            "snaks-order": [props["reference"], props["retrieved"]]}


def statement(prop, kind, value, source, props, retrieved):
    if kind not in ("wikibase-item", "external-id", "string", "url", "time", "monolingualtext"):
        raise p.Stop("Supported attribute datatypes: item, external-id, string, URL, full-date time.")
    if kind == "url": p.url(value)
    if kind in ("external-id", "string"): p.required_text(value, "attribute value")
    main = p.snak(prop, value, kind)
    if kind == "monolingualtext":
        p.required_text(value.get("text"), "title text")
        p.required_text(value.get("language"), "title language")
        main["datavalue"] = {"type": "monolingualtext", "value": value}
    return {"type": "statement", "rank": "normal", "mainsnak": main,
            "references": [reference(source, props, retrieved)]}


def property_check(api, prop, kind):
    import re
    if not isinstance(prop, str) or not re.fullmatch(r"P[1-9][0-9]*", prop):
        raise p.Stop("Attribute property IDs must be explicit P numbers on the target wiki.")
    e = api.entity(prop)
    if e.get("datatype") != kind: raise p.Stop(f"{prop}: incorrect attribute datatype.")
    return {"revision": e["lastrevid"], "label": e.get("labels", {}).get("en", {}).get("value", "")}


def identity(work, props):
    if work.get("doi"):
        return {"property": props["doi"], "datatype": "external-id", "value": p.doi(work["doi"])}
    ident = work.get("stable_identifier")
    if ident:
        if ident.get("datatype") not in ("url", "external-id"):
            raise p.Stop("Stable identifier must be a URL or external identifier.")
        p.required_text(ident.get("value"), "stable identifier")
        if ident["datatype"] == "url": p.url(ident["value"])
        return {k: ident[k] for k in ("property", "datatype", "value")}
    if not work.get("qid"):
        raise p.Stop("Creating a work requires a DOI or reviewed stable identifier; titles alone are insufficient.")
    return None


def find_work(api, work, props):
    ident = identity(work, props)
    explicit = work.get("qid", "")
    candidates = set()
    if ident:
        data = api.call({"action": "query", "list": "search", "srnamespace": 0,
            "srsearch": p.identifier_search(ident['property'], ident['value']),
            "srlimit": 50, "srprop": ""})
        if "continue" in data: raise p.Stop("Too many matches; reconcile manually.")
        for hit in data["query"]["search"]:
            e = api.entity(p.qid(hit["title"]))
            if has_identifier(e, ident, props): candidates.add(e["id"])
        if len(candidates) > 1: raise p.Stop("Multiple items share the identifier; resolve duplicates manually.")
        if explicit and candidates and candidates != {explicit}:
            raise p.Stop("Explicit QID disagrees with identifier search.")
    resolved = explicit or next(iter(candidates), None)
    if resolved:
        e = api.entity(p.qid(resolved))
        if ident and not has_identifier(e, ident, props): raise p.Stop("QID lacks the expected identifier.")
        return e
    # Only a successful, completed search with zero exact matches reaches this point.
    if work.get("creation_reviewed") is not True:
        raise p.Stop("No match. Review title/identifier searches, notability and possible index lag, then attest creation_reviewed.")
    p.required_text(work.get("creation_review_note"), "creation review note")
    return None


def has_identifier(e, ident, props):
    for claim in e.get("claims", {}).get(ident["property"], []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if ident["property"] == props["doi"] and isinstance(value, str): value = p.doi(value)
        if value == ident["value"]: return True
    return False


def metadata(api, work, props, retrieved, property_snapshots):
    title = p.required_text(work.get("title"), "title")
    description = p.required_text(work.get("description"), "description")
    attributes = copy.deepcopy(work.get("attributes", []))
    if not attributes: raise p.Stop("Provide reviewed attributes, including an appropriate instance-of statement.")
    # The role is explicit for independent test-wiki IDs.
    type_prop = work.get("instance_of_property", "P31" if api.environment == "wikidata" else None)
    if not any(a.get("property") == type_prop and a.get("datatype") == "wikibase-item" for a in attributes):
        raise p.Stop("Include an explicit, reviewed instance-of item attribute.")
    ident = identity(work, props)
    if ident:
        same_property = [a for a in attributes if a.get("property") == ident["property"]]
        if same_property: raise p.Stop("Identifier is supplied separately; do not duplicate it in attributes.")
        attributes.append({**ident, "source_url": work.get("metadata_source_url")})
    claims, seen = [], set()
    for a in attributes:
        prop, kind, value = a["property"], a["datatype"], a["value"]
        key = (prop, p.canonical(value))
        if key in seen: raise p.Stop("Repeated attribute in input.")
        seen.add(key)
        property_snapshots[prop] = property_check(api, prop, kind)
        if kind == "wikibase-item": api.entity(p.qid(value))
        claim = statement(prop, kind, value, a["source_url"], props, retrieved)
        if a.get("precision") is not None:
            if kind != "time" or a["precision"] not in (9, 11): raise p.Stop("Date precision must be 9 (year) or 11 (day).")
            claim["mainsnak"]["datavalue"]["value"]["precision"] = a["precision"]
        if a.get("ordinal"):
            if api.environment != "wikidata": raise p.Stop("Map author ordinal qualifiers explicitly for the test wiki.")
            snapshots = property_check(api, "P1545", "string")
            property_snapshots["P1545"] = snapshots
            claim["qualifiers"] = {"P1545": [p.snak("P1545", str(a["ordinal"]), "string")]}
            claim["qualifiers-order"] = ["P1545"]
        claims.append(claim)
    return {"labels": {"en": {"language": "en", "value": title}},
            "descriptions": {"en": {"language": "en", "value": description}}, "claims": claims}


def missing_attributes(entity, payload):
    additions = []
    for claim in payload["claims"]:
        prop = claim["mainsnak"]["property"]
        existing = entity.get("claims", {}).get(prop, [])
        if any(p.without_hashes(c["mainsnak"]) == claim["mainsnak"] and c.get("rank") != "deprecated" for c in existing):
            continue
        if existing:
            raise p.Stop(f"Existing {prop} differs; review it manually before adding attributes.")
        additions.append(claim)
    return additions


def plan(data, api, model):
    p.validate_dataset(data, api.environment)
    props = model["properties"]
    model_snapshot = p.check_model(api, model)
    works, operations, pairs, identifiers, snapshots = {}, [], [], {}, {}
    items_only = data.get("items_only") is True
    for record in data["records"]:
        if items_only:
            if data["dataset"].get("cc0_compatible") is not True or record.get("metadata_reviewed") is not True:
                raise p.Stop("Review source reuse permission and metadata before planning writes; the CSV preparation is not a verification.")
            p.required_text(record.get("verified_by"), "reviewer")
            p.required_text(record.get("review_note"), "review note")
            p.iso_date(record.get("verified_at"))
            p.iso_date(record.get("retrieved"))
        else:
            p.validate_review(record, data["dataset"])
        keys = {}
        for role in ("original", "replication"):
            w = record[role]
            key = p.required_text(w.get("work_key"), "work_key")
            keys[role] = key
            if key in works:
                if works[key]["input"] != w: raise p.Stop("Shared work_key has inconsistent metadata.")
                continue
            ident = identity(w, props)
            ident_key = p.canonical(ident) if ident else w["qid"]
            if ident_key in identifiers: raise p.Stop("Use the same work_key for repeated work identifiers.")
            identifiers[ident_key] = key
            if ident: snapshots[ident["property"]] = property_check(api, ident["property"], ident["datatype"])
            entity = find_work(api, w, props)
            payload = metadata(api, w, props, record["retrieved"], snapshots) if not entity or not items_only else None
            works[key] = {"input": w, "payload": payload, "qid": entity["id"] if entity else None,
                          "revision": entity["lastrevid"] if entity else None}
            if not entity:
                operations.append({"record_id": "create:" + key, "kind": "create", "key": key, "data": payload})
            elif not items_only:
                additions = missing_attributes(entity, payload)
                if additions:
                    operations.append({"record_id": "attributes:" + key, "kind": "attributes", "key": key,
                                       "data": {"claims": additions}})
        if keys["original"] == keys["replication"]: raise p.Stop("A work cannot replicate itself.")
        if items_only: continue
        goal = api.entity(record["cito"]["qid"])
        if goal.get("labels", {}).get("en", {}).get("value") != record["cito"]["label"]:
            raise p.Stop("CiTO label mismatch.")
        pairs.append({"record_id": "link:" + record["record_id"], "kind": "link", "record": record,
                      **keys, "goal_revision": goal["lastrevid"]})
    if len({pair["replication"] for pair in pairs}) != len(pairs):
        raise p.Stop("This pilot permits one relation per replication work.")
    resolved = [w["qid"] for w in works.values() if w["qid"]]
    if len(set(resolved)) != len(resolved): raise p.Stop("Different work keys resolve to the same item.")
    for pair in pairs:
        s, o = works[pair["replication"]], works[pair["original"]]
        if s["qid"] and o["qid"]:
            proposal = p.proposal(api.entity(s["qid"]), o["qid"], pair["record"]["cito"]["qid"], pair["record"], props)
            if proposal["status"] == "skip": continue
        operations.append(pair)
    limit = 20 if items_only else 10
    if not 0 <= len(operations) <= limit:
        raise p.Stop(f"This input requires up to {len(operations)} saves; the limit is {limit}.")
    return {"format": "flora-create-v1", "created_at": p.now(), "batch_id": str(uuid.uuid4()),
            "environment": api.environment, "endpoint": p.TARGETS[api.environment], "model": model,
            "model_snapshot": model_snapshot, "property_snapshots": snapshots, "input": data,
            "works": works, "operations": operations}


def check_unchanged(api, saved, key, mappings, revisions):
    current = find_work(api, saved["works"][key]["input"], saved["model"]["properties"])
    expected = mappings.get(key)
    # Newly created IDs come from the durable journal; indexes may not yet see them.
    if expected:
        if current and current["id"] != expected: raise p.Stop("Identifier now resolves to another item.")
        current = api.entity(expected)
        if current["lastrevid"] != revisions[key]: raise p.Stop("Item changed since review/last verified write. Stop and replan.")
    elif current:
        raise p.Stop("A formerly missing work now exists. Replan to reuse it.")
    return current


def verify_payload(entity, payload):
    for section in ("labels", "descriptions"):
        for lang, value in payload.get(section, {}).items():
            if entity.get(section, {}).get(lang) != value: raise p.Stop("Saved label/description did not verify.")
    for claim in payload.get("claims", []):
        wanted = p.without_hashes(copy.deepcopy(claim))
        wanted.pop("id", None)
        found = False
        for candidate in entity.get("claims", {}).get(claim["mainsnak"]["property"], []):
            actual = p.without_hashes(copy.deepcopy(candidate))
            actual.pop("id", None)
            if actual == wanted: found = True
        if not found: raise p.Stop("Saved statement did not verify. Inspect history; do not retry creation.")


def apply(args):
    saved = p.read_json(args.plan)
    if saved.get("format") != "flora-create-v1" or p.digest(saved) != args.approved_sha256:
        raise p.Stop("Review the exact creation plan and supply its hash.")
    env = saved["environment"]
    if env not in p.TARGETS or saved["endpoint"] != p.TARGETS[env]: raise p.Stop("Invalid endpoint.")
    if not args.apply or not args.data_verified or not args.policy_reviewed or (env == "wikidata" and not args.allow_live):
        raise p.Stop("Explicit apply, data/policy review and production --allow-live flags are required.")
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(saved["created_at"])).total_seconds()
    if not 0 <= age <= 86400: raise p.Stop("Plan expired. Reconcile and review again.")
    if not sys.stdin.isatty(): raise p.Stop("An interactive terminal is required.")
    limit = 20 if saved["input"].get("items_only") is True else 10
    p.validate_dataset(saved["input"], env)
    if len(saved["operations"]) > limit: raise p.Stop("Save limit exceeded.")
    api = p.API(env, args.contact)
    log = str(args.plan) + ".audit.jsonl"
    with closing(p.Journal(str(args.plan) + ".journal.sqlite3", saved, max_attempts=limit)) as journal:
        states = journal.states()
        if any(s != "verified" for s in states.values()):
            raise p.Stop("An uncertain prior save blocks this run. Inspect journal and history; never blindly repeat creation.")
        # Before any first write, rebuild the entire plan using current data.
        if not states:
            print("Checking current Wikidata items and duplicates before login. This may take a few minutes; no items are being saved.")
            fresh = plan(saved["input"], api, saved["model"])
            for field in ("works", "operations", "model_snapshot", "property_snapshots"):
                if fresh[field] != saved[field]: raise p.Stop("Current preview differs; regenerate and review.")
        else:
            if p.check_model(api, saved["model"]) != saved["model_snapshot"]:
                raise p.Stop("Model changed; stop and review.")
            for prop, snap in saved["property_snapshots"].items():
                if api.entity(prop)["lastrevid"] != snap["revision"]: raise p.Stop("Attribute property changed.")
        mappings = {k: w["qid"] for k, w in saved["works"].items() if w["qid"]}
        revisions = {k: w["revision"] for k, w in saved["works"].items() if w["qid"]}
        for _, details in journal.db.execute("SELECT record_id,details FROM attempts ORDER BY rowid"):
            entry = json.loads(details)
            mappings[entry["work_key"]] = entry["qid"]
            revisions[entry["work_key"]] = entry["new_revision"]
        remaining = [op for op in saved["operations"] if op["record_id"] not in states]
        if not remaining:
            print("All planned saves already verified; nothing to send.")
            return
        # This command requires an individual human decision for every save.
        # Check real edit/create rights; the server still enforces all restrictions.
        print("Checks complete. Logging in; each save still requires your confirmation.")
        info = api.authenticate(args.auth, args.expected_user, require_autoconfirmed=False)
        if any(op["kind"] == "create" for op in remaining) and "createpage" not in info.get("rights", []):
            raise p.Stop("Your account/credential needs permission to create pages as well as edit them.")
        phrase = f"APPLY {env} {p.digest(saved)[:12]}"
        print("Target:", api.endpoint, "Account:", info["name"], "Maximum remaining saves:", len(remaining))
        if input(f"Type {phrase}: ").strip() != phrase: raise p.Stop("Cancelled.")
        p.audit(log, "authorized", username=info["name"], endpoint=api.endpoint,
                batch_id=saved["batch_id"], plan_sha256=p.digest(saved))
        token = api.csrf()
        for op in remaining:
            key = op.get("key", op.get("replication"))
            current = check_unchanged(api, saved, key, mappings, revisions)
            payload = op.get("data")
            if op["kind"] == "link":
                original = check_unchanged(api, saved, op["original"], mappings, revisions)
                goal = api.entity(op["record"]["cito"]["qid"])
                if goal["lastrevid"] != op["goal_revision"]: raise p.Stop("CiTO item changed; review again.")
                if not current or not original: raise p.Stop("Creation dependency has not been verified.")
                proposed = p.proposal(current, original["id"], goal["id"], op["record"], saved["model"]["properties"])
                if proposed["status"] == "skip": raise p.Stop("Relation now exists; replan to avoid an unnecessary save.")
                payload = {"claims": [proposed["after"]]}
            print(op["record_id"], json.dumps(payload, indent=2, ensure_ascii=False))
            if input(f"Type SAVE {op['record_id']}: ").strip() != f"SAVE {op['record_id']}": raise p.Stop("Stopped by operator.")
            # Confirmation can take time; check the subject again immediately before saving.
            check_unchanged(api, saved, key, mappings, revisions)
            details = {"work_key": key, "kind": op["kind"], "payload": payload,
                       "base_revision": current["lastrevid"] if current else None}
            journal.reserve(op["record_id"], details)
            p.audit(log, "write_attempt", record_id=op["record_id"], **details)
            params = {"action": "wbeditentity", "data": p.canonical(payload), "token": token,
                      "summary": f"FLoRA reviewed creation pilot {saved['batch_id'][:8]}"}
            if op["kind"] == "create":
                if current: raise p.Stop("Creation target unexpectedly exists.")
                params["new"] = "item"
            else:
                params.update(id=current["id"], baserevid=current["lastrevid"])
            result = api.call(params, post=True, write=True)  # inherited no-retry policy
            result_entity = result.get("entity", {})
            if result.get("success") != 1 or not result_entity.get("lastrevid"):
                raise p.Stop("Unconfirmed save. Inspect journal and contributions before any restart.")
            target = p.qid(result_entity["id"])
            details.update(qid=target, new_revision=result_entity["lastrevid"],
                           diff_url=api.endpoint.split("/w/")[0] + f"/w/index.php?diff={result_entity['lastrevid']}")
            journal.finish(op["record_id"], "written_unverified", details)
            # Ask for descriptions here too; the existing-item helper normally omits them.
            entities = api.call({"action": "wbgetentities", "ids": target,
                "props": "info|claims|labels|descriptions", "languages": "en"})["entities"]
            verify_payload(entities[target], payload)
            journal.finish(op["record_id"], "verified", details)
            p.audit(log, "verified", record_id=op["record_id"], **details)
            mappings[key], revisions[key] = target, result_entity["lastrevid"]
            print("Verified", target, details["diff_url"])
            time.sleep(10)
        print("Completed. Created/reused item IDs:", p.canonical(mappings))


def prepare_csv(csv_path, out):
    source = Path(csv_path)
    with source.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(islice(csv.DictReader(stream), 10))
    if len(rows) != 10: raise p.Stop("The file must contain at least ten data rows.")
    records = []
    def clean(value): return "" if str(value).strip() in ("", "NA", "NaN", "None") else str(value).strip()
    for number, row in enumerate(rows, 1):
        r = {"record_id": clean(row.get("id", "")) or f"row-{number}", "csv_data_row": number,
             "metadata_reviewed": False, "verified_by": "", "verified_at": "",
             "retrieved": "", "review_note": ""}
        for role, suffix in (("original", "o"), ("replication", "r")):
            title, identifier = clean(row.get("title_" + suffix, "")), clean(row.get("doi_" + suffix, ""))
            source_url = "https://doi.org/" + identifier if identifier else clean(row.get("url_" + suffix, ""))
            attributes = [{"property": "P31", "datatype": "wikibase-item", "value": "",
                           "source_url": source_url},
                          {"property": "P1476", "datatype": "monolingualtext",
                           "value": {"text": title, "language": clean(row.get("language_" + suffix, ""))},
                           "source_url": source_url}]
            year = clean(row.get("year_" + suffix, ""))
            if year.isdigit() and len(year) == 4:
                attributes.append({"property": "P577", "datatype": "time", "value": year + "-01-01",
                                   "precision": 9, "source_url": source_url})
            raw_authors = clean(row.get("author_" + suffix, ""))
            authors = json.loads(raw_authors) if raw_authors else []
            for ordinal, author in enumerate(authors, 1):
                name = " ".join(filter(None, (author.get("given", ""), author.get("family", "")))) or author.get("name", "")
                if name: attributes.append({"property": "P2093", "datatype": "string", "value": name,
                                             "ordinal": ordinal, "source_url": source_url})
            for field, prop in (("volume", "P478"), ("issue", "P433"), ("pages", "P304")):
                value = clean(row.get(field + "_" + suffix, ""))
                if value: attributes.append({"property": prop, "datatype": "string", "value": value, "source_url": source_url})
            r[role] = {"work_key": p.doi(identifier) if identifier else role + ":" + r["record_id"],
                "qid": "", "doi": identifier, "title": title, "description": "",
                "metadata_source_url": source_url, "creation_reviewed": False, "creation_review_note": "",
                "attributes": attributes, "unmapped_metadata": {
                    "journal_name": clean(row.get("journal_" + suffix, "")),
                    "alternative_identifier": clean(row.get("alt_identifier_" + suffix, "")),
                    "url": clean(row.get("url_" + suffix, ""))}}
        records.append(r)
    data = {"schema_version": 1, "environment": "wikidata", "items_only": True,
            "source_csv": {"path": str(source.resolve()), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                           "selection": "first 10 data rows in file order; no sorting or replacement"},
            "dataset": {"name": "FLoRA", "version": "CSV SHA256 " + hashlib.sha256(source.read_bytes()).hexdigest(),
                        "url": "", "cc0_compatible": False}, "records": records}
    p.write_new(out, data)
    lines = ["# First ten FLoRA rows — review required", "", f"Source: {source}",
             "", "No Wikidata lookup, login, or edit has been performed by this preparation command.", ""]
    for r in records:
        lines.extend([f"## Row {r['csv_data_row']}: {r['record_id']}", ""])
        for role in ("original", "replication"):
            w = r[role]
            lines.extend([f"**{role.title()}:** {w['title']}", f"DOI: {w['doi']}",
                          f"Journal/source: {w['unmapped_metadata']['journal_name']}", ""])
    Path(str(out) + ".md").write_text("\n".join(lines), encoding="utf-8")
    print("Prepared exactly ten rows; review", out, "before planning any writes.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("prepare-csv")
    a.add_argument("--csv", required=True)
    a.add_argument("--out", required=True)
    a = sub.add_parser("plan")
    a.add_argument("--input", required=True)
    a.add_argument("--environment", choices=p.TARGETS, required=True)
    a.add_argument("--model")
    a.add_argument("--out", required=True)
    a.add_argument("--contact", required=True)
    a.add_argument("--dry-run", action="store_true")
    a = sub.add_parser("apply")
    for arg in ("plan", "approved-sha256", "expected-user", "contact"):
        a.add_argument("--" + arg, required=True)
    a.add_argument("--auth", choices=("oauth2", "oauth1", "bot-password"), default="oauth2")
    for arg in ("apply", "data-verified", "policy-reviewed", "allow-live"):
        a.add_argument("--" + arg, action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare-csv": prepare_csv(args.csv, args.out)
        elif args.command == "apply": apply(args)
        else:
            model = p.model_for(args.environment, args.model)
            saved = plan(p.read_json(args.input), p.API(args.environment, args.contact), model)
            p.write_new(args.out, saved)
            print("Maximum saves:", len(saved["operations"]), "Plan SHA-256:", p.digest(saved))
            print("Review the input, works metadata and ordered operations in", args.out)
        return 0
    except (p.Stop, KeyError, TypeError, ValueError, OSError, sqlite3.Error) as exc:
        print("STOP:", str(exc) if isinstance(exc, p.Stop) else type(exc).__name__, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Stopped. Inspect the journal before resuming.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
