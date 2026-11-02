import json
import threading
import time
import traceback

from .server import HttpError, SSEResponse

KEEPALIVE = 15  # seconds without a frame before an SSE stream writes a keepalive comment (finds gone clients)
TOPICS = ('jobs', 'apps', 'projects', 'devices', 'schedules')
_subs = {}


class Signal(threading.Condition):
    """Condition that counts its notifies. A waiter takes mark() before it looks at the state and then waits for
    the count to change, so a notify sent between the look and the wait is never lost."""

    def __init__(self):
        super().__init__()
        self.seq = 0

    def notify_all(self):  # the caller holds the lock, as for Condition.notify_all
        self.seq += 1
        super().notify_all()

    def mark(self):
        with self:
            return self.seq

    def wait_change(self, seen, timeout=None):
        """-> False when timeout passed with no notify since mark() returned seen."""
        with self:
            return self.wait_for(lambda: self.seq != seen, timeout)


_changed = Signal()
_last = dict.fromkeys(TOPICS, 0)  # topic -> _changed.seq of its newest change


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


def changed(*topics):
    """Publish that the state behind these topics changed (decision 2). Never blocks on stream clients."""
    bad = [t for t in topics if t not in _last]
    if bad or not topics:
        raise ValueError(f'changed() expects one or more of {TOPICS}, got {topics!r}')
    with _changed:
        _changed.notify_all()
        for t in topics:
            _last[t] = _changed.seq


def stream(alive):
    """SSE frames of /api/events. The mark is taken now, before the response headers go out, so a change made
    while the client connects is not lost. Ends on the first wake where alive() is false."""
    seen = _changed.mark()

    def frames():
        nonlocal seen
        last = time.monotonic()
        while True:
            woke = _changed.wait_change(seen, max(0, KEEPALIVE - (time.monotonic() - last)))
            if not alive():
                return
            if woke:
                with _changed:
                    topics = [t for t in TOPICS if _last[t] > seen]
                    seen = _changed.seq
                yield f'event: change\ndata: {json.dumps(topics, separators=(",", ":"))}\n\n'.encode()
            else:
                yield b': keepalive\n\n'
            last = time.monotonic()

    return frames()


def stream_route(req):
    from . import auth  # lazy: auth imports server, which must not import webui modules at load time

    def alive():
        try:
            session, device_id = auth.authenticate(req.headers)
        except HttpError:  # revoked device token: 401
            return False
        return session is not None or device_id is not None

    return SSEResponse(stream(alive))


ROUTES = [('GET', '/api/events', stream_route)]
