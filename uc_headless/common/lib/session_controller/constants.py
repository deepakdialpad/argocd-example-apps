UTF_8 = 'UTF-8'

# HTTP method strings
HTTP_GET = 'GET'
HTTP_PUT = 'PUT'
HTTP_POST = 'POST'
HTTP_PATCH = 'PATCH'

# Pub/Sub ACK or NACK
PUB_SUB_ACK_TYPE_ACK = 'ack'
PUB_SUB_ACK_TYPE_NACK = 'nack'

# Video Recording states.
# NOTE: These states should match the definitions in firespotter/uberconf/constants.py
#  (in the future we may be able to set up the Dockerfile such that we can actually import that
#  file here)
RECORDING_STATUS = ['in_progress', 'ended', 'available',
                    'canceled', 'truncated', 'failed', 'should_cancel']
# Recording has started.
RECORDING_IN_PROGRESS = RECORDING_STATUS[0]
# Recording has ended normally.
RECORDING_ENDED = RECORDING_STATUS[1]
# Recording completed normally and is available for download.
RECORDING_AVAILABLE = RECORDING_STATUS[2]
# Recording was canceled before starting, ex. because user left before the recording was started.
RECORDING_CANCELED = RECORDING_STATUS[3]
# Recording is available for download but the session terminated unexpectedly.
RECORDING_TRUNCATED = RECORDING_STATUS[4]
# Recording failed and we were unable to write the file to storage.
RECORDING_FAILED = RECORDING_STATUS[5]

# Live recording states for the recording and the recorder agent handling the request
# Useful for determining health and recording progress
# NOTE: These are duplicated from firespotter/uberconf/constants.py
RECORDING_LIFECYCLE_STATUS = ['requested', 'recording', 'completed', 'failed']
# Recording has been requested but hasn't been started yet
RECORDING_LIFECYCLE_REQUESTED = RECORDING_LIFECYCLE_STATUS[0]
# Recording is in progress and healthy
RECORDING_LIFECYCLE_RECORDING = RECORDING_LIFECYCLE_STATUS[1]
# Recording is completed
RECORDING_LIFECYCLE_COMPLETED = RECORDING_LIFECYCLE_STATUS[2]
# Recording is done but ended with a failure.
RECORDING_LIFECYCLE_FAILED = RECORDING_LIFECYCLE_STATUS[3]

# Streaming recording states
# Recording states for StreamingRecording model. Corresponds to one StreamingRecording/streaming
# recording request which is handled by one streaming recorder agent.
# However, note one recording request may be associated with multiple recorder clients/layouts.
# NOTE: These states should match the definitions in firespotter/uberconf/constants.py
STREAMING_RECORDING_STATUSES = ['requested', 'in_progress', 'done']
# Streaming recording has been requested but hasn't started yet
STREAMING_RECORDING_STATUS_REQUESTED = STREAMING_RECORDING_STATUSES[0]
# Streaming recording is active
STREAMING_RECORDING_STATUS_IN_PROGRESS = STREAMING_RECORDING_STATUSES[1]
# Streaming recording has stopped or was canceled for any reason - the recording may have ended
#  normally or failed, or the recorder agent heartbeats timed out
STREAMING_RECORDING_STATUS_DONE = STREAMING_RECORDING_STATUSES[2]

# Reasons why streaming recording is in the STREAMING_RECORDING_STATUS_DONE state
# NOTE: These states should match the definitions in firespotter/uberconf/constants.py
STREAMING_RECORDING_COMPLETION_REASONS = ['finished', 'failed', 'heartbeat_expired']
# The streaming recording completed normally
STREAMING_RECORDING_COMPLETION_REASON_FINISHED = STREAMING_RECORDING_COMPLETION_REASONS[0]
# The streaming recording has definitively failed and will not recover nor be retried
# This should be set based on signaling from the recorder agent
STREAMING_RECORDING_COMPLETION_REASON_FAILED = STREAMING_RECORDING_COMPLETION_REASONS[1]
# The heartbeats from the recorder agent handling this streaming recording timed out.
# The recording may or may not still be running, but we presume it failed and will take steps to
#  stop it if possible
STREAMING_RECORDING_COMPLETION_REASON_HEARTBEAT_EXPIRED = STREAMING_RECORDING_COMPLETION_REASONS[2]

# Qualifiers that add context to the streaming recording statuses. only completion reasons for now
# NOTE: These states should match the definitions in firespotter/uberconf/constants.py
STREAMING_RECORDING_STATUS_REASONS = STREAMING_RECORDING_COMPLETION_REASONS

# SIP room connector session states
# NOTE: These states should match the definitions in firespotter/uberconf/constants.py
# SIP room agent states for the SIP client on the meeting. At any given time, a session is
#  considered to be in of these phases.
SIP_ROOM_CONNECTOR_SESSION_STATUSES = [
  'requested',
  'in_progress',
  'done'
]
# A new SIP room connector session has been requested, but hasn't started yet.
SIP_ROOM_CONNECTOR_SESSION_STATUS_REQUESTED = SIP_ROOM_CONNECTOR_SESSION_STATUSES[0]
# SIP room connector session started successfully.
SIP_ROOM_CONNECTOR_SESSION_STATUS_IN_PROGRESS = SIP_ROOM_CONNECTOR_SESSION_STATUSES[1]
# SIP room connector session is done. The session may have ended normally, may have failed and
#  no further recovery attempts or retries will be performed, etc.; refer to the status_reason
#  field for the specific sub-case of the DONE state.
SIP_ROOM_CONNECTOR_SESSION_STATUS_DONE = SIP_ROOM_CONNECTOR_SESSION_STATUSES[2]

