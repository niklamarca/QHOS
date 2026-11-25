from pathlib import Path
import json, os, stat, subprocess, sys, uuid
try:
    token=str(uuid.UUID(sys.argv[1]));assert token==sys.argv[1] and os.getuid()==1000
    marker=Path('/home/usuario/qhos-v2-data/run/keepalive.json')
    fd=os.open(marker,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        assert stat.S_ISREG(info.st_mode) and info.st_uid==1000 and 0<info.st_size<=4096
        raw=os.read(fd,4097);assert len(raw)<=4096
        record=json.loads(raw)
    finally:os.close(fd)
    assert record['token']==token and record['uid']==1000
    proc=Path('/proc')/str(int(record['pid']))
    assert proc.stat().st_uid==1000
    start=(proc/'stat').read_text().rsplit(')',1)[1].split()[19]
    assert start==record['start_ticks']
    assert (proc/'cmdline').read_bytes()==b'/bin/sleep\0infinity\0'
    assert (proc/'exe').resolve()==Path('/bin/sleep').resolve()
    env=dict(os.environ,XDG_RUNTIME_DIR='/run/user/1000',DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/1000/bus')
    for name in ['qhos2-brain.service','qhos2-coletor.service']:
        subprocess.run(['systemctl','--user','is-active','--quiet',name],env=env,check=True,timeout=3,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
except Exception:
    raise SystemExit(2)
print('V2_KEEPALIVE_VERIFIED')
