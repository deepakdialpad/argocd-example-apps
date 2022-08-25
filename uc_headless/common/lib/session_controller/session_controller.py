from __future__ import annotations
import asyncio
import concurrent.futures
import logging
import uuid
from datetime import datetime, timezone
import json
import urllib.parse
from collections import deque
from collections.abc import Callable
import functools
from pathlib import Path
from typing import Optional, Deque, Coroutine, TYPE_CHECKING

import pyppeteer
from pyppeteer import launcher
from pyppeteer.errors import PyppeteerError
# pylint:disable=redefined-builtin
from pyppeteer.errors import PageError, BrowserError, TimeoutError, NetworkError, ElementHandleError
import requests.exceptions
import google.api_core.retry
import google.cloud.storage.retry
import google.auth.exceptions
import google.api_core.exceptions
import google.cloud
import httpx
import asyncinotify
from asyncinotify import Mask

# common.lib imports
from common.lib import google_auth_client, pubsub_subscriber, gcs_client, metrics, wrappers
from common.lib import asyncionotify_tools
from common.lib.session_controller.uc_api_client import UcApiClient
from common.lib.session_controller.recorder_agent_phase_manager import RecorderAgentPhaseManager
from common.lib.session_controller import constants, browser_agent_client
from common.lib.session_controller.recording_result import RecordingResult
from common.lib.exceptions import SessionError

if TYPE_CHECKING:
  # pylint: disable=ungrouped-imports
  import pyppeteer.browser
  import pyppeteer.page
  from google.pubsub_v1.types import pubsub

logger = logging.getLogger(__name__)

# type annotation for the headless session request handler coroutine
# accepts a headless session request dict, a pyppeteer page for the recorder client, and a buffer
#  for the most recent console messages
HeadlessSessionRequestHandler = Callable[
    [dict, pyppeteer.page.Page, Deque[pyppeteer.page.ConsoleMessage]], Coroutine]

# event callback to be executed immediately when a headless session request msg is received
SessionRequestMsgReceivedCallback = Callable[[dict], None]

# initialize headless client function
InitializeClientSession = Callable[[pyppeteer.page.Page], Coroutine]

# module constants
NAVIGATE_TO_CONFERENCE_PAGE_TIMEOUT_SECS = 30
RECORDER_CLIENT_JOIN_CONFERENCE_TIMEOUT_SECS = 15
RECORDER_CLIENT_INITIALIZE_WEBRTC_TIMEOUT_SECS = 10
CONSOLE_MESSAGE_BUFFER_MAX_LEN = 150
UC_HEARTBEAT_INTERVAL_SECS = 5


# pylint: disable=unsubscriptable-object
async def main(handle_request: HeadlessSessionRequestHandler,
               initialize_client_session: InitializeClientSession,
               session_request_received_callback: SessionRequestMsgReceivedCallback,
               pubsub_subscription_id: str, chrome_devtools_endpoint: str, window_size_width: int,
               window_size_height: int, phase_manager: RecorderAgentPhaseManager,
               termination_requested_event: asyncio.Event) -> None:
  # authenticate with GCP service account creds and determine the GCP project id from the
  #  environment
  try:
    _, gcp_project_id = await asyncio.get_running_loop().run_in_executor(
        None,
        google_auth_client.get_gcp_credentials_and_project_id)
  except google.auth.exceptions.GoogleAuthError as err:
    metrics.report_google_auth_error()
    raise err
  else:
    if gcp_project_id is None:
      raise SessionError('Unable to determine GCP project ID')

  # connect to the browser and create a session
  ws_endpoint = await fetch_browser_ws_endpoint(chrome_devtools_endpoint)
  browser = await connect_to_browser(ws_endpoint, window_size_width, window_size_height)
  # create a page for an eventual recorder client
  logger.info('Creating browser page')
  page = await browser.newPage()
  logger.info('Successfully connected to browser and created a new page')

  # track the most recent console messages in case of browser/page errors
  console_message_buffer = deque(maxlen=CONSOLE_MESSAGE_BUFFER_MAX_LEN)
  page.on('console', functools.partial(on_console_message, buffer=console_message_buffer))

  try:
    await pull_and_handle_session_request(
        initialize_client_session,
        handle_request,
        page,
        console_message_buffer,
        gcp_project_id,
        pubsub_subscription_id,
        session_request_received_callback,
        phase_manager,
        termination_requested_event)
  finally:
    try:
      await close_page_and_disconnect_browser(page, browser)
    finally:
      logger.info('Exiting session')


async def fetch_browser_ws_endpoint(browser_url: str) -> str:
  """Fetches a DevTools/CDP WebSocket URL.

  :param browser_url: the local browser URL with the DevTools remote debugging port,
   e.g. 'http://localhost:9000'
  :return: the WebSocket endpoint URL
  """
  logger.info(f'Querying {browser_url} for the browser WebSocket endpoint')
  try:
    # note: launcher.connect() will automatically do this step if you use the 'browserURL'
    #  option, but the implementation blocks as it queries Chrome for the WS endpoint.
    #  so execute it here in a separate thread instead.
    return await asyncio.get_running_loop().run_in_executor(
        None,
        launcher.get_ws_endpoint,
        browser_url)
  except BrowserError as err:
    raise SessionError('Could not make initial connection to browser') from err


async def connect_to_browser(ws_endpoint: str, viewport_width: int, viewport_height: int
                             ) -> pyppeteer.browser.Browser:
  """Creates a browser/CDP session.

  :param viewport_width: browser viewport width
  :param viewport_height; browser viewport height
  :param ws_endpoint: DevTools WS endpoint URL
  :return: the pyppeteer browser session
  """
  logger.info(f'Connecting to browser via {ws_endpoint}')
  viewport_dimensions = {
    'width': viewport_width,
    'height': viewport_height
  }
  try:
    return await launcher.connect(
        browserWSEndpoint=ws_endpoint,
        defaultViewport=viewport_dimensions)
  except BrowserError as err:
    raise SessionError('Could not create CDP session') from err


