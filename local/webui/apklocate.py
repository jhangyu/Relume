"""APKMirror auto-locate (spec marketplace §5, API part §1): find the listing of an enabled app by package with an
exact Play-id match, rate-limited, state per app in the app.apkm_* columns. Every page is fetched by the isolated
runner (src/build/fetch-page.sh); this module only parses the text it wrote, reading attributes and link text."""
import datetime
import html.parser
import json
import pathlib
import re
import shutil
import tempfile
import threading
import time
import urllib.parse
from dataclasses import dataclass

from . import build, db, jobs
from .server import HttpError

MIN_INTERVAL = 10              # s between two APKMirror lookup requests (global)
BLOCK_COOLDOWN = 3600          # s after a block without any lookup request
NOT_FOUND_RETRY = 7 * 86400    # s until a not_found / failed lookup is due again
MAX_CANDIDATES = 5             # app pages checked per lookup
FETCH_TIMEOUT = 120            # s per page: one solver round with its retries
PAGE_MAX_BYTES = 8 * 1024 * 1024
FETCH_PAGE = str(db.REPO_ROOT / "src" / "build" / "fetch-page.sh")
PAGE_FILE = "page.html"
BASE = "https://www.apkmirror.com"
HOSTS = ("www.apkmirror.com", "apkmirror.com")
URL_FIELDS = ("apkmirror_list_url", "apkmirror_example_url", "apkmirror_example_url2", "apkpure_url")
URL_RE = re.compile(r"^https://[^\s\"'<>]+$")
SLUG_RE = re.compile(r"^[A-Za-z0-9%_-]+$")
APP_PATH = re.compile(r"^/apk/([A-Za-z0-9%_-]+)/([A-Za-z0-9%_-]+)/$")
CHALLENGE = re.compile(r"<title>\s*Just a moment|cf-chl|challenge-platform", re.I)
NO_RESULTS = "No results found"
PLAY_LINK = "View on Play Store"
LIST_URL_FORMS = f"{BASE}/uploads/?appcategory=<slug> or {BASE}/apk/<org>/<slug>/"
NEVER_DUE = ("found", "manual", "ambiguous")
RETRY_DUE = ("not_found", "failed", "blocked")
_clock = time.monotonic        # tests replace; wall time for retry_at uses db.utcnow()
_lock = threading.Lock()
# ponytail: in memory, a restart resets the cooldown (spec §14)
_state = {"last": None, "blocked_until": 0.0, "retry_at": None}


@dataclass(frozen=True)
class Cand:
    org: str
    slug: str
    title: str


@dataclass(frozen=True)
class Result:
    state: str
    list_url: str | None
    candidates: list
    error: str | None


class Blocked(Exception):
    """Solver chain failed, a challenge page came back, or the block cooldown is running."""


class PageShape(Exception):
    """The page has neither the markup the parser reads nor a known empty form."""


def search_url(package):
    return f"{BASE}/?post_type=app_release&searchtype=app&s={urllib.parse.quote(package)}"


def app_url(c):
    return f"{BASE}/apk/{c.org}/{c.slug}/"


def list_url(c):
    return f"{BASE}/uploads/?appcategory={c.slug}"


