from __future__ import annotations
import asyncio
import functools
import logging
from datetime import datetime, timezone
import uuid
import urllib.parse
from typing import Deque, Optional, TYPE_CHECKING

import httpx
from pyppeteer.errors import PyppeteerError
# pylint:disable=redefined-builtin
from pyppeteer.errors import PageError, BrowserError, TimeoutError, NetworkError, ElementHandleError

import config
from common.lib import metrics, wrappers
from common.lib.session_controller import session_controller, browser_agent_client
from common.lib.session_controller.recorder_agent_phase_manager import RecorderAgentPhaseManager
from common.lib.session_controller import constants, recorder_state_manager
from common.lib.session_controller.uc_api_client import UcApiClient
from common.lib.kube_client import KubeClient
from common.lib.session_controller.sip_room_connector_session_result import \
  SipRoomConnectorSessionResult
from common.lib.exceptions import SessionError

if TYPE_CHECKING:
  import pyppeteer.browser
  import pyppeteer.page

logger = logging.getLogger(__name__)

# chrome window size
WINDOW_SIZE = f'{config.WINDOW_SIZE_WIDTH}x{config.WINDOW_SIZE_HEIGHT}'
UC_HEARTBEAT_INTERVAL_SECS = 5

# idle SIP agent client initialization timeouts
CLIENT_IDLE_PAGE_LOAD_TIMEOUT_SECS = 5
CLIENT_SERVICE_CHECK_TIMEOUT_SECS = 5
CLIENT_INIT_TIMEOUT_SECS = 15

# headless client join timeouts
CLIENT_NAVIGATE_TO_CONFERENCE_TIMEOUT_SECS = 5
CLIENT_JOIN_CONFERENCE_TIMEOUT_SECS = 60
CLIENT_INITIALIZE_WEBRTC_TIMEOUT_SECS = 10


# pylint: disable=unsubscriptable-object
async def main(phase_manager: RecorderAgentPhaseManager, termination_requested_event: asyncio.Event
               ) -> None:
  """Listen for a sip room connector request and attempt to serve establish a session.

  :param phase_manager: recorder agent phase manager
  :param termination_requested_event: event set when graceful termination is requested. if set
  before a request is received, the controller will exit immediately; else, if a request
  is being processed, the controller will only terminate after the session is done or an error
  occurs.
  :return: None
  """
  chrome_devtools_endpoint = f'{config.DEVTOOLS_URL}:{config.CHROME_DEBUG_PORT}'
  if config.PUBLIC_NODE_IP is None:
    raise Exception('PUBLIC_NODE_IP not found!')
  else:
    logger.info(f'Got host public ip: {config.PUBLIC_NODE_IP}')

  sip_agent_id = str(uuid.uuid4())
  logger.info(f'Generated SIP agent id: {sip_agent_id}')

  # define a request handler that follows the expected function signature
  async def request_handler(sip_room_connector_request: dict, page: pyppeteer.page.Page,
                            console_message_buffer: Deque[pyppeteer.page.ConsoleMessage]) -> None:
    await handle_sip_room_connector_request(
        sip_room_connector_request,
        config.PUBLIC_NODE_IP,
        config.MQTTWSPORT,
        sip_agent_id,
        config.DIALPAD_MEETINGS_APP_URL,
        page,
        console_message_buffer)

  idle_url = build_sip_room_connector_client_idle_url(
      config.DIALPAD_MEETINGS_APP_URL,
      config.IDLE_URL_PATH,
      config.MQTTWSPORT,
      sip_agent_id,
      config.PUBLIC_NODE_IP)
  # define client init handler that follows the expected function signature
  initialize_client_session = functools.partial(navigate_to_idle_url_and_wait_for_initialization,
      idle_url)

  await session_controller.main(
      request_handler,
      initialize_client_session,
      sip_room_connector_request_received,
      config.PUBSUB_SUBSCRIPTION_ID,
      chrome_devtools_endpoint,
      int(config.WINDOW_SIZE_WIDTH),
      int(config.WINDOW_SIZE_HEIGHT),
      phase_manager,
      termination_requested_event)


