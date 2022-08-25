import os

from dotenv import load_dotenv

APP_ENV_CONFIG_FILE = os.getenv('APP_ENV_CONFIG_FILE')

# load the env file
load_dotenv(dotenv_path=APP_ENV_CONFIG_FILE)

X_DISPLAY = os.getenv('X_DISPLAY', ':3')
WINDOW_SIZE_WIDTH = os.getenv('WINDOW_SIZE_WIDTH', '1280')
WINDOW_SIZE_HEIGHT = os.getenv('WINDOW_SIZE_HEIGHT', '720')
X_DISPLAY_DEPTH = os.getenv('X_DISPLAY_DEPTH', '24')
CHROME_DEBUG_PORT = os.getenv('CHROME_DEBUG_PORT', '9000')
STREAMING_MODE = bool(os.getenv('STREAMING_MODE', None))
GCS_RECORDING_BUCKET_NAME = os.getenv('GCS_RECORDING_BUCKET_NAME')
RECORDING_DIR = os.getenv('RECORDING_DIR', '/recording')
RECORDER_AGENT_TYPE = os.getenv('RECORDER_AGENT_TYPE', 'video-recording')
MOSQUITTO_CONFIG_FILE = os.getenv('MOSQUITTO_CONFIG_FILE')
BARESIP_CONFIG_DIR = os.getenv('BARESIP_CONFIG_DIR')
