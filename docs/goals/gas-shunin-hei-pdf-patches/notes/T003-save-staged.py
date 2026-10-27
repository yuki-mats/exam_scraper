#!/usr/bin/env python3
"""Save already-conclusive native single-question reviews through the fixed driver."""
import hashlib
import json
import pathlib
import subprocess
import sys
ROOT=pathlib.Path(__file__).resolve().parents[4]
BASE=ROOT/'output/gas-shunin-hei/pdf_patch_review'
NOTES=pathlib.Path(__file__).parent
DRIVERS={'runs-v4':'T005-review-driver.py','runs-v5':'T007-review-driver.py','runs-v6':'T007-review-driver.py','runs-v7':'T007-review-driver.py','runs-v8':'T007-review-driver.py','runs-v9':'T010-audit-review-driver.py'}

def main():
    terminal=json.loads((BASE/'batch-execution-results-v4.json').read_text())['batches']
    assert len(terminal)==27 and all('exitCode' in x for x in terminal), 'Wait for original serial writer to exit'
    outcomes=[]
    for version, name in DRIVERS.items():
        for result in sorted((BASE/version).glob('recovery-*/stage-result.json')):
            work=result.parent
            if (work/'save-result.json').exists():continue
            rows=json.loads(result.read_text())['results']
            assert len(rows)==1
            if rows[0]['decision'] not in ('fix','no_change'):continue
            frozen=json.loads((work/'frozen-plan.json').read_text()); driver=NOTES/name
            assert hashlib.sha256(driver.read_bytes()).hexdigest()==frozen['driverSha256']
            plans=BASE/version.replace('runs-', 'recovery-plans-')
            key=rows[0]['sourceQuestionId'];slug=key.replace(':','-').replace('問','q');plan=plans/(slug+'.json')
            assert json.loads(plan.read_text())==frozen['plan']
            cmd=[sys.executable,str(driver),'--plan',str(plan),'--work-dir',str(work),'--stage','save','--save']
            with (work/'formal-save-execution.log').open('a') as log:
                r=subprocess.run(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            outcomes.append({'sourceQuestionId':key,'command':cmd,'exitCode':r.returncode})
            print(key,'formal save exit='+str(r.returncode),flush=True)
            assert r.returncode==0
    print('saved conclusive recovery receipts',len(outcomes))
if __name__=='__main__':main()