async def handle_sip_room_connector_request(
    sip_room_connector_request: dict,  public_node_ip: str, mqtt_port: str, sip_agent_id: str,
    app_url: str, page: pyppeteer.page.Page,
    console_message_buffer: Deque[pyppeteer.page.ConsoleMessage]) -> None:
  """Parse the SIP room connector request and handle it if valid.

  :param sip_room_connector_request: JSON dictionary representing the SIP room connector request
  :param public_node_ip: public IP address of the host machine
  :param mqtt_port: MQTT port the client should use top connect to baresip
  :param sip_agent_id: the unique id for the SIP user agent
  :param app_url: base DMs app URL, e.g. https://meetings.dialpad.com
  :param page: pyppeteer page object
  :param console_message_buffer: fixed-size buffer of the most recent console messages on the page
  :return: None
  """
  call_url = sip_room_connector_request['call_url']
  sip_agent_recorder_url = build_sip_room_connector_recorder_client_url(
      call_url,
      sip_room_connector_request['uc_recorder_token'],
      sip_room_connector_request['uc_recorder_signature_hash'],
      mqtt_port,
      sip_agent_id,
      public_node_ip
  )

  # parse conference URL path, including query params, from full conference URL
  sip_agent_recorder_url_path = sip_agent_recorder_url.removeprefix(app_url)
  if sip_agent_recorder_url_path.startswith(app_url):
    raise SessionError(
        f'Cannot join conference - failed to parse conference path from URL: '
        f'{sip_agent_recorder_url_path}')

  session_id = sip_room_connector_request['sip_room_session_id']
  uc_api_client = UcApiClient(
      sip_room_connector_request,
      constants.RECORDER_TYPE_SIP_ROOM_AGENT,
      config.RECORDER_SECRET_BYTES)

  # record the call and upload the recording
  await process_sip_connector_request(
      page,
      session_id,
      sip_agent_recorder_url_path,
      call_url,
      public_node_ip,
      sip_agent_id,
      uc_api_client,
      console_message_buffer)


async def process_sip_connector_request(page: pyppeteer.page.Page, session_id: int,
                                        sip_agent_recorder_url_path: str, call_url: str,
                                        public_node_ip: str, sip_agent_id: str,
                                        uc_client: UcApiClient,
                                        console_message_buffer: Deque[pyppeteer.page.ConsoleMessage]
                                        ) -> None:
  """Serves the SIP connector session, and then once finished, makes a UC API call to write the
  final session status (refer to constants.py).

  If the session fails to start successfully, returns immediately after the API call.

  :param page: pyppeteer page object
  :param session_id: sip room session entity id
  :param sip_agent_recorder_url_path: the URL for the DMs conference with the recorder client
  credentials and other required SIP agent query params
  :param call_url: the base URL for the UC conference room
  :param public_node_ip: public IP address of the host machine
  :param sip_agent_id: the unique id for the SIP user agent
  :param uc_client: UC API client
  :param console_message_buffer: fixed-size buffer of the most recent console messages
  :return: None
  """

  sip_room_connector_session_result = await serve_sip_room_connector_session(
      page,
      session_id,
      sip_agent_recorder_url_path,
      call_url,
      public_node_ip,
      sip_agent_id,
      uc_client,
      console_message_buffer)

  if sip_room_connector_session_result.session_start_timestamp is None:
    logger.error('SIP room connector session failed to start')
  else:
    sip_room_connector_session_status = constants.SIP_ROOM_CONNECTOR_SESSION_STATUS_DONE
    if (
        not sip_room_connector_session_result.did_client_exit_normally
        or not sip_room_connector_session_result.did_session_complete_normally
    ):
      # while the sip session appeared to start successfully, there was a failure with the sip
      #  connector client and/or a sip room agent component (baresip, mosquitto, etc.) at some point
      #  thereafter.
      status_reason = constants.SIP_ROOM_CONNECTOR_SESSION_COMPLETION_REASON_FAILED
    else:
      status_reason = constants.SIP_ROOM_CONNECTOR_SESSION_COMPLETION_REASON_FINISHED
    metrics.report_sip_room_connector_session_status(
        sip_room_connector_session_status,
        status_reason)

    await call_update_sip_room_connector_session_api(
        uc_client,
        session_id,
        status=sip_room_connector_session_status,
        status_reason=status_reason,
        sip_agent_session_completed_date=sip_room_connector_session_result.session_end_timestamp)


