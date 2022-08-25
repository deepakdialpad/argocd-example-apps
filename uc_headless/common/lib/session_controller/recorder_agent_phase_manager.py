from __future__ import annotations
import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from common.lib.session_controller.recorder_state_manager import RecorderStateManager
  from common.lib.kube_client import KubeClient

logger = logging.getLogger(__name__)


class RecorderAgentPhaseManager:
  def __init__(self, recorder_state_manager: RecorderStateManager, kube_api_client: KubeClient
               ) -> None:
    self.recorder_state_manager = recorder_state_manager
    self.kube_api_client = kube_api_client
    self.update_phase_task = None

  async def update_phase(self, phase: str) -> None:
    """1) Updates the local recorder_agent_phase state to 'phase'
    2) Creates an asyncio.Task that sends a k8s API request to update the Pod's recorder_agent_phase
    label. The Task is meant to be run in the background (not awaited in the main control flow of
    the session controller).

    If there was a previous "update phase" task that hasn't been completed already, then cancel it
    before starting the new task.

    :param phase: one of the recorder phases to transition to (see constants.py)
    :return: None
    """
    current_phase = self.recorder_state_manager.get_recorder_agent_phase()
    is_new_phase_valid = self.recorder_state_manager.set_recorder_agent_phase(phase)

    if not is_new_phase_valid:
      logger.error(f'Skipping update recorder agent phase label task for invalid phase {phase}')
    else:
      # cancel the previous phase label update if necessary
      if self.update_phase_task is not None and not self.update_phase_task.done():
        logger.info(f'Cancelling previous task {self.update_phase_task.get_name()}')
        self.update_phase_task.cancel()
        try:
          await self.update_phase_task
        except asyncio.CancelledError:
          logger.info(f'Previous task {self.update_phase_task.get_name()} successfully cancelled')

      try:
        self.update_phase_task = asyncio.create_task(
            self.kube_api_client.update_label_recorder_agent_phase(phase),
            name='update_recorder_agent_phase_task')
      except Exception:  # pylint: disable=broad-except
        logger.exception('Encountered exception while creating task to update recorder_agent_phase '
                         f'label value from {current_phase} to {phase}')
      else:
        self.update_phase_task.add_done_callback(update_phase_task_done_callback)


def update_phase_task_done_callback(task: asyncio.Task) -> None:
  """Handles the result of the recorder phase label update task, logging the result, or, if the task
  ended with an exception, re-raises the exception.

  :param task: the completed recorder agent phase label update asyncio.Task
  :return: None
  """
  logger.debug(f'{task.get_name()} done')
  try:
    # note if the task ended in an exception, it will be re-thrown here.
    result = task.result()
  except asyncio.CancelledError:
    logger.info(f'Task {task.get_name()} was cancelled')
  else:
    logger.debug(f'{task.get_name()} result: {result}')
  # other exceptions will be logged automatically