async def close_page_and_disconnect_browser(page: pyppeteer.page.Page,
                                            browser: pyppeteer.browser.Browser) -> None:
  """Close browser page and disconnect from the browser.

  :param page: pyppeteer page object
  :param browser: pyppeteer browser object
  :return: None
  """
  logger.info('Closing browser page for recorder client')
  try:
    await page.close()
  except PyppeteerError:
    logger.exception('Encountered exception while attempting to close recorder client browser page')

  logger.info('Disconnecting from browser')
  try:
    await browser.disconnect()
  except PyppeteerError:
    logger.exception('Encountered exception while attempting to disconnect from browser')


def on_console_message(console_message: pyppeteer.page.ConsoleMessage,
                       buffer: Deque[pyppeteer.page.ConsoleMessage]) -> None:
  buffer.append(console_message)


async def pull_and_handle_session_request(initialize_client_session: InitializeClientSession,
    handle_request: HeadlessSessionRequestHandler, page: pyppeteer.page.Page,
    console_message_buffer: Deque[pyppeteer.page.ConsoleMessage], gcp_project_id: str,
    pubsub_subscription_id: str,
    session_request_received_callback: SessionRequestMsgReceivedCallback,
    phase_manager: RecorderAgentPhaseManager, should_exit: asyncio.Event) -> None:
  """Initialize the client if needed, wait for a session request, and then handle the request.

  Before listening for a headless session request, executes the 'initialize_client_session'
  function; if applicable, this creates the headless client and performs any initialization steps
  that should be performed eagerly at agent startup.

  If agent termination is requested before a Pub/Sub request is received, stop listening for a
  request and return immediately. If termination is requested after a request is received, finish
  handling the session completely before exiting.

  :param initialize_client_session: that initializes the headless DMs client
  :param handle_request: headless session request handler, to be called after the session request
  is received
  :param page: pyppeteer page object
  :param console_message_buffer: fixed-size buffer of the most recent console messages
  :param gcp_project_id: GCP project id string
  :param pubsub_subscription_id: the unqualified Pub/Sub subscription id, e.g.
  'recorder-agent-subscription'
  :param session_request_received_callback: event callback to be executed immediately when a
  headless session request message is received
  :param phase_manager: recorder agent phase manager
  :param should_exit: an asyncio.Event that, if set, indicates the agent should exit when possible;
  refer above for more details.
  :return: None
  """
  try:
    await initialize_client_session(page)
  except PyppeteerError as err:
    raise SessionError('Encountered exception while initializing client session') from err

  # fetch a session request from the topic to process and ACK it immediately on receipt.
  # since the subscriber pull() call is blocking, run all of this in a separate thread so the
  #  HTTP server is not starved.
  try:
    subscriber_pull_response = await pull_and_ack_session_request(
        gcp_project_id,
        pubsub_subscription_id,
        session_request_received_callback,
        should_exit)
  except google.api_core.exceptions.GoogleAPIError as err:
    metrics.report_pubsub_error()
    raise err

  if subscriber_pull_response is None:
    logger.info('The Pub/Sub subscriber loop stopped without accepting a headless session request')
  else:
    await phase_manager.update_phase(constants.RECORDER_AGENT_PHASE_PROCESSING_REQUEST)

    # parse the headless session request payload
    decoded_message_payload = subscriber_pull_response.received_messages[0].message.data.decode()
    headless_session_request = parse_headless_session_request(decoded_message_payload)
    await handle_request(headless_session_request, page, console_message_buffer)

    logger.info('Uploading recorder client logs')
    try:
      await page.evaluate('window.uploadLogs()')
    except PyppeteerError:
      logger.exception('Encountered exception while attempting to upload the recorder client logs')


async def pull_and_ack_session_request(
    gcp_project_id: str, pubsub_subscription_id: str,
    session_request_received_callback: SessionRequestMsgReceivedCallback,
    should_exit: asyncio.Event
) -> Optional[pubsub.PullResponse]:
  """Creates a new subscriber client, polls the subscription indefinitely until a single
  (video/streaming/sip-room-connector) session request is received, and then acknowledges it on
  receipt.

  If the exit event is observed in between pull() calls, halts the subscriber loop and returns.

  Note the pull() calls are blocking.

  :param gcp_project_id: The GCP project id for the current environment
  :param pubsub_subscription_id: the unqualified Pub/Sub subscription id, e.g.
   'recorder-agent-subscription'
  :param session_request_received_callback: event callback to be executed immediately when a
  headless session request message is received
  :param should_exit: an asyncio.Event that, if set, indicates the pull() calls should be stopped
  :return: Pub/Sub pull response object, or if the subscriber loop was aborted, None
  """
  loop = asyncio.get_running_loop()
  subscriber, pubsub_subscription_path = await loop.run_in_executor(
      None,
      pubsub_subscriber.create_subscriber,
      gcp_project_id,
      pubsub_subscription_id)
  with subscriber:
    subscriber_pull_attempt = 0
    subscriber_pull_response = None
    while not subscriber_pull_response or not subscriber_pull_response.received_messages:
      if should_exit.is_set():
        logger.info('Exiting Pub/Sub subscriber loop')
        return None

      logger.info(f'Listening for messages on {pubsub_subscription_path} '
                  f'(pull attempt {subscriber_pull_attempt})...')
      subscriber_pull_response = await loop.run_in_executor(
          None,
          pubsub_subscriber.pull,
          subscriber,
          pubsub_subscription_path)
      subscriber_pull_attempt += 1

    for msg in subscriber_pull_response.received_messages:
      session_request_received_callback(msg)

    # acknowledge the Pub/Sub message immediately on receipt
    # there are no automatic retries – if a recording fails, the end user can manually initiate a
    #  recording again once their client is notified of the failure
    ack_ids = [msg.ack_id for msg in subscriber_pull_response.received_messages]
    await loop.run_in_executor(
        None,
        pubsub_subscriber.acknowledge_pubsub_messages,
        subscriber,
        pubsub_subscription_path,
        ack_ids)
    return subscriber_pull_response


