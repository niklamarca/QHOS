from pathlib import Path
import datetime, json, os, sys, uuid
token=str(uuid.UUID(sys.argv[1]));pid=int(sys.argv[2])
assert token==sys.argv[1] and os.getuid()==1000 and pid==os.getppid()
proc=Path('/proc')/str(pid)
start=(proc/'stat').read_text().rsplit(')',1)[1].split()[19]
root=Path('/home/usuario/qhos-v2-data/run');root.mkdir(mode=0o700,exist_ok=True)
assert not root.is_symlink()
record={'token':token,'pid':pid,'start_ticks':start,'uid':os.getuid(),'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
temp=root/('keepalive.'+token+'.tmp')
fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as stream:
    json.dump(record,stream);stream.flush();os.fsync(stream.fileno())
temp.replace(root/'keepalive.json')
(root/'keepalive.pid').write_text(str(pid))
(root/'keepalive.pid').chmod(0o600)
