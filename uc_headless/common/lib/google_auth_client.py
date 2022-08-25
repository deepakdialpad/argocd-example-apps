from typing import Optional
import logging
import time

import google.auth
import google.auth.credentials
from google.auth.exceptions import DefaultCredentialsError

logger = logging.getLogger(__name__)

DEFAULT_CREDENTIALS_RETRIES = 15


# pylint: disable=unsubscriptable-object
def get_gcp_credentials_and_project_id(num_retries: int = DEFAULT_CREDENTIALS_RETRIES,
                                       retry_delay_secs: float = 0.2
                                       ) -> (google.auth.credentials.Credentials, Optional[str]):
  """Fetches the default GCP credentials and project id from the current environment via the GCP
  client library.

  Applies a retry strategy for DefaultCredentialsErrors, making a blocking sleep call between
  attempts.

  :param num_retries: max number of retries for a google.auth.credentials.DefaultCredentialsError
  exception
  :param retry_delay_secs: sleep duration between retry attempts
  :return: the GCP credentials object and an optional GCP project ID string – returns None for the
  project id if it cannot be inferred
  """
  fetch_credentials_attempts = 0
  credentials, gcp_project_id = None, None
  while not credentials:
    fetch_credentials_attempts += 1
    try:
      credentials, gcp_project_id = google.auth.default()
    except DefaultCredentialsError as err:
      # with workload identity, requests for credentials during the first few seconds
      #  of a pod's life may fail while the GKE metadata server is starting up. see:
      #  https://cloud.google.com/kubernetes-engine/docs/how-to/workload-identity#limitations
      if fetch_credentials_attempts < num_retries:
        logger.exception(err)
        logger.warning(
            'Unable to fetch GCP credentials – this may be due to a documented race condition with '
            'Workload Identity that can occur during the first few seconds of a Pod\'s life. '
            'Retrying...')
      else:
        raise
    time.sleep(retry_delay_secs)
  return credentials, gcp_project_id
