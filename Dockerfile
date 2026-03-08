FROM ubuntu:24.04

# Suppress interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# System packages: Xvfb for headless display, Qt6 runtime, Python 3.12, curl for uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Headless display
    xvfb \
    # Qt6 runtime libraries required by mgba-qt
    libqt6core6 \
    libqt6gui6 \
    libqt6widgets6 \
    libqt6opengl6 \
    libqt6network6 \
    libgl1 \
    libegl1 \
    # Audio (PulseAudio stub so mgba-qt doesn't abort)
    libpulse0 \
    pulseaudio \
    # Python 3 and pip (uv installs on top)
    python3 \
    python3-pip \
    # curl + ca-certs for downloading uv
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager)
RUN curl -Ls https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Copy mgba-qt binary and libraries (built separately; see docs/building-mgba.md)
# Expects the host to have built mgba-qt and placed the binary at ~/mgba/build/qt/
# The binary is copied into /mgba/ inside the container.
COPY --chown=root:root mgba/ /mgba/
RUN chmod +x /mgba/mgba-qt 2>/dev/null || true

# Copy project source
WORKDIR /app
COPY pyproject.toml uv.lock* ./
COPY src/ src/

# Install Python dependencies (no editable install needed in container)
RUN uv sync --no-dev

# Screenshot and knowledge directories (overridden by Docker volumes)
RUN mkdir -p /data/screenshots /data/knowledge

EXPOSE 8000