async def serve_sip_room_connector_session(
    page: pyppeteer.page.Page, session_id: int, sip_agent_recorder_url_path: str, call_url: str,
    public_node_ip: str, sip_agent_id: str, uc_client: UcApiClient,
    console_message_buffer: Deque[pyppeteer.page.ConsoleMessage]) -> SipRoomConnectorSessionResult:
  """Adds a headless SIP room connector recorder client to the conference until the session should
  end or an error occurs.

  Additionally, if the SIP room session starts successfully, makes a UC API call immediately
  afterwards to update the SIP room session status.

  :param page: pyppeteer page object
  :param session_id: SIP room connector session entity id
  :param sip_agent_recorder_url_path: the URL for the DMs conference with the recorder client
  credentials and other required SIP agent query params
  :param call_url: the base URL for the DMs conference room
  :param public_node_ip: public IP of the host machine
  :param sip_agent_id: the unique id for the SIP user agent
  :param uc_client: UC API client
  :param console_message_buffer: fixed-size buffer of the most recent console messages
  :return: A SipRoomConnectorSessionResult object indicating metadata about the completed SIP room
  connector session and its success/failure (refer to sip_room_connector_session_result.py for
  more details)
  """
  internal_sip_uri = generate_internal_sip_uri(sip_agent_id, public_node_ip)
  logger.info(f'Generated internal SIP agent URI: {internal_sip_uri}')
  resp = await call_update_sip_room_connector_session_api(
      uc_client,
      session_id,
      internal_sip_uri=internal_sip_uri
  )
  if resp is None:
    raise SessionError('Unable to update internal URI for SIP room session')

  session_start_timestamp = await start_sip_room_connector_session(
      session_id,
      page,
      sip_agent_recorder_url_path)
  if session_start_timestamp is None:
    session_result = SipRoomConnectorSessionResult(session_id)
  else:
    # the SIP room session started successfully

    # mark the SIP room session as in_progress
    status = constants.SIP_ROOM_CONNECTOR_SESSION_STATUS_IN_PROGRESS
    await call_update_sip_room_connector_session_api(
        uc_client,
        session_id,
        status=status,
        sip_agent_session_started_date=session_start_timestamp)

    # continue the room connector session until the client leaves the call or an error occurs
    did_client_exit_normally = await await_sip_room_connector_session_completion(page)
    did_sip_session_end_normally, session_end_timestamp = await stop_sip_room_connector_session(
        session_id)

    logger.info(f'SIP room connector session {session_id} complete for conference {call_url}')
    session_result = SipRoomConnectorSessionResult(
        session_id,
        session_start_timestamp=session_start_timestamp,
        session_end_timestamp=session_end_timestamp,
        did_client_exit_normally=did_client_exit_normally,
        did_session_complete_normally=did_sip_session_end_normally)

  metrics.report_sip_room_connector_session_completed()

  # if there was an error, print the client logs in case it was related to a client problem
  if (
      session_result.session_start_timestamp is None or
      not session_result.did_client_exit_normally or
      not session_result.did_session_complete_normally
  ):
    logger.warning(f'Error with SIP room connector session - printing '
                   f'{len(console_message_buffer)} most recent SIP room connector client logs')
    # note: the JS log formatting/coloring added by UC appears in the text
    messages = [f'[ClientLog][{m.type.upper()}] {m.text}' for m in console_message_buffer]
    logger.info('\n'.join(messages))
  return session_result


