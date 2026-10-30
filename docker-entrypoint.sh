#!/bin/sh
# Runs the app as PUID:PGID (default 1000:1000) so Radarr can move and delete
# the files it downloads.
set -e
PUID=${PUID:-1000}
PGID=${PGID:-1000}
umask "${UMASK:-002}"

if [ "$(id -u)" = 0 ]; then
  mkdir -p "$DATA_DIR" "$COMPLETE_DIR/.incomplete"
  chown -R "$PUID:$PGID" "$DATA_DIR"
  # Only the top levels: release folders already handed to Radarr are left alone.
  find "$COMPLETE_DIR" -maxdepth 1 -type d -exec chown "$PUID:$PGID" {} +
  chown -R "$PUID:$PGID" "$COMPLETE_DIR/.incomplete"
  exec python -c 'import os, sys
os.setgroups([])
os.setgid(int(sys.argv[2]))
os.setuid(int(sys.argv[1]))
os.execvp(sys.argv[3], sys.argv[3:])' "$PUID" "$PGID" "$@"
fi
exec "$@"
