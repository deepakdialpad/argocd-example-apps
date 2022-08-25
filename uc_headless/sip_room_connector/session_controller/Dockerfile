# slim Debian-based image
FROM python:3.9.0-slim-buster

WORKDIR /app

# copy the dependencies file to the working directory
COPY sip_room_connector/session_controller/requirements.txt .

# install Python dependencies
RUN pip install --upgrade -r requirements.txt && rm requirements.txt

# copy common/lib into the working directory
RUN mkdir -p ./common/lib
COPY common/lib/ ./common/lib

# copy the contents of the local src directory to the working directory
COPY sip_room_connector/session_controller/src/ .

RUN groupadd -r sip-room-connector && useradd --no-log-init -r -g sip-room-connector sip-room-connector

USER sip-room-connector

CMD ["python", "app.py"]
