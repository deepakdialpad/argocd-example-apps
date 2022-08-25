import logging
import json
import copy
import sys
import base64
import hmac
import hashlib
from typing import Optional

import httpx

from common.lib.session_controller import constants
from common.lib import wrappers
from common.lib.exceptions import SessionError

logger = logging.getLogger(__name__)


# pylint: disable=unsubscriptable-object
class UcApiClient:
  def __init__(self, session_request: dict, request_type: str, recorder_secret_bytes: bytes):
    self.recorder_principal = create_recorder_principal(session_request)
    callback_url = session_request['callback_url']

    if request_type in [constants.RECORDER_TYPE_VIDEO, constants.RECORDER_TYPE_STREAMING]:
      session_id = session_request['recording_id']
    elif request_type == constants.RECORDER_TYPE_SIP_ROOM_AGENT:
      session_id = session_request['sip_room_session_id']
    else:
      raise SessionError(f'Cannot create UcApiClient unrecognized request type {request_type}')

    self.api_endpoint = f'{callback_url}/{session_id}'
    heartbeat_url_path = session_request.get('heartbeat_url_path')
    # check if the heartbeat URL path is provided in the pub/sub request
    # do the check for now to make it easier to support compatibility across versions
    # once the heartbeat logic is fully rolled out, we can remove this in a future release
    if heartbeat_url_path is not None:
      self.video_recording_heartbeat_api_endpoint = f'{self.api_endpoint}' \
                                                    f'{heartbeat_url_path}'
    else:
      self.video_recording_heartbeat_api_endpoint = None

    self.recorder_secret_bytes = recorder_secret_bytes

  def can_send_heartbeats(self):
    return self.video_recording_heartbeat_api_endpoint is not None

  @wrappers.retry_coroutine(httpx.RequestError, tries=5, delay_secs=0.2, backoff=2)
  async def update_recording(self, recording_id: int, recording_status: str,
                             recording_status_reason: Optional[str] = None,
                             recording_start_timestamp: Optional[str] = None,
                             recording_end_timestamp: Optional[str] = None) -> httpx.Response:
    """Calls the internal UC API that updates attributes of the video recording resource that may be
    generated or changed after the resource is first created.

    For example, an update may include values for the recording status or recording start/end
    timestamp fields.

    This method makes an HTTP PUT on the endpoint of the form:
    <UC base URL>/i1/videorecording/:recording_id

    :param recording_id: video or streaming recording entity id
    :param recording_status: one of pre-defined video/streaming recording statuses – see
    constants.py
    :param recording_status_reason: one of pre-defined streaming recording status reasons
    – see constants.py
    :param recording_start_timestamp: start timestamp in UTC format
    :param recording_end_timestamp: stop timestamp in UTC format
    :return: the HTTP response object
    """
    logger.info(f'Calling API to update video recording resource for recording id {recording_id}')
    request_body = {
      'recording_status': recording_status
    }
    if recording_status_reason is not None:
      request_body['recording_status_reason'] = recording_status_reason
    if recording_start_timestamp is not None:
      request_body['date_recording_started'] = recording_start_timestamp
    if recording_end_timestamp is not None:
      request_body['date_recording_ended'] = recording_end_timestamp
    response = await call_api(
        self.api_endpoint,
        constants.HTTP_PUT,
        request_body,
        self.recorder_principal,
        self.recorder_secret_bytes)
    return response

  @wrappers.retry_coroutine(httpx.RequestError, tries=5, delay_secs=0.2, backoff=2)
  async def update_sip_room_connector_session(self, session_id: int, status: Optional[str] = None,
                                              status_reason: Optional[str] = None,
                                              internal_sip_uri: Optional[str] = None,
                                              sip_agent_session_started_date: Optional[str] = None,
                                              sip_agent_session_completed_date: Optional[str] = None
  ) -> httpx.Response:
    """Calls the internal UC API that updates attributes of the sip room connector session resource
    that may be generated or changed after the resource is first created.

    For example, an update may include values for the session status.

    This method makes an HTTP PUT on the endpoint of the form:
    <UC base URL>/i1/siproomconnectorsessions/:session_id

    :param session_id: SIP room connector session entity id
    :param status: one of pre-defined SIP room session statuses – see constants.py
    :param status_reason: the SIP room connector session status reason (refer to constants.py)
    :param internal_sip_uri: internal SIP URI for the current SIP agent
    :param sip_agent_session_started_date: the datetime when the SIP session was successfully
    started on this SIP agent
    :param sip_agent_session_completed_date: the datetime when the SIP session was completed on this
    SIP agent
    :return: the HTTP response object
    """
    logger.info(f'Calling API to update SIP room session {session_id} resource')
    request_body = {}
    if status is not None:
      request_body['status'] = status
    if status_reason is not None:
      request_body['status_reason'] = status_reason
    if internal_sip_uri is not None:
      request_body['internal_sip_uri'] = internal_sip_uri
    if sip_agent_session_started_date is not None:
      request_body['sip_agent_session_started_date'] = sip_agent_session_started_date
    if sip_agent_session_completed_date is not None:
      request_body['sip_agent_session_completed_date'] = sip_agent_session_completed_date
    response = await call_api(
        self.api_endpoint,
        constants.HTTP_PUT,
        request_body,
        self.recorder_principal,
        self.recorder_secret_bytes)
    return response

  async def recording_lifecyle_status(self, recording_id: int, status: str) -> httpx.Response:
    """Sends heartbeat reporting the recording lifecycle status.

    Makes a request to <UC base URL>/i1/videorecording/:recording_id/live

    :param recording_id: video recording entity id
    :param status: current recording lifecycle status (see RECORDING_LIFECYCLE_STATUS in
    constants.py for the possible states)
    :return: the httpx.Response
    """
    logger.debug(f'Calling recording lifecycle status heartbeat for recording id {recording_id}')
    request_body = {
      'recording_lifecycle_status': {
        'status': status
      }
    }
    response = await call_api(
        self.video_recording_heartbeat_api_endpoint,
        constants.HTTP_PUT,
        request_body,
        self.recorder_principal,
        self.recorder_secret_bytes,
        verbose_logging=False)
    return response


