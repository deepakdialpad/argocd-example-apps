# slim Debian-based image
FROM python:3.9.0-slim-buster

WORKDIR /app

# copy the dependencies file to the working directory
COPY video_recording/session_controller/requirements.txt .

# install Python dependencies
RUN pip install --upgrade -r requirements.txt && rm requirements.txt

# copy common/lib into the working directory
RUN mkdir -p ./common/lib
COPY common/lib/ ./common/lib

# copy the session controller source to the working directory
COPY video_recording/session_controller/src/ .

RUN groupadd -r recorder-agent && useradd --no-log-init -r -g recorder-agent recorder-agent

USER recorder-agent

CMD ["python", "app.py"]
