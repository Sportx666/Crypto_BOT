# Binance spot bot (Tkinter/customtkinter GUI) — runs behind a virtual
# display (Xvfb) exposed over VNC so it can run unattended on a VPS while
# still being viewable/interactable exactly like the local GUI.
FROM python:3.11-slim

LABEL maintainer="CryptoBOT" \
      description="Binance spot trading bot with virtual display + VNC"

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-tk \
    tk-dev \
    xvfb \
    x11vnc \
    fluxbox \
    fonts-dejavu \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# Note: runs as root (not botuser) — Xvfb/VNC process management inside a
# single supervisord tree is simpler this way. Isolation comes from Docker
# itself; this container should not be exposed beyond a VPN/SSH tunnel.
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only the legacy bot's code (not bot/ — that's the separate HL image)
COPY UI.py main.py scheduler.py env_loader.py ./
COPY core/       ./core/
COPY strategy/   ./strategy/
COPY indicators/ ./indicators/
COPY misc/       ./misc/
COPY trade/      ./trade/
COPY utilities/  ./utilities/
COPY backtest/   ./backtest/

RUN mkdir -p /app/logs /app/data

COPY docker/binance/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY docker/binance/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV DISPLAY=:99 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# VNC port — do not expose publicly without a firewall/VPN in front of it.
EXPOSE 5900

ENTRYPOINT ["/entrypoint.sh"]
