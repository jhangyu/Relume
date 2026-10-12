#!/bin/sh
# runs inside the container: start Xvfb on :98 (cfbypass already owns :99 in the shared network namespace), then the given command
Xvfb :98 -screen 0 1920x1080x24 -nolisten tcp &
export DISPLAY=:98
for _ in $(seq 1 50); do [ -S /tmp/.X11-unix/X98 ] && break; sleep 0.1; done
exec "$@"
