"""Read-only Linux observer. Run via the approved SSH account, not inside Agent.
Outputs one JSON line each 10 seconds. No credential file is read.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import time
from pathlib import Path


def command(*args):
    return subprocess.run(args,text=True,capture_output=True,timeout=8).stdout.strip()


def snapshot(unit, previous):
    properties={}
    for line in command('systemctl','show',unit,'--property=MainPID,ActiveState,SubState').splitlines():
        key,_,value=line.partition('=')
        properties[key]=value
    pid=int(properties.get('MainPID','0'))
    stamp=time.time()
    data={'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'epoch':stamp,
          'pid':pid,'active_state':properties.get('ActiveState'),'sub_state':properties.get('SubState')}
    if pid:
        try:
            status=Path(f'/proc/{pid}/status').read_text()
            data['rss_kib']=int(re.search(r'^VmRSS:\s+(\d+)',status,re.M)[1])
            data['uid']=int(re.search(r'^Uid:\s+(\d+)',status,re.M)[1])
            data['no_new_privs']=int(re.search(r'^NoNewPrivs:\s+(\d+)',status,re.M)[1])
            fields=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
            ticks=int(fields[11])+int(fields[12])
            data['cpu_ticks']=ticks
            if previous and previous.get('pid')==pid and stamp>previous['epoch']:
                data['cpu_percent_one_core']=100*(ticks-previous.get('cpu_ticks',ticks))/os.sysconf('SC_CLK_TCK')/(stamp-previous['epoch'])
            total=0
            for block in re.split(r'\n(?=\S)',command('ss','-tinp')):
                if f'pid={pid},' in block:
                    total+=sum(int(n) for n in re.findall(r'bytes_sent:(\d+)',block))
            data['tcp_bytes_sent_open_connections']=total
            if previous and previous.get('pid')==pid:
                prior=previous.get('tcp_bytes_sent_open_connections',total)
                data['tcp_sent_delta']=total-prior if total>=prior else None
        except (OSError,AttributeError,ValueError):
            data['process_read_race']=True
    data['system']={
        'proc_stat_cpu':Path('/proc/stat').read_text().splitlines()[0],
        'loadavg':Path('/proc/loadavg').read_text().strip(),
        'meminfo':{key:int(value.split()[0])*1024 for key,value in
                   (line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
                   if key in {'MemTotal','MemAvailable','SwapTotal','SwapFree'}},
        'uptime_seconds':float(Path('/proc/uptime').read_text().split()[0]),
        'net_dev':Path('/proc/net/dev').read_text(),
        'statvfs_root':{},
    }
    fs=os.statvfs('/')
    data['system']['statvfs_root']={key:getattr(fs,key) for key in ['f_blocks','f_bfree','f_bavail','f_frsize','f_files','f_ffree']}
    return data


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--unit',default='mhm-phase02e-agent.service')
    parser.add_argument('--seconds',type=int,default=2400)
    args=parser.parse_args()
    start=time.monotonic();previous=None
    while time.monotonic()-start<args.seconds:
        value=snapshot(args.unit,previous)
        print(json.dumps(value,separators=(',',':')),flush=True)
        previous=value
        time.sleep(10)


if __name__=='__main__':
    main()