# Reasons why the SIP room connector session is in the SIP_ROOM_CONNECTOR_SESSION_STATUS_DONE state.
SIP_ROOM_CONNECTOR_SESSION_COMPLETION_REASONS = ['finished', 'failed', 'heartbeat_expired']
# The SIP room connector session completed normally.
SIP_ROOM_CONNECTOR_SESSION_COMPLETION_REASON_FINISHED =\
  SIP_ROOM_CONNECTOR_SESSION_COMPLETION_REASONS[0]
# The SIP room connector session has definitively failed; it has stopped and no further attempts to
#  recover will be made. This should be set based on signaling from the SIP agent.
SIP_ROOM_CONNECTOR_SESSION_COMPLETION_REASON_FAILED =\
  SIP_ROOM_CONNECTOR_SESSION_COMPLETION_REASONS[1]
# The heartbeats from the SIP agent handling this session timed out. The SIP session/agent may or
# may not still be running, but we presume it failed and will take steps to stop it if possible.
SIP_ROOM_CONNECTOR_SESSION_COMPLETION_REASON_HEARTBEAT_EXPIRED =\
  SIP_ROOM_CONNECTOR_SESSION_COMPLETION_REASONS[2]

# Qualifiers that add context to the SIP room connector session statuses.
# Only completion reasons for now.
SIP_ROOM_CONNECTOR_SESSION_STATUS_REASONS = SIP_ROOM_CONNECTOR_SESSION_COMPLETION_REASONS

# Stop recording process status check interval
FFMPEG_STOP_STATUS_CHECK_INTERVAL = 0.2

# Max stop recording status checks
FFMPEG_STOP_MAX_STATUS_CHECKS = 5

# Field name for the recorder_principal, which must be included in every external API request as a
# top-level field in the JSON object request payload
# This key must map to the recorder principal object (see uc_api_client.py for details).
RECORDER_PRINCIPAL_FIELD_NAME = 'recorder_principal'

# Request payload signature custom HTTP header name
REQUEST_SIGNATURE_HEADER_NAME = 'X-Signature'


# The following list represents a set of exhaustive, mutually-exclusive phases that the recorder
#  agent goes through during its lifetime. In other words, the recorder agent must be in exactly one
#  of these states at any given time (once the first phase is initialized).
# K8s can read the phase state to make decisions about when a pod can be removed, restarted, etc.
RECORDER_AGENT_PHASES = [
    # The recorder agent enters this phase as soon as the session-controller starts, and remains in
    # this phase until a Pub/Sub message is received or a failure occurs.
    'awaiting_request',
    # This phase begins as soon as a request is received. Once the request is completely processed
    # (when the recording_session_controller has no more work to do) or a failure occurs, the
    # recorder agent will transition to the completed state
    'processing_request',
    # Completed state - at this point, this recorder agent has either completely finished processing
    #  a recording request or failed at any point during its lifetime and will no longer attempt to
    #  recover from the failure.
    # In either case, the Pod will make no further progress and should be terminated.
    'completed'
]

RECORDER_AGENT_PHASE_AWAITING_REQUEST = RECORDER_AGENT_PHASES[0]
RECORDER_AGENT_PHASE_PROCESSING_REQUEST = RECORDER_AGENT_PHASES[1]
RECORDER_AGENT_PHASE_COMPLETED = RECORDER_AGENT_PHASES[2]

# Recorder leg types
# NOTE: These states should match the definitions in firespotter/uberconf/constants.py
RECORDER_LEG_TYPES = ['video', 'streaming']
RECORDER_LEG_TYPE_VIDEO = RECORDER_LEG_TYPES[0]
RECORDER_LEG_TYPE_STREAMING = RECORDER_LEG_TYPES[1]
# NOTE: SIP 'recorder' client types are full participants and thus do not have a RecorderLeg

# Recorder (client) types
# NOTE: These states should match the definitions in firespotter/uberconf/constants.py
RECORDER_TYPES = [RECORDER_LEG_TYPE_VIDEO, RECORDER_LEG_TYPE_STREAMING, 'sip_room_agent']
RECORDER_TYPE_VIDEO = RECORDER_TYPES[0]
RECORDER_TYPE_STREAMING = RECORDER_TYPES[1]
RECORDER_TYPE_SIP_ROOM_AGENT = RECORDER_TYPES[2]

# Recorder agent types
RECORDER_AGENT_TYPES = ['video-recording', 'streaming-recording', 'sip-room-connector']
RECORDER_AGENT_TYPE_VIDEO = RECORDER_AGENT_TYPES[0]
RECORDER_AGENT_TYPE_STREAMING = RECORDER_AGENT_TYPES[1]
RECORDER_AGENT_TYPE_SIP_ROOM_AGENT = RECORDER_AGENT_TYPES[2]

# FFmpeg command timeout – max meeting length
FFMPEG_TIMEOUT_SECS = 5 * 60 * 60
# Timeout for killall command that's used to stop FFmpeg
STOP_FFMPEG_COMMAND_TIMEOUT_SECS = 10

# Max number of readiness checks before failing
MAX_BROWSER_AGENT_READINESS_CHECKS = 50
# Constant amount of delay to wait after a failed readiness checks
BROWSER_AGENT_READINESS_CHECK_RETRY_DELAY_SECS = 0.1