def create_recorder_principal(session_request: dict) -> dict:
  """Builds and returns a standard object, dubbed the 'recorder principal', that represents the
   unique identity of a recorder agent instance handling a particular recording.

  The object is a union of suitable fields from the recording request.

  The principal must be included in every API request to the UC service made by the recorder
  agent, as a top-level field in the JSON request payload. The contents of the principal object
  should be validated by the UC service as part of the API authentication.

  :param session_request: the video or streaming-recording session request
  :return: for a video/streaming recorder_type, a dictionary with the following structure:
  {
     # DMs call id for the recorded call
    'call_id': <int entity id>,
     # StreamingRecording or VideoCallRecording entity id
    'recording_id': <int entity id>,
    # Constant indicating whether this is an agent for a video, streaming recording, or
    #  sip-room-connector recorder type
    'recorder_type': <string>
  }

  for a sip room connector recorder_type, a dictionary with the following structure:
  {
    # SipRoomConnectorSession entity id
    'sip_room_connector_session_id': <int entity id>
    # Constant indicating whether this is an agent for a video, streaming recording, or
    #  sip-room-connector recorder type
    'recorder_type': <string>
  }
  """
  recorder_token = json.loads(base64.b64decode(session_request['uc_recorder_token']))
  recorder_type = recorder_token['recorder_type']

  if recorder_type == constants.RECORDER_TYPE_SIP_ROOM_AGENT:
    sip_room_connector_session_id = recorder_token['sip_room_session_id']
    recorder_principal = {
        'sip_room_session_id': sip_room_connector_session_id,
        'recorder_type': recorder_type
    }
  elif recorder_type in [constants.RECORDER_TYPE_VIDEO, constants.RECORDER_TYPE_STREAMING]:
    call_id = recorder_token['call_id']
    recording_id = session_request['recording_id']

    recorder_principal = {
        'call_id': call_id,
        'recording_id': recording_id,
        'recorder_type': recorder_type
    }
  else:
    raise SessionError(f'Cannot create recorder principal: unrecognized recorder_type '
                       f'{recorder_type}')

  return recorder_principal


