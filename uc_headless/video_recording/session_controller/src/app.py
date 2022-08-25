import asyncio
import logging
import sys

import recording_session_controller
import config

from common.lib.session_controller import server

if __name__ == '__main__':
  logging.basicConfig(
      level=getattr(logging, config.LOG_LEVEL.upper()),
      format='%(asctime)s %(name)s [%(levelname)s] %(message)s',
      stream=sys.stdout,
      force=True)
  logging.getLogger('asyncio').setLevel(logging.DEBUG)

  asyncio.run(
      server.main(
          recording_session_controller.main,
          config.POD_NAME,
          config.POD_NAMESPACE,
          recording_dir=config.RECORDING_DIR))
