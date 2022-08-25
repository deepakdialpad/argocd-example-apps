from __future__ import annotations
import logging
from typing import TYPE_CHECKING

from kubernetes import client, config as kubernetes_config

from common.lib import wrappers

if TYPE_CHECKING:
  import multiprocessing

kubernetes_config.load_incluster_config()

logger = logging.getLogger(__name__)


class KubeClient:
  def __init__(self, pod_name: str, pod_namespace: str):
    """Kube client constructor.

    :param pod_name: Name of the current k8s Pod.
    :param pod_namespace: Namespace to use for API requests.
    """
    self.api = client.CoreV1Api()
    self.pod_name = pod_name
    self.pod_namespace = pod_namespace

  @wrappers.retry_coroutine(client.ApiException, delay_secs=0.2, tries=8)
  async def kill_self_pod(self) -> multiprocessing.pool.ApplyResult:
    """Sends a k8s API request to terminate the current Pod where the session controller is running.

    Note the request is sent asynchronously with support from the multiprocessing package instead of
    asyncio.
    :return: a multiprocessing.ApplyResult that wraps the API response object
    """
    logger.info(f'Terminating self pod {self.pod_name} in namespace {self.pod_namespace}')
    return self.api.delete_namespaced_pod(
        self.pod_name,
        self.pod_namespace,
        grace_period_seconds=0,
        async_req=True)

  @wrappers.retry_coroutine(client.ApiException, delay_secs=0.2)
  async def update_label_recorder_agent_phase(self, phase: str) -> multiprocessing.pool.ApplyResult:
    """Sends a k8s API request to set the recorder_agent_phase label value for the Pod where the
    session controller is running.

    Note the request is sent asynchronously with support from the multiprocessing package instead of
    asyncio.
    :param phase: one of the recorder agent phases defined in constants.py
    :return: a multiprocessing.ApplyResult that wraps the API response object
    """
    labels_patch_body = {
        'metadata': {
            'labels': {
                'recorder_agent_phase': phase
            }
        }
    }
    logger.info(f'Sending PATCH request for self pod {self.pod_name} in namespace '
                f'{self.pod_namespace}: {labels_patch_body}')
    # default PATCH strategy for the client library is strategic merge, so as intended, this will
    #  only add or replace the recorder_agent_phase label value (not replace the entire set of
    #  labels)
    return self.api.patch_namespaced_pod(
        self.pod_name,
        self.pod_namespace,
        labels_patch_body,
        async_req=True)
