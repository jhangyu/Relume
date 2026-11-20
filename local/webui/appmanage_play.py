"""Google Play delivery URLs for one device profile: the phone downloads from Google, the server stores no APK.
Spec: docs/superpowers/plans/2026-11-20-app-manage-plan.md §1.8. Log lines carry host and split names, never a query."""
import urllib.parse
import uuid

from . import build, db, devices, ota, playversion, settings

PROFILE_BY_ABI = build.APKEEP_DEVICE


class PlayLookupError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class _NullLog:
    def line(self, text):
        pass


def profile_for(dev):
    """apkeep profile of the phone's preferred (first) ABI."""
    abis = [a.strip() for a in (dev.get("abi_list") or "").split(",") if a.strip()]
    abi = abis[0] if abis else "none"
    if abi not in PROFILE_BY_ABI:
        raise PlayLookupError("NO_PLAY_PROFILE", f"no apkeep profile for ABI {abi} of device {dev.get('name')}")
    return PROFILE_BY_ABI[abi]


def play_locale(dev):
    """apkeep locale (language[_REGION], script dropped) of the phone's first system locale: app rows report it at sync,
    ADB rows are read live. No English default: an unknown locale would install the app in the wrong language."""
    try:
        tag = dev["locale"] if devices.is_app(dev) else devices.adb_locale(dev)
    except (devices.InventoryFailed, devices.AdbTimeout) as e:
        raise PlayLookupError("PLAY_LOOKUP_FAILED", f"cannot read the locale of device {dev['name']}: {e}") from e
    if not tag or not ota.LOCALE_RE.match(tag):
        raise PlayLookupError("PLAY_LOOKUP_FAILED", f"expected a BCP-47 locale like zh-TW for device {dev['name']}, "
                                                    f"got {tag!r} (a Relume app reports it at its next sync)")
    language, *rest = tag.split("-")
    return "_".join([language] + [p for p in rest if len(p) == 2])


def resolve(package, dev, log=None):
    """{'version_code': int, 'apks': [{'name', 'url'}]}, base first. Raises PlayLookupError."""
    log = log or _NullLog()
    profile = profile_for(dev)
    locale = play_locale(dev)
    where = f"{package} for device {dev.get('name')} (profile {profile}, locale {locale})"
    gp = settings.google_play()
    if gp is None:
        raise PlayLookupError("PLAY_LOOKUP_FAILED", f"Google Play credentials not set in Settings, needed for {where}")
    ini = db.KEY / f"apkeep-am-{uuid.uuid4().hex}.ini"
    settings.write_secret(ini, f"[google]\nemail = {gp['email']}\naas_token = {gp['aas_token']}\n")
    play = playversion.PlayClient(apkeep=build.APKEEP, ini=ini, device=profile, log=log, locale=locale)
    play.package = package
    try:
        _, code = play.details()
        d = play.delivery(code)
    except playversion.VersionCodeError as e:
        raise PlayLookupError("PLAY_LOOKUP_FAILED", f"Play lookup of {where} failed: {e}") from e
    finally:
        play.close()
        settings.write_secret(ini, None)
    apks = [{"name": "base", "url": d["url"]}] + [{"name": s["name"], "url": s["url"]} for s in d["splits"]]
    log.line(f"Play delivery {where} versionCode {code}: "
             + ", ".join(f"{a['name']}@{urllib.parse.urlsplit(a['url']).hostname}" for a in apks))
    return {"version_code": code, "apks": apks}
