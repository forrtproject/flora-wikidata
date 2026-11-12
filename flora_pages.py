#!/usr/bin/env python3
"""FLoRA page-by-page item creation on Wikidata.

  prepare   Read-only. Takes the next N CSV rows not yet handled, reconciles every
            work on Wikidata, reads DOI registry metadata (Crossref/DataCite), and
            writes a creation plan plus REVIEW.md. Rows needing a human decision are
            held and listed, never guessed.
  publish   One login and one typed confirmation for the whole page. Every save is
            tagged for EditGroups, so the page can be inspected or undone as a group.
  status    Pages so far, their state, edit-group links, held rows.
  discard   Set aside an unpublished page (moved to pages/_discarded, not deleted).

Pages are strictly sequential: the next page can only be prepared after the
previous one (starting with the 10-row pilot) is fully published.
"""
import argparse
import copy
import difflib
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import requests

import flora_pilot as p
import flora_create as c

ROOT = Path(__file__).resolve().parent
USER = "Hb19988"
CONTACT = "https://www.wikidata.org/wiki/User:Hb19988"
DEFAULT_COUNT = 25
MAX_AUTHORS = 300
LABEL_MAX = 250
# Wikidata class per work kind, with the English label checked live before planning.
CLASSES = {"article": ("Q13442814", "scholarly article"),
           "preprint": ("Q580922", "preprint"),
           "preregistration": ("Q47461344", "written work")}
SCHOLARLY_ARTICLE = CLASSES["article"][0]
# Characters verified to work in haswbstatement DOI searches. Others (< > ; % [ ])
# could make a search silently miss an existing item, so those works are held.
SEARCHABLE_DOI = re.compile(r"10\.[0-9]{4,9}/[A-Z0-9._/():-]+")
DATASET = {"name": "FLoRA", "url": "https://forrt.org/replication-hub/flora/", "cc0_compatible": True,
           "reuse_basis": "Selection from FLoRA; new bibliographic facts verified from Crossref/DataCite "
                          "metadata (CC0). No abstracts, outcome quotes, or replication judgments imported.",
           "reuse_sources": ["https://www.crossref.org/documentation/retrieve-metadata/",
                             "https://support.datacite.org/docs/harvesting-datacite-doi-metadata"]}


class Hold(Exception):
    """This work/row needs a human decision; it is listed, not created."""


def today():
    return datetime.now(timezone.utc).date().isoformat()


# ---------------------------------------------------------------- workspace

def pilot_paths(root):
    return root / "first-ten.ready.json", root / "six-items.create.plan.json"


def read_attempts(plan_path):
    path = Path(str(plan_path) + ".journal.sqlite3")
    if not path.exists():
        return []
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT record_id,status,details FROM attempts").fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        db.close()
    return [(rid, status, json.loads(details)) for rid, status, details in rows]


def plan_state(plan_path):
    if plan_path is None:
        return "nothing to publish"
    saved = p.read_json(plan_path)
    ops = [op["record_id"] for op in saved["operations"]]
    statuses = {rid: status for rid, status, _ in read_attempts(plan_path)}
    if any(s == "pending" for s in statuses.values()):
        return "UNCERTAIN SAVE"
    if any(s == "written_unverified" for s in statuses.values()):
        return "saved, needs re-check"
    done = sum(1 for op in ops if statuses.get(op) == "verified")
    if done == len(ops):
        return "published"
    return f"partly published ({done}/{len(ops)})" if done else "prepared, not published"


def list_pages(root=ROOT):
    pages = []
    pilot_input, pilot_plan = pilot_paths(root)
    if pilot_plan.exists() and pilot_input.exists():
        records = p.read_json(pilot_input)["records"]
        pages.append({"number": 1, "name": "pilot", "dir": root, "plan": pilot_plan,
                      "record_ids": [r["record_id"] for r in records],
                      "rows": [r.get("csv_data_row") for r in records], "held": []})
    for d in sorted((root / "pages").glob("page-*")):
        m = p.read_json(d / "page.json")
        pages.append({"number": m["number"], "name": d.name, "dir": d,
                      "plan": d / "plan.json" if m.get("plan") else None,
                      "record_ids": m["ready_ids"] + [h["record_id"] for h in m["held"]],
                      "rows": m["rows"], "held": m["held"]})
    for page in pages:
        page["state"] = plan_state(page["plan"])
    return pages