def recording_request_received(recording_request_msg: dict) -> None:
  """Callback for receipt of a recording request message.

  :param recording_request_msg: a message holding a recording session request
  :return: None
  """
  logger.info(f'Received recording request: {recording_request_msg}')
  metrics.report_recording_request_received()


def parse_headless_session_request(decoded_message_payload: str) -> dict:
  """Parse JSON string payload of the Pub/Sub message and extract the session request.

  Interprets the request as a video-recording, streaming-recording, or SIP-room-connector session
  request, or else raises an exception if the type is not recognized.

  :param decoded_message_payload: JSON string payload for the headless session request
  :return: dictionary representation of the headless session request
  """
  headless_session_request_payload = json.loads(decoded_message_payload)
  logger.info(f'PubSub request: {headless_session_request_payload}')
  session_request = headless_session_request_payload.get('recorder_request_message')
  if session_request is None:
    session_request = headless_session_request_payload.get('streaming_recording_request')
  if session_request is None:
    session_request = headless_session_request_payload['sip_room_agent_request']
  logger.info(f'Parsed headless session request:\n{json.dumps(session_request, indent=2)}')
  return session_request


async def record_call(page: pyppeteer.page.Page, recording_id: int, recorder_client_url: str,
                      call_url: str, recording_base_directory: str, recording_path: str,
                      gcs_bucket: str, uc_client: UcApiClient,
                      console_message_buffer: Deque[pyppeteer.page.ConsoleMessage],
                      gcs_output_path: Optional[str] = None,
                      gcs_client_executor: Optional[concurrent.futures.Executor] = None,
                      is_streaming: bool = False
                      ) -> RecordingResult:
  """Adds a headless recorder client to the conference and records the screen (with both audio and
  video).

  Additionally, if the recording starts successfully, makes a UC API call immediately afterwards to
  write the recording start timestamp and status.

  :param page: pyppeteer page object
  :param recording_id: video recording entity id
  :param recorder_client_url: the URL for the UC conference with the recorder client credentials
  :param call_url: the base URL for the DMs conference room
  :param recording_base_directory: local base directory where the recording directory is held, e.g.
  /recording
  :param recording_path: absolute path to use for either 1) the local recording file, if this is a
  video recording, or else if this is a streaming recording - 2) the local directory where the
  primary playlist index file should be stored
  :param gcs_bucket: output GCS bucket
  :param uc_client: UC API client
  :param console_message_buffer: fixed-size buffer of the most recent console messages
  :param gcs_output_path: output path relative to the root of the GCS bucket
  :param gcs_client_executor: concurrent.futures.Executor for running GCS tasks
  :param is_streaming: indicates whether this is a regular or streaming recording

  :return: A RecordingResult object indicating metadata about the completed recording and its
  success/failure (refer to recording_result.py for more details)
  """
  # create the recorder client and begin recording
  recording_start_timestamp = await start_recording(
      page,
      recorder_client_url,
      recording_id,
      recording_base_directory,
      recording_path,
      gcs_bucket,
      gcs_output_path=gcs_output_path,
      gcs_client_executor=gcs_client_executor,
      is_streaming=is_streaming)

  if recording_start_timestamp is None:
    recording_result = RecordingResult(recording_id)
  else:
    # the recording started successfully

    # call the heartbeat API on an interval to signal to UC that the recording is active
    if uc_client.can_send_heartbeats():
      heartbeat_task = asyncio.create_task(
          send_uc_heartbeats(uc_client, recording_id))
    else:
      heartbeat_task = None

    # mark the recording as in_progress
    recording_status = (constants.RECORDING_IN_PROGRESS if not is_streaming
                        else constants.STREAMING_RECORDING_STATUS_IN_PROGRESS)
    await call_update_recording_api(
        uc_client,
        recording_id,
        recording_status,
        recording_start_timestamp=recording_start_timestamp)

    # record the call until the recorder exits the conference or an error occurs
    did_recorder_exit_normally = await await_recording_completion(page)

    # query the browser agent to stop ffmpeg / complete the recording
    did_recording_complete_normally, recording_end_timestamp = await stop_recording(
        recording_id,
        is_streaming)
    logger.info(f'Recording complete for conference {call_url} and recording id {recording_id}')
    recording_result = RecordingResult(
        recording_id,
        recording_start_timestamp=recording_start_timestamp,
        recording_end_timestamp=recording_end_timestamp,
        did_recorder_exit_normally=did_recorder_exit_normally,
        did_recording_complete_normally=did_recording_complete_normally
    )
    # cancel the UC heartbeats now that recording is completed
    if heartbeat_task is not None:
      heartbeat_task.cancel()
      try:
        await heartbeat_task
      except asyncio.CancelledError:
        logger.info('UC heartbeat task successfully cancelled')
      except Exception:  # pylint: disable=broad-except
        logger.exception('Encountered unexpected exception with recording lifecycle status '
                         'heartbeat')

  metrics.report_recording_completed()

  # if there was an error, print the client logs in case it was related to a client problem
  if (
      recording_result.recording_start_timestamp is None or
      not recording_result.did_recorder_exit_normally or
      not recording_result.did_recording_complete_normally
  ):
    logger.warning(f'Error with recording - printing {len(console_message_buffer)} most '
                   f'recent recorder client logs')
    # note the JS log formatting/coloring added by UC appears in the text
    messages = [f'[ClientLog][{m.type.upper()}] {m.text}' for m in console_message_buffer]
    logger.info('\n'.join(messages))

  return recording_result


