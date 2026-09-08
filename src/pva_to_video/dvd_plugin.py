import logging
from asyncio import sleep
from threading import Condition
from time import monotonic

from davidia.models.messages import ImageData, ImageMessage, SourceConfigModel
from davidia.server.plugins import SourcePlugin
from numpy import ndarray

from .pvaccess import PVStream, get_stream

logger = logging.getLogger("main")


class PVASourceConfig(SourceConfigModel):
    pv_name: str
    min_period: float


class PVASourcePlugin(SourcePlugin):
    name = "EPICS PV"

    def __init__(self, pv_name: str, min_period: float):
        self.pv_name = pv_name
        self.min_period = min_period
        self.stream: PVStream | None = None
        self.data_ready = Condition()
        self.data: ndarray | None = None
        self.next_send_time = 0.0

    def description(self):
        return f"generates a 2D array from {self.pv_name}"

    async def start(self):
        if self.stream is None:
            self.stream = await get_stream(self.pv_name, self.callback)
            logger.debug("Started stream from %s: %s", self.pv_name, self.stream)
        await super().start()

    def callback(self, value):
        """
        Called by PVStream on every new value
        """
        self.data = value
        with self.data_ready:
            self.data_ready.notify_all()

    async def has_next(self):
        # rate limit and wait for period
        now = monotonic()
        delay = self.next_send_time - now
        if delay > 0:
            await sleep(delay)

        self.next_send_time = monotonic() + self.min_period
        with self.data_ready:
            return self.data_ready.wait()

    def next_data(self):
        if self.data is None:
            return None
        data = ImageData(values=self.data)
        return ImageMessage(plot_id=self.plot_id, im_data=data)
