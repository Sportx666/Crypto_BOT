#!/bin/bash
set -e

mkdir -p /root/.vnc

# VNC password comes from the VNC_PASSWORD env var (set it in .env or the
# compose environment — do NOT leave this at the default on a real VPS).
x11vnc -storepasswd "${VNC_PASSWORD:-changeme}" /root/.vnc/passwd

exec supervisord -n -c /etc/supervisor/conf.d/supervisord.conf
