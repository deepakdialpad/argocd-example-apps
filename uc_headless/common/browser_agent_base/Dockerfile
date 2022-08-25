# phusion (Ubuntu) base image
FROM phusion/baseimage:18.04-1.0.0

# inspired by:
# - https://github.com/jessfraz/dockerfiles/blob/master/chrome/stable/Dockerfile
# - https://github.com/parlaylabs/fatline/blob/2.0/master/infra/docker/interop-agent/Dockerfile

# install dependencies
RUN add-apt-repository -y ppa:jonathonf/ffmpeg-4 \
   && apt-get update && apt-get install -y \
    # Chrome dependencies
    apt-transport-https \
    curl \
    # additional dependencies – FFmpeg, xvfb, Alsa utils for pulse audio, Pulse Audio, pip.
    libpulse0 \
    alsa-oss \
    alsa-utils \
    pulseaudio \
    pulseaudio-utils \
    xvfb \
    ffmpeg \
    --no-install-recommends \
    && curl -sSL https://dl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] https://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y \
    google-chrome-stable \
    --no-install-recommends

COPY pulseaudio/ /etc/pulse