def ledger(pages):
    """Works this tool already created: work_key -> QID. Uncertain saves stop everything."""
    created = {}
    for page in pages:
        if page["plan"] is None:
            continue
        for rid, status, details in read_attempts(page["plan"]):
            if status == "pending":
                raise p.Stop(f"Page {page['number']} has an uncertain save ({rid}). Inspect it before preparing more.")
            if details.get("qid"):
                created[details["work_key"]] = details["qid"]
    return created


def default_csv(root):
    if os.environ.get("FLORA_CSV"):
        return Path(os.environ["FLORA_CSV"])
    pilot_input, _ = pilot_paths(root)
    if pilot_input.exists():
        return Path(p.read_json(pilot_input)["source_csv"]["path"])
    raise p.Stop("Give the FLoRA CSV with --csv.")


# ---------------------------------------------------------------- registry

class Registry:
    """Read-only DOI registration metadata. Crossref and DataCite metadata are CC0."""

    def __init__(self, contact, session=None):
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": f"FLoRA-reviewed-pilot/{p.VERSION} ({contact})"})
        self.last = 0.0

    def get(self, url):
        for attempt in range(4):
            time.sleep(max(0, 1.0 - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            try:
                r = self.session.get(url, timeout=(10, 45), allow_redirects=False)
            except requests.RequestException:
                if attempt < 3:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise p.Stop("Network failure reading DOI registry metadata; nothing was saved. Try again later.") from None
            if r.status_code == 404:
                return None
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                retry = r.headers.get("Retry-After", "")
                delay = max(5, 2 ** (attempt + 1), int(retry) if retry.isdigit() else 0)
                if delay > 120:
                    raise p.Stop("A DOI registry asked for a long pause; try again later.")
                time.sleep(delay)
                continue
            if r.status_code != 200:
                raise p.Stop(f"A DOI registry answered HTTP {r.status_code}; nothing was saved.")
            try:
                return r.json()
            except ValueError:
                raise p.Stop("A DOI registry returned invalid JSON; nothing was saved.") from None
        raise p.Stop("DOI registry retry budget exhausted; try again later.")

    def lookup(self, doi):
        ra = self.get("https://doi.org/doiRA/" + quote(doi, safe="/"))
        agency = ra[0].get("RA", "") if isinstance(ra, list) and ra and isinstance(ra[0], dict) else ""
        if agency == "Crossref":
            url = "https://api.crossref.org/works/" + quote(doi, safe="")
            data = self.get(url)
            if not data or not isinstance(data.get("message"), dict):
                raise Hold("DOI not found at Crossref")
            return "crossref", url, data["message"]
        if agency == "DataCite":
            url = "https://api.datacite.org/dois/" + quote(doi, safe="")
            data = self.get(url)
            if not data or not isinstance(data.get("data", {}).get("attributes"), dict):
                raise Hold("DOI not found at DataCite")
            return "datacite", url, data["data"]["attributes"]
        raise Hold(f"DOI registration agency is {agency or 'unknown'}; only Crossref and DataCite are handled automatically")


# ---------------------------------------------------------------- metadata

def clean_title(raw):
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw or ""))
    text = " ".join(text.split())
    return re.sub(r"\s+([?.,;:!])", r"\1", text)


def norm(text):
    return " ".join(re.sub(r"[^0-9a-z]+", " ", (text or "").lower()).split())


def titles_agree(a, b):
    na, nb = norm(a), norm(b)
    if not na or not nb or na == nb:
        return True
    short, long_ = sorted((na, nb), key=len)
    if long_.startswith(short + " ") and (len(short.split()) >= 3 or len(short) >= 0.5 * len(long_)):
        return True  # one side omits the subtitle
    for raw, other in ((a, nb), (b, na)):  # "Main title: subtitle" vs "Main title"
        head = norm(re.split(r"\s*[:?!.]\s+|\s+[-–—]\s+", raw, maxsplit=1)[0])
        if head and head == other:
            return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.85


def _first(value):
    if isinstance(value, list):
        return value[0] if value and isinstance(value[0], str) else ""
    return value if isinstance(value, str) else ""


def _person(given, family, name="", personal=True):
    given, family, name = (" ".join((x or "").split()) for x in (given, family, name))
    if given or family:
        return " ".join(filter(None, (given, family)))
    if personal and name.count(",") == 1:
        family, given = (x.strip() for x in name.split(","))
        return " ".join(filter(None, (given, family)))
    return name


