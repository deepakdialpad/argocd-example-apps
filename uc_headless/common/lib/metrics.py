from __future__ import annotations
import logging
import os
from typing import Optional, TYPE_CHECKING

import datadog

from common.lib.session_controller import constants

if TYPE_CHECKING:
  from datadog.dogstatsd.context import TimedContextManagerDecorator

logger = logging.getLogger(__name__)

FEATURE_METRIC_PREFIX = 'ucr'
PROJECT_METRIC_PREFIX = 'recorder_agent'
METRIC_PREFIX = f'{FEATURE_METRIC_PREFIX}.{PROJECT_METRIC_PREFIX}'

METRIC_CATEGORY_PERFORMANCE = f'{FEATURE_METRIC_PREFIX}.category:performance'
METRIC_CATEGORY_RECORDING_LIFECYCLE = f'{FEATURE_METRIC_PREFIX}.category:recording_lifecycle'
METRIC_CATEGORY_SIP_ROOM_CONNECTOR_SESSION_LIFECYCLE = f'{FEATURE_METRIC_PREFIX}.category:' \
                                                       f'sip_room_connector_session_lifecycle'
METRIC_CATEGORY_ERROR = f'{FEATURE_METRIC_PREFIX}.category:error'
METRIC_CATEGORIES = [
  METRIC_CATEGORY_PERFORMANCE,
  METRIC_CATEGORY_RECORDING_LIFECYCLE,
  METRIC_CATEGORY_SIP_ROOM_CONNECTOR_SESSION_LIFECYCLE,
  METRIC_CATEGORY_ERROR
]

RECORDER_AGENT_TYPE = os.getenv('RECORDER_AGENT_TYPE')
if RECORDER_AGENT_TYPE is not None:
  if RECORDER_AGENT_TYPE == constants.RECORDER_AGENT_TYPE_VIDEO:
    SERVICE_TAG = 'service:video-recorder'
  elif RECORDER_AGENT_TYPE == constants.RECORDER_AGENT_TYPE_STREAMING:
    SERVICE_TAG = 'service:streaming-recorder'
  elif RECORDER_AGENT_TYPE == constants.RECORDER_AGENT_TYPE_SIP_ROOM_AGENT:
    SERVICE_TAG = 'service:sip-room-connector'
  else:
    logging.warning('Unrecognized value for environment variable RECORDER_AGENT_TYPE. Using '
                    'default value video-recorder for service tag')
    SERVICE_TAG = 'service:video-recorder'
elif os.getenv('STREAMING_MODE') == 'True':
  SERVICE_TAG = 'service:streaming-recorder'
else:
  SERVICE_TAG = 'service:video-recorder'

# default tags to apply to all metrics submitted by this statsd client
DEFAULT_TAGS = [SERVICE_TAG]

datadog_options = {
  'statsd_host': os.getenv('DD_AGENT_HOST'),
  'statsd_port': 8125,
  'statsd_constant_tags': DEFAULT_TAGS
}
logger.debug(f'Initializing datadog with options {datadog_options}')
datadog.initialize(**datadog_options)

statsd = datadog.statsd


def report_google_auth_error() -> None:
  """Reports metric indicating that the flow to fetch the credentials associated with the recorder
  agent's GCP service account threw an exception.

  :return: None
  """
  _report_error('google_auth_error')


def report_pubsub_error() -> None:
  """Reports metric indicating the Pub/Sub flow for the recorder agent threw an exception.

  :return: None
  """
  _report_error('pubsub_error')


def report_missing_recording_file() -> None:
  """Reports metric indicating the specified recording file was not found when the recorder agent
  started the upload process.

  :return: None
  """
  _report_error('missing_recording_file')


def report_upload_error() -> None:
  """Reports metric indicating an exception was encountered during the attempted upload of the
  recording file.

  :return: None
  """
  _report_error('file_upload_error')


# pylint: disable=unsubscriptable-object
def report_hls_file_upload_error(
    exception_name: Optional[str] = None, status_code: Optional[str] = None,
    file_type: Optional[str] = None, hls_quality_level: Optional[str] = None) -> None:
  """Reports metric indicating an exception was encountered during the attempted upload of the
  HLS stream file.

  :param exception_name: name of the exception class
  :param status_code: HTTP status code for the error, if applicable
  :param file_type: "m3u8" or "ts" file type
  :param hls_quality_level: a string indicating the quality level for the file, if applicable. e.g.
  'v0' for the lowest quality level
  :return: None
  """
  tags = []
  if exception_name is not None:
    tags.append(f'exception_name:{exception_name}')
  if status_code is not None:
    tags.append(f'status_code:{status_code}')
  if file_type is not None:
    tags.append(f'file_type:{file_type}')
  if hls_quality_level is not None:
    tags.append(f'hls_quality_level:{hls_quality_level}')

  _report_error('hls_file_upload_error', tags)