class _Search(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.cands, self.title, self.link, self.text, self.no_results = 0, [], False, None, [], False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "div" and "appRow" in classes:
            self.rows += 1
        elif tag == "h5" and "appRowTitle" in classes:
            self.title = True
        elif tag == "a" and self.title and self.link is None:
            self.link, self.text = APP_PATH.match(a.get("href") or ""), []

    def handle_endtag(self, tag):
        if tag == "a" and self.link is not None:
            if self.link:
                self.cands.append(Cand(self.link[1], self.link[2], " ".join("".join(self.text).split())))
            self.link, self.title = None, False
        elif tag == "h5":
            self.title = False

    def handle_data(self, data):
        if self.link is not None:
            self.text.append(data)
        elif NO_RESULTS in data:
            self.no_results = True


def parse_search(text):
    """Candidates of h5.appRowTitle links of the form /apk/<org>/<slug>/, page order, de-duplicated."""
    p = _Search()
    p.feed(text)
    p.close()
    if not p.rows and not p.no_results:
        raise PageShape(f"search page: expected div.appRow rows or {NO_RESULTS!r}, got neither")
    seen, out = set(), []
    for c in p.cands:
        if (c.org, c.slug) not in seen:
            seen.add((c.org, c.slug))
            out.append(c)
    return out


class _Play(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and PLAY_LINK in (a.get("title"), a.get("alt")):
            self.hrefs.append(a.get("href") or "")


def play_id(text):
    """Package id of the app page's "View on Play Store" link; None when the link carries no id."""
    p = _Play()
    p.feed(text)
    p.close()
    if not p.hrefs:
        raise PageShape(f"app page: expected a {PLAY_LINK!r} link, got none")
    u = urllib.parse.urlsplit(p.hrefs[0])
    if u.hostname != "play.google.com":
        return None
    ids = urllib.parse.parse_qs(u.query).get("id")
    return ids[0] if ids else None


def decide(package, name, fetch):
    """Decision table of API part §1.2: fetch(url) -> page text, raises Blocked or PageShape."""
    url = search_url(package)
    try:
        cands = parse_search(fetch(url))
        ranked = sorted(cands, key=lambda c: c.title.casefold() != name.casefold())[:MAX_CANDIDATES]
        matches = []
        for c in ranked:
            url = app_url(c)
            if play_id(fetch(url)) == package:
                matches.append(c)
    except Blocked as e:
        return Result("blocked", None, [], str(e))
    except PageShape as e:
        return Result("failed", None, [], f"{url}: {e}")
    if not matches:
        return Result("not_found", None, [],
                      f"no APKMirror app page links to Play id {package} ({len(ranked)} candidates checked)")
    if len(matches) == 1:
        return Result("found", list_url(matches[0]), [], None)
    return Result("ambiguous", None, matches, f"{len(matches)} APKMirror apps link to Play id {package}")


def _wall(seconds):
    return db.iso(db.parse_iso(db.utcnow()) + datetime.timedelta(seconds=seconds))


def _block(reason):
    """Start the cooldown; returns the Blocked to raise."""
    with _lock:
        _state["blocked_until"] = _clock() + BLOCK_COOLDOWN
        _state["retry_at"] = _wall(BLOCK_COOLDOWN)
        return Blocked(f"blocked until {_state['retry_at']}: {reason}")


def throttle(cancel_event):
    """Wait until MIN_INTERVAL after the previous lookup request (rate limit = time as business data), then record
    this one. Raises Blocked during the cooldown and jobs.JobFailed('cancelled') when the job is cancelled."""
    with _lock:  # ponytail: the wait holds the lock, one lookup at a time is the goal
        now = _clock()
        if now < _state["blocked_until"]:
            raise Blocked(f"blocked until {_state['retry_at']}: cooldown after an earlier block")
        if _state["last"] is not None and now < _state["last"] + MIN_INTERVAL:
            if cancel_event.wait(_state["last"] + MIN_INTERVAL - now):
                raise jobs.JobFailed("cancelled")
        _state["last"] = _clock()


def _fetch(url, log, cancel_event):
    throttle(cancel_event)
    work = tempfile.mkdtemp(prefix="apklocate-", dir=db.DATA / "work")
    try:
        pathlib.Path(work).chmod(0o770)  # the isolated runner uid shares the server's gid
        try:
            rc = jobs.run_isolated(log, ["bash", FETCH_PAGE, url, PAGE_FILE], jobs.runner_path(work),
                                   env_keys=build.SOLVER_ENV, timeout=FETCH_TIMEOUT)
        except jobs.JobFailed as e:
            if str(e) == "cancelled":
                raise
            raise _block(f"solver chain {e} on {url}")
        if rc != 0:
            raise _block(f"FlareSolverr and CFBypass both failed on {url} (runner exit {rc})")
        with jobs.open_runner_file(work, PAGE_FILE) as f:
            raw = f.read(PAGE_MAX_BYTES + 1)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if len(raw) > PAGE_MAX_BYTES:
        raise PageShape(f"page expected at most {PAGE_MAX_BYTES} bytes, got more")
    text = raw.decode("utf-8", "replace")
    if CHALLENGE.search(text):
        raise _block(f"Cloudflare challenge page on {url}")
    return text


def _row(package):
    r = db.conn().execute("SELECT * FROM app WHERE package = ?", (package,)).fetchone()
    if r is None:
        raise HttpError(404, f"app {package}: no app row")
    return r


def locate(package, log, cancel_event):
    """Look the package up when due (API part §1.4 table) and write the apkm_* columns and a found list URL.
    Raises only jobs.JobFailed('cancelled')."""
    r = _row(package)
    state = r["apkm_state"]
    if r["apkmirror_list_url"] or r["apkpure_url"] or state in NEVER_DUE:
        return
    if state in RETRY_DUE and r["apkm_retry_at"] and r["apkm_retry_at"] > db.utcnow():
        return
    s = db.conn().execute("SELECT name FROM store_app WHERE package = ?", (package,)).fetchone()
    log.line(f"[*] APKMirror lookup for {package}")
    res = decide(package, s["name"] if s and s["name"] else package, lambda url: _fetch(url, log, cancel_event))
    retry = {"not_found": lambda: _wall(NOT_FOUND_RETRY), "failed": lambda: _wall(NOT_FOUND_RETRY),
             "blocked": lambda: _state["retry_at"]}.get(res.state, lambda: None)()
    cands = [{"title": c.title, "url": app_url(c), "list_url": list_url(c)} for c in res.candidates]
    with db.tx() as c:  # a URL the user set during the lookup wins
        c.execute("UPDATE app SET apkm_state = ?, apkm_candidates = ?, apkm_error = ?, apkm_checked_at = ?, "
                  "apkm_retry_at = ?, apkmirror_list_url = ? "
                  "WHERE package = ? AND apkmirror_list_url IS NULL AND apkpure_url IS NULL",
                  (res.state, json.dumps(cands) if cands else None,
                   f"APKMirror lookup for {package}: {res.error}" if res.error else None,
                   db.utcnow(), retry, res.list_url, package))
    log.line(f"[{'+' if res.state == 'found' else '!'}] APKMirror lookup for {package}: {res.state}"
             f"{' ' + res.list_url if res.list_url else ''}{': ' + res.error if res.error else ''}")


def validate_list_url(url):
    """Normalised https://www.apkmirror.com/uploads/?appcategory=<slug>, or HttpError 400 naming both forms."""
    bad = HttpError(400, f"download.apkmirror_list_url expected {LIST_URL_FORMS}, got {url!r}")
    if not isinstance(url, str) or not URL_RE.match(url):
        raise bad
    u = urllib.parse.urlsplit(url)
    if u.hostname not in HOSTS or u.fragment:
        raise bad
    m = APP_PATH.match(u.path)
    if m and not u.query:
        return list_url(Cand(m[1], m[2], ""))
    q = urllib.parse.parse_qs(u.query)
    if u.path == "/uploads/" and list(q) == ["appcategory"] and len(q["appcategory"]) == 1 \
            and SLUG_RE.match(q["appcategory"][0]):
        return f"{BASE}/uploads/?appcategory={q['appcategory'][0]}"
    raise bad


def _url_value(package, field, v):
    if v is None or v == "":
        return None
    try:
        if field == "apkmirror_list_url":
            return validate_list_url(v)
        if isinstance(v, str) and URL_RE.match(v):
            return v
        raise HttpError(400, f"download.{field} expected an https:// URL, got {v!r}")
    except HttpError as e:
        raise HttpError(400, f"app {package}: {e.message}") from e


def validate_download(package, fields):
    """{field: normalised URL or None} of a "download" body, or HttpError 400 naming the app, field and value."""
    if not isinstance(fields, dict):
        raise HttpError(400, f"app {package}: download expected an object, got {type(fields).__name__}")
    unknown = sorted(set(fields) - set(URL_FIELDS))
    if unknown:
        raise HttpError(400, f"app {package}: download expected keys {', '.join(URL_FIELDS)}, got {', '.join(unknown)}")
    return {f: _url_value(package, f, v) for f, v in fields.items()}


def put_download(package, fields):
    """Set download URL fields (all validated first); a set list URL -> 'manual', a cleared one -> 'pending'."""
    vals = validate_download(package, fields)
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO app(package) VALUES (?)", (package,))
        for f, v in vals.items():  # f from URL_FIELDS
            c.execute(f"UPDATE app SET {f} = ? WHERE package = ?", (v, package))
        if "apkmirror_list_url" in vals:
            c.execute("UPDATE app SET apkm_state = ?, apkm_candidates = NULL, apkm_error = NULL, apkm_retry_at = NULL "
                      "WHERE package = ?", ("manual" if vals["apkmirror_list_url"] else "pending", package))


def download_state(package):
    """The API "download" object (spec §5.4); None when the package has no app row."""
    r = db.conn().execute("SELECT * FROM app WHERE package = ?", (package,)).fetchone()
    if r is None:
        return None
    return {"state": r["apkm_state"], **{f: r[f] for f in URL_FIELDS},
            "candidates": json.loads(r["apkm_candidates"]) if r["apkm_candidates"] else [],
            "error": r["apkm_error"], "retry_at": r["apkm_retry_at"], "checked_at": r["apkm_checked_at"]}


def reset_lookup(package):
    """Manual retry (spec §5.3): back to 'pending' so the next {item_id} run looks it up."""
    r = _row(package)
    with _lock:
        until = _state["retry_at"] if _clock() < _state["blocked_until"] else None
    if until:
        raise HttpError(409, f"APKMirror lookup for {package} blocked until {until}", "apkmirror_blocked",
                        retry_at=until)
    if r["apkm_state"] in ("found", "manual"):
        raise HttpError(409, f"app {package}: lookup expected state pending, not_found, failed, ambiguous or blocked, "
                             f"got {r['apkm_state']} ({r['apkmirror_list_url']})")
    with db.tx() as c:
        c.execute("UPDATE app SET apkm_state = 'pending', apkm_candidates = NULL, apkm_error = NULL, apkm_retry_at = NULL "
                  "WHERE package = ?", (package,))