async def navigate_to_conference_page(page: pyppeteer.page.Page, recorder_client_url: str,
                                      timeout_secs: int = NAVIGATE_TO_CONFERENCE_PAGE_TIMEOUT_SECS
                                      ) -> None:
  """Navigates to the DMs conference page as a headless recorder client, waiting until the initial
  loading of the page is complete.

  Does not retry on a timeout, as the provided timeout :timeout_secs should already allow for
  more than enough time to load the page, such that the chance of success with additional attempts
  should be low. Retries on all other Pyppeteer errors (BrowserError, PageError, etc.), which are
  more likely to occur immediately and are possibly recoverable.

  :param page: pyppeteer page object
  :param recorder_client_url: the URL for the DMs conference with the recorder client credentials
  :param timeout_secs: timeout for the page load
  :return: None
  """
  @wrappers.retry_coroutine((PageError, BrowserError, NetworkError, ElementHandleError), tries=5,
      delay_secs=0.2, backoff=1)
  async def _navigate_to_conference_page():
    logger.info(f'Navigating to conference URL: {recorder_client_url}')
    # navigate to the conference URL to create the headless recorder client – the client will join
    #  the conference automatically
    await page.goto(recorder_client_url, wait_until='load', timeout=timeout_secs * 1000)

  try:
    await _navigate_to_conference_page()
    logger.info('Successfully loaded UC conference page for recorder client')
  except TimeoutError as err:
    raise SessionError('Timed out trying to load conference page') from err
  except PyppeteerError as err:
    raise SessionError(
        'Encountered Pyppeteer error while trying to load conference page') from err


@wrappers.retry_coroutine((PageError, BrowserError, NetworkError, ElementHandleError), tries=3,
    delay_secs=0.2, backoff=1)
async def wait_for_recorder_initialization(page: pyppeteer.page.Page,
                                           join_conference_timeout_secs: int,
                                           init_webrtc_timeout_secs: int,
                                           is_sip_room_connector_session: bool = False) -> None:
  """Asynchronously polls the recorder client until it's in the conference and prepared to receive
  media from other participants.

  Repeatedly evaluates JS functions directly in the browser page to test these readiness conditions.

  :param page: pyppeteer page object
  :param join_conference_timeout_secs: timeout for the conference join
  :param init_webrtc_timeout_secs: timeout for the client's WebRTC setup
  :param is_sip_room_connector_session: flag indicating whether this a SIP room connector headless
  session type or not
  :return: None
  """
  logger.info('Waiting for recorder client IN_CONFERENCE Room state...')
  # wait for the recorder client to enter the conference
  # note that the page load and initialization of the UC app accounts for some wait time here
  with metrics.timed('load_conference_duration_secs'):
    await page.waitForFunction(
        'window.isInConference()',
        timeout=join_conference_timeout_secs * 1000)
  logger.info('Client Room state is IN_CONFERENCE – recorder client is in the meeting')
  logger.info('Waiting for recorder client to initialize media')
  # now wait for WebRTC before actually recording media
  with metrics.timed('media_init_duration_secs'):
    await page.waitForFunction('window.hasWebrtcLocal()', timeout=init_webrtc_timeout_secs * 1000)
  logger.info('Recorder client initialized WebRTC connection')
  if is_sip_room_connector_session:
    logger.info('SIP room connector client finished initializing media')
  else:
    logger.info('Ready to record view')


async def start_recording(page: pyppeteer.page.Page, recorder_client_url: str, recording_id: int,
                          recording_base_directory: str, recording_path: str, gcs_bucket: str,
                          gcs_output_path: Optional[str] = None,
                          gcs_client_executor: Optional[concurrent.futures.Executor] = None,
                          is_streaming: bool = False,
                          ) -> Optional[str]:
  """Performs all the steps to initiate a recording.

  Creates a recorder client in the browser, waits for it to join the conference and initialize, and
  then starts the screen recording.

  Catches any exceptions and merely logs them, so that the session controller can still perform
  completion handling even if the recording fails.

  :param page: pyppeteer page object
  :param recorder_client_url: the conference URL, including the recorder client credentials
  :param recording_id: video recording entity id
  :param recording_base_directory: local base directory where the recording directory is held, e.g.
  /recording
  :param recording_path: absolute path to use for either 1) the local recording file, if this is a
  video recording, or else if this is a streaming recording - 2) the local base path where the
  primary playlist index file should be stored
  :param gcs_bucket: output GCS bucket
  :param gcs_output_path: output path relative to the root of the GCS bucket
  :param gcs_client_executor: concurrent.futures.Executor for running GCS tasks
  :param is_streaming: indicates whether this is a regular or streaming recording
  :return: recording start timestamp if the recording is started successfully; otherwise, None
  """
  recording_start_timestamp = None
  try:
    await navigate_to_conference_page(page, recorder_client_url)
    await wait_for_recorder_initialization(
        page,
        RECORDER_CLIENT_JOIN_CONFERENCE_TIMEOUT_SECS,
        RECORDER_CLIENT_INITIALIZE_WEBRTC_TIMEOUT_SECS)

    # create the HLS playlist folder locally and in GCS
    if is_streaming:
      recording_dir_path = Path(recording_path)
      recording_dir_path.mkdir(parents=True, exist_ok=True)
      await asyncio.get_running_loop().run_in_executor(
          gcs_client_executor,
          gcs_client.create_folder,
          gcs_bucket,
          gcs_output_path)

      # sync the HLS files for this recording to GCS
      asyncio.create_task(
          watch_and_sync_recording_dir(
              recording_path,
              recording_base_directory,
              gcs_bucket,
              gcs_client_executor=gcs_client_executor))

    # Sleep for 2 seconds before starting the recording.
    # UC-9334: Helps keep audio and video in sync.
    # *Warning* this is a hack to solve the issue for the time being – needs to be removed once an
    # actual root cause and fix are established.
    await asyncio.sleep(2)

    # query the browser agent container to start ffmpeg / begin recording
    await start_recording_screen(recording_id, recording_path, is_streaming)
  except Exception:  # pylint: disable=broad-except
    logger.exception('Failed to start recording')
    metrics.report_start_recording_error()
  else:
    recording_start_timestamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
    logger.info(f'Recording started successfully at {recording_start_timestamp}')
    metrics.report_recording_started()
  return recording_start_timestamp


