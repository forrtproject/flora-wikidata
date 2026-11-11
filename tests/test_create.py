import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from test_pilot import fixture, entity
import flora_pilot as p
import flora_create as c


class FakeAPI(p.FixtureAPI):
    def __init__(self):
        data, model, values = fixture()
        super().__init__(values, 'test')
        self.model = model
        self.endpoint = p.TARGETS['test']
        self.counter = 950000000
        self.posts = []
        self.fixture['entities']['P31'] = {**entity('P31', 'instance of'), 'datatype': 'wikibase-item'}
        self.fixture['entities']['Q1'] = entity('Q1', 'test work')

    def call(self, params, **kwargs):
        if params['action'] == 'query': return {'query': {'search': []}}
        if params['action'] == 'wbgetentities': return {'entities': {params['ids']: self.entity(params['ids'])}}
        if params['action'] == 'wbeditentity':
            self.posts.append(params)
            self.counter += 1
            key = 'Q' + str(self.counter)
            payload = json.loads(params['data'])
            result = {**entity(key), **payload, 'claims': {}}
            for i, claim in enumerate(payload['claims']):
                claim['id'] = key + '$' + str(i)
                result['claims'].setdefault(claim['mainsnak']['property'], []).append(claim)
            self.fixture['entities'][key] = result
            return {'success': 1, 'entity': result}
        raise AssertionError(params)

    def authenticate(self, method, user, **kwargs): return {'name': user, 'rights': ['edit', 'createpage']}
    def csrf(self): return 'FAKE-SECRET'


def input_data():
    data = {'schema_version': 1, 'environment': 'test', 'items_only': True,
            'dataset': {'name': 'TEST', 'version': 'v1', 'url': 'https://forrt.org/flora/', 'cc0_compatible': True}, 'records': []}
    for i in range(10):
        record = {'record_id': str(i), 'metadata_reviewed': True, 'verified_by': 'TEST',
                  'review_note': 'Synthetic test', 'verified_at': '2026-11-11', 'retrieved': '2026-11-11'}
        for role in ('original', 'replication'):
            key = role + str(i)
            record[role] = {'work_key': key, 'title': key, 'description': 'synthetic test work', 'doi': '10.1234/' + key,
                            'metadata_source_url': 'https://forrt.org/flora/', 'creation_reviewed': True,
                            'creation_review_note': 'Synthetic fixture', 'instance_of_property': 'P31',
                            'attributes': [{'property': 'P31', 'datatype': 'wikibase-item', 'value': 'Q1',
                                            'source_url': 'https://forrt.org/flora/'}]}
        data['records'].append(record)
    return data


class CreationTests(unittest.TestCase):
    def test_twenty_missing_items_for_first_ten_pairs(self):
        api = FakeAPI()
        planned = c.plan(input_data(), api, api.model)
        self.assertEqual(len(planned['operations']), 20)
        self.assertTrue(all(op['kind'] == 'create' for op in planned['operations']))

    def test_existing_item_reused_without_attribute_changes(self):
        api = FakeAPI()
        data = input_data()
        data['records'][0]['original'] = {'work_key': 'existing', 'qid': 'Q1', 'title': 'existing'}
        planned = c.plan(data, api, api.model)
        self.assertEqual(len(planned['operations']), 19)
        self.assertEqual(planned['works']['existing']['qid'], 'Q1')

    def test_shared_original_created_only_once(self):
        api = FakeAPI()
        data = input_data()
        data['records'][1]['original'] = copy.deepcopy(data['records'][0]['original'])
        planned = c.plan(data, api, api.model)
        self.assertEqual(len(planned['operations']), 19)

    def test_unreviewed_input_stops(self):
        api = FakeAPI()
        data = input_data()
        data['records'][0]['metadata_reviewed'] = False
        with self.assertRaises(p.Stop): c.plan(data, api, api.model)

    def test_missing_creation_review_stops(self):
        api = FakeAPI()
        data = input_data()
        data['records'][0]['original']['creation_reviewed'] = False
        with self.assertRaises(p.Stop): c.plan(data, api, api.model)

    def test_eleven_rows_rejected(self):
        api = FakeAPI()
        data = input_data()
        data['records'].append(copy.deepcopy(data['records'][0]))
        with self.assertRaises(p.Stop): c.plan(data, api, api.model)

    def test_year_precision_and_monolingual_title(self):
        api = FakeAPI()
        api.fixture['entities']['P577'] = {**entity('P577'), 'datatype': 'time'}
        api.fixture['entities']['P1476'] = {**entity('P1476'), 'datatype': 'monolingualtext'}
        work = input_data()['records'][0]['original']
        work['attributes'] += [
            {'property': 'P577', 'datatype': 'time', 'value': '1972-01-01', 'precision': 9, 'source_url': 'https://forrt.org/flora/'},
            {'property': 'P1476', 'datatype': 'monolingualtext', 'value': {'text': 'A title', 'language': 'en'}, 'source_url': 'https://forrt.org/flora/'}]
        payload = c.metadata(api, work, api.model['properties'], '2026-11-11', {})
        claims = {x['mainsnak']['property']: x for x in payload['claims']}
        self.assertEqual(claims['P577']['mainsnak']['datavalue']['value']['precision'], 9)
        self.assertEqual(claims['P1476']['mainsnak']['datavalue']['type'], 'monolingualtext')

    def test_first_ten_in_file_order_without_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'flora.csv'
            with path.open('w', encoding='utf-8', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=['id', 'doi_o', 'doi_r', 'title_o', 'title_r'])
                writer.writeheader()
                for i in range(12): writer.writerow({'id': str(12-i), 'title_o': 'original', 'title_r': 'replication'})
            out = Path(tmp) / 'review.json'
            c.prepare_csv(path, out)
            data = p.read_json(out)
            self.assertEqual([r['record_id'] for r in data['records']], [str(12-i) for i in range(10)])
            self.assertTrue(all(not r['metadata_reviewed'] for r in data['records']))

    def test_twenty_mock_creations_login_verify_and_resume(self):
        api = FakeAPI()
        planned = c.plan(input_data(), api, api.model)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'plan.json'
            p.write_new(path, planned)
            args = SimpleNamespace(plan=str(path), approved_sha256=p.digest(planned), apply=True,
                allow_live=False, data_verified=True, policy_reviewed=True, auth='oauth2', expected_user='Hb19988', contact='test')
            def consent(prompt):
                return prompt.removeprefix('Type ').removesuffix(': ')
            with patch('flora_pilot.API', return_value=api), patch('sys.stdin.isatty', return_value=True), \
                 patch('builtins.input', side_effect=consent), patch('flora_create.time.sleep'), patch('sys.stdout', new=io.StringIO()):
                c.apply(args)
                c.apply(args)
            self.assertEqual(len(api.posts), 20)
            self.assertTrue(all(x['new'] == 'item' for x in api.posts))
            self.assertNotIn('FAKE-SECRET', Path(str(path)+'.audit.jsonl').read_text())


if __name__ == '__main__': unittest.main()
