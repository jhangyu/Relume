"""versionName -> Google Play versionCode: cache, latest pair, digit pattern, bisection, web hints.
Design: docs/logs/2026-10-31-design-resolver.md. Every returned code was confirmed by reading the APK manifest."""
import html.parser
import itertools
import json
import os
import re
import selectors
import subprocess
import tempfile
import time
import urllib.parse

from webui import apkmanifest, db, upstream

MAX_PLAY_CALLS = 16        # one call = one details request or one purchase+delivery request, shared by a lookup
HINT_RESERVE = 2           # calls kept back for the web hints
MAX_CONSECUTIVE_MISSES = 3
MAX_PART_WIDTH = 3
MAX_SEARCH_PAGES = 10
FETCH_TIMEOUT = 30
PROBE_TIMEOUT = 120
CLOSE_TIMEOUT = 10
MAX_HTML_BYTES = 2 << 20
MAX_API_BYTES = 4 << 20
STDERR_TAIL = 500
EXODUS_SEARCH = "https://reports.exodus-privacy.eu.org/en/reports/search/{}/"
APKPURE_API = "https://api.pureapk.com/m/v3/cms/app_version?hl=en-US&package_name={}"
APKPURE_HEADERS = {"x-cv": "3172501", "x-sv": "29", "x-abis": "arm64-v8a,armeabi-v7a,armeabi,x86,x86_64",
                   "x-gp": "1"}
APKPURE_PKG, APKPURE_CODE, APKPURE_NAME = 4, 5, 6   # protobuf field numbers
PB_MAX_DEPTH = 8
DOTTED = re.compile(r"\d+(\.\d+)*")


class VersionCodeError(Exception):
    pass


class _Final(VersionCodeError):
    """A lookup failure that already names every step."""


def _key(name):
    return tuple(int(p) for p in name.split(".")) if DOTTED.fullmatch(name) else None


# ---- Play probe session ------------------------------------------------------------------------------------------

class PlayClient:
    """One apkeep probe=session process per lookup (one Google login). Needs `package` set before the first call."""

    def __init__(self, apkeep, ini, device, log):
        self.apkeep, self.ini, self.device, self.log = apkeep, ini, device, log
        self.package = None
        self.calls = 0
        self._proc = self._tmp = self._err = None
        self._buf = b""

    def _start(self):
        (db.DATA / "work").mkdir(parents=True, exist_ok=True)
        self._tmp = tempfile.TemporaryDirectory(dir=db.DATA / "work")
        self._err = tempfile.TemporaryFile()
        self._proc = subprocess.Popen(
            [self.apkeep, "-a", self.package, "-d", "google-play", "-i", str(self.ini),
             "-o", f"device={self.device},probe=session", self._tmp.name],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._err)

    def _stderr_tail(self):
        self._err.seek(0)
        return self._err.read()[-STDERR_TAIL:].decode(errors="replace").strip()

    def _fail(self, what):
        raise VersionCodeError(f"{what} (apkeep exit {self._proc.poll()}, stderr: {self._stderr_tail() or 'empty'})")

    def _line(self, deadline):
        """Next stdout line; fails on EOF or when the deadline passes."""
        with selectors.DefaultSelector() as sel:
            sel.register(self._proc.stdout, selectors.EVENT_READ)
            while b"\n" not in self._buf:
                if not sel.select(max(0.0, deadline - time.monotonic())):
                    self._proc.kill()
                    self._proc.wait()
                    self._fail(f"no answer from the Play probe within {PROBE_TIMEOUT} s")
                data = os.read(self._proc.stdout.fileno(), 65536)
                if not data:
                    self._proc.wait()
                    self._fail("Play probe ended before answering")
                self._buf += data
        line, _, self._buf = self._buf.partition(b"\n")
        return line.decode(errors="replace")

    def _ask(self, request, mode, vc=None):
        if self._proc is None:
            self._start()
        self.calls += 1
        try:
            self._proc.stdin.write(request.encode() + b"\n")
            self._proc.stdin.flush()
        except OSError:
            self._proc.wait()
            self._fail(f"Play probe closed its input before request {request!r}")
        deadline = time.monotonic() + PROBE_TIMEOUT
        while True:
            line = self._line(deadline)
            try:
                answer = json.loads(line)
            except ValueError:
                continue  # stock text such as the Terms-of-Service notice
            if isinstance(answer, dict) and "mode" in answer:
                break
        if answer.get("mode") != mode or answer.get("vc") != vc:
            raise VersionCodeError(f"Play probe answered {answer.get('mode')}/{answer.get('vc')} to request "
                                   f"{request!r}, expected {mode}/{vc}")
        if not answer.get("ok") and not answer.get("notFound"):
            raise VersionCodeError(f"Play probe {request!r} failed: {answer.get('error')}")
        return answer

    def details(self):
        a = self._ask("details", "details")
        if not a.get("ok"):
            raise VersionCodeError(f"Play has no details for {self.package}: {a.get('error')}")
        return a["versionString"], a["versionCode"]

    def version_at(self, code):
        a = self._ask(str(code), "delivery", code)
        if not a.get("ok"):
            return None
        return self._read_manifest(a["url"])

    @staticmethod
    def _read_manifest(url):
        """Manifest of the APK at url through range reads (4 requests); the first answer's final URL is reused."""
        def get(u, a, b, n):
            status, headers, body, final = upstream.http_get(
                u, {"Range": f"bytes={a}-{b}"}, timeout=FETCH_TIMEOUT, max_bytes=n)
            if status != 206:
                raise VersionCodeError(f"APK range {a}-{b}: expected status 206, got {status}")
            return headers, body, final
        try:
            headers, _, final = get(url, 0, 0, 1)
            m = re.fullmatch(r"bytes 0-0/(\d+)", headers.get("content-range", ""))
            if not m:
                raise VersionCodeError(f"APK size: expected Content-Range 'bytes 0-0/<size>', got "
                                       f"{headers.get('content-range')!r}")
            return apkmanifest.read_manifest(lambda off, n: get(final, off, off + n - 1, n)[1], int(m[1]))
        except (OSError, ValueError, apkmanifest.ManifestError) as e:
            raise VersionCodeError(f"APK manifest read failed: {e}") from e

    def close(self):
        if self._proc is None:
            return
        try:
            self._proc.stdin.close()
            try:
                rc = self._proc.wait(CLOSE_TIMEOUT)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
                self.log.line(f"Play probe did not exit within {CLOSE_TIMEOUT} s after input end, killed")
            else:
                if rc != 0:
                    self.log.line(f"Play probe exited with {rc} after input end: {self._stderr_tail()}")
        finally:
            self._proc.stdout.close()
            self._err.close()
            self._tmp.cleanup()
            self._proc = None


