from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
# pylint: disable=unsubscriptable-object
class RecordingResult:
  """Immutable representation of the result of a completed recording. Includes metadata about the
  recording and its success/failure.
  """
  # Unique recording entity id
  recording_id: int

  # Timestamp when the screen recording is started, or, if there was a failure to start the
  #  recording, None
  recording_start_timestamp: Optional[str] = None

  # Timestamp when the screen recording was stopped, or if there was a failure to stop the screen
  #  recording, the timestamp when we detected or assumed (e.g. after a timeout) the stop failure.
  #  None if there was no attempt to stop the recording at all.
  #
  # Note, if ffmpeg failed before the session controller attempts to stop it, this timestamp may
  #  differ significantly from when a recording actually terminates.
  recording_end_timestamp: Optional[str] = None

  # Boolean value indicating whether the recorder client left the conference normally or there
  #  was an unexpected error with the client / browser session while recording:
  #  True if the recorder client appears to exit the conference normally
  #  False if the recorder client or the browser session fails with an error
  #  None if the recording was never started at all
  did_recorder_exit_normally: Optional[bool] = None

  # Boolean value indicating whether the screen recording stopped successfully or not:
  #  True if the screen recording was stopped successfully with exit code 0
  #  False if the screen recording did not terminate successfully for any reason
  #  None if the recording was never started at all
  did_recording_complete_normally: Optional[bool] = None
