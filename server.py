from capsule import Mixer
import Queue
import tornado.web
import tornado.ioloop
import tornado.template
import tornadio2.server
import threading
import logging
from random import shuffle
from lame import Lame
import multiprocessing
import soundcloud
client = soundcloud.Client(client_id="6325e96fcef18547e6552c23b4c0788c")

prime_limit = 2
frames = 2


def good_track(track):
    return track.streamable and track.duration < 360000 and track.duration > 90000

encoder = None
queue = Queue.Queue()


logging.basicConfig(format="%(asctime)s P%(process)-5d (%(levelname)8s) %(module)16s%(lineno)5d: %(uid)32s %(message)s")
log = logging.getLogger(__name__)


class StreamHandler(tornado.web.RequestHandler):
    listeners = []
    frame = None
    last_send = None

    @classmethod
    def on_new_frame(cls, *args, **kwargs):
        tornado.ioloop.IOLoop.instance().add_callback(lambda: cls.stream_frames(*args, **kwargs))

    @classmethod
    def stream_frames(cls, done):
        while not queue.empty():
            cls.frame = queue.get_nowait()
            remove = []
            for listener in cls.listeners:
                try:
                    listener.write(cls.frame)
                    listener.flush()
                except:
                    remove.append(listener)
            for listener in remove:
                listener.on_finish()
            if remove:
                cls.update_frame_size()

    @classmethod
    def update_frame_size(cls):
        log.info("Listener count: %d", len(cls.listeners))
        encoder.frame_interval = min(16, max(1, int(0.08 * len(cls.listeners))))
        log.info("Setting frame size to: %d frames per send.", encoder.frame_interval)

    @tornado.web.asynchronous
    def get(self):
        log.info("Added new listener at %s", self.request.remote_ip)
        self.set_header("Content-Type", "audio/mpeg")
        if self.frame:
            self.write(self.frame)
        self.flush()
        self.listeners.append(self)
        self.update_frame_size()

    def on_finish(self):
        log.info("Removed listener at %s", self.request.remote_ip)
        self.listeners.remove(self)
        self.update_frame_size()


class FrameBufHandler(tornado.web.RequestHandler):
    def get(self, new_val):
        encoder.frame_interval = int(new_val)


def start():
    at = threading.Thread(target=add_tracks)
    at.daemon = True
    at.start()

    class SocketConnection(tornadio2.conn.SocketConnection):
        __endpoints__ = {}

    application = tornado.web.Application(
        tornadio2.TornadioRouter(SocketConnection).apply_routes([
            # Static assets for local development
            (r"/(favicon.ico)", tornado.web.StaticFileHandler, {"path": "static/img/"}),
            (r"/static/(.*)", tornado.web.StaticFileHandler, {"path": "static/"}),

            # Modifiers
            (r"/framebuf/(\d+)", FrameBufHandler),

            # Main stream
            (r"/stream.mp3", StreamHandler)]),
        socket_io_port=8193,
        enabled_protocols=['websocket', 'xhr-multipart', 'xhr-polling', 'jsonp-polling']
    )

    application.listen(8192)
    tornadio2.server.SocketServer(application)


def add_tracks():
    global encoder
    track_queue = multiprocessing.Queue(1)
    pcm_queue = multiprocessing.Queue()

    encoder = Lame(StreamHandler.on_new_frame, open('testout.mp3', 'w'), oqueue=queue)
    encoder.frame_interval = frames
    encoder.start()
    sent = 0

    m = Mixer(inqueue=track_queue, outqueue=pcm_queue)
    m.start()

    audio_buffer = []
    try:
        offsets = range(0, 40)
        shuffle(offsets)
        for i in offsets:
            tracks = filter(good_track, client.get('/tracks', order='hotness', limit=20, offset=i))
            shuffle(tracks)
            for track in tracks:
                log.info("Adding new track.")
                track_queue.put(track.obj)
                sent += 1
                log.info("Added new track.")
                if sent < 2:
                    continue
                if len(audio_buffer) > 1:
                    log.info("Encoding...")
                    encoder.add_pcm(audio_buffer.pop(0))
                    log.info("Encoded!")
                log.info("Waiting for PCM...")
                audio_buffer.append(pcm_queue.get())
    finally:
        pass

if __name__ == "__main__":
    start()