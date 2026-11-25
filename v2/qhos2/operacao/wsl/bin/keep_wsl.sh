#!/bin/sh
set -eu
token="${1:?startup token required}"
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
systemctl --user start qhos2-brain.service qhos2-coletor.service
systemctl --user is-active --quiet qhos2-brain.service
systemctl --user is-active --quiet qhos2-coletor.service
/usr/bin/python3 /home/usuario/qhos-v2/bin/write_keepalive.py "$token" "$$"
exec /bin/sleep infinity
