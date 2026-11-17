"""Audit saved quotes under MVIX v1.1; apply only failed snapshots with a backup."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from marginlab.mvix_repair import replay,apply_repairs


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True)
    parser.add_argument('--report',required=True)
    parser.add_argument('--legacy-rate',type=float)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--backup')
    args=parser.parse_args()
    if args.apply and not args.backup:parser.error('--apply requires --backup')
    report_path=Path(args.report).resolve()
    if report_path in {Path(args.database).resolve(),Path(args.backup).resolve() if args.backup else None}:
        parser.error('Report, database and backup paths must differ')
    report,plans=replay(args.database,args.legacy_rate)
    # Reserve the audit file before any data mutation.
    with open(os.open(report_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w') as output:
        report['applied']=0;json.dump(report,output,indent=2);output.flush();os.fsync(output.fileno())
        if args.apply:
            report['applied']=apply_repairs(args.database,plans,args.backup)
            output.seek(0);json.dump(report,output,indent=2);output.truncate()
    print(json.dumps({k:v for k,v in report.items() if k not in ('recovered','valid_changes')}))


if __name__=='__main__':main()
