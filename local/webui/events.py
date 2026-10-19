import traceback

_subs = {}


def subscribe(type, fn):
    """fn(payload); runs in the emitting thread; must not do network IO."""
    _subs.setdefault(type, []).append(fn)


def emit(type, payload):
    for fn in list(_subs.get(type, ())):
        try:
            fn(payload)
        except Exception:
            traceback.print_exc()


def reset():
    _subs.clear()
