"""Initialize a root-owned Railway volume, then run the bot without root privileges."""
import os
from pathlib import Path
import pwd
import sys

if os.getuid() == 0:
    account = pwd.getpwnam('fortune')
    data = Path(os.environ.get('DATA_DIR', '/data')).resolve()
    if data in (Path('/'), Path('/app'), Path('/etc'), Path('/usr'), Path('/var')):
        raise SystemExit('DATA_DIR must be a dedicated bot data directory (normally /data).')
    data.mkdir(parents=True, exist_ok=True)
    os.chown(data, account.pw_uid, account.pw_gid)
    # Repair files from a prior root-run deployment without following symlinks.
    for directory, dirs, files in os.walk(data, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if not path.is_symlink():
                os.chown(path, account.pw_uid, account.pw_gid)
    os.initgroups(account.pw_name, account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
os.execvp(sys.argv[1], sys.argv[1:])