async def start_sip_room_connector_session(session_id: int, page: pyppeteer.page.Page,
                                           sip_agent_recorder_url_path: str) -> str:
  """Performs all the steps to initiate a SIP room connector session.

  Creates a SIP room connector recorder client in the browser, waits for it to join the
  conference and initialize.

  Catches any exceptions and merely logs them, so that the session controller can still perform
  completion handling even if the session fails.

  :param session_id: SIP room connector session entity id
  :param page: pyppeteer page object
  :param sip_agent_recorder_url_path: the URL for the DMs conference with the recorder client
  credentials and other required SIP agent query params
  :return:
  """
  async def start_browser_agent_session():
    logger.info('Starting SIP room connector session')
    try:
      resp = await browser_agent_client.start_sip_room_connector_session(session_id)
      # throws an HTTPStatusError if response code is  >= 400
      resp.raise_for_status()
    except (httpx.RequestError, httpx.InvalidURL) as err:
      raise SessionError(
          'Failed to start SIP room connector session: start request failed') from err
    except httpx.HTTPStatusError as err:
      raise SessionError('Failed to start SIP room connector session: browser agent returned '
                         f'response with status code {err.response.status_code}') from err

  session_start_timestamp = None
  try:
    await wait_for_sip_call_established(page)
    await wait_for_is_ready_to_join_conference(page)
    await join_conference(page, sip_agent_recorder_url_path)
    # TODO: handle the waiting room case. for now, we just use a high timeout
    await session_controller.wait_for_recorder_initialization(
        page,
        CLIENT_JOIN_CONFERENCE_TIMEOUT_SECS,
        CLIENT_INITIALIZE_WEBRTC_TIMEOUT_SECS,
        is_sip_room_connector_session=True)
    await start_browser_agent_session()
  except Exception:  # pylint: disable=broad-except
    logger.exception('Failed to start SIP room connector session')
    metrics.report_start_sip_room_connector_session_error()
  else:
    session_start_timestamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
    logger.info(f'SIP room connector session started successfully at {session_start_timestamp}')
    metrics.report_sip_room_connector_session_started()
  return session_start_timestamp


async def stop_sip_room_connector_session(session_id: int) -> (bool, str):
  """Performs all the steps to stop the SIP room connector session.

  Catches any exceptions and merely logs them, so that the session controller can still perform
  completion handling, even if there's a failure.

  :param session_id: SIP room connector session entity id
  :return: 1) a boolean value indicating whether the session was stopped successfully or not and
  2) the session stop timestamp.
  """
  # TODO(bill): perform any stop steps here if needed - UC-12078
  async def stop() -> None:
    logger.info('Stopping SIP room connector session')
    try:
      resp = await browser_agent_client.stop_sip_room_connector_session(session_id)
      # throws an HTTPStatusError if response code is >= 400
      resp.raise_for_status()
    except (httpx.RequestError, httpx.InvalidURL) as err:
      raise SessionError('Failed to stop SIP room connector session: stop SIP room connector '
                         'request failed') from err
    except httpx.HTTPStatusError as err:
      raise SessionError('Browser agent returned stop SIP room connector session response with '
                         f'code {err.response.status_code}') from err
    else:
      logger.info(f'Successfully stopped SIP room connector session {session_id}')

  session_stopped_successfully = False
  try:
    await stop()
  except Exception:   # pylint: disable=broad-except
    logger.exception('Failed to stop SIP room connector session')
    metrics.report_stop_sip_room_connector_session_error()
  else:
    session_stopped_successfully = True
  session_end_timestamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
  return session_stopped_successfully, session_end_timestamp


