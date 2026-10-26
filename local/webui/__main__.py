import os
import sys

from . import auth, db, jobs, scheduler, server, settings

USAGE = 'usage: python3 -m webui [serve]'


# Modes of the data subdirectories, set at every boot so an empty volume needs no host preparation. The server
# creates them, so they are owned by the server uid. The isolated runner (other uid, same gid) writes work and
# apk-cache. The key dir (adb key, UI GitHub token) is the server's only.
DIR_MODES = {'work': 0o770, 'apk-cache': 0o770}


def prepare_data():
    db.KEY.mkdir(parents=True, exist_ok=True)
    if db.KEY.stat().st_uid == os.getuid():
        db.KEY.chmod(0o700)
    for d in db.SUBDIRS:
        p = db.DATA / d
        p.mkdir(parents=True, exist_ok=True)
        # only dirs this process owns: a pre-existing host-owned dir keeps its mode, also when we run as root
        # (0770 would lock the runner out of a root-run server's host-made data/work 0755)
        if d in DIR_MODES and p.stat().st_uid == os.getuid():
            p.chmod(DIR_MODES[d])


def boot(start=False, names=None):
    db.init()
    prepare_data()
    db.migrate()
    mods = server.load_modules(server.MODULES if names is None else names)
    for m in mods:
        if hasattr(m, 'register'):
            m.register()
    scheduler.ensure_update()
    if start:
        auth.apply_env_password()
        settings.check_token_file()
        jobs.recover()
        jobs.start()
        scheduler.start()
        for m in mods:
            if hasattr(m, 'start'):
                m.start()
    return mods


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else 'serve'
    if cmd != 'serve':
        print(USAGE, file=sys.stderr)
        return 2
    mods = boot(start=True)
    srv = server.make_server(mods, '0.0.0.0', 47000)
    print('Relume listening on :47000', flush=True)
    srv.serve_forever()
    return 0


if __name__ == '__main__':
    sys.exit(main())
