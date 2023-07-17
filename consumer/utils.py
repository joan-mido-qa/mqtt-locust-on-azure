import gzip
import json
import logging
from datetime import datetime

import pandas as pd
from azure.eventhub import EventData

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger("Consumer")

logger.setLevel(level=logging.INFO)


class Events:
    def __init__(self) -> None:
        self.dts: list[datetime] = []
        self.delays: list[int] = []
        self.ids: list[str] = []

    def add(self, dt: datetime, delay: int, id: str) -> None:
        # Round the delay time (ms) to improve memory performance

        if delay < 100:
            rounded_delay = round(delay)

        elif delay < 1000:
            rounded_delay = round(delay, -1)

        elif delay < 10000:
            rounded_delay = round(delay, -2)

        else:
            rounded_delay = round(delay, -3)

        self.dts.append(dt)
        self.delays.append(rounded_delay)
        self.ids.append(id)

    @property
    def df(self) -> pd.DataFrame:
        return pd.DataFrame(
            data={"run_id": self.ids, "delay_ms": self.delays},
            index=pd.DatetimeIndex(self.dts),
        )


def delay_ms(event_ts: float, telemetry_ts: float) -> int:
    return int((event_ts - telemetry_ts) * 1000)


def event_info(event: EventData) -> dict | None:
    try:
        body = event.body_as_json()

    except Exception:
        try:
            body = json.loads(gzip.decompress(list(event.body)[0]))  # type: ignore

        except Exception:
            logger.warning(f"Event could not be processed: {event.body}")

            return None

    # "info" key has telemetry timestamp and test run ID

    try:
        return body["info"]  # type: ignore

    except Exception:
        logger.warning(f"Unexpected Event: {body}")

        return None
