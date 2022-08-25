ARG BASE_IMAGE=gcr.io/uberconf/sip-room-agent-base:latest
FROM $BASE_IMAGE

# install python3 / pip
RUN DEBIAN_FRONTEND=noninteractive \
    add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y \
    python3.9 \
    python3.9-distutils \
    python3-pip \
    --no-install-recommends \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.6 1 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.9 2 \
    && python3 -m pip install --upgrade pip setuptools wheel

# install the browser_agent Python dependencies
COPY common/browser_agent/requirements.txt /tmp/requirements.txt
RUN pip3 install --upgrade -r /tmp/requirements.txt && rm /tmp/requirements.txt

# make the working directory
WORKDIR /app

# copy common/lib into the working directory
RUN mkdir -p ./common/lib
COPY common/lib/ ./common/lib

# copy the browser agent source into the working directory
COPY common/browser_agent/src/ .

# run the init script as the root user (since thus far, we haven't gotten pulse working without running as root)
USER root
# ideally, we should create a new user with minimal privilleges that can at least run Chrome, and if possible, pulse as
#  well. since we are running everything as root, it's important not to run untrusted code in Chrome, although this
#  generally should not be an issue for this application.

RUN usermod -aG pulse,pulse-access root

COPY sip_room_connector/sip_room_agent/mosquitto/generate_mqtt_ws_certs.sh generate_mqtt_ws_certs.sh
COPY sip_room_connector/sip_room_agent/interop-init interop-init

# run the Python-based browser-agent, which starts Chrome/Xvfb/Pulse Audio and then serves as an HTTP-based daemon
#  that wraps FFmpeg
CMD ["./interop-init"]
