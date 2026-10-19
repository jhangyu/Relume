import sys

from . import auth, db, jobs, scheduler, server, settings

USAGE = 'usage: python3 -m webui [serve|reset-password|ci-plan|ci-build|export-seed]'


def boot(start=False, names=None):
    db.init()
    db.migrate()
    mods = server.load_modules(server.MODULES if names is None else names)
    by_name = {m.__name__.rsplit('.', 1)[1]: m for m in mods}
    if 'catalog' in by_name:
        by_name['catalog'].seed_import()
    for m in mods:
        if hasattr(m, 'register'):
            m.register()
    if start:
        settings.check_token_file()
        auth.ensure_password()
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
    if cmd in ('ci-plan', 'ci-build', 'export-seed'):
        boot()
        from . import ci
        return ci.main(argv)
    if cmd == 'reset-password':
        boot()
        pw = auth.new_password()
        auth.set_password(pw)
        with db.tx() as c:
            c.execute('DELETE FROM sessions')
        print(f'admin password: {pw}', flush=True)
        return 0
    if cmd != 'serve':
        print(USAGE, file=sys.stderr)
        return 2
    mods = boot(start=True)
    srv = server.make_server(mods, '0.0.0.0', 47000)
    print('Patchbay listening on :47000', flush=True)
    srv.serve_forever()
    return 0


if __name__ == '__main__':
    sys.exit(main())