# ---- cache ------------------------------------------------------------------------------------------------------

def remember(package, version_name, device, version_code):
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO play_version_code (package, version_name, device, version_code, "
                  "verified_at) VALUES (?, ?, ?, ?, ?)", (package, version_name, device, version_code, db.utcnow()))


def _cached(package, device):
    rows = db.conn().execute("SELECT version_name, version_code FROM play_version_code WHERE package = ? "
                             "AND device = ?", (package, device)).fetchall()
    return {r["version_code"]: r["version_name"] for r in rows}


# ---- pattern ----------------------------------------------------------------------------------------------------

def _encode(name, widths):
    """First part as is, later parts zero-padded to widths; None if a part is wider or the part count differs."""
    parts = name.split(".")
    if len(parts) != len(widths) + 1:
        return None
    out = parts[0]
    for p, w in zip(parts[1:], widths):
        if len(p) > w:
            return None
        out += p.zfill(w)
    return out


def predict(target, known):
    """Codes predicted for target from known {code: name}, best candidate first. Wider later parts rank first."""
    n = target.count(".")
    found = []
    for widths in sorted(itertools.product(range(1, MAX_PART_WIDTH + 1), repeat=n), key=lambda w: w[::-1],
                         reverse=True):
        prefixes = set()
        for code, name in known.items():
            e, c = _encode(name, widths), str(code)
            if e is None or not c.endswith(e) or len(c) == len(e):
                prefixes = None
                break
            prefixes.add(c[:-len(e)])
        e = _encode(target, widths)
        if prefixes and len(prefixes) == 1 and e is not None:
            code = int(prefixes.pop() + e)
            if code not in found:
                found.append(code)
    return found


# ---- web hints --------------------------------------------------------------------------------------------------