def create_api_request_payload_with_principal(api_request: dict, recorder_principal: dict) -> dict:
  """Merges a copy of the principal object into a copy of the request payload object.

  The principal object and its key are included as a top-level value/field of the result.

  :param api_request: full request payload dict to send as a JSON object in the request payload.
  :param recorder_principal: recorder principal object identifying the recorder agent instance -
  see create_recorder_principal() for more details
  :return: dict representing the full API request load, which includes the principal object
  """
  request_payload = copy.deepcopy(api_request)
  request_payload[constants.RECORDER_PRINCIPAL_FIELD_NAME] = copy.deepcopy(recorder_principal)
  return request_payload


def create_request_payload_signature(api_request_payload: dict,
                                     recorder_secret_bytes: bytes) -> str:
  """Signs the request payload with the recorder agent secret using SHA-256 and returns the
  signature.

  Before signing, sorts the :api_request_payload object by key and converts it to a JSON-encoded
  string.

  :param api_request_payload: full API request payload dict (i.e. including the recorder_principal
  key/value)
  :param recorder_secret_bytes: a secret shared between UC and the recorder agent application
  :return: signature of request payload, signed with :recorder_secret_bytes
  """
  # convert the payload to dict to JSON, and sort the object by key to normalize it.
  # the server must do the same when creating a payload signature to compare against the signature
  #  provided in the header.
  request_payload_string = json.dumps(api_request_payload, sort_keys=True)
  payload_bytes = bytes(request_payload_string, constants.UTF_8)
  return hmac.new(recorder_secret_bytes, payload_bytes, hashlib.sha256).hexdigest()


async def call_api(api_endpoint: str, http_method: str, api_request: dict, recorder_principal: dict,
                   recorder_secret_bytes: bytes, verbose_logging: bool = True) -> httpx.Response:
  """Makes an HTTP :http_method request to :api_endpoint.

  Merges copies of the :recorder_principal and :api_request to form the JSON request payload
  (refer to create_api_request_payload_with_principal()).

  Signs a sorted version of the result (refer to create_request_payload_signature()) and includes
  it as a base64-encoded header value.

  :param api_endpoint: the full API endpoint URI, e.g.
  https://ucstaging.com/il/videorecording/<video_recording_id>
  :param http_method: HTTP method/verb
  :param api_request: the payload dictionary
  :param recorder_principal: recorder principal object describing the recorder agent / recording
  instance - see create_recorder_principal() for more details
  :param recorder_secret_bytes: a secret shared between UC and the recorder agent application
  :param verbose_logging: determines whether logs regarding requests and responses should be logged
  at the INFO or DEBUG level
  :return: the HTTP response object
  """
  log_level = getattr(logging, 'INFO') if verbose_logging else getattr(logging, 'DEBUG')

  # create the full request payload by attaching the recorder principal to the API request
  request_payload = create_api_request_payload_with_principal(api_request, recorder_principal)
  # generate request payload signature and add it as a header
  request_payload_signature_bytes = bytes(
      create_request_payload_signature(request_payload, recorder_secret_bytes),
      constants.UTF_8)
  headers = {
      constants.REQUEST_SIGNATURE_HEADER_NAME: base64.b64encode(request_payload_signature_bytes)
  }
  http_method = http_method.upper()
  logger.log(
      log_level,
      f'Sending request {http_method} {api_endpoint} with request body: '
      f'{json.dumps(request_payload, indent=2)}')
  async with httpx.AsyncClient() as client:
    try:
      resp = await client.request(
          http_method,
          api_endpoint,
          headers=headers,
          json=request_payload)
    except (httpx.RequestError, httpx.InvalidURL) as err:
      err_type, err_val, _ = sys.exc_info()
      logger.error(f'Request {http_method} {api_endpoint} failed due to {err_type}: {err_val}')
      raise err
    else:
      logger.log(log_level, f'Received response for {http_method} {api_endpoint}: {resp}')
      return resp
