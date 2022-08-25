import contextvars
import threading
import logging
from typing import Optional

from google.cloud import storage
from google.cloud.storage.retry import DEFAULT_RETRY
import google.api_core.retry

logger = logging.getLogger(__name__)

CACHE_CONTROL_NO_CACHE = 'no-store, max-age=0'

# per-thread GCS storage client
# pylint: disable=unsubscriptable-object
gcs_client_cv: contextvars.ContextVar[Optional[storage.Client]] = contextvars.ContextVar(
    'gcs_client_cv', default=None)


def set_up_exec_thread_gcs_client() -> None:
  """Initializes a worker thread for GCS operations.

  Sets a thread-local google.cloud.storage.Client object that can be reused across tasks
  submitted to the worker thread.

  :return: None
  """
  client = storage.Client()
  gcs_client_cv.set(client)
  thread_id = threading.get_ident()
  logger.info(f'Created GCS client {client} in thread {thread_id}')


def upload_file(bucket_name: str, result_filename: str, file_path: str,
                upload_id: Optional[str] = None, cache_control: Optional[str] = None,
                timeout_secs: Optional[float] = None,
                retry: Optional[google.api_core.retry.Retry] = DEFAULT_RETRY,
                log_level: int = logging.INFO) -> None:
  """Uploads a local file to a GCS bucket.

  Note, by default the library:
    - performs a resumable upload if needed
    - applies a retry strategy upon upload failures

  :param bucket_name: GCS bucket name
  :param result_filename: unqualified filename to use for the GCS file
  :param file_path: fully-qualified path of the source file to upload
  :param upload_id: a randomly generated id to track the file upload attempt (for debugging purposes
   only)
  :param cache_control: cache control settings to set as metadata on the GCS file
  :param timeout_secs: upload operation timeout
  :param retry: an optional google.api_core.retry.Retry object to configure the retry policy used by
  the client library. if None is provided, the operation will never be retried.
  see https://googleapis.dev/python/storage/latest/retry_timeout.html for more info.
  :param log_level: logging level
  :return: None
  """
  storage_client = gcs_client_cv.get() or storage.Client()
  bucket = storage_client.bucket(bucket_name)
  blob = bucket.blob(result_filename)
  if cache_control is not None:
    blob.cache_control = cache_control
  kwargs = {}
  if timeout_secs is not None:
    kwargs['timeout'] = timeout_secs
  blob.upload_from_filename(file_path, retry=retry, **kwargs)
  log_msg = f'Successfully uploaded file {result_filename} to GCS bucket {bucket_name}'
  if upload_id is not None:
    log_msg = f'[{upload_id}] ' + log_msg
  logger.log(log_level, log_msg)


def create_folder(bucket_name: str, folder_name: str, timeout_secs: Optional[float] = None,
                  retry: Optional[google.api_core.retry.Retry] = DEFAULT_RETRY,
                  log_level: str = logging.INFO) -> None:
  """Creates a GCS folder in the provided bucket.

  Note, by default, the library applies a retry strategy for some types of failures.

  :param bucket_name: GCS bucket name
  :param folder_name: name for the folder
  :param timeout_secs: create operation timeout
  :param retry: an optional google.api_core.retry.Retry object to configure the retry policy used by
  the client library. if None is provided, the operation will never be retried.
  see https://googleapis.dev/python/storage/latest/retry_timeout.html for more info.
  :param log_level: logging level
  :return: None
  """
  storage_client = gcs_client_cv.get() or storage.Client()
  bucket = storage_client.bucket(bucket_name)
  blob = bucket.blob(f'{folder_name}/')
  kwargs = {}
  if timeout_secs is not None:
    kwargs['timeout'] = timeout_secs
  blob.upload_from_string('', retry=retry, **kwargs)
  logger.log(log_level, f'Successfully uploaded folder {folder_name} to GCS bucket {bucket_name}')


def delete_object(bucket_name: str, object_path: str, timeout_secs: Optional[float] = None,
                  retry: Optional[google.api_core.retry.Retry] = DEFAULT_RETRY,
                  log_level: str = logging.INFO) -> None:
  """Deletes an object from the provided bucket.

  Note, by default, the library applies a retry strategy for some types of failures.

  :param bucket_name: GCS bucket name
  :param object_path: path within the bucket to the file or directory
  :param timeout_secs: delete object operation timeout
  :param retry: an optional google.api_core.retry.Retry object to configure the retry policy used by
  the client library. if None is provided, the operation will never be retried.
  see https://googleapis.dev/python/storage/latest/retry_timeout.html for more info.
  :param log_level: logging level
  :return: None
  """
  storage_client = gcs_client_cv.get() or storage.Client()
  bucket = storage_client.bucket(bucket_name)
  blob = bucket.blob(object_path)
  kwargs = {}
  if timeout_secs is not None:
    kwargs['timeout'] = timeout_secs
  blob.delete(retry=retry, **kwargs)
  logger.log(log_level, f'Successfully deleted file {object_path} from GCS bucket {bucket_name}')
