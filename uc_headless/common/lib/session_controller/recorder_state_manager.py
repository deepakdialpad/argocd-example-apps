import logging
from typing import Optional

from common.lib.session_controller import constants

logger = logging.getLogger(__name__)


class RecorderStateManager:
  def __init__(self) -> None:
    self.recorder_agent_phase = None

  def set_recorder_agent_phase(self, phase: str) -> bool:
    """Sets the phase if valid.
    :param phase: one of the recorder agent phases defined in constants.py
    :return: boolean indicating whether the phase was set or not
    """
    if phase not in constants.RECORDER_AGENT_PHASES:
      logger.error(f'Recorder agent phase {phase} unrecognized')
      return False
    else:
      logger.info(f'Recorder agent phase transition from {self.recorder_agent_phase} to {phase}')
      self.recorder_agent_phase = phase
      return True

  def get_recorder_agent_phase(self) -> Optional[str]:  # pylint: disable=unsubscriptable-object
    """Get the operational phase of the recorder agent.
    :return: one of the recorder agent phases defined in constants.py, or None if the phase has not
    been initialized yet
    """
    return self.recorder_agent_phase

  def is_live(self) -> bool:
    """Returns boolean value indicating the liveness of the recorder agent.
    :return: liveness value
    """
    return self.recorder_agent_phase not in [constants.RECORDER_AGENT_PHASE_COMPLETED]


state_manager = RecorderStateManager()
