#!/usr/bin/env bash
# Deploy this App Lab app to the Arduino UNO Q and (re)start it.
#
#   ./deploy.sh             copy working copy to the board, rebuild/flash, start
#   ./deploy.sh logs        follow the app's logs on the board
#   ./deploy.sh board-logs  follow the logs of board_host.py (screen, reSpeaker)
#   ./deploy.sh stop        stop the app (and board_host.py)
#   ./deploy.sh setup       once per board: allow board_host.py to use the
#                           reSpeaker's USB controls (asks for the sudo password)
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
UDEV_RULE='SUBSYSTEM=="usb", ATTRS{idVendor}=="2886", ATTRS{idProduct}=="001a", MODE="0666"'

cd "$(dirname "$0")"

# board_host.py runs on the board itself: the app container can reach neither the
# display nor the reSpeaker's USB controls
PIDFILE="$DIR/.board/host.pid"
STOP_HOST="if [ -f '$PIDFILE' ]; then kill \"\$(cat '$PIDFILE')\" 2>/dev/null || true; rm -f '$PIDFILE'; fi"

# Runs on the board; reads the app files as a tar stream on stdin.
PRUNE=$(for k in $KEEP; do printf -- '-o -path ./%s ' "$k"; done)
read -r -d '' REMOTE_SCRIPT <<EOF || true
set -e
arduino-app-cli app stop '$DIR' </dev/null >/dev/null 2>&1 || true
$STOP_HOST
# Its predecessor from older deploys ([s]: the pattern must not match this script itself)
pkill -f 'python/[s]creen_host.py' || true
mkdir -p '$DIR' && cd '$DIR'
# Remove old files (so deleted/renamed files disappear), keep App Lab's cache and the models
find . -mindepth 1 \\( -path ./.cache $PRUNE\\) -prune -o -type f -exec rm -f {} +
tar -xzf -
echo '>> Starting board_host.py (screen, reSpeaker)'
mkdir -p .board
DISPLAY=:0 setsid nohup python3 -u '$DIR/python/board_host.py' > .board/host.log 2>&1 < /dev/null &
echo \$! > '$PIDFILE'
sleep 2
kill -0 "\$(cat '$PIDFILE')" 2>/dev/null || { echo '!! board_host.py failed:'; tail -5 .board/host.log; }
grep -h 'reSpeaker' .board/host.log | sed 's/^/>> /' || true
echo '>> Starting app (builds + flashes sketch, starts python)'
arduino-app-cli app start '$DIR' </dev/null
EOF

case "${1:-deploy}" in
  logs)
    exec ssh -t "$BOARD" "arduino-app-cli app logs --follow '$DIR'"
    ;;
  board-logs)
    exec ssh -t "$BOARD" "tail -n 50 -f '$DIR/.board/host.log'"
    ;;
  stop)
    exec ssh "$BOARD" "arduino-app-cli app stop '$DIR'; $STOP_HOST"
    ;;
  setup)
    exec ssh -t "$BOARD" "echo '$UDEV_RULE' | sudo tee /etc/udev/rules.d/99-respeaker.rules >/dev/null \
      && sudo udevadm control --reload-rules && sudo udevadm trigger --attr-match=idVendor=2886 \
      && echo '>> reSpeaker USB controls enabled (board_host.py picks them up within seconds)'"
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
    echo "usage: $0 [deploy|logs|board-logs|stop|setup]" >&2
    exit 1
    ;;
esac
