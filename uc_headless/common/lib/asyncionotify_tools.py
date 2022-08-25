from __future__ import annotations
import asyncio
from collections.abc import Callable
from typing import Coroutine, Optional
import logging

import asyncinotify

logger = logging.getLogger(__name__)


async def watch_path(
    path: str, mask: asyncinotify.Mask,
    event_handler: Callable[[asyncinotify.Event, Optional[asyncio.Task]], Coroutine],
    log_level: int = logging.DEBUG) -> None:
  """Watches a filesystem object for inotify events and runs the provided callback.

  :param path: absolute path to a file or directory to watch for inotify events on
  :param mask: asyncionotify bitmask describing the set of inotify events to watch for
  :param event_handler: async callback called on each event. if there was a previous,
  possibly incomplete, event handler task for an event at the same file path, is also passed as an
  argument to the handler. if the previous task was known to be completed, it may not be passed to
  the handler.
  :param log_level: logging level for events
  :return: None
  """
  logger.info(f'Watching inotify events at path {path}')
  # dictionary to track most recent event handler task by task name
  current_tasks = {}
  with asyncinotify.Inotify() as inotify:
    inotify.add_watch(path, mask)
    async for event in inotify:
      logger.log(log_level, event)
      event_path = str(event.path)
      existing_task = current_tasks.get(event_path)
      new_task = asyncio.create_task(event_handler(event, existing_task), name=event_path)
      current_tasks[event_path] = new_task
      current_tasks = {
          task_name: task for (task_name, task) in current_tasks.items() if not task.done()
      }
