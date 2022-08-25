"""This config file reads and exposes the environment variables defined in the k8s Pod spec and
ConfigMaps for the session-controller container.
"""

import os
from common.lib.session_controller import constants

POD_NAME = os.getenv('POD_NAME')
POD_NAMESPACE = os.getenv('POD_NAMESPACE')
RECORDER_AGENT_TYPE = os.getenv('RECORDER_AGENT_TYPE', 'video-recording')
GCS_RECORDING_BUCKET_NAME = os.getenv('GCS_RECORDING_BUCKET_NAME')
PUBSUB_SUBSCRIPTION_ID = os.getenv('PUBSUB_SUBSCRIPTION_ID', 'recorder-agent-subscription')
RECORDING_DIR = os.getenv('RECORDING_DIR', '/recording')
X_DISPLAY = os.getenv('X_DISPLAY', ':3')
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
    os.getenv('RECORDER_AGENT_SECRET', 'placeholder-secret'),
    constants.UTF_8)
