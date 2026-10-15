#!/usr/bin/env python3
"""Explicitly rebind an exclusively owned offline Hub task backup at its restore root."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.service_contract import ServiceError
from hub.task_store import TaskStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--database-sha256', required=True)
    parser.add_argument('--registration-sha256', required=True)
    parser.add_argument('--confirm-offline', action='store_true', required=True,
                        help='Caller has stopped all users of this copied store and retained its backup')
    args = parser.parse_args(argv)
    try:
        result = TaskStore(args.root).restore_storage(database_sha256=args.database_sha256,
                  registration_sha256=args.registration_sha256, confirm_offline=args.confirm_offline)
    except ServiceError as error:
        print(json.dumps({'code': error.code, 'details': error.details, 'outcome': error.outcome}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
