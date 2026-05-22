"""
Shared lock for serializing audio stream start/stop calls.

CoreAudio's AUHAL (Hardware Abstraction Layer Audio Unit) raises
kAudioUnitErr_CannotDoInCurrentContext (-10863) when a new stream is
initialized while another stream's render callback is already running.
Holding this lock during sd.InputStream/OutputStream.start() calls
ensures the operations are serialized across all pipeline threads.
"""
import threading

audio_init_lock = threading.Lock()
