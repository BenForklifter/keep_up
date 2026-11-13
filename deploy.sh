#!/usr/bin/env bash
# Deploy keep_up to an Arduino UNO Q over ssh and (re)start it.
#
#   ./deploy.sh             copy the working tree to the board, build and flash the
#                           sketch, start the app and board_host.py
#   ./deploy.sh logs        follow the app's log
#   ./deploy.sh board-logs  follow the log of board_host.py (screen, mic array)
#   ./deploy.sh stop        stop the app and board_host.py
#   ./deploy.sh setup       once per board: give board_host.py access to the
#                           reSpeaker's USB controls (asks for the sudo password)
#
# BOARD is the board's ssh target, e.g. BOARD=arduino@192.168.1.20 ./deploy.sh
# (`hostname -I` on the board shows its address).
set -euo pipefail

if [[ -z "${BOARD:-}" ]]; then
  echo "BOARD is not set, e.g. BOARD=arduino@192.168.1.20 $0 ${1:-}" >&2
  exit 1
fi
APP="keep_up"
DIR="/home/arduino/ArduinoApps/$APP"
# Kept on the board across deploys, not in git: the speech model (downloaded on the
# first start, python/speech.py) and the settings made on the screen (python/screen.py).
KEEP="models .board/settings.json"
UDEV_RULE='SUBSYSTEM=="usb", ATTRS{idVendor}=="2886", ATTRS{idProduct}=="001a", MODE="0666"'

cd "$(dirname "$0")"

# board_host.py runs on the board itself: the app container can reach neither the
# monitor nor the reSpeaker's USB controls.
PIDFILE="$DIR/.board/host.pid"
STOP_HOST="if [ -f '$PIDFILE' ]; then kill \"\$(cat '$PIDFILE')\" 2>/dev/null || true; rm -f '$PIDFILE'; fi"

# Runs on the board; reads the app files as a tar stream on stdin.
PRUNE=$(for k in $KEEP; do printf -- '-o -path ./%s ' "$k"; done)
read -r -d '' REMOTE_SCRIPT <<EOF || true
set -e
arduino-app-cli app stop '$DIR' </dev/null >/dev/null 2>&1 || true
$STOP_HOST
mkdir -p '$DIR' && cd '$DIR'
# Remove old files, so that deleted and renamed ones disappear; keep App Lab's cache and KEEP
find . -mindepth 1 \\( -path ./.cache $PRUNE\\) -prune -o -type f -exec rm -f {} +
tar -xzf -
echo '>> Starting board_host.py (screen, mic array)'
mkdir -p .board
DISPLAY=:0 setsid nohup python3 -u '$DIR/python/board_host.py' > .board/host.log 2>&1 < /dev/null &
echo \$! > '$PIDFILE'
sleep 2
kill -0 "\$(cat '$PIDFILE')" 2>/dev/null || { echo '!! board_host.py failed:'; tail -5 .board/host.log; }
grep -h 'reSpeaker' .board/host.log | sed 's/^/>> /' || true
echo '>> Starting the app (builds and flashes the sketch, starts the Python app)'
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
