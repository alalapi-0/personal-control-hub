#!/usr/bin/env python3
"""Explicit local-owner preview worker; never called by source sync or GET."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.capture_worker import CaptureWorker
from hub.connection_records import RecordError
from hub.project_service import ProjectService
from hub.service_contract import ServiceError


def read_json(path):
    data = Path(path).read_bytes()
    if len(data) > 131072:
        raise ValueError('input too large')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    web = commands.add_parser('capture'); web.add_argument('--request', required=True)
    web.add_argument('--executable-path')
    existing = commands.add_parser('import-existing'); existing.add_argument('--manifest', required=True)
    existing.add_argument('--policy', required=True)
    args = parser.parse_args()
    try:
        worker = CaptureWorker(ProjectService(Path(args.root)))
        if args.command == 'capture':
            result = worker.run(read_json(args.request), executable_path=args.executable_path)
            failed = result['record']['status'] == 'failed'
        else:
            result = worker.import_existing(read_json(args.manifest), read_json(args.policy)); failed = False
        print(json.dumps({'ok': not failed, 'data': result}, ensure_ascii=False))
        return 2 if failed else 0
    except (RecordError, ServiceError, OSError, ValueError, KeyError):
        print(json.dumps({'ok': False, 'error': {'code': 'PREVIEW_REQUEST_REJECTED'}}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