def from_crossref(d):
    kind, subtype = d.get("type", ""), d.get("subtype", "")
    if kind == "journal-article":
        cls = "article"
    elif kind == "posted-content" and subtype == "preprint":
        cls = "preprint"
    else:
        raise Hold(f"Crossref type '{kind}{'/' + subtype if subtype else ''}' needs a manual modelling decision")
    parts = ((d.get("published") or d.get("issued") or {}).get("date-parts") or [[None]])[0] or [None]
    return {"cls": cls, "title": clean_title(_first(d.get("title"))),
            "subtitle": clean_title(_first(d.get("subtitle"))), "year": parts[0],
            "authors": [_person(a.get("given"), a.get("family"), a.get("name"), personal=False)
                        for a in d.get("author") or [] if isinstance(a, dict)],
            "extra": {"P478": d.get("volume"), "P433": d.get("issue"), "P304": d.get("page")},
            "issns": [x for x in d.get("ISSN") or [] if isinstance(x, str)],
            "language": d.get("language") or ""}


def from_datacite(d):
    types = d.get("types") or {}
    general, specific = types.get("resourceTypeGeneral", ""), (types.get("resourceType") or "").strip().lower()
    if general == "StudyRegistration" or specific in ("preregistration", "pre-registration"):
        cls = "preregistration"
    elif general == "Preprint" or specific == "preprint":
        cls = "preprint"
    elif general == "JournalArticle":
        cls = "article"
    else:
        raise Hold(f"DataCite type '{general}/{specific}' needs a manual modelling decision")
    titles = [t for t in d.get("titles") or [] if isinstance(t, dict)]
    main = [t for t in titles if not t.get("titleType")] or titles
    year = d.get("publicationYear")
    return {"cls": cls, "title": clean_title(main[0].get("title", "") if main else ""), "subtitle": "",
            "year": int(year) if str(year or "").isdigit() else None,
            "authors": [_person(a.get("givenName"), a.get("familyName"), a.get("name"),
                                personal=a.get("nameType", "Personal") == "Personal")
                        for a in d.get("creators") or [] if isinstance(a, dict)],
            "extra": {}, "issns": [], "language": d.get("language") or ""}


def csv_language(work):
    for a in work.get("attributes", []):
        if a.get("property") == "P1476":
            return (a.get("value") or {}).get("language", "")
    return ""


def description(meta, doi):
    year = meta["year"]
    if meta["cls"] == "article":
        return f"scholarly article first published in {year}"
    if meta["cls"] == "preprint":
        return f"preprint first posted in {year}"
    if doi.startswith("10.17605/OSF.IO/"):
        return f"research preregistration deposited on OSF in {year}"
    return f"research preregistration published in {year}"


def creation_attributes(meta, url, journal):
    title, year = meta["title"], meta["year"]
    if not title:
        raise Hold("the DOI registry has no title")
    if len(title) > LABEL_MAX:
        raise Hold("title is longer than Wikidata's 250-character label limit")
    if not isinstance(year, int) or not 1500 <= year <= datetime.now(timezone.utc).year:
        raise Hold("the DOI registry has no usable publication year")
    if len(meta["authors"]) > MAX_AUTHORS:
        raise Hold(f"more than {MAX_AUTHORS} authors; review manually")
    names = [n for n in meta["authors"] if n]
    if len(set(names)) != len(names):
        raise Hold("the same author name appears twice in the registry record")
    values = [title] + names + [v for v in meta["extra"].values() if isinstance(v, str)]
    if any(ord(ch) < 32 for v in values for ch in v):
        raise Hold("control characters in registry metadata")
    attrs = []

    def add(prop, kind, value, **extra):
        attrs.append({"property": prop, "datatype": kind, "value": value, "source_url": url, **extra})

    add("P31", "wikibase-item", CLASSES[meta["cls"]][0])
    add("P1476", "monolingualtext", {"text": title, "language": "en"})
    add("P577", "time", f"{year:04d}-01-01", precision=9)
    for ordinal, name in enumerate(meta["authors"], 1):
        if name:
            add("P2093", "string", name, ordinal=ordinal)
    for prop in ("P478", "P433", "P304"):
        value = meta["extra"].get(prop)
        if isinstance(value, str) and value.strip():
            add(prop, "string", value.strip())
    if journal:
        add("P1433", "wikibase-item", journal)
    return attrs


# ---------------------------------------------------------------- Wikidata lookups

