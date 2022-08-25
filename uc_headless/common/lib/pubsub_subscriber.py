from __future__ import annotations
import logging
from typing import TYPE_CHECKING

from google.cloud import pubsub_v1

if TYPE_CHECKING:
  from google.pubsub_v1.types import pubsub

#  pylint: disable=wrong-import-position
from common.lib.session_controller import constants

logger = logging.getLogger(__name__)


def create_subscriber(gcp_project_id: str, pubsub_subscription_id: str
                      ) -> (pubsub_v1.SubscriberClient, str):
  """Creates a Pub/Sub subscriber client and configures it with the subscription name.

  The fully-qualified subscription identifier is built from the :gcp_project_id and
  :pubsub_subscription_id.

  :param gcp_project_id: GCP project id of the current environment
  :param pubsub_subscription_id: unqualified pubsub subscription name, e.g.
  'recorder-agent-subscription'
  :return: the subscriber client and fully-qualified subscription name
  """
  subscriber = pubsub_v1.SubscriberClient()
  # creates a fully qualified identifier in the form:
  #  projects/{project_id}/subscriptions/{subscription_id}
  pubsub_subscription_path = subscriber.subscription_path(gcp_project_id, pubsub_subscription_id)
  return subscriber, pubsub_subscription_path


def pull(subscriber: pubsub_v1.SubscriberClient, pubsub_subscription_path: str,
         max_messages: int = 1) -> pubsub.PullResponse:
  """Wraps :meth:`google.cloud.pubsub_v1.subscriber.client.Client.pull` - makes a single blocking
  pull() call on the provided subscription.

  At most, :max_messages may be returned in the pull response object.

  :param subscriber: subscriber client object
  :param pubsub_subscription_path: fully-qualified subscription identifier
  :param max_messages: maximum number of messages to pull
  :return: a pull response API object containing the messages returned from the call
  """
  return subscriber.pull(subscription=pubsub_subscription_path, max_messages=max_messages)


def pull_messages(subscriber: pubsub_v1.SubscriberClient, pubsub_subscription_path: str,
                  max_messages: int = 1) -> pubsub.PullResponse:
  """Repeatedly makes a blocking pull call on the subscription until a response with at least
  one message is received.

  At most, :max_messages may be returned in the pull response object.

  :param subscriber: subscriber client object
  :param pubsub_subscription_path: fully-qualified subscription identifier
  :param max_messages: maximum number of messages (> 0) to pull
  :return: a pull response API object containing the messages returned in the first non-empty
  response
  """
  subscriber_pull_response = None
  subscriber_pull_attempt = 0
  while not subscriber_pull_response or not subscriber_pull_response.received_messages:
    logger.info(f'Listening for messages on {pubsub_subscription_path} '
                f'(pull attempt {subscriber_pull_attempt})...')
    subscriber_pull_attempt += 1
    subscriber_pull_response = subscriber.pull(
        subscription=pubsub_subscription_path,
        max_messages=max_messages)
  return subscriber_pull_response


def acknowledge_pubsub_messages(subscriber: pubsub_v1.SubscriberClient,
                                pubsub_subscription_path: str, ack_ids: list[str],
                                ack_type: str = constants.PUB_SUB_ACK_TYPE_ACK) -> None:
  """Acknowledge message(s)s corresponding to :ack_ids.

  If the operation is PUB_SUB_ACK_TYPE_ACK, simply acknowledges the message(s).

  If the :ack_type is PUB_SUB_ACK_TYPE_NACK, NACKs the message(s) by setting their ack deadlines to
  0 seconds. As a result, the messages should be redelivered.

  :param subscriber: subscriber client object
  :param pubsub_subscription_path: fully-qualified Pub/Sub subscription id
  :param ack_ids: ack ids for the message(s) to acknowledge
  :param ack_type: PUB_SUB_ACK_TYPE_ACK or PUB_SUB_ACK_TYPE_NACK as defined in constants.py
  :return: None
  """
  if ack_type == constants.PUB_SUB_ACK_TYPE_ACK:
    logger.info(f'ACKing Pub/Sub messages with ack ids: {ack_ids}')
    subscriber.acknowledge(subscription=pubsub_subscription_path, ack_ids=ack_ids)
  elif ack_type == constants.PUB_SUB_ACK_TYPE_NACK:
    logger.info(f'NACKing Pub/Sub messages with ack ids: {ack_ids}')
    # use ack_deadline_seconds=0 param to NACK the messages
    subscriber.acknowledge(
        subscription=pubsub_subscription_path,
        ack_ids=ack_ids,
        ack_deadline_seconds=0)
  else:
    logger.error(f'Unknown acknowledgement type {ack_type} – skipping acknowledgement for ack '
                 f'ids: {ack_ids}')
