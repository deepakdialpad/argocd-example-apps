from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
# pylint: disable=unsubscriptable-object
class SipRoomConnectorSessionResult:
  """Immutable representation of the result of a completed sip room connector session. Includes
  metadata about the session and its success/failure.
  """
  # Unique sip room connector session entity id
  sip_room_connector_session_id: int

  # Timestamp when the session started, or, if there was a failure to start the session, None
  session_start_timestamp: Optional[str] = None

  # Timestamp when the session was stopped, or if there was a failure to stop the session, the
  #  timestamp when we detected or assumed (e.g. after a timeout) the stop failure.
  # Otherwise, if there was no attempt to stop the session at all, this is None.
  session_end_timestamp: Optional[str] = None

  # Boolean value indicating whether the sip room connector client left the conference normally or
  #  there was an unexpected error with the client or another sip room connector component while
  #  the session was ongoing:
  # True if the client appears to exit the conference normally
  # False if the client or a sip room connector agent component fails with an error
  # None if the session was never started at all
  did_client_exit_normally: Optional[bool] = None

  # Boolean value indicating whether the overall sip room connector session was stopped successfully
  #  or not:
  # True if the session was stopped without error
  # False if the session did not terminate successfully for any reason
  # None if the session was never started at all
  did_session_complete_normally: Optional[bool] = None
