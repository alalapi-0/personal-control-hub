#!/usr/bin/env python3
"""Explicit owned worker entry; production execution remains disabled."""
import argparse
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from hub.task_store import TaskStore
from hub.task_worker import TaskWorker
from hub.task_service import root_identity


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture-control-root',type=Path)
    parser.add_argument('--owned-fixture-root',type=Path)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--css-trial',action='store_true',help='trusted local exact owner-approved CSS trial only')
    args=parser.parse_args()
    if args.css_trial:
        if args.fixture_control_root is not None or args.owned_fixture_root is not None or not args.once:
            parser.error('CSS trial requires --once and has no client-selected roots')
        from hub import css_trial
        store=TaskStore(ROOT)
        grant=store.grant('csp-css-trial-grant-v1')
        if grant is None:parser.error('no exact local CSS trial grant registered')
        css_trial.validate(grant,live=True)
        worker=TaskWorker(store,writer_check=lambda g:css_trial.writer_check(g),only_task_id=css_trial.TASK)
        worker.run_one()
        task=store.task(css_trial.TASK)
        print('CSS trial '+task['status']+'; canonical promotion/checks require trusted local reconciliation',flush=True)
        return 0 if task['status']=='validating'else 1
    if args.fixture_control_root is None or args.owned_fixture_root is None:
        print('Execution is disabled; no verified project grant or isolated runtime registered.')
        return 2
    control=args.fixture_control_root.absolute()
    # This local-only test entry cannot authorize a browser-selected real checkout.
    if control.parent!=Path('/tmp') or not control.name.startswith('hub-lwb-canary-') or control.resolve()!=control:
        parser.error('expected the exact owned disposable fixture control root')
    store=TaskStore(control)
    owned_root=args.owned_fixture_root.absolute()
    if owned_root.parent!=Path('/tmp') or not owned_root.name.startswith('hub-lwb-runtime-'):
        parser.error('expected the exact Root-created disposable fixture')
    identity=root_identity(owned_root)
    def owned(grant):
        root=Path(grant['root'])
        return root==owned_root and grant['root_identity']==identity
    worker=TaskWorker(store,writer_check=owned)
    print('Owned fixture worker ready',flush=True)
    try:
        while True:
            worked=worker.run_one()
            if args.once:return 0
            if not worked:time.sleep(.25)
    except KeyboardInterrupt:return 0


if __name__=='__main__':raise SystemExit(main())