def doi_items(api, doi, props):
    data = api.call({"action": "query", "list": "search", "srnamespace": 0,
                     "srsearch": p.identifier_search(props["doi"], doi), "srlimit": 50, "srprop": ""})
    if "continue" in data:
        raise Hold("too many Wikidata items match this DOI")
    ident = {"property": props["doi"], "datatype": "external-id", "value": doi}
    found = set()
    for hit in data["query"]["search"]:
        e = api.entity(p.qid(hit["title"]))
        if c.has_identifier(e, ident, props):
            found.add(e["id"])
    return found


def title_candidates(api, title):
    data = api.call({"action": "wbsearchentities", "search": title[:LABEL_MAX], "language": "en",
                     "type": "item", "limit": 10})
    out = []
    for hit in data.get("search", []):
        label = norm(hit.get("label") or hit.get("match", {}).get("text", ""))
        if label and (label == norm(title) or difflib.SequenceMatcher(None, label, norm(title)).ratio() >= 0.9):
            out.append(hit["id"])
    return out


def journal_items(api, issns, cache):
    found = set()
    for issn in issns:
        issn = issn.strip().upper()
        if not re.fullmatch(r"[0-9]{4}-[0-9]{3}[0-9X]", issn):
            continue
        if issn not in cache:
            data = api.call({"action": "query", "list": "search", "srnamespace": 0,
                             "srsearch": p.identifier_search("P236", issn), "srlimit": 10, "srprop": ""})
            qids = set()
            for hit in data["query"]["search"]:
                e = api.entity(p.qid(hit["title"]))
                claims = e.get("claims", {})
                has_issn = any(cl.get("mainsnak", {}).get("datavalue", {}).get("value") == issn
                               for cl in claims.get("P236", []))
                # A mistyped article item can carry its journal's ISSN; never use it as the journal.
                is_article = any(p.item_value(cl.get("mainsnak", {})) == SCHOLARLY_ARTICLE
                                 for cl in claims.get("P31", []))
                if has_issn and not is_article:
                    qids.add(e["id"])
            cache[issn] = qids
        found |= cache[issn]
    return found


def check_classes(api):
    for qid, label in CLASSES.values():
        actual = api.entity(qid).get("labels", {}).get("en", {}).get("value")
        if actual != label:
            raise p.Stop(f"{qid} is labelled '{actual}', expected '{label}'. Stop and review the class mapping.")


# ---------------------------------------------------------------- assessment

def assess(api, registry, work, created, cache, props, page_label):
    """-> ("reuse"|"create", work dict, notes) or ("hold", reason, [])."""
    key = work["work_key"]
    if key in cache:
        return copy.deepcopy(cache[key])
    try:
        result = _assess(api, registry, work, created, cache, props, page_label)
    except Hold as exc:
        result = ("hold", str(exc), [])
    cache[key] = copy.deepcopy(result)
    return result


def _assess(api, registry, work, created, cache, props, page_label):
    if work.get("csv_problems"):
        raise Hold("; ".join(work["csv_problems"]))
    if not work.get("doi"):
        raise Hold("no DOI in the CSV; identify the work manually")
    doi = work["work_key"]
    if not SEARCHABLE_DOI.fullmatch(doi):
        raise Hold("DOI contains characters Wikidata search may misread; check for an existing item manually")
    title_csv = work.get("title", "")
    if doi in created:
        return "reuse", {"work_key": doi, "qid": created[doi], "doi": doi, "title": title_csv}, \
            ["created by an earlier FLoRA page"]
    matches = doi_items(api, doi, props)
    if len(matches) > 1:
        raise Hold(f"several Wikidata items carry this DOI ({', '.join(sorted(matches))}); resolve the duplicates first")
    if matches:
        return "reuse", {"work_key": doi, "qid": matches.pop(), "doi": doi, "title": title_csv}, []
    service, url, data = registry.lookup(doi)
    meta = from_crossref(data) if service == "crossref" else from_datacite(data)
    notes = []
    language_csv, language_registry = csv_language(work), str(meta["language"]).lower()
    # Clean means recorded, not guessed: either the CSV or the registry must say English.
    if language_csv != "en" and not language_registry.startswith("en"):
        raise Hold(f"title language not confirmed as English (CSV: {language_csv or 'none'}, "
                   f"registry: {language_registry or 'none'})")
    if not (titles_agree(title_csv, meta["title"]) or
            titles_agree(title_csv, f"{meta['title']} {meta['subtitle']}")):
        raise Hold("the CSV title differs from the DOI registry title; check that the DOI is right")
    candidates = title_candidates(api, meta["title"])
    if candidates:
        raise Hold(f"possible existing item without this DOI ({', '.join(candidates)}); check manually")
    journals = journal_items(api, meta["issns"], cache.setdefault("__issn__", {}))
    journal = next(iter(journals)) if len(journals) == 1 else None
    if len(journals) > 1:
        notes.append("several journal items share the ISSN; 'published in' left out")
    elif meta["issns"] and not journals:
        notes.append("journal not found on Wikidata by ISSN; 'published in' left out")
    w = {"work_key": doi, "qid": "", "doi": doi, "title": meta["title"], "description": description(meta, doi),
         "metadata_source_url": url, "creation_reviewed": True,
         "creation_review_note": f"No Wikidata item with this DOI and no item with a matching English label on "
                                 f"{today()}; metadata from the {service.title()} registration record. "
                                 f"Operator reviews {page_label} before publishing.",
         "attributes": creation_attributes(meta, url, journal)}
    return "create", w, notes


