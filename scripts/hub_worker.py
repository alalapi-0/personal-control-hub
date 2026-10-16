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
    parser.add_argument('--ordinary-readonly',action='store_true',help='One exact pre-admitted registered ordinary read-only task')
    parser.add_argument('--task-id',help='Exact existing ordinary task identity; never a workspace path')
    parser.add_argument('--control-root',type=Path,help='Existing Hub data root for an explicitly registered ordinary task')
    args=parser.parse_args()
    if args.ordinary_readonly:
        if not args.once or not args.task_id or args.css_trial or args.fixture_control_root is not None or args.owned_fixture_root is not None:
            parser.error('Ordinary read-only execution requires --once --task-id and no fixture/CSS options')
        from hub import ordinary_tasks
        from hub.service_contract import ServiceError
        try:
            store=TaskStore(args.control_root or ROOT)
            store.check_storage()  # No storage or grant initialization/recovery.
            task=store.task(args.task_id)
            if not task or task['payload'].get('kind')!=ordinary_tasks.KIND:
                raise ServiceError('ORDINARY_REGISTERED_TASK_REQUIRED')
            grant=store.grant(task['intent']['grant']['grant_id'])
            if grant!=task['intent']['grant'] or grant.get('effect')!='read_only':
                raise ServiceError('GRANT_VERSION_STALE')
            ordinary_tasks.check_registration(grant,store.root)
            ordinary_tasks.validate_grant(grant,live=True)
            def readonly_owned(value):
                ordinary_tasks.check_registration(value,store.root)
                return bool(ordinary_tasks.validate_grant(value,live=True)) and value['effect']=='read_only'
            worker=TaskWorker(store,writer_check=readonly_owned,only_task_id=args.task_id)
            worked=worker.run_one()
            current=store.task(args.task_id)
            print('Ordinary read-only task '+current['status']+'; owner acceptance remains independent',flush=True)
            return 0 if worked and current['status']=='checks_complete' else 1
        except (ServiceError,OSError,ValueError,TypeError,KeyError) as error:
            print(getattr(error,'code','ORDINARY_WORKER_REJECTED'),flush=True)
            return 2
    if args.task_id is not None or args.control_root is not None:
        parser.error('--task-id/--control-root require --ordinary-readonly')
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
