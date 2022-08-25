from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
import os.path
from typing import Deque, TYPE_CHECKING

import config
from common.lib import gcs_client, metrics
from common.lib.session_controller import session_controller
from common.lib.session_controller.recorder_agent_phase_manager import RecorderAgentPhaseManager
from common.lib.session_controller import constants, recorder_state_manager
from common.lib.session_controller.uc_api_client import UcApiClient
from common.lib.kube_client import KubeClient

if TYPE_CHECKING:
  import pyppeteer.browser
  import pyppeteer.page

logger = logging.getLogger(__name__)

# chrome window size
WINDOW_SIZE = f'{config.WINDOW_SIZE_WIDTH}x{config.WINDOW_SIZE_HEIGHT}'
UC_HEARTBEAT_INTERVAL_SECS = 5


# pylint: disable=unsubscriptable-object
async def main(phase_manager: RecorderAgentPhaseManager, termination_requested_event: asyncio.Event
               ) -> None:
  """Listen for a recording request and attempt to record the call.

  :param phase_manager: recorder agent phase manager
  :param termination_requested_event: event set when graceful termination is requested. if set
  before a recording request is received, the controller will exit immediately; else, if a request
  is being processed, the controller will only terminate after the recording is done or an error
  occurs.
  :return: None
  """
  chrome_devtools_endpoint = f'{config.DEVTOOLS_URL}:{config.CHROME_DEBUG_PORT}'

  # define a handler that follows the expected function signature
  async def request_handler(recording_request: dict, page: pyppeteer.page.Page,
                            console_message_buffer: Deque[pyppeteer.page.ConsoleMessage]) -> None:
    await handle_recording_request(
        recording_request,
        config.GCS_RECORDING_BUCKET_NAME,
        config.RECORDING_DIR,
        page,
        console_message_buffer)

  async def initialize_client_session(_page: pyppeteer.page.Page) -> None:
    # do nothing - create the client after a request is received
    pass

  await session_controller.main(
      request_handler,
      initialize_client_session,
      session_controller.recording_request_received,
      config.PUBSUB_SUBSCRIPTION_ID,
      chrome_devtools_endpoint,
      int(config.WINDOW_SIZE_WIDTH),
      int(config.WINDOW_SIZE_HEIGHT),
      phase_manager,
      termination_requested_event)


async def handle_recording_request(recording_request: dict, gcs_bucket: str,
                                   recording_base_directory: str, page: pyppeteer.page.Page,
                                   console_message_buffer: Deque[pyppeteer.page.ConsoleMessage]
                                   ) -> None:
  """Parse the recording request and handle it if valid.

  :param recording_request: JSON dictionary representing the recording request
  :param gcs_bucket: output GCS bucket name
  :param recording_base_directory: local base directory where the recording directory is held, e.g.
  /recording
  :param page: pyppeteer page object
  :param console_message_buffer: fixed-size buffer of the most recent console messages on the page
  :return: None
  """
  call_url = recording_request['call_url']
  recorder_client_url = session_controller.build_recorder_client_url(
      call_url,
      recording_request['uc_recorder_token'],
      recording_request['uc_recorder_signature_hash']
  )
  recording_id = recording_request['recording_id']
  gcs_recording_id = recording_request['gcs_recording_id']
  recording_filename = f'{gcs_recording_id}.mp4'
  recording_path = f'{recording_base_directory}/{recording_filename}'

  uc_api_client = UcApiClient(
      recording_request,
      constants.RECORDER_TYPE_VIDEO,
      config.RECORDER_SECRET_BYTES)

  # record the call and upload the recording
  await process_recording_request(
      page,
      recording_id,
      recorder_client_url,
      call_url,
      recording_filename,
      recording_base_directory,
      recording_path,
      gcs_bucket,
      uc_api_client,
      console_message_buffer)