async def stop_recording(recording_id: int, is_streaming: bool = False) -> (bool, str):
  """Performs all the steps to stop the recording.

  Makes a request to stop the screen recording command. Catches any exceptions and merely logs them,
  so that the session controller can still perform completion handling, even if there's a failure.

  :param recording_id: video recording entity id
  :param is_streaming: indicates whether this is a regular or streaming recording
  :return: a boolean value indicating whether the recording was stopped successfully or not and the
   recording stop timestamp.
  """
  recording_stopped_successfully = False
  try:
    await stop_recording_screen(recording_id, is_streaming)
    recording_stopped_successfully = True
  except Exception:  # pylint: disable=broad-except
    logger.exception('Failed to stop recording')
    metrics.report_stop_recording_error()
  recording_end_timestamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
  return recording_stopped_successfully, recording_end_timestamp


async def start_recording_screen(recording_id: int, recording_path: str,
                                 is_streaming: bool = False) -> None:
  """Makes a request to the browser agent to start the screen recording.

  :param recording_id: video recording entity id
  :param recording_path: fully-qualified local file path for either 1) the recording file if this
  is a video recording, or else if this is a streaming recording, 2) the base path where the primary
  playlist index file should be stored
  :param is_streaming: indicates whether this is a regular or streaming recording
  :return: None
  """
  logger.info('Starting recording')
  try:
    resp = await browser_agent_client.start_recording(recording_id, recording_path, is_streaming)
    # throws an HTTPStatusError if response code is  >= 400
    resp.raise_for_status()
  except (httpx.RequestError, httpx.InvalidURL) as err:
    raise SessionError(
        'Failed to start screen recording: start recording request failed') from err
  except httpx.HTTPStatusError as err:
    raise SessionError('Failed to start screen recording: browser agent returned response '
                       f'with status code {err.response.status_code}') from err


async def stop_recording_screen(recording_id: int, is_streaming: bool = False) -> None:
  """Makes a request to the browser agent to stop the screen recording.

  :param recording_id: video recording entity id
  :param is_streaming: indicates whether this is a regular or streaming recording
  :return: None
  """
  logger.info('Stopping recording')
  try:
    resp = await browser_agent_client.stop_recording(recording_id, is_streaming)
    exit_code = resp.json().get('exit_code')
    # throws an HTTPStatusError if response code is >= 400
    resp.raise_for_status()
  except (httpx.RequestError, httpx.InvalidURL) as err:
    raise SessionError('Failed to stop screen recording: stop recording request failed') from err
  except httpx.HTTPStatusError as err:
    raise SessionError('Browser agent returned stop recording response with code '
                       f'{err.response.status_code}') from err
  else:
    logger.info(f'Successfully stopped recording {recording_id}')
    if exit_code is not None and exit_code != 0:
      metrics.report_ffmpeg_non_zero_exit_code()


@metrics.timed('recording_duration_secs')
async def await_recording_completion(page: pyppeteer.page.Page) -> bool:
  """Asynchronously polls the DMs recorder client until it leaves the conference or an error occurs.

  :param page: pyppeteer page object
  :return: True if the recorder client left the room without error, else False
  """
  logger.info('Recording until recorder client leaves the conference')
  return await await_conference_exit(page, '!window.isInConference()')


async def await_conference_exit(page: pyppeteer.page.Page, page_function: str,
                                is_sip_room_connector_session: bool = False) -> bool:
  """Asynchronously polls the DMs client until it leaves the conference or an error occurs.

  :param page: pyppeteer page object
  :param page_function: JS expression to evaluate
  :param is_sip_room_connector_session: flag indicating whether this a SIP room connector headless
  session type or not
  :return: True if the client left the room without error, else False
  """
  client_exited_normally = False
  try:
    # uses 'polling' parameter default – 'raf' (requestAnimationFrame)
    # disable the timeout with the timeout=0 argument.
    # the polling will continue indefinitely until a false-y value is returned or an error occurs.
    await page.waitForFunction(page_function, timeout=0)
    logger.info('Client left room')
    client_exited_normally = True
  except Exception:  # pylint: disable=broad-except
    if is_sip_room_connector_session:
      logger.exception('Encountered unexpected error during SIP room connector session')
      metrics.report_sip_room_connector_session_error()
    else:
      logger.exception('Encountered unexpected error during recording')
      metrics.report_recording_error()
  return client_exited_normally


async def send_uc_heartbeats(uc_client: UcApiClient, recording_id: int) -> None:
  """Sends recording lifecycle status updates to UC at a regular interval.

  :param uc_client: UC client
  :param recording_id: video recording entity id
  :return: None
  """
  while True:
    await asyncio.create_task(
        call_recording_heartbeat_api(uc_client,
            recording_id,
        constants.RECORDING_LIFECYCLE_RECORDING))
    await asyncio.sleep(UC_HEARTBEAT_INTERVAL_SECS)


async def call_recording_heartbeat_api(uc_client: UcApiClient,
                                       recording_id: int,
                                       status: str) -> Optional[httpx.Response]:
  """Makes an API call to update the recording lifecycle status.

  Catches any exceptions and merely logs them, so that the core recording handling can continue even
  in the event of an API call error.

  :param uc_client: UC client
  :param recording_id: video recording entity id
  :param status:
  :return: The HTTP response or None if an exception occurred or the request succeeded but the
  response code is not 200
  """
  try:
    resp = await uc_client.recording_lifecyle_status(recording_id, status)
  except Exception:  # pylint: disable=broad-except
    # just log exceptions here, so we don't fail hard in response to an API request error
    logger.exception('Encountered exception during recording lifecycle status update API request')
    metrics.report_heartbeat_api_error()
    return None
  else:
    if resp.status_code == 200:
      return resp
    else:
      metrics.report_heartbeat_api_non_200_response_code()
      logger.error('Received non-200 response code for recording lifecycle status update API '
                   'request')
      return None


