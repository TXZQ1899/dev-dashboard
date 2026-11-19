"""Run the TCP bridge and save each original report for reproducible aggregation."""
import json
import os
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent

def collect(app_id, environment, output, progress=print):
    output=Path(output); output.mkdir(parents=True,exist_ok=True,mode=0o700); output.chmod(0o700)
    errors=[]; paths=[]; os.umask(0o077)
    with (HERE/'tcp_api_bridge.py').open('rb') as source:
        proc=subprocess.Popen(['docker','--context','lenovo','exec','-i','envscope-local','python3','-',app_id,environment],stdin=source,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        for line in proc.stdout:
            row=json.loads(line); kind=row.get('event'); ip=row.get('ip','')
            if kind=='report':
                path=output/('tcp-'+ip+'.json'); tmp=path.with_suffix('.tmp')
                tmp.write_text(json.dumps(row['report'],ensure_ascii=False),encoding='utf-8');tmp.chmod(0o600);os.replace(tmp,path);paths.append(path)
                progress('%s TCP 结果：%s，实际观察 %s 秒' % (ip,row['report'].get('status'),row['report'].get('coverage',{}).get('actualSeconds')))
            elif kind in ('error','fatal'):
                errors.append((ip,row.get('message','未知错误')));progress('%s TCP 检查失败：%s' % errors[-1])
            elif kind=='start': progress('%s PID %s：开始 %s 秒 TCP 检测' % (ip,row['pid'],row['seconds']))
            elif kind=='progress': progress('%s：%s，采样 %s 次，已运行 %s 秒' % (ip,row.get('phase'),row.get('samples'),row.get('elapsed')))
        rc=proc.wait()
        if rc: errors.append(('', 'TCP 桥接失败（退出码 %d）' % rc))
    return paths,errors

if __name__=='__main__':
    paths,errors=collect(sys.argv[1],sys.argv[2],sys.argv[3],lambda x:print(x,flush=True))
    sys.exit(1 if errors or not paths else 0)
