#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub.css_capability_recovery import run
if __name__=='__main__':
    result=run();print(result['status']);raise SystemExit(0 if result['status']=='PASS_CURRENT_EVIDENCE_ONLY'else 1)