def build_recorder_client_url(conference_url: str, recorder_token: str,
                              recorder_signature_hash: str) -> str:
  """Builds the URL for the conference, with the recorder client credentials as query params.

  :param conference_url: base URL for the UC conference room to be recorded, e.g.
  https://uberconference.com/room/<room name>
  :param recorder_token: auth token for the recorder, allowing it to enter the conference as a
  headless client
  :param recorder_signature_hash: hash of the recorder client token
  :return: the resulting conference URL for the recorder client
  """
  params = {
    'recorder_token': recorder_token,
    'recorder_signature_hash': recorder_signature_hash
  }

  query_params = urllib.parse.urlencode(params)

  return f'{conference_url}?{query_params}'


async def await_browser_agent_readiness(
    retry_delay_secs: float = constants.BROWSER_AGENT_READINESS_CHECK_RETRY_DELAY_SECS,
    max_readiness_checks: int = constants.MAX_BROWSER_AGENT_READINESS_CHECKS
) -> (bool, httpx.Response):
  """Waits until the browser agent is ready to accept a new recording.

  Make requests on the readiness endpoint until a positive response is received, and retries
  up to the configured amount of times if applicable. If determined that the current condition
  cannot be recovered from, the retries are halted.

  :param retry_delay_secs: constant delay between readiness checks
  :param max_readiness_checks: maximum number of retries for the readiness check
  :return: a tuple with a boolean indicating whether the browser agent is ready or not, and the most
  recent response object
  """
  async def do_readiness_checks() -> (bool, httpx.Response):
    is_ready = False
    resp = None
    readiness_check = 0
    while not is_ready and readiness_check < max_readiness_checks:
      try:
        resp = await browser_agent_client.browser_agent_is_ready()
        # raises HTTPStatusError for response codes >= 400
        resp.raise_for_status()
      except httpx.RequestError as err:
        # re-raise if this is the last check
        if readiness_check + 1 >= max_readiness_checks:
          raise err
        # otherwise, continue since the browser agent may just still be starting up
      except httpx.HTTPStatusError:
        # if for reason, there is a recording or sip room connector session is already in progress,
        #  stop the check so that we attempt to handle this condition
        has_recording = resp is not None and resp.json().get('recording') is not None
        has_sip_room_connector_session = (resp is not None and
                                          resp.json().get('sip_room_connector_session') is not None)
        if has_recording or has_sip_room_connector_session:
          break
        # otherwise, continue since the browser/sip connector agent may just still be starting up
      else:
        is_ready = True

      await asyncio.sleep(retry_delay_secs)
      readiness_check += 1
    return is_ready, resp

  ready = False
  response = None
  try:
    ready, response = await do_readiness_checks()
  except Exception:  # pylint: disable=broad-except
    logger.exception('Browser agent failed readiness checks')

  if not ready:
    metrics.report_browser_agent_readiness_check_error()

  return ready, response


async def terminate_browser_agent() -> bool:
  """Sends request to terminate the browser agent.

  :return: boolean indicating whether request was successful
  """
  success = False
  try:
    resp = await browser_agent_client.terminate_browser_agent()
    # raises HTTPStatusError for response codes >= 400
    resp.raise_for_status()
  except Exception:  # pylint: disable=broad-except
    logger.exception('Encountered error while attempting to request browser agent termination')
    metrics.report_browser_agent_termination_error()
  else:
    logger.info('Successfully requested browser agent termination')
    success = True
  return success


async def call_update_recording_api(uc_client: UcApiClient,
                                    recording_id: int,
                                    recording_status: str,
                                    recording_status_reason: Optional[str] = None,
                                    recording_start_timestamp: Optional[str] = None,
                                    recording_end_timestamp: Optional[str] = None
                                    ) -> Optional[httpx.Response]:
  """Call "update recording" API to write changes to the recording metadata. Works for regular or
  streaming recordings.

  Catches any exceptions and merely logs them, so that the core recording handling can continue even
  in the event of an API call error.

  :param uc_client: UC API client
  :param recording_id: video recording entity id
  :param recording_status: the recording status (refer to constants.py)
  :param recording_status_reason: the recording status reason (refer to constants.py)
  :param recording_start_timestamp: optional timestamp for when the recording started
  :param recording_end_timestamp: optional timestamp for when the recording ended
  :return: the HTTP response object if the request completed without error and returned a 200 status
  code, otherwise, None
  """
  try:
    resp = await uc_client.update_recording(
      recording_id,
      recording_status=recording_status,
      recording_status_reason=recording_status_reason,
      recording_start_timestamp=recording_start_timestamp,
      recording_end_timestamp=recording_end_timestamp)
  except Exception:  # pylint: disable=broad-except
    # just log exceptions here, so we don't fail hard in response to an API request error
    logger.exception('Encountered exception during update recording API request')
    metrics.report_update_recording_api_error()
    return None
  else:
    # only consider 200 response codes a success
    if resp.status_code == 200:
      return resp
    else:
      logger.error('Received non-200 response code for update recording API request')
      metrics.report_update_recording_api_non_200_response_code()
      return None


