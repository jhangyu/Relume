# Play delivery URL probe (2026-11-20)

**Verdict:** portable across clients. The server resolves the URLs; another client fetches them with a plain GET (no cookies, no auth header) and gets the APK bytes. Cross-IP portability was not tested here: both clients share one public IP. The first live run with a phone on mobile data is the cross-IP check.

| Question | Answer |
|---|---|
| Token configured | Yes (`settings.google_play()` is not None). |
| URLs without downloading | Yes: the apkeep fork, `-o probe=session`, stdin `<versionCode>` → JSON `{url, splits[{name,url}]}`. No new patch. |
| Cross-client fetch | The host fetched all 4 URLs resolved inside the container: HTTP 206, `PK` header. |
| Bound params | The URL query has only `token` and `cpn` (16 chars). No visible IP, expiry or device param; the token is opaque, so binding inside it is unknown. |
| Splits per profile | With profile `px_9a` the splits are `config.arm64_v8a`, `config.en`, `config.xxhdpi`. Choosing the profile per phone (ABI, density, language) needs a new mapping from the device row. |

## Log
- `docker exec -w /repo/local relume-app python3 /tmp/probe.py com.spotify.music px_9a /tmp/o1.jsonl details` → `apkeep_rc 0`, versionCode 148387875 (9.2.0.2053).
- `docker exec -w /repo/local relume-app python3 /tmp/probe.py com.spotify.music px_9a /tmp/o1.jsonl 148387875` → `apkeep_rc 0`, mode delivery, 4 URLs on host `play.googleapis.com` with params `token`, `cpn`.
- `docker exec relume-app cat /tmp/o1.jsonl | python3 fetch.py` (a one-off script, since deleted, that GETs each URL with Range `bytes=0-1023`, run on the macOS host):

| APK | Status | Bytes | Header | Total size |
|---|---|---|---|---|
| base | 206 | 1024 | zip | 101256400 |
| config.arm64_v8a | 206 | 1024 | zip | 34145588 |
| config.en | 206 | 1024 | zip | 1069465 |
| config.xxhdpi | 206 | 1024 | zip | 545870 |

## Locale probe (after 0f2a2c6)
- The user ran `docker exec -w /repo/local relume-app python3 -c "$(cat locale_probe.py)" com.spotify.music zh_TW` (a one-off script, since deleted, that runs apkeep details + delivery and prints split names only). It passes `-o device=px_9a,locale=zh_TW,probe=session` to the pinned apkeep and needed no image change.
- Output: `com.spotify.music versionCode 148387875 locale zh_TW splits: config.arm64_v8a, config.xxhdpi, config.zh`.
- Verdict: Play honors the locale, so a zh-TW request gets `config.zh` where the default profile got `config.en`.

The commands were run by the user because the permission layer denied the agent's token-reading `docker exec`. The user removed the probe script and `/tmp/o1.jsonl` from the container afterwards.
