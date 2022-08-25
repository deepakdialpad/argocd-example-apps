import asyncio
import json
import sys
import logging
import subprocess
import copy
import functools
import signal

import quart
from quart import Quart, request, make_response
from hypercorn.config import Config
from hypercorn.asyncio import serve

import config
import constants

DEFAULT_RESPONSE_HEADERS = {
  'Content-Type': 'application/json'
}
WINDOW_SIZE = f'{config.WINDOW_SIZE_WIDTH}x{config.WINDOW_SIZE_HEIGHT}'
STOP_FFMPEG_TIMEOUT_SECS = 10

browser_agent = Quart(__name__)

browser_agent.termination_event = None

# the current recording process, if any
browser_agent.recording_process = None
# the current SIP room connector session, if any
browser_agent.sip_room_connector_session = None
# browser agent processes
browser_agent.pulseaudio_process = None
browser_agent.xvfb_process = None
browser_agent.browser_process = None
# SIP-room-agent-specific processes
browser_agent.mosquitto_process = None
browser_agent.baresip_process = None

logging.basicConfig(
    level=getattr(logging, 'INFO'),
    format='%(asctime)s %(name)s [%(levelname)s] %(message)s',
    stream=sys.stdout)

logger = logging.getLogger('browser_agent')


class RecordingProcess:
  def __init__(self, ffmpeg, recording_id, recording_file_path):
    self.ffmpeg = ffmpeg
    self.recording_id = recording_id
    self.recording_file_path = recording_file_path


class SipRoomConnectorSession:
  def __init__(self, session_id):
    self.session_id = session_id


