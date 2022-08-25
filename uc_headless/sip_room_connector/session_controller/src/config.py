"""This config file reads and exposes the environment variables defined in the k8s Pod spec and
ConfigMaps for the session-controller container.
"""

import os

from dotenv import load_dotenv

from common.lib.session_controller import constants

APP_ENV_CONFIG_FILE = os.getenv('APP_ENV_CONFIG_FILE')

# load the env file
load_dotenv(dotenv_path=APP_ENV_CONFIG_FILE)

POD_NAME = os.getenv('POD_NAME')
POD_NAMESPACE = os.getenv('POD_NAMESPACE')
RECORDER_AGENT_TYPE = os.getenv('RECORDER_AGENT_TYPE', 'sip-room-connector')
PUBSUB_SUBSCRIPTION_ID = os.getenv('PUBSUB_SUBSCRIPTION_ID', 'sip-room-connector-subscription')
# browser window dimensions
WINDOW_SIZE_WIDTH = os.getenv('WINDOW_SIZE_WIDTH', '1280')
WINDOW_SIZE_HEIGHT = os.getenv('WINDOW_SIZE_HEIGHT', '720')
# chrome devtools endpoint
DEVTOOLS_URL = os.getenv('DEVTOOLS_URL', 'http://localhost')
CHROME_DEBUG_PORT = os.getenv('CHROME_DEBUG_PORT', '9000')
# recording_session_controller log level
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
# recorder secret, which should be known only to the recorder and the UC service
RECORDER_SECRET_BYTES = bytes(
    os.getenv('SIP_ROOM_CONNECTOR_AGENT_SECRET', 'placeholder-secret'),
    constants.UTF_8)
PUBLIC_NODE_IP = os.getenv('PUBLIC_NODE_IP')
MQTTWSPORT = os.getenv('MQTTWSPORT')
DIALPAD_MEETINGS_APP_URL = os.getenv('DIALPAD_MEETINGS_APP_URL')
IDLE_URL_PATH = os.getenv('IDLE_URL_PATH')