async def navigate_to_idle_url_and_wait_for_initialization(idle_url: str, page: pyppeteer.page.Page,
    sip_agent_client_idle_page_load_timeout_secs: float = CLIENT_IDLE_PAGE_LOAD_TIMEOUT_SECS,
    sip_agent_client_service_check_timeout_secs: float = CLIENT_SERVICE_CHECK_TIMEOUT_SECS,
    sip_agent_client_init_timeout_secs: float = CLIENT_INIT_TIMEOUT_SECS) -> None:
  """Creates the DMs SIP agent client, navigates to the idle page, and waits until the client is
  ready to accept a session.

  :param idle_url: path for the SIP room connector client idle URL path, e.g.
  /siproomagentidle
  :param page: pyppeteer page object
  :param sip_agent_client_idle_page_load_timeout_secs: idle page load timeout
  :param sip_agent_client_service_check_timeout_secs: timeout for the check that waits until the
  client SIP agent service, which exposes the headless client JS API, is ready
  :param sip_agent_client_init_timeout_secs: timeout for the check that waits until the headless
  client is fully initialized and readu to accept a room connector session
  :return: None
  """
  @wrappers.retry_coroutine((PageError, BrowserError, NetworkError, ElementHandleError), tries=5,
      delay_secs=0.2, backoff=1)
  async def _navigate_to_idle_url():
    logger.info(f'Navigating to SIP room agent client idle URL: {idle_url}')
    # navigate to the idle URL for the SIP agent
    await page.goto(
        idle_url,
        wait_until='load',
        timeout=sip_agent_client_idle_page_load_timeout_secs * 1000)

  try:
    await _navigate_to_idle_url()
  except TimeoutError as err:
    raise SessionError('Timed out trying to load SIP room agent client idle page') from err
  except PyppeteerError as err:
    raise SessionError(
        'Encountered Pyppeteer error while trying to load SIP room agent client idle page') from err
  else:
    logger.info('Successfully loaded idle page for SIP room agent client')

  @wrappers.retry_coroutine((PageError, BrowserError, NetworkError, ElementHandleError), tries=5,
      delay_secs=0.2, backoff=1)
  async def _wait_for_sip_room_agent_service():
    logger.info('Waiting for SIP room agent client service to be ready')
    await page.waitForFunction(
        '() => window.sipRoomAgentService',
        timeout=sip_agent_client_service_check_timeout_secs * 1000)

  try:
    await _wait_for_sip_room_agent_service()
  except TimeoutError as err:
    raise SessionError(
        'Timed out while waiting for the SIP room agent client service to initialize') from err
  except PyppeteerError as err:
    raise SessionError(
        'Encountered Pyppeteer error while waiting for the SIP room agent client service to'
        'initialize') from err
  else:
    logger.info('SIP room agent client service initialized')

  @wrappers.retry_coroutine((PageError, BrowserError, NetworkError, ElementHandleError), tries=3,
      delay_secs=0.2, backoff=1)
  async def _wait_for_sip_room_agent_client_init():
    logger.info('Waiting for SIP room agent client initialization')
    await page.waitForFunction(
        'window.sipRoomAgentService.isReadyForSession()',
        timeout=sip_agent_client_init_timeout_secs * 1000)

  try:
    await _wait_for_sip_room_agent_client_init()
  except TimeoutError as err:
    raise SessionError(
        'Timed out while waiting for the SIP room agent client to initialize') from err
  except PyppeteerError as err:
    raise SessionError(
        'Encountered Pyppeteer error while waiting for the SIP room agent client to initialize'
    ) from err
  else:
    logger.info('SIP room agent client is ready to accept new session')


async def wait_for_sip_call_established(page: pyppeteer.page.Page,
                                        call_established_timeout_secs: float = 30) -> None:
  """Waits for the SIP call to be established up until a timeout.

  :param page: pyppeteer page object
  :param call_established_timeout_secs: maximum time to wait for the SIP call to be established
  :return: None
  """
  logger.info('Waiting for call established event from baresip')
  try:
    await page.waitForFunction(
        'window.sipRoomAgentService.isSipCallActive()',
        timeout=call_established_timeout_secs * 1000)
  except TimeoutError as err:
    raise SessionError('Timed out waiting for baresip call established event') from err
  except PyppeteerError as err:
    raise SessionError(
        'Encountered Pyppeteer error while waiting for baresip call established event') from err
  else:
    logger.info('Baresip call established')