async def upload_hls_file(gcs_bucket: str, gcs_filename: str, file_path: str, upload_id: str,
    cache_control: Optional[str] = None, timeout_secs: Optional[float] = None,
    retry: Optional[google.api_core.retry.Retry] = google.cloud.storage.retry.DEFAULT_RETRY,
    hedge_request_deadline_secs: Optional[float] = None,
    recording_stream_directory: Optional[str] = None, log_level: int = logging.DEBUG,
    gcs_client_executor: Optional[concurrent.futures.Executor] = None) -> None:
  """Uploads a HLS file (.m3u8 or .ts) to GCS.

  Blocks until the upload completes successfully or with an error.

  :param gcs_bucket: GCS bucket name
  :param gcs_filename: GCS object name
  :param file_path: local file path to file to upload
  :param upload_id: a randomly generated id to track the file upload attempt (for debugging purposes
   only)
  :param cache_control: cache control to set on GCS object metadata
  :param timeout_secs: timeout for the upload operation
  :param retry: an optional google.api_core.retry.Retry object to configure the retry policy used by
  the client library. if None is provided, the operation will never be retried.
  see https://googleapis.dev/python/storage/latest/retry_timeout.html for more info.
  :param hedge_request_deadline_secs: a soft deadline to wait for before hedging the upload request
  - if this expires, start a second ("hedged") upload request in parallel.
  :param recording_stream_directory: optional directory of the variant stream, if applicable
  :param log_level: log level for the upload function
  :param gcs_client_executor: executor to run the upload task in
  :return: None
  """
  file_type = gcs_filename.split('.')[-1]
  # extract the quality level string if this is a variant stream file
  hls_quality_level = (recording_stream_directory.split('/')[-1]
                       if recording_stream_directory is not None else None)

  def upload_fn(timeout):
    metric_tags = [
        f'file_type:{file_type}'
    ]
    if hls_quality_level is not None:
      metric_tags.append(f'hls_quality_level:{hls_quality_level}')

    with metrics.timed('upload_hls_file_duration_secs', tags=metric_tags):
      gcs_client.upload_file(
        gcs_bucket,
        gcs_filename,
        file_path,
        upload_id=upload_id,
        cache_control=cache_control,
        timeout_secs=timeout,
        retry=retry,
        log_level=log_level)

  async def do_upload(duration):
    completed = False
    try:
      await asyncio.get_running_loop().run_in_executor(gcs_client_executor, upload_fn, duration)
    except google.cloud.exceptions.GoogleCloudError as err:
      logger.exception(f'[{upload_id}] Failed to upload {gcs_filename} to bucket {gcs_bucket}')
      http_code = str(int(err.code)) if err.code is not None else None
      metrics.report_hls_file_upload_error(
          hls_quality_level=hls_quality_level,
          file_type=file_type,
          status_code=http_code,
          exception_name=str(type(err)))
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as err:
      logger.exception(f'[{upload_id}] Timeout or connection error while uploading {gcs_filename} '
                       f'to bucket {gcs_bucket}')
      metrics.report_hls_file_upload_error(
          hls_quality_level=hls_quality_level,
          file_type=file_type,
          exception_name=str(type(err)))
    except asyncio.CancelledError as err:
      logger.exception(f'[{upload_id}] Cancelled operation while uploading {gcs_filename} to bucket'
                       f' {gcs_bucket}')
      raise err
    except Exception:  # pylint: disable=broad-except
      logger.exception(f'[{upload_id}] Unexpected error while uploading {gcs_filename} to bucket'
                       f' {gcs_bucket}')
    else:
      completed = True
    return completed

  # if a hedge deadline is provided, apply a request hedging strategy
  if hedge_request_deadline_secs is not None:
    upload_task1 = asyncio.create_task(do_upload(timeout_secs))
    done, pending = await asyncio.wait(
        {upload_task1},
        timeout=hedge_request_deadline_secs,
        return_when=asyncio.FIRST_COMPLETED)
    if upload_task1 in pending:
      logger.warning(f'[{upload_id}] Upload task {upload_task1} for file {gcs_filename} in bucket '
                     f'{gcs_bucket} is still pending after {hedge_request_deadline_secs} secs. '
                     'Starting a second request in parallel.')
      upload2_timeout_secs = timeout_secs - hedge_request_deadline_secs
      upload_task2 = asyncio.create_task(do_upload(upload2_timeout_secs + 0.1))
      done, pending = await asyncio.wait(
        {upload_task1, upload_task2},
        timeout=upload2_timeout_secs,
        return_when=asyncio.FIRST_COMPLETED)
      upload_task1_success, upload_task2_success = False, False
      if upload_task1 in done:
        try:
          upload_task1_success = await upload_task1
        except asyncio.CancelledError:
          logger.debug(f'[{upload_id}] Hedged upload 1 task {upload_task1} for file '
                       f'{gcs_filename} in bucket {gcs_bucket} was cancelled')
        else:
          logging.info(f'[{upload_id}] Hedged upload 1 {upload_task1} for file {gcs_filename} in '
                       f'bucket {gcs_bucket} completed with status: {upload_task1_success}')
      if upload_task2 in done:
        try:
          upload_task2_success = await upload_task2
        except asyncio.CancelledError:
          logger.debug(f'[{upload_id}] Hedged upload 2 task {upload_task2} for file '
                       f'{gcs_filename} in bucket {gcs_bucket} was cancelled')
        else:
          logging.info(f'[{upload_id}] Hedged upload 2 {upload_task1} for file {gcs_filename} in '
                       f'bucket {gcs_bucket} completed with status: {upload_task2_success}')
      if done and not upload_task1_success and not upload_task2_success:
        logging.error(f'[{upload_id}] At least one of the hedged upload tasks {upload_task1}, '
                      f'{upload_task2} for file {gcs_filename} in bucket {gcs_bucket} completed, '
                      f'but none succeeded')
      if not done:
        logging.error(f'[{upload_id}] None of the hedged upload tasks {upload_task1}, '
                      f'{upload_task2} for file {gcs_filename} in bucket {gcs_bucket} have '
                      f'finished after {timeout_secs}')
      for pending_upload in pending:
        logging.debug(f'[{upload_id}] Canceling pending upload task {pending_upload} for file '
                      f'{gcs_filename} in bucket {gcs_bucket}')
        pending_upload.cancel()
        try:
          success = await pending_upload
        except asyncio.CancelledError:
          logger.debug(f'[{upload_id}] Upload task {pending_upload} for file {gcs_filename} in '
                       f'bucket {gcs_bucket} cancelled successfully')
        else:
          logger.debug(f'[{upload_id}] Could not cancel upload task {pending_upload} for file '
                       f'{gcs_filename} in bucket {gcs_bucket}: task already completed with '
                       f'status {success}')
  else:
    await do_upload(timeout_secs)


