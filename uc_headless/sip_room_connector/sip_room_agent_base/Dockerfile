# phusion (Ubuntu) base image
FROM phusion/baseimage:18.04-1.0.0 as sip_agent_base

# inspired by:
# - https://github.com/jessfraz/dockerfiles/blob/master/chrome/stable/Dockerfile
# - https://github.com/parlaylabs/fatline/blob/2.0/master/infra/docker/interop-agent/Dockerfile

# install dependencies
RUN add-apt-repository ppa:eh5/pulseaudio-a2dp \
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
    --no-install-recommends

RUN apt-get update && apt-get install -y \
    libcanberra-gtk3-0 \
    libcanberra-gtk0 \
	libcanberra-gtk3-module \
	ca-certificates \
	curl \
	gnupg \
	hicolor-icon-theme \
	libgl1-mesa-dri \
	libgl1-mesa-glx \
	libpango1.0-0 \
	libv4l-0 \
	fonts-symbola \
    gstreamer1.0-0 \
    gstreamer1.0-libav \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-x \
    libasound2 \
    libavcodec58 \
    libavdevice58 \
    libavformat58 \
    libavutil55 \
    libc6 \
    libcairo2 \
    libglib2.0-0 \
    libgstreamer-plugins-base1.0-0 \
    libgstreamer1.0-0 \
    libopus0 \
    libspandsp2 \
    libspeexdsp1 \
    libswscale4 \
    libv4l-0 \
    libx11-6\
    libx264-152\
    libxext6 \
    libnss3-tools \
    sudo \
    alsa-utils \
    net-tools \
    x11vnc \
    xvfb \
    fonts-liberation \
    libappindicator3-1 \
    openssl \
    libssl1.0-dev \
    wget \
    xdg-utils \
	--no-install-recommends \
    && apt-add-repository -y ppa:mosquitto-dev/mosquitto-ppa \
	&& apt-get update && apt-get install -y \
    libmosquitto-dev mosquitto mosquitto-clients \
    && curl -sSL https://dl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] https://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list

FROM sip_agent_base as baresip_build

RUN apt-get update && apt-get install -y \
    build-essential \
    debhelper \
    libtool-bin \
    librem-dev \
    libre-dev \
    libasound2-dev \
    libx264-dev \
    libavformat-dev \
    libavdevice-dev \
    libswscale-dev \
    libopus-dev \
    libxcb1-dev \
    libspandsp-dev \
    libv4l-dev \
    libgstreamer-plugins-good1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    libpulse-dev \
    dpkg-dev \
    --no-install-recommends

ADD sip_room_connector/sip_room_agent_base/baresip/g722_1-0.2.0.tar.gz /src/
WORKDIR /src/g722_1-0.2.0
RUN ./autogen.sh \
    && ./configure --prefix=/usr \
    && make \
    && make install \
    && libtool --finish /usr/lib \
    && ldconfig
COPY sip_room_connector/sip_room_agent_base/baresip/rem /src/rem
WORKDIR /src/rem
RUN make \
    && make install \
    && dpkg-buildpackage

COPY sip_room_connector/sip_room_agent_base/baresip/libre /src/libre
WORKDIR /src/libre
RUN make \
    && make install \
    && dpkg-buildpackage

COPY sip_room_connector/sip_room_agent_base/baresip/baresip /src/baresip
WORKDIR /src/baresip
RUN make \
    && dpkg-buildpackage

FROM sip_agent_base

RUN apt-get update && apt-get install -y \
    google-chrome-stable \
    --no-install-recommends

COPY sip_room_connector/sip_room_agent_base/pulseaudio/ /etc/pulse
COPY --from=baresip_build /src/libre_0.5.7_amd64.deb /src/libre_0.5.7_amd64.deb
COPY --from=baresip_build /src/librem_0.5.2_amd64.deb  /src/librem_0.5.2_amd64.deb
COPY --from=baresip_build /src/libbaresip_0.5.8_amd64.deb  /src/libbaresip_0.5.8_amd64.deb
COPY --from=baresip_build /src/baresip_0.5.8_amd64.deb  /src/baresip_0.5.8_amd64.deb
COPY --from=baresip_build /usr/lib/libg722_1.so* /usr/lib/

RUN dpkg -i /src/libre_0.5.7_amd64.deb \
    && dpkg -i /src/librem_0.5.2_amd64.deb \
    && dpkg -i /src/libbaresip_0.5.8_amd64.deb \
    && dpkg -i /src/baresip_0.5.8_amd64.deb

RUN mkdir -m 740 -p /var/run/mosquitto
RUN chown mosquitto: /var/run/mosquitto

# Copy the custom mosquitto config into the image.
COPY sip_room_connector/sip_room_agent_base/mosquitto/mosquitto.conf /etc/mosquitto/mosquitto.conf

# Copy customized config files for system fonts, ALSA and baresip as well as init script into their respective locations.
COPY sip_room_connector/sip_room_agent_base/local.conf /etc/fonts/local.conf
COPY sip_room_connector/sip_room_agent_base/baresip/baresip-config /root/.baresip/config
COPY sip_room_connector/sip_room_agent_base/baresip/baresip-accounts /root/.baresip/dev-accounts

