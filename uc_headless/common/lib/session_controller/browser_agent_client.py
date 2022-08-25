import logging

import httpx

from common.lib.session_controller import constants
from common.lib import wrappers

START_RECORDING_REQUEST_TIMEOUT_SECS = 10
STOP_RECORDING_REQUEST_TIMEOUT_SECS = 20
START_SIP_ROOM_CONNECTOR_SESSION_REQUEST_TIMEOUT_SECS = 5
STOP_SIP_ROOM_CONNECTOR_SESSION_REQUEST_TIMEOUT_SECS = 5
READY_REQUEST_TIMEOUT_SECS = 0.1
TERMINATE_REQUEST_TIMEOUT_SECS = 15

logger = logging.getLogger(__name__)


async def start_recording(recording_id: int, recording_path: str,
                          is_streaming: bool = False) -> httpx.Response:
  """Makes an HTTP request on the "start recording" endpoint served by the browser-agent.

  Upon receiving a start recording request, the browser-agent should start ffmpeg.

  :param recording_id: video recording entity id
  :param recording_path: local absolute file path for either 1) the recording file if this
  is a video recording, or else for a streaming recording, 2) the base path where the primary
  playlist index file should be stored
  :param is_streaming: indicates whether this is a regular or streaming recording
  :return: the HTTP response object
  """
  if is_streaming:
    request_body = {
        'recording_path': recording_path
    }
    return await call_api(
        get_start_streaming_recording_endpoint(recording_id),
        constants.HTTP_POST,
        START_RECORDING_REQUEST_TIMEOUT_SECS,
        request_body=request_body)
  else:
    request_body = {
      'recording_path': recording_path
    }
    return await call_api(
        get_start_recording_endpoint(recording_id),
        constants.HTTP_POST,
        START_RECORDING_REQUEST_TIMEOUT_SECS,
        request_body=request_body)


async def stop_recording(recording_id: int, is_streaming: bool = False) -> httpx.Response:
  """Makes an HTTP request to the "stop recording" endpoint served by the browser-agent.

  Upon receiving a stop recording request, the browser-agent should stop ffmpeg and thereby complete
  the recording.

  :param recording_id: video recording entity id
  :param is_streaming: indicates whether this is a regular or streaming recording
  :return: the HTTP response object
  """
  if is_streaming:
    endpoint = get_stop_streaming_recording_endpoint(recording_id)
  else:
    endpoint = get_stop_recording_endpoint(recording_id)

  return await call_api(
      endpoint,
      constants.HTTP_POST,
      STOP_RECORDING_REQUEST_TIMEOUT_SECS)


async def start_sip_room_connector_session(session_id: int) -> httpx.Response:
  """Makes an HTTP request to the "start SIP room connector session" endpoint served by the
  browser-agent.

  Upon receiving a start request, the browser-agent should add state to track the in-progress
  session.

  :param session_id: SIP room connector session id
  :return: the HTTP response object
  """
  return await call_api(
      get_start_sip_room_connector_session_endpoint(session_id),
      constants.HTTP_POST,
      START_SIP_ROOM_CONNECTOR_SESSION_REQUEST_TIMEOUT_SECS)


async def stop_sip_room_connector_session(session_id: int) -> httpx.Response:
  """Makes an HTTP request to the "stop SIP room connector session" endpoint served by the
  browser-agent.

  Upon receiving a stop request, the browser-agent should clear state about the in-progress
  session.

  :param session_id: SIP room connector session id
  :return: the HTTP response object
  """
  return await call_api(
      get_stop_sip_room_connector_session_endpoint(session_id),
      constants.HTTP_POST,
      STOP_SIP_ROOM_CONNECTOR_SESSION_REQUEST_TIMEOUT_SECS)


async def browser_agent_is_ready() -> httpx.Response:
  """Makes an HTTP request request to the "ready" endpoint.

  A successful response indicates the browser-agent is ready to accept a recording.

  :return: the HTTP response object
  """
  # let the caller choose how to retry
  return await call_api(
      'http://localhost:5000/browser-agent/ready',
      constants.HTTP_GET,
      READY_REQUEST_TIMEOUT_SECS,
      retry=False)


async def terminate_browser_agent() -> httpx.Response:
  """Makes an HTTP request to the "terminate" endpoint.

  Upon receiving a terminate request, the browser agent should perform any required cleanup and
  exit the application.

  :return: the HTTP response object
  """
  return await call_api(
      'http://localhost:5000/browser-agent/terminate',
      constants.HTTP_POST,
      TERMINATE_REQUEST_TIMEOUT_SECS)


async def call_api(endpoint: str, http_method: str, request_timeout_secs: float,
                   request_body: dict = None, retry: bool = True) -> httpx.Response:
  """Makes an HTTP request to the browser-agent.

  :param endpoint: HTTP endpoint for the desired API call
  :param http_method: the HTTP method to use for the request
  :param request_body: request body dict, to be sent as the JSON request payload
  :param request_timeout_secs: maximum duration that the HTTP client will wait for a response to the
   request
  :param retry: whether the HTTP request should be retried with exponential backoff on connection
  or timeout errors
  :return: the HTTP response object
  """
  http_method = http_method.upper()

  async def _call_api() -> httpx.Response:
    logger.info(f'Requesting {http_method} {endpoint} with request body: {request_body}')

    async with httpx.AsyncClient() as client:
      resp = await client.request(
          http_method,
          endpoint,
          json=request_body,
          timeout=request_timeout_secs)
      logger.info(f'Got response: {resp}')
      logger.info(f'Response body: {resp.json()}')

    return resp

  @wrappers.retry_coroutine(httpx.RequestError, tries=5, delay_secs=0.2, backoff=2)
  async def _call_api_with_retries() -> httpx.Response:
    return await _call_api()

  return await _call_api_with_retries() if retry else await _call_api()


def get_start_recording_endpoint(recording_id: int) -> str:
  return f'http://localhost:5000/recording/{recording_id}/start'


def get_start_streaming_recording_endpoint(recording_id: int) -> str:
  return f'http://localhost:5000/streamingrecording/{recording_id}/start'


def get_start_sip_room_connector_session_endpoint(session_id: int) -> str:
  return f'http://localhost:5000/sip_room_connector/{session_id}/start'


def get_stop_recording_endpoint(recording_id: int) -> str:
  return f'http://localhost:5000/recording/{recording_id}/stop'


def get_stop_streaming_recording_endpoint(recording_id: int) -> str:
  return f'http://localhost:5000/streamingrecording/{recording_id}/stop'


def get_stop_sip_room_connector_session_endpoint(session_id: int) -> str:
  return f'http://localhost:5000/sip_room_connector/{session_id}/stop'
