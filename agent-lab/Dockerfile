# Hedef masaüstü + AgentLab servisleri (Sprint 0): Ubuntu 24.04, Xvfb 1280x800, xterm.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive DISPLAY=:99 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip xvfb xterm x11-utils git ca-certificates fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY services ./services
COPY ui ./ui
COPY contracts ./contracts
COPY fixtures ./fixtures
RUN pip3 install --break-system-packages fastapi "uvicorn[standard]" sse-starlette mss pynput pillow anthropic jsonschema
ENV PYTHONPATH=/app/services
EXPOSE 8799
# Xvfb + xterm (ajanın göreceği masaüstü) + gateway
CMD sh -c "Xvfb :99 -screen 0 1280x800x24 & sleep 0.5; xterm -geometry 120x40+20+20 -fa DejaVuSansMono -fs 11 & \
           python3 -m agentlab.gateway --host 0.0.0.0 --port 8799 --with-display ${AGENTLAB_FLAGS:-}"