@browser_agent.before_serving
async def startup() -> None:
  logger.info('Starting browser agent...')

  if config.RECORDER_AGENT_TYPE == constants.RECORDER_AGENT_TYPE_SIP_ROOM_AGENT:
    logger.info('Starting mosquitto')
    try:
      browser_agent.mosquitto_process = await asyncio.create_subprocess_exec(
          *create_mosquitto_args(config.MOSQUITTO_CONFIG_FILE),
          stdout=asyncio.subprocess.PIPE,
          stderr=asyncio.subprocess.STDOUT)
    except (subprocess.SubprocessError, ValueError) as err:
      logger.exception('Failed to start mosquitto - exiting browser agent')
      raise err

    asyncio.create_task(monitor_process('mosquitto', browser_agent.mosquitto_process))

    logger.info('Starting baresip')
    try:
      browser_agent.baresip_process = await asyncio.create_subprocess_exec(
          *create_baresip_args(config.X_DISPLAY, config.BARESIP_CONFIG_DIR),
          stdout=asyncio.subprocess.PIPE,
          stderr=asyncio.subprocess.STDOUT)
    except (subprocess.SubprocessError, ValueError) as err:
      logger.exception('Failed to start baresip - exiting browser agent')
      raise err

    asyncio.create_task(monitor_process('baresip', browser_agent.baresip_process))

  # start pulseaudio
  logger.info('Starting pulseaudio')
  try:
    browser_agent.pulseaudio_process = await asyncio.create_subprocess_exec(
        *create_pulseaudio_args(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT)
  except (subprocess.SubprocessError, ValueError) as err:
    logger.exception('Failed to start pulseaudio - exiting browser agent')
    raise err

  asyncio.create_task(monitor_process('pulseaudio', browser_agent.pulseaudio_process))

  # start Xvfb
  logger.info('Starting Xvfb')
  try:
    browser_agent.xvfb_process = await asyncio.create_subprocess_exec(
        *create_xvfb_args(config.X_DISPLAY, WINDOW_SIZE, config.X_DISPLAY_DEPTH),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT)
  except (subprocess.SubprocessError, ValueError) as err:
    logger.exception('Failed to start Xvfb - exiting browser agent')
    raise err

  asyncio.create_task(monitor_process('xvfb', browser_agent.xvfb_process))

  # start the browser
  logger.info('Starting Chrome')
  use_fake_device_for_media_stream = config.RECORDER_AGENT_TYPE in [
      constants.RECORDER_AGENT_TYPE_VIDEO,
      constants.RECORDER_AGENT_TYPE_STREAMING
  ]
  try:
    browser_agent.browser_process = await asyncio.create_subprocess_exec(
        *create_chrome_args(
            config.X_DISPLAY,
            config.CHROME_DEBUG_PORT,
            config.WINDOW_SIZE_WIDTH,
            config.WINDOW_SIZE_HEIGHT,
            use_fake_device_for_media_stream=use_fake_device_for_media_stream),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT)
  except (subprocess.SubprocessError, ValueError) as err:
    logger.exception('Failed to start browser - exiting browser agent')
    raise err

  asyncio.create_task(monitor_process('chrome', browser_agent.browser_process))


@browser_agent.after_serving
async def shutdown() -> None:
  logger.info('Shutting down browser agent...')
  # we could manually clean up the browser/xvfb/pulseaudio processes here, but with tini-init,
  # they'll get reaped after this process exits anyway


@browser_agent.route('/browser-agent/ready', methods=['GET'])
async def ready() -> quart.Response:
  """Local readiness probe that indicates whether the browser-agent is ready to start a recording.

  Note this is not a k8s readiness probe.

  :return: the HTTP response with code 200 if the recording is ready, or a 500 code otherwise. if
  a recording is still ongoing, returns information which may help handle this unexpected
  condition gracefully.
  """
  is_sip_room_connector_agent = (config.RECORDER_AGENT_TYPE ==
                                 constants.RECORDER_AGENT_TYPE_SIP_ROOM_AGENT)
  is_browser_running = (browser_agent.browser_process is not None
                        and browser_agent.browser_process.returncode is None)
  is_pulseaudio_running = (browser_agent.pulseaudio_process is not None
                           and browser_agent.pulseaudio_process.returncode is None)
  is_xvfb_running = (browser_agent.xvfb_process is not None
                     and browser_agent.xvfb_process.returncode is None)
  is_mosquitto_running = (browser_agent.mosquitto_process is not None
                          and browser_agent.mosquitto_process.returncode is None)
  is_baresip_running = (browser_agent.baresip_process is not None
                        and browser_agent.baresip_process.returncode is None)
  is_recording = browser_agent.recording_process is not None
  has_sip_room_connector_session = browser_agent.sip_room_connector_session is not None

  if is_recording:
    # the browser agent cannot record because there may be an ongoing recording. this situation is
    #  really unlikely but could occur if e.g. the session controller crashes while a recording is
    #  active
    reason = 'Browser agent is already recording'
    logger.warning(f'{reason}!')
    status_code = 500
    status = 'error'
    body = {
        'recording': {
            'recording_id': browser_agent.recording_process.recording_id,
            'recording_file_path': browser_agent.recording_process.recording_file_path
        }
    }
  elif has_sip_room_connector_session:
    # the browser agent cannot start a new room connector session because there may be one in
    # progress already. this situation is really unlikely but could occur if e.g. the session
    # controller crashes while a room connector session is active
    reason = 'Browser agent already has SIP room connector session'
    logger.warning(f'{reason}!')
    status_code = 500
    status = 'error'
    body = {
        'sip_room_connector_session': {
            'session_id': browser_agent.sip_room_connector_session.session_id
        }
    }
  elif (
      not is_browser_running or
      not is_pulseaudio_running or
      not is_xvfb_running or
      (is_sip_room_connector_agent and not is_mosquitto_running) or
      (is_sip_room_connector_agent and not is_baresip_running)
  ):
    # the browser agent cannot record because one the browser or one of the media-related processes
    #  isn't running
    reason = 'One or more required browser agent processes are not running'
    status_code = 500
    status = 'error'
    body = None
  else:
    status = 'success'
    status_code = 200
    reason = None
    body = None

  response = await _build_response(status_code, status=status, reason=reason, body=body)
  return response


@browser_agent.route('/browser-agent/terminate', methods=['POST'])
async def terminate() -> (dict, int):
  logger.info('Browser agent received termination request')

  body = None
  if browser_agent.recording_process is not None:
    logger.warning('Termination requested but FFMpeg process found!')
    stop_ffmpeg()
    body = {
        'recording': {
            'recording_id': browser_agent.recording_process.recording_id,
            'recording_file_path': browser_agent.recording_process.recording_file_path
        }
    }
  elif browser_agent.sip_room_connector_session is not None:
    logger.warning('Termination requested but SIP room connector session found!')
    body = {
        'sip_room_connector_session': {
            'session_id': browser_agent.sip_room_connector_session.session_id
        }
    }

  # set the termination event. afterwards, the after_serving shutdown hook should run.
  browser_agent.termination_event.set()

  return await _build_response(
      200,
      status='success',
      reason='Initiating termination steps',
      body=body)


@browser_agent.route('/recording/<recording_id>/start', methods=['POST'])
async def start_recording(recording_id: str) -> quart.Response:
  logger.info(f'Received start recording request for recording {recording_id}')
  request_body = json.loads(await request.get_data())
  recording_path = request_body.get('recording_path', None)
  if recording_path is None:
    response = await _build_response(400, status='error', reason='Request missing recording_path')
  else:
    response = await _start_recording(
      recording_id,
      create_ffmpeg_args(WINDOW_SIZE, config.X_DISPLAY, recording_path),
      recording_path)

  return response


@browser_agent.route('/streamingrecording/<recording_id>/start', methods=['POST'])
async def start_streaming_recording(recording_id: str) -> quart.Response:
  logger.info(f'Received start recording request for streaming recording {recording_id}')
  request_body = json.loads(await request.get_data())
  recording_directory = request_body.get('recording_path')
  if recording_directory is None:
    response = await _build_response(400, status='error', reason='Request missing '
                                                                 'recording_path')
  else:
    recording_command = create_ffmpeg_args_hls(
      recording_directory,
      WINDOW_SIZE,
      config.X_DISPLAY,
      hls_playlist_size=18,
      hls_chunk_length_secs=1)
    logger.debug(f'Starting streaming recording with command: {recording_command}')
    response = await _start_recording(recording_id, recording_command)
  return response


async def _start_recording(recording_id: str, recording_command: list[str],
                           recording_path: str = None) -> quart.Response:
  logger.info(f'Received start recording request for recording {recording_id}')
  if browser_agent.recording_process is not None:
    existing_recording_id = browser_agent.recording_process.recording_id
    reason = f'A recording with id {existing_recording_id} is already in progress'
    response = await _build_response(400, status='error', reason=reason)
  else:
    try:
      ffmpeg = subprocess.Popen(
          recording_command,
          stdin=subprocess.PIPE,
          stdout=subprocess.PIPE,
          stderr=subprocess.STDOUT)
    except (subprocess.SubprocessError, ValueError):
      logger.exception(f'Failed to start recording {recording_id}')
      response = await _build_response(500, status='error', reason='Subprocess error')
    else:
      logger.info(f'Started recording {recording_id}')
      browser_agent.recording_process = RecordingProcess(
          ffmpeg,
          recording_id,
          recording_path)
      response = await _build_response(200, status='success')
  return response


@browser_agent.route('/recording/<recording_id>/stop', methods=['POST'])
async def stop_recording(recording_id: str) -> quart.Response:
  return await _stop_recording(recording_id)


@browser_agent.route('/streamingrecording/<recording_id>/stop', methods=['POST'])
async def stop_streaming_recording(recording_id: str) -> quart.Response:
  return await _stop_recording(recording_id)


async def _stop_recording(recording_id: str) -> quart.Response:
  logger.info(f'Received stop recording request for recording {recording_id}')
  if browser_agent.recording_process is None:
    reason = 'No recording process found'
    logger.error(f'Cannot stop recording {recording_id}: {reason})')
    response = await _build_response(404, status='error', reason=reason)
  elif recording_id != browser_agent.recording_process.recording_id:
    reason = f'Request to stop recording with id {recording_id} does not match ongoing recording ' \
             f'with id {browser_agent.recording_process.recording_id}'
    logger.error(f'Cannot stop recording {recording_id}: {reason}')
    response = await _build_response(404, status='error', reason=reason)
  elif browser_agent.recording_process.ffmpeg.poll() is not None:
    exit_code = browser_agent.recording_process.ffmpeg.returncode
    reason = f'FFmpeg already returned with exit code {exit_code}'
    logger.error(f'Cannot stop recording {recording_id}: {reason}')
    browser_agent.recording_process = None
    resp_body = {
      'exit_code': exit_code
    }
    response = await _build_response(500, body=resp_body, status='warn', reason=reason)
  else:
    # FFmpeg appears to be running, so attempt to terminate the process and wait for it to exit.
    exit_code, reason = stop_ffmpeg()
    if exit_code is None:
      response = await _build_response(500, status='error', reason=reason)
    else:
      resp_body = {
        'exit_code': exit_code
      }
      if exit_code == 0:
        response = await _build_response(200, body=resp_body, status='success', reason=reason)
      else:
        response = await _build_response(500, body=resp_body, status='error', reason=reason)

  return response


@browser_agent.route('/sip_room_connector/<session_id>/start', methods=['POST'])
async def start_sip_room_connector_session(session_id: str) -> quart.Response:
  """Start SIP room connector session.

  This handler merely adds to state to track the in-progress session but does do anything to
  initiate the session itself.

  :param session_id: SIP room connector session id
  :return: response object
  """
  logger.info(f'Received start SIP room connector request for session {session_id}')
  if browser_agent.sip_room_connector_session is not None:
    reason = f'A SIP room connector session with id {session_id} is already in progress'
    response = await _build_response(400, status='error', reason=reason)
  else:
    browser_agent.sip_room_connector_session = SipRoomConnectorSession(session_id=session_id)
    response = await _build_response(200, status='success')

  return response


@browser_agent.route('/sip_room_connector/<session_id>/stop', methods=['POST'])
async def stop_sip_room_connector_session(session_id: str) -> quart.Response:
  """Stop SIP room connector session.

  This handler merely removes state for the in-progress session but does not do any steps to stop
  the session itself.

  :param session_id: SIP room connector session id
  :return: response object
  """
  logger.info(f'Received stop SIP room connector request for session {session_id}')
  if browser_agent.sip_room_connector_session is None:
    reason = 'No SIP room connector session found'
    logger.error(f'Cannot stop SIP room connector session {session_id}: {reason})')
    response = await _build_response(404, status='error', reason=reason)
  elif session_id != browser_agent.sip_room_connector_session.session_id:
    reason = f'Request to stop SIP room connector session with id {session_id} does not match ' \
             f'ongoing session with id {browser_agent.sip_room_connector_session.session_id}'
    logger.error(f'Cannot stop SIP room connector session {session_id}: {reason}')
    response = await _build_response(404, status='error', reason=reason)
  else:
    browser_agent.sip_room_connector_session = None
    response = await _build_response(200, status='success')

  return response


async def _build_response(status_code: int, body: dict = None, status: str = None,
                          reason: str = None, headers: dict[str, str] = None) -> quart.Response:
  merged_body = {}
  if status is not None:
    merged_body['status'] = status
  if reason is not None:
    merged_body['reason'] = reason
  if body is not None:
    merged_body.update(body)

  merged_headers = copy.deepcopy(DEFAULT_RESPONSE_HEADERS)
  if headers is not None:
    merged_headers.update(headers)

  response = await make_response(merged_body, status_code)
  response.headers = copy.deepcopy(merged_headers)

  return response


def create_pulseaudio_args() -> list[str]:
  """Builds and returns the Pulse Audio command as a tokenized list of strings.

  Pulse Audio is used to process audio to/from chrome and ffmpeg.

  As opposed to ALSA, Pulse is a userspace audio approach that does not require sound cards nor
  additional kernel drivers/modules to install virtual audio devices.

  :return: pulseaudio options list
  """
  return [
      'pulseaudio',
      '--system',
      '--disallow-exit',
      '--disallow-module-loading',
      # verbose logging
      '--log-level=4',
      '--log-target=stderr'
  ]


def create_xvfb_args(x_display: str, window_size: str, x_display_depth: str) -> list[str]:
  """Builds and returns the Xvfb command as a tokenized list of strings.

  :param x_display: X display Chrome should use, e.g. ":3"
  :param window_size: string representing the video frame dimension in the format '<width>x<height>'
  :param x_display_depth: X display color depth
  :return: Xvfb options list
  """
  # Xvfb serves as an in-memory display server for the headless browser
  return [
      'Xvfb',
      x_display,
      '-screen', '0',
      f'{window_size}x{x_display_depth}',
      '-shmem',
      '-noreset',
      '-ac',
      '+extension', 'RANDR'
  ]


def create_chrome_args(x_display: str, chrome_debug_port: str, window_size_width: str,
                       window_size_height: str, use_fake_device_for_media_stream: bool = True
                       ) -> list[str]:
  """Builds and returns the Chrome command as a tokenized list of strings.

  :param x_display: X display Chrome should use, e.g. ":3"
  :param chrome_debug_port: DevTools protocol debugging port to use
  :param window_size_width: browser window width
  :param window_size_height: browser window height
  :param use_fake_device_for_media_stream: whether the -use-fake-device-for-media-stream flag should
  be used in the Chrome command
  :return: list of options for env/google-chrome commands
  """
  args = [
      # set up a new env to run chrome in, with the display env variable set as follows
      'env',
      f'DISPLAY={x_display}',
      # start chrome
      'google-chrome',
      '--disable-gpu',
      '--no-default-browser-check',
      '--disable-dev-shm-usage',
      '--kiosk',
      '--no-first-run',
      '--alsa-output-device=\'plug:default\'',
      '--noerrdialogs',
      '--autoplay-policy=no-user-gesture-required',
      '--window-position=0,0',
      f'--window-size={window_size_width},{window_size_height}',
      '--start-fullscreen',
      '--disable-software-rasterizer',
      '--disable-notifications',
      f'--remote-debugging-port={chrome_debug_port}',
      '--test-type',
      '--use-fake-ui-for-media-stream',
      '--disable-permissions-api',
      # together, the configured options for --check-for-update-interval and
      # --simulate-outdated-no-au avoid automatic Chrome updates and the associated UI notifications
      '--check-for-update-interval=\'604800\'',
      '--simulate-outdated-no-au=\'Tue, 31 Dec 2099 23:59:59 GMT\'',
      '--no-sandbox',
      'about:blank'
  ]

  if use_fake_device_for_media_stream:
    # the video/streaming recorder clients still grab media devices. Pass this flag to use a mock
    #  device.
    # the clients code should really just avoid using devices for headless video/streaming recorders
    #  in the first place, but this flag is useful as a quick fix to prevent misleading getUserMedia
    #  error logs and to avoid the extra time spent waiting for missing devices to be ready.
    args.append('--use-fake-device-for-media-stream')

  return args


def create_mosquitto_args(mosquitto_config_file: str) -> list[str]:
  """Builds and returns the Chrome command as a tokenized list of strings.

  :param mosquitto_config_file: mosquitto config file path
  :return: list of options for mosquitto command
  """
  return [
      'mosquitto',
      '-c', mosquitto_config_file
  ]


def create_baresip_args(x_display: str, baresip_config_dir: str) -> list[str]:
  """Builds and returns the baresip command as a tokenized list of strings.

  :param x_display: X display baresip should use, e.g. ":3"
  :param baresip_config_dir: directory path for baresip config files
  :return: list of options for baresip command
  """
  return [
      'env',
      f'DISPLAY={x_display}',
      # start baresip
      'baresip',
      '-f', baresip_config_dir
  ]


def create_ffmpeg_args(window_size: str, x_display: str, recording_file_path: str) -> list[str]:
  """Builds and returns the ffmpeg command as a tokenized list of strings.

  Options and any associated value(s) are separate entries in the returned list.
  e.g. the option/arg '-loglevel error' is expressed in the result list as:
  [..., '-loglevel', 'error', ...]

  :param window_size: string representing the video frame dimension in the format '<width>x<height>'
  :param x_display: string representing the X display to use, e.g. ':3'
  :param recording_file_path: absolute file path where the video recording file should
  be stored in the local volume
  :return: ffmpeg options list
  """
  return [
    'ffmpeg',
    # 1) global params
    # overwrite output files
    '-y',
    '-loglevel', 'error',
    # 2) video input params
    # video frame dimensions (format: <width>x<height>)
    '-video_size', window_size,
    '-framerate', '30',
    # video input type: screen recording via x11
    '-f', 'x11grab',
    # don't draw mouse pointer
    '-draw_mouse', '0',
    # video input (X display number string, e.g. ':3')
    '-i', x_display,
    # 3) audio input params
    # audio input type: pulse audio
    '-f', 'pulse',
    # fixed channels # of channels (2)
    '-ac', '2',
    # audio input source: pulse default
    '-i', 'default',
    # 4) output params
    # experimental strictness mode (so we can use the native FFmpeg AAC audio encoder,
    #  which is considered experimental)
    '-strict', '-2',
    # video codec: H.264/libx264 encoder
    '-c:v', 'libx264',
    # quality level for "constant quality mode"
    '-crf', '23',
    # encoding preset
    '-preset', 'veryfast',
    # pixel format
    # yuv420p - YUV color space with 4:2:0 chroma subsampling and planar color alignment
    '-pix_fmt', 'yuv420p',
    # recording output file path
    recording_file_path
  ]


def create_ffmpeg_encode_output_args_hls(index: int, video_frame_height: int,
                                         video_frame_width: int, max_bitrate_kbps: int,
                                         hls_chunk_length_secs: float) -> list[str]:
  """Builds and returns ffmpeg x264 output encoding params for HLS as a tokenized list of strings.

  :param index: ffmpeg variant stream index
  :param video_frame_height: video frame height dimension
  :param video_frame_width: video frame width dimension
  :param max_bitrate_kbps: maximum bitrate
  :param hls_chunk_length_secs: target duration of an HLS segment
  :return: list of command args
  """
  return [
      '-map', '1:a:0',
      f'-c:a:{index}', 'aac',
      f'-b:a:{index}', '128k',
      f'-r:a:{index}', '44100',
      '-map', '0:v:0',
      f'-c:v:{index}', 'libx264',
      '-crf', '23',
      f'-s:v:{index}', f'{video_frame_width}x{video_frame_height}',
      # encoding preset - determines a collection of options that provide a certain encoding speed
      #  to compression ratio. see http://trac.ffmpeg.org/wiki/Encode/H.264 for more info
      # use ultrafast - the fastest possible preset
      '-preset', 'veryfast',
      # tune preset for fast encoding and low latency streams
      # see http://trac.ffmpeg.org/wiki/Encode/H.264 for more info
      '-tune', 'zerolatency',
      # maximum video bitrate (see https://trac.ffmpeg.org/wiki/Limiting%20the%20output%20bitrate)
      f'-maxrate:v:{index}', f'{max_bitrate_kbps}k',
      # buffer size - used to calculate and correct average bitrate
      # lower values improve encode times but impacts video quality
      f'-bufsize:v:{index}', f'{max_bitrate_kbps / 2}k',
      # sets the max number of b frames used in a GOP
      f'-bf:v:{index}', '2',
      # GOP size - creates a key frame every hls_chunk_secs
      # smaller GOP values reduce video quality but needed to get the correct HLS segment slice
      '-g', f'{int(hls_chunk_length_secs * 30)}',
      '-keyint_min', f'{int(hls_chunk_length_secs * 30)}',
      # disable scene detection for a constant key frame every hls_chunk_secs
      '-sc_threshold', '0',
      # pixel format
      # use yuv420p - YUV color space with 4:2:0 chroma subsampling and planar color alignment
      '-pix_fmt', 'yuv420p'
  ]


def create_ffmpeg_args_hls(recording_directory: str, window_size: str, x_display: str,
                           hls_playlist_size: int, hls_chunk_length_secs: float) -> list[str]:
  """Builds and returns the ffmpeg command as a tokenized list of strings.

  Options and any associated value(s) are separate entries in the returned list.
  e.g. the option/arg '-loglevel error' is expressed in the result list as:
  [..., '-loglevel', 'error', ...]

  :param recording_directory: absolute local path where the primary playlist index should be stored
  :param window_size: string representing the video frame dimension in the format '<width>x<height>'
  :param x_display: string representing the X display to use, e.g. ':3'
  be stored in the local volume
  :param hls_playlist_size: maximum number of entries in the HLS playlist
  :param hls_chunk_length_secs: target length of an HLS segment
  :return: ffmpeg options list
  """

  # global params
  global_params = [
      'ffmpeg',
      # overwrite output files
      '-y',
      '-loglevel', 'error'
  ]

  # input params for 240p, 480p, and 720p streams
  input_params = [
      # 2) video input params
      # video frame dimensions (format: <width>x<height>)
      '-video_size', window_size,
      '-framerate', '30',
      # video input type: screen recording via x11
      '-f', 'x11grab',
      # don't draw mouse pointer
      '-draw_mouse', '0',
      # video input (X display number string, e.g. ':3')
      '-i', x_display,
      # 3) audio input params
      # audio input type: pulse audio
      '-f', 'pulse',
      # fixed channels # of channels (2)
      '-ac', '2',
      # audio input source: pulse default
      '-i', 'default'
  ]

  encode_240p_output_params = create_ffmpeg_encode_output_args_hls(
      index=0,
      video_frame_width=426,
      video_frame_height=240,
      max_bitrate_kbps=300,
      hls_chunk_length_secs=hls_chunk_length_secs)

  encode_480p_output_params = create_ffmpeg_encode_output_args_hls(
      index=1,
      video_frame_width=854,
      video_frame_height=480,
      max_bitrate_kbps=800,
      hls_chunk_length_secs=hls_chunk_length_secs)

  encode_720p_output_params = create_ffmpeg_encode_output_args_hls(
      index=2,
      video_frame_width=1280,
      video_frame_height=720,
      max_bitrate_kbps=1500,
      hls_chunk_length_secs=hls_chunk_length_secs)

  hls_params = [
      '-f', 'hls',
      '-var_stream_map', 'v:0,a:0 v:1,a:1 v:2,a:2',
      '-master_pl_name', 'index.m3u8',
      '-hls_time', str(hls_chunk_length_secs),
      '-hls_list_size', str(hls_playlist_size),
      '-hls_flags', 'delete_segments',
      # recording output file path
      '-hls_segment_filename', f'{recording_directory}/v%v/index_%03d.ts',
      f'{recording_directory}/v%v/index.m3u8'
  ]

  output_params = encode_240p_output_params + encode_480p_output_params + encode_720p_output_params
  return global_params + input_params + output_params + hls_params


def stop_ffmpeg(timeout_secs: float = STOP_FFMPEG_TIMEOUT_SECS) -> (int, str):
  """Gracefully stops the FFmpeg process and reads any remaining data from the stdout pipe, if
  FFmpeg is running.

  If it's detected that FFmpeg is not running, just returns None. Otherwise, sends 'q' to STDIN to
  stop the process. Blocks until stdout data is read and the process terminates.

  If FFmpeg was successfully stopped, or if the process had already exited, sets
  browser_agent.recording_process to None.

  :param timeout_secs - timeout for subprocess.communicate() call
  :return: a tuple with:
  1) The process exit code, if the process was stopped successfully within the timeout, else, None.
  2) Reason string describing whether or not FFmpeg was stopped successfully, and if not, why.
  """
  exit_code = None
  if browser_agent.recording_process is None:
    reason = 'No recording process found'
    logger.warning(f'Cannot stop FFmpeg: {reason}')
  elif browser_agent.recording_process.ffmpeg.poll() is not None:
    exit_code = browser_agent.recording_process.ffmpeg.returncode
    reason = f'The recording process already returned with exit code {exit_code}'
    logger.warning(f'Cannot stop FFmpeg for recording id '
                   f'{browser_agent.recording_process.recording_id}: {reason}')
    browser_agent.recording_process = None
  else:
    try:
      outs, _ = browser_agent.recording_process.ffmpeg.communicate(
        b'q',
        timeout=timeout_secs)
    except (subprocess.SubprocessError, ValueError) as err:
      if isinstance(err, subprocess.TimeoutExpired):
        reason = f'Timed out after {timeout_secs} seconds while waiting for ' \
                 f'FFmpeg to exit'
      elif issubclass(type(err), subprocess.SubprocessError):
        reason = 'Subprocess error while attempting to stop FFmpeg'
      else:
        reason = 'Error while attempting to terminate FFmpeg'
      logger.exception(f'Cannot stop FFmpeg for recording id: {reason}')

      logger.info('Forcibly killing FFmpeg')
      # send SIGKILL
      browser_agent.recording_process.ffmpeg.kill()
    else:
      exit_code = browser_agent.recording_process.ffmpeg.returncode
      stdout = outs.decode('utf-8')
      if exit_code == 0:
        reason = 'FFmpeg exited normally'
        logger.info(f'Successfully stopped recording with id '
                    f'{browser_agent.recording_process.recording_id}')
        logger.info(f'FFmpeg stdout: {stdout}')
      else:
        reason = f'FFmpeg terminated with non-zero exit code {exit_code}'
        logger.error(f'Recording with id {browser_agent.recording_process.recording_id} was '
                     f'stopped but {reason}')
        logger.error(f'FFmpeg stdout: {stdout}')
    finally:
      browser_agent.recording_process = None

  return exit_code, reason


async def monitor_process(name: str, proc: asyncio.subprocess.Process) -> int:
  """Monitors a process until completion.

  Reads/logs each line of output from stdout as soon as it's available. Each log line is prefixed
  with the process name like: '[name] '.

  ***Requires that the process is spawned with arguments stdout=asyncio.subprocess.PIPE and
  stderr=asyncio.subprocess.STDOUT***

  :param name: simple name for the process.
  :param proc: a subprocess
  :return: the process exit code
  """
  while proc.returncode is None:
    # read a single line of output
    try:
      line = await asyncio.wait_for(proc.stdout.readline(), timeout=1.0)
    except asyncio.TimeoutError:
      line = None
    if line:
      decoded_line = line.decode('utf-8').rstrip()
      if decoded_line:
        logger.info(f'[{name}] {decoded_line}')

  if proc.returncode != 0:
    logger.error(f'Monitored process {name} terminated with non-zero exit code {proc.returncode}!')
  else:
    logger.info(f'Monitored process {name} terminated with exit code {proc.returncode}')

  # grab any remaining data in stdout that wasn't read above
  stdout, _ = await proc.communicate()
  stdout = stdout.decode('utf-8').rstrip()
  if stdout:
    logger.info(f'Printing remaining output for monitored process {name}:')
    for line in stdout.splitlines():
      if line:
        logger.info(f'[{name}] {line}')

  return proc.returncode


async def graceful_termination_requested() -> None:
  """Handler for graceful termination of this browser agent.

  :return: None
  """
  # Merely log the event, since the responsibility of managing the lifecycle is delegated to the
  # session controller. Instead, the browser agent container should clean up and exit when the
  # session controller tells it to.
  logger.info(f'Received graceful termination signal {signal.SIGTERM}')


async def main() -> None:
  # add graceful termination handler on the event loop
  loop = asyncio.get_running_loop()
  termination_event = asyncio.Event()
  browser_agent.termination_event = termination_event
  term_fn = functools.partial(graceful_termination_requested)
  loop.add_signal_handler(
      signal.SIGTERM,
      lambda: asyncio.create_task(term_fn()))

  # start the HTTP server
  hypercorn_config = Config()
  hypercorn_config.bind = ['0.0.0.0:5000']

  await serve(
      browser_agent,
      hypercorn_config,
      shutdown_trigger=termination_event.wait)


if __name__ == '__main__':
  asyncio.run(main())
