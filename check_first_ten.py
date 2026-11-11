"""Read-only online test: reconcile the first ten CSV rows; never log in or save."""
import argparse
import csv
import hashlib
from itertools import islice
import json
from pathlib import Path

import flora_pilot as p


class ReadOnlyAPI(p.API):
    def call(self, params, *, post=False, write=False):
        if post or write or params.get('action') not in ('query', 'wbgetentities'):
            raise p.Stop('The online test permits only read requests.')
        return super().call(params)


def check(csv_path, out, username):
    source = Path(csv_path)
    with source.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(islice(csv.DictReader(stream), 10))
    if len(rows) != 10:
        raise p.Stop('Expected at least ten CSV data rows.')
    api = ReadOnlyAPI('wikidata', 'https://www.wikidata.org/wiki/User:' + username)
    result = {'checked_at': p.now(), 'mode': 'read-only; no login; no writes',
              'csv': str(source.resolve()), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'rows': [], 'works': {}}
    account = api.call({'action': 'query', 'list': 'users', 'ususers': username,
                        'usprop': 'groups|rights|editcount|registration'})['query']['users'][0]
    result['public_account_info'] = account
    for number, row in enumerate(rows, 1):
        pair = {'csv_row': number, 'record_id': row.get('id', '')}
        for role, suffix in (('original', 'o'), ('replication', 'r')):
            raw_doi = row.get('doi_' + suffix, '').strip()
            title = row.get('title_' + suffix, '').strip()
            identifier = p.doi(raw_doi) if raw_doi and raw_doi != 'NA' else ''
            key = identifier or role + ':' + str(number)
            pair[role] = key
            if key in result['works']: continue
            work = {'doi': identifier, 'title': title, 'exact_matches': [], 'title_candidates': []}
            if identifier:
                hits = api.call({'action': 'query', 'list': 'search', 'srnamespace': 0,
                    'srsearch': p.identifier_search('P356', identifier), 'srlimit': 50, 'srprop': ''})
                if 'continue' in hits: raise p.Stop('Identifier results need pagination; inspect manually.')
                for hit in hits['query']['search']:
                    entity = api.entity(p.qid(hit['title']))
                    if identifier in p.entity_dois(entity, p.PROPERTIES):
                        work['exact_matches'].append({'qid': entity['id'], 'revision': entity['lastrevid'],
                            'label': entity.get('labels', {}).get('en', {}).get('value', '')})
            if len(work['exact_matches']) == 1:
                work['status'] = 'reuse_existing'
            elif len(work['exact_matches']) > 1:
                work['status'] = 'ambiguous_identifier'
            else:
                hits = api.call({'action': 'query', 'list': 'search', 'srnamespace': 0,
                    'srsearch': json.dumps(title), 'srlimit': 5, 'srprop': ''})
                for hit in hits['query']['search']:
                    e = api.entity(p.qid(hit['title']))
                    work['title_candidates'].append({'qid': e['id'],
                        'label': e.get('labels', {}).get('en', {}).get('value', '')})
                work['status'] = 'no_exact_doi_match_review_before_creation'
            result['works'][key] = work
            print(f"Row {number} {role}: {work['status']} " + ', '.join(m['qid'] for m in work['exact_matches']), flush=True)
        result['rows'].append(pair)
    counts = {}
    for work in result['works'].values(): counts[work['status']] = counts.get(work['status'], 0) + 1
    result['counts'] = counts
    target = Path(out)
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    lines = ['# First ten rows: online read-only test', '', 'No login or Wikidata edits were made.',
             '', 'Checked: ' + result['checked_at'], 'Source: ' + str(source.resolve()), '',
             f"Ten rows; {len(result['works'])} distinct works.", '', 'Results: ' + p.canonical(counts), '',
             '## Public account information', '', '```json', json.dumps(account, indent=2), '```', '',
             'A missing DOI match is a creation candidate, not proof that no item exists. Check title matches and source metadata.', '']
    for pair in result['rows']:
        lines += [f"## Row {pair['csv_row']}: {pair['record_id']}", '']
        for role in ('original', 'replication'):
            work = result['works'][pair[role]]
            lines += [f"**{role.title()}:** {work['title']}", '', f"Status: {work['status']}",
                      f"DOI: {work['doi']}", '']
            for match in work['exact_matches'] + work['title_candidates']:
                lines += [f"- [{match['qid']}](https://www.wikidata.org/wiki/{match['qid']}): {match['label']}"]
            lines.append('')
    Path(str(target) + '.md').write_text('\n'.join(lines), encoding='utf-8')
    print('Results:', p.canonical(counts), flush=True)
    print('Report:', str(target) + '.md', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', required=True)
    parser.add_argument('--out', default='online-check.json')
    parser.add_argument('--username', default='Hb19988')
    args = parser.parse_args()
    try:
        check(args.csv, args.out, args.username)
    except p.Stop as exc:
        parser.exit(2, 'STOP: ' + str(exc) + '\n')