async def watch_and_sync_recording_dir(
    recording_directory: str, recording_base_directory: str, gcs_bucket: str,
    gcs_client_executor: Optional[concurrent.futures.Executor] = None) -> None:
  """Recursively watches the local directory for a streaming recording and syncs changes to the GCS
  bucket.

  Watches for the following events:
  - Primary playlist index file (e.g /recording/<call id>/<stream id>/<playlist type>/index.m3u8)
  creation/update
  - Variant stream directory file (e.g.
  /recording/<call id>/<stream id>/<playlist type>/v<variant index>) creation
  - Variant stream index file (e.g.
  /recording/<call id>/<stream id>/<playlist type>/v<variant index>/index.m3u8) creation/update
  - Variant stream segment file (e.g
  /recording/<call id>/<stream id>/<playlist type>/v<variant index>/index_<timestamp>.ts) creation

  :param recording_directory: path 1) within the local recording_base_directory for the stream and
  2) the output directory relative to the root of the bucket, e.g.
  <call id>/<stream id>/<playlist type>. contains all the HLS files for the stream.
  :param recording_base_directory: local base directory where the recording directory is held, e.g.
  /recording
  :param gcs_bucket: GCS bucket name to sync changes to
  :param gcs_client_executor: concurrent.futures.Executor for running GCS tasks
  :return: None
  """
  # inotify event handler
  async def handle(event: asyncinotify.Event, _existing_task: Optional[asyncio.Task]) -> None:
    abs_path_str = str(event.path)
    gcs_object_name = abs_path_str.lstrip(recording_base_directory)
    if Mask.CREATE in event.mask and Mask.ISDIR in event.mask:
      logger.info(f'Streaming directory {abs_path_str} created')
      # create the stream directory in GCS
      try:
        await asyncio.get_running_loop().run_in_executor(
            gcs_client_executor,
            functools.partial(
                gcs_client.create_folder,
                gcs_bucket,
                gcs_object_name,
                timeout_secs=1.5))
      except google.cloud.exceptions.GoogleCloudError:
        logger.exception(f'Failed to create folder {gcs_object_name} in {gcs_bucket}')
      except Exception:  # pylint: disable=broad-except
        logger.exception(f'Unexpected error while creating folder {gcs_object_name} in '
                         f'{gcs_bucket}')
      # watch the directory for this variant stream and sync the local index/segments with GCS
      asyncio.create_task(
          watch_and_sync_stream_directory(
              abs_path_str,
              recording_base_directory,
              gcs_bucket,
              gcs_client_executor=gcs_client_executor))
    elif Mask.CLOSE_WRITE in event.mask and abs_path_str.endswith('.m3u8'):
      # generate a random id to track the file upload
      upload_id = str(uuid.uuid4())
      logger.info(f'[{upload_id}] Uploading primary playlist index file {str(event.path.name)}')
      await upload_hls_file(
          gcs_bucket,
          gcs_object_name,
          abs_path_str,
          upload_id=upload_id,
          cache_control=gcs_client.CACHE_CONTROL_NO_CACHE,
          timeout_secs=2,
          retry=None,
          log_level=logging.INFO,
          gcs_client_executor=gcs_client_executor)

  # listen for HLS primary playlist index creation/updates, and variant stream directory creation
  # sync changes to GCS
  events_mask = Mask.CREATE | Mask.CLOSE_WRITE | Mask.OPEN | Mask.MOVED_TO
  await asyncionotify_tools.watch_path(recording_directory, events_mask, handle)


async def watch_and_sync_stream_directory(
    recording_stream_directory: str, recording_base_directory: str, gcs_bucket: str,
    gcs_client_executor: Optional[concurrent.futures.Executor] = None) -> None:
  """Watches the local directory for an HLS variant stream and syncs the changes to the GCS bucket.

  Watches for the following events:
  - Variant stream index file (e.g. /recording/<id>/v<variant index>/index.m3u8) creation/updates
  - Variant stream segment file (e.g /recording/<id>/v<variant index>/index_<timestamp>.ts) creation

  :param recording_stream_directory: absolute path to local directory for the variant stream of a
  given recording, e.g. /recording/<recording id>/v<variant stream index>
  :param recording_base_directory: base directory where the recording directory is held, e.g.
  /recording
  :param gcs_bucket: GCS bucket name to sync changes to
  :param gcs_client_executor: concurrent.futures.Executor for running GCS tasks
  :return: None
  """
  # inotify event handler
  async def handle(event: asyncinotify.Event, existing_task: Optional[asyncio.Task]) -> None:
    abs_path_str = str(event.path)
    gcs_filename = abs_path_str.lstrip(recording_base_directory)

    # if there is an in-progress task for the same file, wait for it to complete first
    if existing_task is not None and not existing_task.done():
      logger.info(f'Waiting for existing task {existing_task.get_name()} to complete')
      await existing_task
      logger.info(f'Existing task {existing_task.get_name()} completed')

    if event.mask in Mask.MOVED_TO and abs_path_str.endswith('.m3u8'):
      upload_id = str(uuid.uuid4())
      logger.info(f'[{upload_id}] Uploading index file {gcs_filename} to bucket {gcs_bucket}')
      await upload_hls_file(
          gcs_bucket,
          gcs_filename,
          abs_path_str,
          upload_id=upload_id,
          cache_control=gcs_client.CACHE_CONTROL_NO_CACHE,
          timeout_secs=0.8,
          hedge_request_deadline_secs=0.4,
          retry=None,
          log_level=logging.INFO,
          recording_stream_directory=recording_stream_directory,
          gcs_client_executor=gcs_client_executor)
    elif event.mask in Mask.CLOSE_WRITE and abs_path_str.endswith('.ts'):
      upload_id = str(uuid.uuid4())
      logger.info(f'[{upload_id}] Uploading segment file {gcs_filename} to bucket {gcs_bucket}')
      await upload_hls_file(
          gcs_bucket,
          gcs_filename,
          abs_path_str,
          upload_id=upload_id,
          hedge_request_deadline_secs=0.4,
          timeout_secs=1.5,
          retry=None,
          log_level=logging.INFO,
          recording_stream_directory=recording_stream_directory,
          gcs_client_executor=gcs_client_executor)

  # listen for HLS segment creation and index update events for the variant stream
  # sync changes to GCS
  await asyncionotify_tools.watch_path(
      recording_stream_directory,
      Mask.CLOSE_WRITE | Mask.MOVED_TO,
      handle)
