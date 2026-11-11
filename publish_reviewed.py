"""Interactive entry point for the six reviewed creations; never runs on import."""
import os
from pathlib import Path
from types import SimpleNamespace
import sys

import flora_pilot as p
import flora_create as c


def main():
    root = Path(__file__).resolve().parent
    plan_path = root / 'six-items.create.plan.json'
    expected_hash = (root / 'six-items.create.plan.sha256').read_text().strip()
    saved = p.read_json(plan_path)
    if p.digest(saved) != expected_hash:
        raise p.Stop('Plan changed. Prepare and review it again before publishing.')
    if not sys.stdin.isatty():
        raise p.Stop('Open this launcher yourself in a local terminal.')
    print('PUBLIC WIKIDATA CREATION — account Hb19988')
    print('Review CREATION_REVIEW.md before continuing.')
    print('This will propose these six items:')
    for op in saved['operations']:
        print(' -', saved['works'][op['key']]['input']['title'])
    print('\nExisting items will be reused. No outcome classifications are published.')
    if input('Type REVIEWED to confirm you reviewed these public creations (anything else cancels): ').strip() != 'REVIEWED':
        raise p.Stop('Cancelled without logging in or saving.')
    credentials = p.load_local_credentials('FLORA_WD_')
    username = os.environ.get('FLORA_WD_BOT_USERNAME') or credentials.get('FLORA_WD_BOT_USERNAME')
    if not username:
        username = input('Generated bot-password login name [Hb19988@FLoRA]: ').strip() or 'Hb19988@FLoRA'
    os.environ['FLORA_WD_BOT_USERNAME'] = username
    print('Login uses the local credential if configured; otherwise it asks privately for the generated bot password.')
    args = SimpleNamespace(plan=str(plan_path), approved_sha256=expected_hash,
        apply=True, data_verified=True, policy_reviewed=True, allow_live=True,
        auth='bot-password', expected_user='Hb19988', contact='https://www.wikidata.org/wiki/User:Hb19988')
    c.apply(args)


if __name__ == '__main__':
    try:
        main()
    except p.Stop as exc:
        print('STOP:', exc)
        sys.exit(2)
    except KeyboardInterrupt:
        print('Stopped. Inspect the journal before resuming an interrupted save.')
        sys.exit(130)
