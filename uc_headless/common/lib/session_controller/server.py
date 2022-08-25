import asyncio
import logging
import signal
import functools
from collections.abc import Callable
from pathlib import Path
from typing import Coroutine, Optional

from quart import Quart
from hypercorn.config import Config
from hypercorn.asyncio import serve

# common.lib imports
from common.lib.session_controller import session_controller
from common.lib.session_controller.recorder_agent_phase_manager import RecorderAgentPhaseManager
from common.lib.session_controller import constants, recorder_state_manager
from common.lib import metrics
from common.lib.kube_client import KubeClient
from common.lib.exceptions import SessionError

app = Quart(__name__)
logger = logging.getLogger('app')

SessionControllerFn = Callable[[RecorderAgentPhaseManager, asyncio.Event], Coroutine]


# pylint: disable=unsubscriptable-object
@app.route('/live')
async def liveness() -> (str, int):
  """Defines liveness probe for the session controller.

  :return the recorder agent phase and the HTTP response code indicating the liveness; returns a
  200-level code if the recorder agent is live, else a 500-level code
  """
  phase = recorder_state_manager.state_manager.get_recorder_agent_phase()
  resp_code = 200 if recorder_state_manager.state_manager.is_live() else 503
  return phase, resp_code


async def trigger_termination_request(termination_requested_event: asyncio.Event) -> None:
  """Attempts to initiate graceful termination for this recorder agent.

  :param termination_requested_event: event that signals the session controller to shut down i
  possible
  :return: None
  """
  logger.info(f'Received graceful termination signal {signal.SIGTERM}')
  termination_requested_event.set()


async def main(recording_controller_fn: SessionControllerFn, pod_name: str, pod_namespace: str,
               recording_dir: Optional[str] = None) -> None:
  try:
    await _main(recording_controller_fn, pod_name, pod_namespace, recording_dir)
  except Exception:  # pylint: disable=broad-except
    logger.exception('Session controller failed with unhandled exception')
    metrics.report_terminated_with_error()
  finally:
    # terminate the browser agent so that it exits and restarts
    await session_controller.terminate_browser_agent()
    logger.info('Exiting session controller')


# pylint:disable=unnecessary-lambda
async def _main(recording_controller_fn: SessionControllerFn, pod_name: str, pod_namespace: str,
                recording_dir: Optional[str] = None) -> None:
  # add graceful termination handler on the event loop
  loop = asyncio.get_running_loop()
  termination_requested_event = asyncio.Event()
  term_fn = functools.partial(trigger_termination_request, termination_requested_event)
  loop.add_signal_handler(
      signal.SIGTERM,
      lambda: asyncio.create_task(term_fn()))

  # create the KubeClient
  kube_client = KubeClient(pod_name, pod_namespace)

  # create the recorder agent phase manager and initialize the phase label to awaiting_request
  recorder_agent_phase_manager = RecorderAgentPhaseManager(
      recorder_state_manager.state_manager,
      kube_client)
  await recorder_agent_phase_manager.update_phase(constants.RECORDER_AGENT_PHASE_AWAITING_REQUEST)

  # start the HTTP server
  hypercorn_config = Config()
  hypercorn_config.bind = ['0.0.0.0:8080']
  # when a graceful term signal is received, hypercorn blocks until shutdown_trigger completes
  # before executing its own shutdown handler.
  # pass an empty future here so that our custom handler is guaranteed to complete. this means the
  # hypercorn handler does not run
  # (unfortunately, there currently seems to be no way to do this in the opposite order)
  asyncio.create_task(serve(app, hypercorn_config, shutdown_trigger=lambda: asyncio.Future()))

  # remove any existing recording files
  if recording_dir is not None:
    for path in Path(recording_dir).glob('*'):
      if path.is_file():
        path.unlink()
        logger.info(f'Removed existing recording file {path}')

  # ensure the browser-agent is ready before listening for a recording request
  (success, resp) = await session_controller.await_browser_agent_readiness()
  if not success:
    # check if the browser agent is already processing a recording for some reason
    if resp is not None and resp.json().get('recording') is not None:
      recording_id = resp.json().get('recording').get('recording_id')
      logger.error(f'Browser agent already has recording {recording_id}!')
      # TODO: if we detect this condition we can try to recover gracefully by stopping and uploading
      #  the recording, but this shouldn't really happen in practice.
    elif resp is not None and resp.json().get('sip_room_connector_session') is not None:
      session_id = resp.json().get('sip_room_connector_session').get('session_id')
      logger.error(f'SIP room connector agent already has session {session_id}!')
    raise SessionError('Agent failed readiness checks')

  recording_session_controller_task = asyncio.create_task(
      recording_controller_fn(recorder_agent_phase_manager, termination_requested_event))

  try:
    # exit the entire application when the recording_session_controller runner exists
    await recording_session_controller_task
  finally:
    await recorder_agent_phase_manager.update_phase(constants.RECORDER_AGENT_PHASE_COMPLETED)