def build_page(rows, api, registry, created, number, csv_path, csv_sha):
    """rows: [(row_number, csv_row)]. Returns (input_data or None, manifest, review rows)."""
    props = p.model_for("wikidata")["properties"]
    label = f"FLoRA page {number}"
    check_classes(api)
    records = c.csv_records(rows)
    cache, ready, held, review = {}, [], [], []
    qid_owner, label_desc = {}, {}
    for record in records:
        results = {role: assess(api, registry, record[role], created, cache, props, label)
                   for role in ("original", "replication")}
        reasons = [f"{role}: {r[1]}" for role, r in results.items() if r[0] == "hold"]
        keys = {role: record[role]["work_key"] for role in results}
        if keys["original"] == keys["replication"]:
            reasons.append("original and replication are the same work")
        # Two different works must not end up on one item, and two new items must not
        # share label+description (Wikidata rejects that pair as a conflict).
        qids, pairs = dict(qid_owner), dict(label_desc)
        if not reasons:
            for role, (status, w, _) in results.items():
                if status == "reuse" and qids.setdefault(w["qid"], keys[role]) != keys[role]:
                    reasons.append(f"{role}: {w['qid']} is already used for a different DOI in this page")
                if status == "create" and pairs.setdefault((w["title"].casefold(), w["description"]), keys[role]) != keys[role]:
                    reasons.append(f"{role}: another new item in this page has the same title and description")
        if reasons:
            held.append({"row": record["csv_data_row"], "record_id": record["record_id"], "reasons": reasons})
            continue
        qid_owner, label_desc = qids, pairs
        for role, (status, w, notes) in results.items():
            review.append({"row": record["csv_data_row"], "role": role, "status": status, "work": w, "notes": notes})
        record.update(metadata_reviewed=True, verified_at=today(), retrieved=today(),
                      verified_by="flora_pages.py registry and Wikidata checks; operator reviews REVIEW.md before publishing",
                      review_note="Bibliographic metadata from DOI registration records; original/replication roles "
                                  "from the CSV only select works. No outcome assertion imported.")
        record["original"], record["replication"] = (copy.deepcopy(results[r][1]) for r in ("original", "replication"))
        ready.append(record)
    row_numbers = [n for n, _ in rows]
    page = {"number": number, "first_row": min(row_numbers), "last_row": max(row_numbers), "rows": len(rows)}
    data = None
    if ready:
        data = {"schema_version": 1, "environment": "wikidata", "items_only": True, "page": page,
                "source_csv": {"path": str(Path(csv_path).resolve()), "sha256": csv_sha,
                               "selection": f"{len(rows)} not-yet-handled data rows in file order"},
                "dataset": {**DATASET, "version": "CSV SHA256 " + csv_sha}, "records": ready}
    manifest = {"number": number, "created_at": p.now(), "csv": str(Path(csv_path).resolve()), "csv_sha256": csv_sha,
                "rows": row_numbers, "ready_ids": [r["record_id"] for r in ready], "held": held,
                "plan": bool(ready)}
    return data, manifest, review


# ---------------------------------------------------------------- review file