class _Search(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.next = [], None   # rows: (versionName, source, href)
        self._href = self._small = self._page = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag == "a" and "report-link" in cls:
            self._href = a.get("href")
        elif tag == "div" and "small" in cls and self._href:
            self._small = ""
        elif tag == "a" and "page-link" in cls:
            self._page = a.get("href")

    def handle_data(self, data):
        if self._small is not None:
            self._small += data
        if data.strip() == "Next" and self._page not in (None, "#"):
            self.next = self._page

    def handle_endtag(self, tag):
        if tag == "div" and self._small is not None:
            m = re.fullmatch(r"Version (.+) - (\S+)", self._small.strip())
            if m:
                self.rows.append((m[1], m[2], self._href))
            self._small = self._href = None


class _Report(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.docs, self._pre, self._code = [], False, False

    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self._pre = True
        elif tag == "code" and self._pre:
            self._code = True
            self.docs.append("")

    def handle_endtag(self, tag):
        if tag == "code":
            self._code = False
        elif tag == "pre":
            self._pre = False

    def handle_data(self, data):
        if self._code:
            self.docs[-1] += data


def _get(url, headers, max_bytes):
    status, _, body, _ = upstream.http_get(url, headers, timeout=FETCH_TIMEOUT, max_bytes=max_bytes)
    if status != 200:
        raise VersionCodeError(f"{url}: expected status 200, got {status}")
    return body


def exodus_code(package, version_name):
    """(code or None, outcome text). Exodus Privacy reports list the versionCode of scanned Play versions."""
    url = EXODUS_SEARCH.format(package)
    for _ in range(MAX_SEARCH_PAGES):
        page = _get(url, {}, MAX_HTML_BYTES).decode(errors="replace")
        if not re.search(rf"<title>Reports list for {re.escape(package)} - ", page):
            return None, "search page not recognised"
        p = _Search()
        p.feed(page)
        for name, source, href in p.rows:
            if name == version_name and source == "google":
                doc = _Report()
                doc.feed(_get(urllib.parse.urljoin(url, href), {}, MAX_HTML_BYTES).decode(errors="replace"))
                for text in doc.docs:
                    try:
                        d = json.loads(text)
                    except ValueError:
                        continue
                    if (d.get("handle"), d.get("version")) == (package, version_name) \
                            and re.fullmatch(r"\d+", str(d.get("version_code", ""))):
                        return int(d["version_code"]), "found"
                return None, f"report {href} does not carry {package} {version_name} with a version code"
        if not p.next:
            return None, f"{version_name} not listed"
        url = urllib.parse.urljoin(url, p.next)
    return None, f"{version_name} not listed in {MAX_SEARCH_PAGES} pages"


def _pb_parse(b):
    """[(field, wire type, value)] or None if b is not a protobuf message."""
    i, out = 0, []

    def varint():
        nonlocal i
        n = s = 0
        while True:
            x = b[i]
            i += 1
            n |= (x & 0x7f) << s
            s += 7
            if x < 0x80:
                return n

    try:
        while i < len(b):
            k = varint()
            f, w = k >> 3, k & 7
            if f == 0:
                return None
            if w == 0:
                v = varint()
            elif w == 2:
                n = varint()
                v = b[i:i + n]
                i += n
                if i > len(b):
                    return None
            elif w in (1, 5):
                v = b[i:i + (8 if w == 1 else 4)]
                i += 8 if w == 1 else 4
            else:
                return None
            out.append((f, w, v))
    except IndexError:
        return None
    return out


def _pb_walk(b, depth=0):
    m = _pb_parse(b)
    if m is None or depth > PB_MAX_DEPTH:
        return
    yield m
    for _, w, v in m:
        if w == 2 and len(v) > 1:
            yield from _pb_walk(v, depth + 1)


def apkpure_codes(body, package, version_name):
    codes = set()
    for m in _pb_walk(body):
        d = {f: v for f, w, v in m if w == 2}
        if d.get(APKPURE_PKG) == package.encode() and d.get(APKPURE_NAME) == version_name.encode() \
                and re.fullmatch(rb"\d+", d.get(APKPURE_CODE, b"")):
            codes.add(int(d[APKPURE_CODE]))
    return codes


def apkpure_code(package, version_name):
    codes = apkpure_codes(_get(APKPURE_API.format(package), APKPURE_HEADERS, MAX_API_BYTES), package, version_name)
    if not codes:
        return None, f"{version_name} not listed"
    if len(codes) > 1:
        return None, f"ambiguous, codes {sorted(codes)}"
    return codes.pop(), "found"


# ---- lookup -----------------------------------------------------------------------------------------------------

class _Lookup:
    def __init__(self, package, version_name, log, play):
        self.package, self.name, self.log, self.play = package, version_name, log, play
        self.key = _key(version_name)
        self.known = {}      # code -> versionName of every pair seen: cache, details, probes
        self.steps = []

    def fail(self, message):
        return _Final(f"{message}, Play calls {self.play.calls} of {MAX_PLAY_CALLS}: "
                                + "; ".join(self.steps))

    def step(self, text):
        self.steps.append(text)
        self.log.line(f"versionCode {self.package} {self.name}: {text}")

    def probe(self, code):
        """True = code is the wanted version, False = other version or no such code."""
        m = self.play.version_at(code)
        if m is None:
            return None
        if (m.package, m.version_code) != (self.package, code):
            raise VersionCodeError(f"probe {code}: the APK says {m.package} {m.version_code}, "
                                   f"expected {self.package} {code}")
        self.known[code] = m.version_name
        return m.version_name == self.name

    def run(self):
        play = self.play
        play.package = self.package
        cached = _cached(self.package, play.device)
        for code, name in cached.items():
            if name == self.name:
                self.step(f"cache: hit {code}")
                return code
        self.known.update(cached)
        self.steps.append("cache: no entry")

        latest_name, latest_code = play.details()
        self.step(f"details: latest {latest_name} = {latest_code}")
        hit = self.probe(latest_code)
        if hit is None:
            raise self.fail(f"device profile {play.device} cannot get {self.package}")
        if hit:
            return latest_code
        if self.key is None:
            self.step("pattern: versionName is not dotted numbers")
            self.step("bisection: versionName is not dotted numbers")
        else:
            code = self.pattern() or self.bisect()
            if code:
                return code
        for label, source in (("Exodus Privacy", exodus_code), ("APKPure", apkpure_code)):
            code = self.hint(label, source)
            if code:
                return code
        raise self.fail(f"no Google Play versionCode for {self.package} {self.name} (device {play.device})")

    def pattern(self):
        for code in predict(self.name, self.known)[:1]:
            if code not in self.known:
                hit = self.probe(code)
                self.step(f"pattern: predicted {code} " + ("confirmed" if hit else
                          "not on Play" if hit is None else f"is {self.known[code]}"))
                return code if hit else None
        self.step(f"pattern: no encoding fits {', '.join(f'{n} = {c}' for c, n in self.known.items())}")

    def bounds(self):
        pairs = [(c, _key(n)) for c, n in self.known.items() if _key(n)]
        lo = max((c for c, k in pairs if k < self.key), default=None)
        hi = min((c for c, k in pairs if k > self.key), default=None)
        return lo, hi

    def bisect(self):
        lo, hi = self.bounds()
        if hi is None:
            self.step("bisection: newer than the latest Play version")
            return None
        misses = probes = 0
        step = 1
        while True:
            lo, hi = self.bounds()
            if lo is not None and hi - lo == 1:
                return self.stop(f"no code between {lo} ({self.known[lo]}) and {hi} ({self.known[hi]})", probes)
            code = hi - step - misses if lo is None else (lo + hi) // 2 + misses
            if code < 1 or (lo is not None and code >= hi):
                return self.stop(f"no code left below {hi}" if lo is None else f"gap between {lo} and {hi}", probes)
            if misses >= MAX_CONSECUTIVE_MISSES:
                return self.stop(f"{misses} codes in a row missing near {code}", probes)
            if MAX_PLAY_CALLS - self.play.calls <= HINT_RESERVE:
                return self.stop("call budget reached", probes)
            if code in self.known:
                return self.stop(f"code {code} is {self.known[code]}, not comparable", probes)
            hit = self.probe(code)
            probes += 1
            if hit is None:
                misses += 1
                continue
            if hit:
                self.step(f"bisection: found {code} after {probes} probes")
                return code
            misses = 0
            if lo is None:
                step *= 2

    def stop(self, why, probes):
        self.step(f"bisection: {why} after {probes} probes")

    def hint(self, label, source):
        if MAX_PLAY_CALLS - self.play.calls < 1:
            self.step(f"{label}: skipped, call budget used")
            return None
        try:
            code, outcome = source(self.package, self.name)
        except (OSError, ValueError, VersionCodeError) as e:
            self.step(f"{label}: {e}")
            return None
        if code is None:
            self.step(f"{label}: {outcome}")
            return None
        if self.known.get(code) not in (None, self.name):
            self.step(f"{label}: hint {code} is {self.known[code]}")
            return None
        hit = self.probe(code)
        if hit:
            self.step(f"{label}: hint {code} confirmed")
            return code
        self.step(f"{label}: hint {code} not confirmed ("
                  + ("Play: code not found" if hit is None else f"APK says {self.known[code]}") + ")")
        return None


def resolve(package, version_name, log, play):
    """versionCode of package at version_name for play.device, or VersionCodeError naming every step."""
    lookup = _Lookup(package, version_name, log, play)
    try:
        return lookup.run()
    except _Final:
        raise
    except (OSError, ValueError, VersionCodeError, apkmanifest.ManifestError) as e:
        raise lookup.fail(f"lookup of {package} {version_name} (device {play.device}) failed: {e}") from e
    finally:
        play.close()
