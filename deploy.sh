#!/usr/bin/env bash
# Deploy this App Lab app to the Arduino UNO Q and (re)start it.
#
#   ./deploy.sh         copy working copy to the board, rebuild/flash, start
#   ./deploy.sh logs    show the app's logs on the board
#   ./deploy.sh stop    stop the app
#
# Override the board address if mDNS doesn't resolve:
#   BOARD=arduino@192.168.x.y ./deploy.sh
set -euo pipefail

# mDNS name gets a "-N" suffix on busy networks; check with: avahi-browse -rt _arduino._tcp
BOARD="${BOARD:-arduino@benFelixUno-2.local}"
APP="keep_up"
REMOTE_DIR="/home/arduino/ArduinoApps/$APP"

cd "$(dirname "$0")"

case "${1:-deploy}" in
  logs)
    exec ssh -t "$BOARD" "arduino-app-cli app logs --follow '$REMOTE_DIR'"
    ;;
  stop)
    exec ssh "$BOARD" "arduino-app-cli app stop '$REMOTE_DIR'"
    ;;
  deploy)
    echo ">> Deploying $APP to $BOARD:$REMOTE_DIR"
    # Ship every file git would track (committed or not, minus .gitignore'd ones),
    # then on the board: stop app, replace files (keep App Lab's .cache), start app.
    git ls-files -z --cached --others --exclude-standard \
      | tar --null --ignore-failed-read -czf - -T - \
      | ssh "$BOARD" "set -e
          arduino-app-cli app stop '$REMOTE_DIR' </dev/null >/dev/null 2>&1 || true
          mkdir -p '$REMOTE_DIR'
          find '$REMOTE_DIR' -mindepth 1 -maxdepth 1 ! -name .cache -exec rm -rf {} +
          tar -xzf - -C '$REMOTE_DIR'
          echo '>> Starting app (builds + flashes sketch, starts python)'
          arduino-app-cli app start '$REMOTE_DIR' </dev/null"
    echo ">> Done. Logs: ./deploy.sh logs"
    ;;
  *)
    echo "usage: $0 [deploy|logs|stop]" >&2
    exit 1
    ;;
esac
