#!/usr/bin/env python3
"""Prepare is local-only. install/enable/rollback are explicit future effects."""
import argparse
import json
import sys
from pathlib import Path
CODE_ROOT=Path(__file__).absolute().parents[1]
sys.path.insert(0,str(CODE_ROOT/'src'))
from hub.rr_install import prepare,check,install,enable,rollback
from hub.rr_payload import RRFeedbackError
from hub.service_contract import ServiceError

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('prepare');p.add_argument('--data-root',required=True);p.add_argument('--output',required=True)
    p.add_argument('--project-id',action='append',required=True);p.add_argument('--gateway-uid',type=int);p.add_argument('--gateway-gid',type=int)
    p.add_argument('--device',action='append',default=[])
    for action in ('check','install','enable','rollback'):
        p=sub.add_parser(action);p.add_argument('--candidate',required=True)
    args=parser.parse_args(argv)
    try:
        if args.action=='prepare':
            value=prepare(CODE_ROOT,args.data_root,args.output,python=sys.executable,project_ids=args.project_id,
                          gateway_uid=args.gateway_uid,gateway_gid=args.gateway_gid,devices={x:'personal-control-hub'for x in args.device})
            value={'status':'prepared_disabled','program_id':value['program_id'],'missing':value['missing']}
        else:
            value={'status':'checked','program_id':check(args.candidate)['program_id']} if args.action=='check' else globals()[args.action](args.candidate)
        print(json.dumps(value));return 0
    except (RRFeedbackError,ServiceError,OSError,ValueError,KeyError,TypeError):
        print('{"status":"unavailable","code":"RR_NATIVE_CANDIDATE_REJECTED"}',file=sys.stderr);return 1
if __name__=='__main__':raise SystemExit(main())
