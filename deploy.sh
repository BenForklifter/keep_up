#!/usr/bin/env bash
# Deploy this App Lab app to the Arduino UNO Q and (re)start it.
#
#   ./deploy.sh         copy working copy to the board, rebuild/flash, start
#   ./deploy.sh logs    follow the app's logs on the board
#   ./deploy.sh stop    stop the app (and the screen)
#
# Override the board address if its IP changed:
#   BOARD=arduino@192.168.x.y ./deploy.sh
# (find it on the board's monitor with `hostname -I`)
set -euo pipefail

# IP, because the .local name keeps changing on the busy hackathon network
BOARD="${BOARD:-arduino@192.168.1.216}"
APP="keep_up"
DIR="/home/arduino/ArduinoApps/$APP"
# Not in git, kept on the board across deploys: the speech models, which the app
# downloads on its first start (see python/speech.py).
KEEP="models"

cd "$(dirname "$0")"

# The screen process runs on the board itself (the app container can't reach the display)
PIDFILE="$DIR/.screen/host.pid"
STOP_SCREEN="if [ -f '$PIDFILE' ]; then kill \"\$(cat '$PIDFILE')\" 2>/dev/null || true; rm -f '$PIDFILE'; fi"

# Runs on the board; reads the app files as a tar stream on stdin.
PRUNE=$(for k in $KEEP; do printf -- '-o -path ./%s ' "$k"; done)
read -r -d '' REMOTE_SCRIPT <<EOF || true
set -e
arduino-app-cli app stop '$DIR' </dev/null >/dev/null 2>&1 || true
$STOP_SCREEN
mkdir -p '$DIR' && cd '$DIR'
# Remove old files (so deleted/renamed files disappear), keep App Lab's cache and the models
find . -mindepth 1 \\( -path ./.cache $PRUNE\\) -prune -o -type f -exec rm -f {} +
tar -xzf -
echo '>> Starting screen on the monitor'
mkdir -p .screen
DISPLAY=:0 setsid nohup python3 '$DIR/python/screen_host.py' > .screen/host.log 2>&1 < /dev/null &
echo \$! > '$PIDFILE'
sleep 2
kill -0 "\$(cat '$PIDFILE')" 2>/dev/null || { echo '!! screen failed:'; tail -5 .screen/host.log; }
echo '>> Starting app (builds + flashes sketch, starts python)'
arduino-app-cli app start '$DIR' </dev/null
EOF

case "${1:-deploy}" in
  logs)
    exec ssh -t "$BOARD" "arduino-app-cli app logs --follow '$DIR'"
    ;;
  stop)
    exec ssh "$BOARD" "arduino-app-cli app stop '$DIR'; $STOP_SCREEN"
    ;;
  deploy)
    echo ">> Deploying $APP to $BOARD:$DIR"
    # Ship every file git would track (committed or not, minus .gitignore'd ones)
    git ls-files -z --cached --others --exclude-standard \
      | tar --null --ignore-failed-read -czf - -T - \
      | ssh "$BOARD" "$REMOTE_SCRIPT"
    echo ">> Done. Logs: ./deploy.sh logs"
    ;;
  *)
    echo "usage: $0 [deploy|logs|stop]" >&2
    exit 1
    ;;
esac
