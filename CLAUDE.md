# Relume project rules

- Test entry: `python3 scripts/test.py` (legs `python`, `shell`, `android`; macOS and Linux only, no Windows). Run it before every commit that touches code.
- Full-suite budget: 90 s. Reason: Gradle daemon cold start for the Android leg plus the Python suite, measured at about 70 s serial on 85d44ea (`docs/logs/2026-10-25-per-app-contract.md` parking lot). Per-test cap: 1 s. Over either limit means rewrite or delete the test, not raise the limit.
- Linux gate (the server's real platform): `docker build -f local/Dockerfile --target app -t relume-gate . && docker run --rm -v "$PWD:/repo" -w /repo relume-gate env -u RELUME_VERSION python3 scripts/test.py --legs python,shell`.
- Android leg: `ANDROID_HOME=<sdk> python3 scripts/test.py --legs android`.
- Never push to an upstream remote. The only remote is the private `jhangyu/Relume`.