async def wait_for_is_ready_to_join_conference(page: pyppeteer.page.Page,
                                               is_ready_for_session_timeout_secs: float = 5
                                               ) -> None:
  """Waits until the DMs SIP room connector client is ready to join the conference.

  :param page: pyppeteer page object
  :param is_ready_for_session_timeout_secs: max time to wait for the conference to be ready for a
  session
  :return: None
  """
  logger.info('Waiting for client to be ready for SIP room connector session')
  try:
    await page.waitForFunction(
        'window.sipRoomAgentService.isReadyForSession()',
        timeout=is_ready_for_session_timeout_secs * 1000)
  except TimeoutError as err:
    raise SessionError(
        'Timed out waiting for client to be ready for SIP room connector session') from err
  except PyppeteerError as err:
    raise SessionError(
        'Encountered Pyppeteer error while waiting for client to become ready for SIP room '
        'connector session') from err
  else:
    logger.info('Client is ready for SIP room connector session')


async def join_conference(page: pyppeteer.page.Page, sip_agent_recorder_url_path: str) -> None:
  """Loads the DMs conference page as a headless SIP room connector recorder client, waiting until
  the initial loading of the page is complete.

  Does not retry on a timeout, as the provided timeout :timeout_secs should already allow for
  more than enough time to load the page, such that the chance of success with additional attempts
  should be low. Retries on all other Pyppeteer errors (BrowserError, PageError, etc.), which are
  more likely to occur immediately and are possibly recoverable.

  :param page: pyppeteer page object
  :param sip_agent_recorder_url_path: the URL for the DMs conference with the recorder client
  credentials and other required SIP agent query params
  :return: None
  """
  logger.info(f'Joining conference with URL path: {sip_agent_recorder_url_path}')
  join_conference_fn_name = 'window.sipRoomAgentService.joinConference'
  try:
    # join the conference to create the headless SIP room connector agent client
    await page.evaluate(
        f'(sip_agent_recorder_url_path) => {join_conference_fn_name}(sip_agent_recorder_url_path)',
        sip_agent_recorder_url_path)
  except TimeoutError as err:
    raise SessionError('Timed out trying to load conference page') from err
  except PyppeteerError as err:
    raise SessionError(
        'Encountered Pyppeteer error while trying to load conference page') from err
  else:
    logger.info('Successfully started loading DMs conference page for SIP room connector client')


@metrics.timed('sip_room_connector_session_duration_secs')
async def await_sip_room_connector_session_completion(page: pyppeteer.page.Page) -> bool:
  """Asynchronously polls the DMs client until it leaves the conference, the SIP device hangs up,
  or an error occurs.

  :param page: pyppeteer page object
  :return: True if the client left the room without error, else False
  """
  logger.info('Serving SIP room connector session until client leaves the conference')
  return await session_controller.await_conference_exit(
      page,
      '!window.isInConference()',
      is_sip_room_connector_session=True)


async def call_update_sip_room_connector_session_api(uc_client: UcApiClient, session_id: int,
    status: Optional[str] = None, status_reason: Optional[str] = None,
    internal_sip_uri: Optional[str] = None, sip_agent_session_started_date: Optional[str] = None,
    sip_agent_session_completed_date: Optional[str] = None
) -> Optional[httpx.Response]:
  """Call "update sip room connector session" API to write changes concerning the session metadata.

  Catches any exceptions and merely logs them, so that the core session handling can continue even
  in the event of an API call error.

  :param uc_client: UC API client
  :param session_id: SIP room connector session entity id
  :param status: the SIP room connector session status (refer to constants.py)
  :param status_reason: the SIP room connector session status reason (refer to constants.py)
  :param internal_sip_uri: internal SIP URI for the current SIP agent
  :param sip_agent_session_started_date: the datetime when the SIP session was successfully started
  on this SIP agent
  :param sip_agent_session_completed_date: the datetime when the SIP session was completed on this
  SIP agent
  :return: the HTTP response object if the request completed without error and returned a 200 status
  code, otherwise, None
  """
  try:
    resp = await uc_client.update_sip_room_connector_session(
        session_id,
        status=status,
        status_reason=status_reason,
        internal_sip_uri=internal_sip_uri,
        sip_agent_session_started_date=sip_agent_session_started_date,
        sip_agent_session_completed_date=sip_agent_session_completed_date)
  except Exception:  # pylint: disable=broad-except
    # just log exceptions here, so we don't fail hard in response to an API request error
    logger.exception('Encountered exception during update SIP room connector session API request')
    metrics.report_update_sip_room_connector_session_api_error()
    return None
  else:
    # only consider 200 response codes a success
    if resp.status_code == 200:
      return resp
    else:
      logger.error('Received non-200 response code for update SIP room connector session API '
                   'request')
      metrics.report_update_sip_room_connector_session_api_non_200_response_code()
      return None


