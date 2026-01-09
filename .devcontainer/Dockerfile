FROM debian:bookworm

ARG DEBIAN_FRONTEND=noninteractive
ARG IDF_VERSION=v5.2.2

RUN apt-get update && apt-get install -y \
  git curl python3 python3-pip python3-venv \
  cmake ninja-build gperf ccache \
  flex bison build-essential \
  libffi-dev libssl-dev dfu-util \
  usbutils \
  && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/esp && cd /opt/esp && \
  git clone --recursive https://github.com/espressif/esp-idf.git idf && \
  cd idf && git checkout ${IDF_VERSION} && \
  git submodule update --init --recursive && \
  ./install.sh && \
  python3 -m pip install --no-cache-dir -r requirements.txt

ENV IDF_PATH=/opt/esp/idf
WORKDIR /work

# ---- Arduino CLI ----
ARG ARDUINO_CLI_VERSION=1.4.0

# Ensure CA certs are present for HTTPS downloads
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Install Arduino CLI to a directory already in PATH
RUN curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh \
  | BINDIR=/usr/local/bin sh -s ${ARDUINO_CLI_VERSION}

RUN arduino-cli version