async def process_recording_request(page: pyppeteer.page.Page, recording_id: int,
                                    recorder_client_url: str, call_url: str,
                                    recording_filename: str, recording_base_directory: str,
                                    recording_path: str, gcs_bucket: str, uc_client: UcApiClient,
                                    console_message_buffer: Deque[pyppeteer.page.ConsoleMessage]
                                    ) -> None:
  """Records the call, attempts to upload the resulting recording file, and makes a UC API call to
  write the final recording status (refer to constants.py).

  The final recording status is based on the success/failure of the recording and file upload step.

  If the recording fails to start, returns immediately after the API call.

  :param page: pyppeteer page object
  :param recording_id: video recording entity id
  :param recorder_client_url: the URL for the UC conference with the recorder client credentials
  :param call_url: the base URL for the UC conference room
  :param recording_filename: unqualified recording filename to use for the GCS file
  :param recording_base_directory: local base directory where the recording directory is held, e.g.
  /recording
  :param recording_path: fully-qualified local file path for recording file
  :param gcs_bucket: output GCS bucket
  :param uc_client: UC API client
  :param console_message_buffer: fixed-size buffer of the most recent console messages
  :return: None
  """
  recording_result = await session_controller.record_call(
      page,
      recording_id,
      recorder_client_url,
      call_url,
      recording_base_directory,
      recording_path,
      gcs_bucket,
      uc_client,
      console_message_buffer)

  # report final recording lifecycle status to UC
  if uc_client.can_send_heartbeats():
    if (
        not recording_result.did_recording_complete_normally or
        not recording_result.did_recorder_exit_normally
    ):
      final_recording_lifecycle_status = constants.RECORDING_LIFECYCLE_FAILED
    else:
      final_recording_lifecycle_status = constants.RECORDING_LIFECYCLE_COMPLETED
    logger.info(f'Reporting final recording lifecycle status: {final_recording_lifecycle_status}')
    await session_controller.call_recording_heartbeat_api(
        uc_client,
        recording_id,
        constants.RECORDING_LIFECYCLE_COMPLETED)

  if recording_result.recording_start_timestamp is None:
    # if the recording failed to start, mark the status as failed and exit
    timestamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
    await session_controller.call_update_recording_api(
        uc_client,
        recording_id,
        recording_status=constants.RECORDING_FAILED,
        recording_start_timestamp=timestamp,
        recording_end_timestamp=timestamp)
    logger.error('Recording failed to start')
  else:
    # as long as the recording started, attempt to upload the file. even if the recording failed,
    #  there may be a partial recording available
    did_upload_succeed = await asyncio.get_running_loop().run_in_executor(
        None,
        upload_recording,
        recording_id,
        recording_path,
        recording_filename,
        gcs_bucket)

    if not did_upload_succeed:
      recording_status = constants.RECORDING_FAILED
    elif (
        not recording_result.did_recorder_exit_normally
        or not recording_result.did_recording_complete_normally
    ):
      # while the recording appeared to start successfully, there was a failure with the recorder
      #  and/or FFmpeg at some point thereafter.
      # a recording file was uploaded, but it likely only has a partial recording at most.
      recording_status = constants.RECORDING_TRUNCATED
    else:
      recording_status = constants.RECORDING_AVAILABLE

    metrics.report_recording_status(recording_status)

    await session_controller.call_update_recording_api(
        uc_client,
        recording_id,
        recording_status,
        recording_start_timestamp=recording_result.recording_start_timestamp,
        recording_end_timestamp=recording_result.recording_end_timestamp)


def upload_recording(recording_id: str, recording_path: str, filename: str, gcs_bucket: str
                     ) -> bool:
  """Uploads the recording file in the local volume to GCS.

  :param recording_id: unique video recording entity id
  :param recording_path: fully-qualified local file path for recording file
  :param filename: unqualified recording filename to use for the GCS file
  :param gcs_bucket: output GCS bucket
  :return True if the file was successfully uploaded, else False
  """
  logger.info(f'Uploading recording file {filename} for recording id {recording_id}')
  did_successful_upload = False
  if not os.path.isfile(recording_path):
    logger.error(f'Cannot upload recording – recording file not found at path {recording_path}!')
    metrics.report_missing_recording_file()
  else:
    try:
      with metrics.timed('upload_file_duration_secs'):
        gcs_client.upload_file(gcs_bucket, filename, recording_path)
      did_successful_upload = True
    except Exception as ex:
      logging.exception(ex)
      logger.error(f'Failed to upload recording file {filename} for recording id: {recording_id}')
      metrics.report_upload_error()
  return did_successful_upload


if __name__ == '__main__':
  recorder_agent_phase_manager = RecorderAgentPhaseManager(
      recorder_state_manager.state_manager,
      KubeClient(config.POD_NAME, config.POD_NAMESPACE))
  asyncio.get_event_loop().run_until_complete(main(recorder_agent_phase_manager, asyncio.Event()))
