from functools import wraps
import asyncio
import logging
from typing import Callable, Any, Tuple, Awaitable, Type, Union

logger = logging.getLogger(__name__)


# pylint: disable=unsubscriptable-object
def retry_coroutine(retryable_exceptions: Union[Tuple[Type[Exception], ...], Type[Exception]],
                    tries: int = 3, delay_secs: float = 1, backoff: int = 2,
                    max_delay_secs: float = 60
                    ) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
  """Returns a decorator for retrying coroutines on the provided exception(s) up to :tries times.

  The delay between attempts is determined by:
   min(:delay_secs * :backoff^(i), :max_delay_secs)
  where i is the attempt number, beginning with i=0 after the first attempt.

  :param retryable_exceptions: exception type(s) to retry on
  :param tries: max number of attempts
  :param delay_secs: initial delay between retries
  :param backoff: backoff multiplier
  :param max_delay_secs: maximum delay in seconds – the actual delay between retry attempts maxes
  out at :max_delay_secs, even if the calculated delay based on :backoff, :delay_secs, and the
  attempt # is higher

  :return: retry decorator for coroutine functions
  """
  def retry_decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Wraps a coroutine in a retry handler that behaves according to parameters above.

    :param func: the coroutine function to wrap in retry logic
    :return: the retry coroutine that wraps the input coroutine
    """
    @wraps(func)
    async def retry_function(*args: Any, **kwargs: Any) -> Awaitable[Any]:
      itries, idelay = tries, delay_secs
      while itries > 1:
        try:
          return await func(*args, **kwargs)
        except retryable_exceptions:
          logger.exception(f'Retrying {func.__name__} in {idelay} seconds after exception')
          await asyncio.sleep(idelay)
          itries -= 1
          idelay = min(idelay * backoff, max_delay_secs)
      return await func(*args, **kwargs)
    return retry_function
  return retry_decorator
