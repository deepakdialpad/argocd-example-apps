from __future__ import annotations
import asyncio
import concurrent.futures
import logging
from typing import Deque, TYPE_CHECKING

import config
from common.lib import gcs_client
from common.lib import metrics
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
# (quality variants) * (new/updated files per chunk) * (max parallel requests per file) *
#   (additional buffer factor)
GCS_CLIENT_EXECUTOR_THREADS = 3 * 2 * 2 * 3


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
    # do nothing when the SIP agent starts up - defer client creation until a request is received
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
                                   recording_base_directory: str,
                                   page: pyppeteer.page.Page,
                                   console_message_buffer: Deque[pyppeteer.page.ConsoleMessage]
                                   ) -> None:
  """Parse the recording request and handle it if valid.

  :param recording_request: JSON dictionary representing the recording request
  :param page: pyppeteer page object
  :param gcs_bucket: output GCS bucket
  :param recording_base_directory: local base directory where the recording directory is held, e.g.
  /recording
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
  stream_output_path = recording_request['stream_output_path']
  local_recording_path = f'{recording_base_directory}/{stream_output_path}'
  # TODO: once we add support for multiple recorder clients, parse the layouts and stream type
  #  from the request
  uc_api_client = UcApiClient(
      recording_request,
      constants.RECORDER_TYPE_STREAMING,
      config.RECORDER_SECRET_BYTES)

  # record the call and upload the recording
  await process_recording_request(
      page,
      recording_id,
      recorder_client_url,
      call_url,
      recording_base_directory,
      local_recording_path,
      gcs_bucket,
      uc_api_client,
      console_message_buffer,
      gcs_output_path=stream_output_path)


async def process_recording_request(page: pyppeteer.page.Page, recording_id: int,
                                    recorder_client_url: str, call_url: str,
                                    recording_base_directory: str, local_recording_path: str,
                                    gcs_bucket: str, uc_client: UcApiClient,
                                    console_message_buffer: Deque[pyppeteer.page.ConsoleMessage],
                                    gcs_output_path: str
                                    ) -> None:
  """Records the call, attempts to upload the resulting recording file, and makes a UC API call to
  write the final recording status (refer to constants.py).

  The final recording status is based on the success/failure of the recording and file upload step.

  If the recording fails to start, returns immediately after the API call.

  :param page: pyppeteer page object
  :param recording_id: video recording entity id
  :param recorder_client_url: the URL for the UC conference with the recorder client credentials
  :param call_url: the base URL for the UC conference room
  :param recording_base_directory: local base directory where the recording directory is held, e.g.
  /recording
  :param local_recording_path: absolute path where the primary playlist index file should be
  stored within the local recording volume
  :param gcs_bucket: output GCS bucket
  :param uc_client: UC API client
  :param console_message_buffer: fixed-size buffer of the most recent console messages
  :param gcs_output_path: output path relative to the root of the bucket
  :return: None
  """
  with concurrent.futures.ThreadPoolExecutor(
      max_workers=GCS_CLIENT_EXECUTOR_THREADS,
      thread_name_prefix='gcs-client-thread',
      initializer=gcs_client.set_up_exec_thread_gcs_client
  ) as gcs_client_executor:
    recording_result = await session_controller.record_call(
        page,
        recording_id,
        recorder_client_url,
        call_url,
        recording_base_directory,
        local_recording_path,
        gcs_bucket,
        uc_client,
        console_message_buffer,
        gcs_output_path=gcs_output_path,
        gcs_client_executor=gcs_client_executor,
        is_streaming=True)

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
    logger.error('Recording failed to start')
  else:
    recording_status = constants.STREAMING_RECORDING_STATUS_DONE
    if (
        not recording_result.did_recorder_exit_normally
        or not recording_result.did_recording_complete_normally
    ):
      # while the recording appeared to start successfully, there was a failure with the recorder
      #  and/or FFmpeg at some point thereafter.
      recording_status_reason = constants.STREAMING_RECORDING_COMPLETION_REASON_FAILED
    else:
      recording_status_reason = constants.STREAMING_RECORDING_COMPLETION_REASON_FINISHED
    metrics.report_recording_status(
        recording_status,
        status_reason=recording_status_reason,
        is_streaming=True)

    await session_controller.call_update_recording_api(
        uc_client,
        recording_id,
        recording_status,
        recording_status_reason=recording_status_reason)


if __name__ == '__main__':
  recorder_agent_phase_manager = RecorderAgentPhaseManager(
      recorder_state_manager.state_manager,
      KubeClient(config.POD_NAME, config.POD_NAMESPACE))
  asyncio.get_event_loop().run_until_complete(main(recorder_agent_phase_manager, asyncio.Event()))