def report_recording_request_received() -> None:
  """Reports metric indicating a recording request was received.

  :return: None
  """
  _report_recording_session_lifecycle_event('recording_request_received')


def report_recording_started() -> None:
  """Reports metric indicating the recording processed by this agent appears to have been started
  successfully.

  :return: None
  """
  _report_recording_session_lifecycle_event('recording_started')


def report_recording_completed() -> None:
  """Reports metric indicating the recording processed by this agent was completed (possibly
  with an error).

  :return: None
  """
  _report_recording_session_lifecycle_event('recording_completed')


def report_ffmpeg_non_zero_exit_code() -> None:
  """Reports metric indicating that the ffmpeg recording command terminated with a non-zero exit
  code.

  :return: None
  """
  _report_error('ffmpeg_non_zero_exit_code')


def report_start_recording_error() -> None:
  """Reports a metric indicating an error occurred when the recorder agent attempted to start the
  recording.

  :return: None
  """
  _report_error('start_recording_error')


def report_recording_error() -> None:
  """Reports a metric indicating there was an unexpected error while the recording was in
  progress, such as a crash with the recorder client or browser.

  :return: None
  """
  _report_error('recording_error')


def report_stop_recording_error() -> None:
  """Reports a metric indicating an error occurred when the recorder agent attempted to stop the
  recording.

  :return: None
  """
  _report_error('stop_recording_error')


def report_update_recording_api_non_200_response_code() -> None:
  """Reports metrics indicating an update recording API call returned a non-200 response code.

  :return: None
  """
  _report_error('update_recording_api_non_200_resp_code')


def report_update_recording_api_error() -> None:
  """Reports metric indicating an exception was encountered during an update recording API call.

  :return: None
  """
  _report_error('update_recording_api_error')


def report_heartbeat_api_non_200_response_code() -> None:
  """Reports metrics indicating a heartbeat API call returned a non-200 response code.

  :return: None
  """
  _report_error('heartbeat_api_non_200_resp_code')


def report_heartbeat_api_error() -> None:
  """Reports metric indicating an exception was encountered during a heartbeat API call.

  :return: None
  """
  _report_error('heartbeat_api_error')


# pylint: disable=unsubscriptable-object
def report_recording_status(status: str, status_reason: Optional[str] = None,
                            is_streaming: Optional[bool] = False) -> None:
  """Reports metric indicating an update to the recording status.

  :param status: a recording status (one of the values defined in `constants.RECORDING_STATUS`)
  :param status_reason: streaming recording status reason, if applicable (one of the values defined
  in `constants.STREAMING_RECORDING_STATUSES`)
  :param is_streaming: flag indicating whether this is a streaming recorder or video recorder agent
  :return: None
  """
  is_error = False
  if is_streaming:
    status = status.lower() if status in constants.STREAMING_RECORDING_STATUSES else 'unknown'
    if (
        status == constants.STREAMING_RECORDING_STATUS_DONE and status_reason is not None and
        status_reason != constants.STREAMING_RECORDING_COMPLETION_REASON_FINISHED
    ):
      is_error = True
  else:
    status = status.lower() if status in constants.RECORDING_STATUS else 'unknown'
    is_error = status not in [constants.RECORDING_AVAILABLE, constants.RECORDING_IN_PROGRESS]

  tags = []
  if status_reason is not None:
    tags.append(f'status_reason:{status_reason}')

  if is_error:
    _report_error(f'recording_status.{status}', tags=tags)
  else:
    statsd.increment(f'{METRIC_PREFIX}.recording_status.{status}', sample_rate=1, tags=tags)


def report_sip_room_connector_session_request_received() -> None:
  """Reports metrics indicating a SIP room connector session request was received.

  :return: None
  """
  _report_sip_room_connector_session_lifecycle_event('sip_room_connector_session_request_received')


def report_sip_room_connector_session_started() -> None:
  """Reports metric indicating the SIP room connector session processed by the current agent
  appears to have been started successfully.

  :return: None
  """
  _report_sip_room_connector_session_lifecycle_event('sip_room_connector_session_started')


def report_sip_room_connector_session_completed() -> None:
  """Reports metric indicating the SIP room connector session processed by the current agent was
  completed (possibly with an error).

  :return: None
  """
  _report_sip_room_connector_session_lifecycle_event('sip_room_connector_session_completed')


def report_start_sip_room_connector_session_error() -> None:
  """Reports a metric indicating an error occurred when the current agent agent attempted to start
  the SIP room connector session.

  :return: None
  """
  _report_error('start_sip_room_connector_session_error')


def report_sip_room_connector_session_error() -> None:
  """Reports a metric indicating there was an unexpected error while the SIP room connector session
  was in progress, such as a crash with the recorder client or browser.

  :return: None
  """
  _report_error('sip_room_connector_session_error')