def review_markdown(manifest, saved, review):
    n = manifest["number"]
    lines = [f"# FLoRA page {n}: review before publishing", "",
             f"CSV rows {min(manifest['rows'])}-{max(manifest['rows'])} ({len(manifest['rows'])} rows not handled by earlier pages).",
             f"CSV SHA-256 `{manifest['csv_sha256'][:16]}…`. Prepared {manifest['created_at'][:16].replace('T', ' ')} UTC.",
             "", "**Nothing has been saved.** Publishing creates the new items below under your account.", ""]
    creates = [r for r in review if r["status"] == "create"]
    reuses = [r for r in review if r["status"] == "reuse"]
    lines += ["| | Count |", "|---|---:|",
              f"| Rows ready | {len(manifest['ready_ids'])} |",
              f"| Rows held for you (not touched) | {len(manifest['held'])} |",
              f"| Existing items reused | {len({r['work']['qid'] for r in reuses})} |",
              f"| New items to create | {len(saved['operations']) if saved else 0} |", ""]
    urls = c.group_urls(saved) if saved else {}
    if saved:
        lines += [f"Plan SHA-256: `{p.digest(saved)}`. The plan expires 24 hours after preparation.", "",
                  "Only rows whose original **and** replication are both clean are included. Originals and "
                  "replications are saved as **separate edit groups** (originals first, with a confirmation "
                  "before each group), so either side can be undone without touching the other:", ""]
        lines += [f"- {group}: {url}" for group, url in urls.items()]
        lines += ["", "Undoing created items needs an administrator to delete them; EditGroups files that request.", ""]
    key_group = {op["key"]: c.op_group(saved, op) for op in saved["operations"]} if saved else {}
    for group, heading in (("originals", "New items: original studies"),
                           ("replications", "New items: replication and reproduction studies")):
        rows_in_group = [r for r in creates if key_group.get(r["work"]["work_key"]) == group]
        if not rows_in_group:
            continue
        lines += [f"## {heading}", "", "| Row | Title | Class | Year | Journal | Authors | DOI |",
                  "|---:|---|---|---:|---|---:|---|"]
        seen = set()
        for r in rows_in_group:
            w = r["work"]
            if w["work_key"] in seen:
                continue
            seen.add(w["work_key"])
            attrs = w["attributes"]
            get = lambda prop: [a["value"] for a in attrs if a["property"] == prop]
            cls = next(name for qid, name in CLASSES.values() if qid == get("P31")[0])
            journal = get("P1433")
            title = w["title"].replace("|", "\\|")
            lines.append(f"| {r['row']} | {title} | {cls} | {get('P577')[0][:4]} | "
                         f"{'[' + journal[0] + '](https://www.wikidata.org/wiki/' + journal[0] + ')' if journal else '—'} | "
                         f"{len(get('P2093'))} | [{w['doi']}](https://doi.org/{w['doi']}) |")
        notes = [(r["row"], r["work"]["title"], n) for r in rows_in_group for n in r["notes"]]
        if notes:
            lines += ["", "Notes:"] + [f"- Row {row}, *{t}*: {n}" for row, t, n in notes]
        lines.append("")
    if reuses:
        lines += ["## Reused existing items", ""]
        lines += [f"- Row {r['row']} {r['role']}: [{r['work']['qid']}](https://www.wikidata.org/wiki/{r['work']['qid']}) "
                  f"(DOI {r['work']['doi']}){' — ' + '; '.join(r['notes']) if r['notes'] else ''}" for r in reuses]
        lines.append("")
    if manifest["held"]:
        lines += ["## Held rows (need your decision; nothing is created for them)", ""]
        lines += [f"- Row {h['row']} (`{h['record_id']}`): {'; '.join(h['reasons'])}" for h in manifest["held"]]
        lines.append("")
    lines += ["## Before you publish", "",
              "- Open a few DOI links and new-item rows and compare them with the publisher's page.",
              "- Check the held rows later; they stay out of every future page until you decide.",
              "- Publish with `.\\PUBLISH_PAGE.cmd`: one confirmation for the originals, then one for the "
              "replications. You can stop after the originals, check them, and run it again for the replications.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- commands

def prepare(count, csv_path=None, start=1, root=ROOT, api=None, registry=None):
    if not 1 <= count <= p.PAGE_MAX_ROWS:
        raise p.Stop(f"A page holds 1 to {p.PAGE_MAX_ROWS} rows.")
    pages = list_pages(root)
    if not pages:
        raise p.Stop("Run and publish the 10-row pilot first.")
    for page in pages:
        if page["state"] not in ("published", "nothing to publish"):
            raise p.Stop(f"Page {page['number']} ({page['name']}) is {page['state']}. Publish it (or discard it if "
                         "nothing was saved) before preparing the next page.")
    created = ledger(pages)
    covered = {rid for page in pages for rid in page["record_ids"]}
    csv_path = Path(csv_path) if csv_path else default_csv(root)
    csv_sha = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    rows, seen = [], set()
    for number, row in c.csv_rows(csv_path, 1, 10 ** 7):
        rid = c.clean_csv(row.get("id", "")) or f"row-{number}"
        if number < start or rid in covered or rid in seen:
            continue
        seen.add(rid)
        rows.append((number, row))
        if len(rows) == count:
            break
    if not rows:
        print("Every CSV row is already handled. Nothing to prepare.")
        return None
    number = max(page["number"] for page in pages) + 1
    out = root / "pages" / f"page-{number:03d}"
    if out.exists():
        raise p.Stop(f"{out} already exists.")
    print(f"Preparing page {number}: {len(rows)} rows starting at CSV row {rows[0][0]}. Read-only; nothing is saved.")
    api = api or p.API("wikidata", CONTACT)
    registry = registry or Registry(CONTACT)
    data, manifest, review = build_page(rows, api, registry, created, number, csv_path, csv_sha)
    saved = c.plan(data, api, p.model_for("wikidata")) if data else None
    out.mkdir(parents=True)
    if saved:
        p.write_new(out / "input.json", data)
        p.write_new(out / "plan.json", saved)
        (out / "plan.sha256").write_text(p.digest(saved) + "\n", encoding="utf-8")
    p.write_new(out / "page.json", manifest)
    (out / "REVIEW.md").write_text(review_markdown(manifest, saved, review), encoding="utf-8")
    print(f"Page {number}: {len(manifest['ready_ids'])} rows ready, {len(manifest['held'])} held, "
          f"{len(saved['operations']) if saved else 0} new items to create.")
    print("Review:", out / "REVIEW.md")
    return out


def find_page(root, name=None):
    pages = [pg for pg in list_pages(root) if pg["name"] != "pilot"]
    if not pages:
        raise p.Stop("No prepared page. Run PREPARE_PAGE.cmd first.")
    if name is None:
        return pages[-1]
    for page in pages:
        if page["name"] == name or str(page["number"]) == str(name).lstrip("0"):
            return page
    raise p.Stop(f"No page named {name}.")


def publish(name=None, root=ROOT, each=False):
    page = find_page(root, name)
    if page["plan"] is None:
        print(f"Page {page['number']} has nothing to create (all rows reused or held).")
        return
    if page["state"] == "published":
        print(f"Page {page['number']} is already published.")
        return
    plan_path = page["plan"]
    approved = (page["dir"] / "plan.sha256").read_text(encoding="utf-8").strip()
    saved = p.read_json(plan_path)
    if p.digest(saved) != approved:
        raise p.Stop("The page plan changed after preparation. Discard it and prepare the page again.")
    if not sys.stdin.isatty():
        raise p.Stop("Open this launcher yourself in a local terminal.")
    print(f"PUBLIC WIKIDATA CREATION, page {page['number']}, account {USER}: up to {len(saved['operations'])} new items.")
    print("Review first:", page["dir"] / "REVIEW.md")
    print("Typing the APPLY phrase later confirms that you reviewed that file and the checklist in PAGES.md.")
    credentials = p.load_local_credentials("FLORA_WD_")
    username = os.environ.get("FLORA_WD_BOT_USERNAME") or credentials.get("FLORA_WD_BOT_USERNAME")
    if not username:
        username = input(f"Generated bot-password login name [{USER}@FLoRA]: ").strip() or f"{USER}@FLoRA"
    os.environ["FLORA_WD_BOT_USERNAME"] = username
    c.apply(SimpleNamespace(plan=str(plan_path), approved_sha256=approved, apply=True, data_verified=True,
                            policy_reviewed=True, allow_live=True, auth="bot-password", expected_user=USER,
                            contact=CONTACT, confirm="each" if each else "page"))


def status(root=ROOT):
    pages = list_pages(root)
    if not pages:
        print("No pilot or pages yet.")
        return
    held_total = 0
    for page in pages:
        groups = []
        if page["plan"] is not None:
            saved = p.read_json(page["plan"])
            verified = {rid for rid, s, _ in read_attempts(page["plan"]) if s == "verified"}
            for group, url in c.group_urls(saved).items():
                ops = [op for op in saved["operations"] if c.op_group(saved, op) == group]
                done = sum(1 for op in ops if op["record_id"] in verified)
                groups.append(f"          {group}: {done}/{len(ops)} created; edit group {url}")
            work = f"{len(verified)}/{len(saved['operations'])} created"
        else:
            work = "no creations"
        rows = [r for r in page["rows"] if r]
        held_total += len(page["held"])
        print(f"Page {page['number']:>3} {page['name']:<9} rows {min(rows)}-{max(rows)}: {page['state']}; {work}; "
              f"{len(page['held'])} held")
        for line in groups:
            print(line)
    print(f"Handled CSV records: {sum(len(pg['record_ids']) for pg in pages)}; held for manual review: {held_total}.")


def refresh_pilot(root=ROOT, api=None):
    """Re-plan the 10-row pilot after its 24-hour plan expired. Read-only; refuses after any save."""
    pilot_input, pilot_plan = pilot_paths(root)
    if read_attempts(pilot_plan):
        raise p.Stop("The pilot already has save attempts. Do not refresh it; run CREATE_MISSING_ITEMS.cmd to resume.")
    old = p.read_json(pilot_plan)
    print("Re-checking the pilot against current Wikidata. Read-only; nothing is saved.")
    fresh = c.plan(p.read_json(pilot_input), api or p.API("wikidata", CONTACT), p.model_for("wikidata"))
    same = all(c.comparable(fresh, f) == c.comparable(old, f) for f in ("works", "operations"))
    archive = root / "superseded" / f"pilot-plan-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    archive.mkdir(parents=True)
    for path in (pilot_plan, root / "six-items.create.plan.sha256", Path(str(pilot_plan) + ".journal.sqlite3")):
        if path.exists():
            shutil.move(str(path), str(archive / path.name))
    p.write_new(pilot_plan, fresh)
    digest = p.digest(fresh)
    (root / "six-items.create.plan.sha256").write_text(digest + "\n", encoding="utf-8")
    review = root / "CREATION_REVIEW.md"
    if review.exists():
        text = re.sub(r"Plan SHA-256: [0-9a-f]{64}", "Plan SHA-256: " + digest, review.read_text(encoding="utf-8"))
        if not same:
            text = ("> **The refreshed plan differs from the reviewed one.** Current Wikidata data changed; compare "
                    f"`six-items.create.plan.json` with `{archive.name}` before publishing.\n\n") + text
        review.write_text(text, encoding="utf-8")
    print(f"Refreshed. Valid for 24 hours. {len(fresh['operations'])} creations. Old plan kept in {archive}.")
    print("Same creations as the reviewed plan." if same else
          "WARNING: the creations differ from the reviewed plan. Review CREATION_REVIEW.md and the plan again.")
    return same


def plan_expiry(plan_path):
    created = datetime.fromisoformat(p.read_json(plan_path)["created_at"])
    return created.timestamp() + 86400


def discard(name, root=ROOT):
    page = find_page(root, name)
    if page["plan"] is not None and read_attempts(page["plan"]):
        raise p.Stop("This page has save attempts; it cannot be discarded. Publish (resume) it instead.")
    target = root / "pages" / "_discarded" / f"{page['dir'].name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(page["dir"]), str(target))
    print(f"Page {page['number']} set aside in {target}. Its rows will be offered again by the next prepare.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("prepare")
    a.add_argument("--count", type=int, default=DEFAULT_COUNT)
    a.add_argument("--start-row", type=int, default=1, help="skip CSV rows before this one")
    a.add_argument("--csv")
    a = sub.add_parser("publish")
    a.add_argument("page", nargs="?")
    a.add_argument("--each", action="store_true",
                   help="confirm every save individually (usable before the account is autoconfirmed)")
    sub.add_parser("status")
    sub.add_parser("refresh-pilot")
    a = sub.add_parser("discard")
    a.add_argument("page")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            prepare(args.count, args.csv, args.start_row)
        elif args.command == "publish":
            publish(args.page, each=args.each)
        elif args.command == "status":
            status()
        elif args.command == "refresh-pilot":
            refresh_pilot()
        else:
            discard(args.page)
        return 0
    except (p.Stop, KeyError, TypeError, ValueError, OSError, sqlite3.Error) as exc:
        print("STOP:", str(exc) if isinstance(exc, p.Stop) else f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Stopped. Inspect the page state (PAGE_STATUS.cmd) before resuming.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