def build_sip_room_connector_client_idle_url(app_url: str, idle_url_path: str, mqtt_port: str,
                                             sip_agent_id: str, public_node_ip: str) -> str:
  """Builds the idle DMs URL to navigate to when the SIP room agent first starts up.

  :param app_url: base DMs app URL, e.g. https://meetings.dialpad.com
  :param idle_url_path: path for the SIP room connector client idle URL path, e.g.
  /siproomagentidle
  :param mqtt_port: MQTT port the client should use top connect to baresip
  :param sip_agent_id: the unique id for the SIP user agent
  :param public_node_ip: public IP address of the host machine
  """
  params = {
      'is_sip_room_agent': 'true',
      'baresip_port': mqtt_port,
      'sip_agent_guid': sip_agent_id,
      'host_public_ip': public_node_ip
  }

  query_params = urllib.parse.urlencode(params)

  return f'{app_url}{idle_url_path}?{query_params}'


def build_sip_room_connector_recorder_client_url(conference_url: str, recorder_token: str,
                                                 recorder_signature_hash: str, mqtt_port: str,
                                                 sip_agent_id: str, public_node_ip: str) -> str:
  """Builds the URL for the conference, with the recorder client credentials as query params.

  :param conference_url: base URL for the UC conference room to be recorded, e.g.
  https://uberconference.com/room/<room name>
  :param recorder_token: auth token for the recorder, allowing it to enter the conference as a
  headless client
  :param recorder_signature_hash: hash of the recorder client token
  :param mqtt_port: MQTT port the client should use top connect to baresip
  :param sip_agent_id: the unique id for the SIP user agent
  :param public_node_ip: public IP address of the host machine
  :return: the resulting conference URL for the recorder client
  """
  params = {
    'recorder_token': recorder_token,
    'recorder_signature_hash': recorder_signature_hash,
    'is_sip_room_agent': 'true',
    'baresip_port': mqtt_port,
    'sip_agent_guid': sip_agent_id,
    'host_public_ip': public_node_ip
  }

  query_params = urllib.parse.urlencode(params)

  return f'{conference_url}?{query_params}'


def generate_internal_sip_uri(sip_agent_id: str, public_node_ip: str, sip_port: int = 5060) -> str:
  """Creates internal SIP URI, i.e. the SIP agent endpoint that internal services use.

  :param sip_agent_id: the unique id for the SIP agent
  :param public_node_ip: public IP of the host machine
  :param sip_port: SIP port to use for the endpoint
  :return: the full internal SIP URI
  """
  return f'sip:{sip_agent_id}@{public_node_ip}:{sip_port}'


def sip_room_connector_request_received(session_request_msg: dict) -> None:
  """Callback for receipt of a SIP room connector session request message.

  :param session_request_msg: a message holding a headless session request
  :return: None
  """
  logger.info(f'Received SIP room connector session request: {session_request_msg}')
  metrics.report_sip_room_connector_session_request_received()


if __name__ == '__main__':
  recorder_agent_phase_manager = RecorderAgentPhaseManager(
      recorder_state_manager.state_manager,
      KubeClient(config.POD_NAME, config.POD_NAMESPACE))
  asyncio.get_event_loop().run_until_complete(main(recorder_agent_phase_manager, asyncio.Event()))