def report_stop_sip_room_connector_session_error() -> None:
  """Reports a metric indicating an error occurred when the current agent attempted to stop the SIP
  room connector session.

  :return: None
  """
  _report_error('stop_sip_room_connector_session_error')


def report_sip_room_connector_session_status(status: str,
                                             status_reason: Optional[str] = None) -> None:
  """Reports metric indicating an update to the SIP room connector session status and reason.

  :param status: a recording status (one of the values defined in
  `constants.SIP_ROOM_CONNECTOR_SESSION_STATUSES`)
  :param status_reason: a recording status reasons (one of the values defined in
  `constants.SIP_ROOM_CONNECTOR_SESSION_STATUS_REASONS`)
  :return: None
  """
  is_error = False
  status = status.lower() if status in constants.SIP_ROOM_CONNECTOR_SESSION_STATUSES else 'unknown'
  if (
      status == constants.SIP_ROOM_CONNECTOR_SESSION_STATUS_DONE and
      status_reason != constants.SIP_ROOM_CONNECTOR_SESSION_COMPLETION_REASON_FINISHED
  ):
    is_error = True

  tags = []
  if status_reason is not None:
    tags.append(f'status_reason:{status_reason}')

  if is_error:
    _report_error(f'sip_room_connector_session_status.{status}', tags=tags)
  else:
    statsd.increment(
        f'{METRIC_PREFIX}.sip_room_connector_session_status.{status}',
        sample_rate=1,
        tags=tags)


def report_update_sip_room_connector_session_api_non_200_response_code() -> None:
  """Reports metrics indicating an update sip room connector session API call returned a non-200
  response code.

  :return: None
  """
  _report_error('update_sip_room_connector_session_api_non_200_resp_code')


def report_update_sip_room_connector_session_api_error() -> None:
  """Reports metric indicating an exception was encountered during an update sip room connector
  session API call.

  :return: None
  """
  _report_error('update_sip_room_connector_session_api_error')


def report_browser_agent_termination_error() -> None:
  """Reports metric indicating there was an error when trying to terminate the browser agent
  container.

  :return: None
  """
  _report_error('browser_agent_termination_error')


def report_browser_agent_readiness_check_error() -> None:
  """Reports metric indicating that the browser agent's readiness could not be confirmed through
  the local probe.

  :return: NOne
  """
  _report_error('browser_agent_readiness_check_error')


def report_terminated_with_error() -> None:
  """Reports a metric that should be recorded whenever the session controller terminates due to an
  unhandled exception.

  :return: None
  """
  _report_error('terminated_with_error')


# pylint: disable=unsubscriptable-object
def _report_error(error_type: str, tags: Optional[list] = None) -> None:
  tags = tags or []
  tags.append(METRIC_CATEGORY_ERROR)
  tags.append(f'{FEATURE_METRIC_PREFIX}.error_type:{PROJECT_METRIC_PREFIX}.{error_type}')
  statsd.increment(
      f'{METRIC_PREFIX}.error',
      sample_rate=1,
      tags=tags)


def _report_recording_session_lifecycle_event(event_name: str) -> None:
  """Reports metric for a (video/streaming) recording session lifecycle event.

  :param event_name: session lifecycle event name for the metric
  :return: None
  """
  _report_session_lifecycle_event(
      event_name,
      tags=[METRIC_CATEGORY_RECORDING_LIFECYCLE])


def _report_sip_room_connector_session_lifecycle_event(event_name: str) -> None:
  """Reports metric for a SIP room connector session lifecycle event.

  :param event_name: session lifecycle event name for the metric
  :return: None
  """
  _report_session_lifecycle_event(
      event_name,
      tags=[METRIC_CATEGORY_SIP_ROOM_CONNECTOR_SESSION_LIFECYCLE])


def _report_session_lifecycle_event(event_name: str, tags: list) -> None:
  """Reports metric for a (streaming-recording/video-recording/sip-room-connector session lifecycle
  event.

  :param event_name: session lifecycle event name for the metric
  :param tags: tags to apply to the metric
  :return: None
  """
  statsd.increment(f'{METRIC_PREFIX}.{event_name}', sample_rate=1, tags=tags)


# datadog.statsd wrappers
# pylint: disable=unsubscriptable-object
def timed(metric_name: str, tags: Optional[list] = None) -> TimedContextManagerDecorator:
  """Wraps :meth:`datadog.dogstatsd.DogStatsd.timed` context manager / decorator (refer to
  https://datadogpy.readthedocs.io/en/latest/#datadog.dogstatsd.base.DogStatsd.timed)

  Always adds a default tag to specify the performance metric category, and prefixes the metric
  name.

  :param metric_name: base name for the metric
  :param tags: tags to add to the metric
  :return: a timing context manager
  """
  metric = f'{METRIC_PREFIX}.{metric_name}'
  if tags is not None:
    tags = list(set(tags + [METRIC_CATEGORY_PERFORMANCE]))
  return statsd.timed(metric, sample_rate=1, tags=tags)
