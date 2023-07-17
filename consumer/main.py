import asyncio
import os
from datetime import datetime
from datetime import timezone
from uuid import uuid4

import numpy as np
import pandas as pd
from azure.eventhub import EventData
from azure.eventhub._eventprocessor.common import CloseReason
from azure.eventhub.aio import EventHubConsumerClient
from azure.eventhub.aio import PartitionContext
from azure.eventhub.extensions.checkpointstoreblobaio import BlobCheckpointStore
from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync

from consumer.utils import delay_ms
from consumer.utils import event_info
from consumer.utils import Events
from consumer.utils import logger

events_queue: list[pd.DataFrame] = []


async def write_events(host: str, token: str, org: str) -> None:
    while True:
        if events_queue:
            dfs, events_queue[:] = events_queue.copy(), []

            delays = (
                pd.concat([*dfs])
                .groupby([pd.Grouper(freq="1min"), "run_id", "delay_ms"])
                .agg(
                    count=("delay_ms", "size"),
                )
                .reset_index()
                .rename(columns={"level_0": "_time"})
                .set_index("_time")
            )

            # Add a UUID to not override equal time entries

            delays["uuid"] = str(uuid4())
            delays["delay_ms"] = delays["delay_ms"].astype(str)

            async with InfluxDBClientAsync(
                url=host,
                token=token,
                org=org,
                timeout=60000,
            ) as client:
                await client.write_api().write(
                    "eventhub",
                    record=delays,
                    data_frame_measurement_name="data",
                    data_frame_tag_columns=["run_id", "uuid", "delay_ms"],
                )

        await asyncio.sleep(60)


async def process_batch(data: list[EventData]) -> None:
    events = Events()

    for event in data:
        info = event_info(event)

        if info:
            if event.enqueued_time and event.raw_amqp_message.annotations:
                enqueues_ts: float = event.raw_amqp_message.annotations.get(b"x-opt-enqueued-time", None)

                # Filter Test Data by ID using Grafana Variables

                events.add(
                    id=info["run_id"],
                    dt=event.enqueued_time.astimezone(tz=timezone.utc),
                    delay=delay_ms(enqueues_ts / 1000, info["timestamp"]),
                )

            else:
                logger.warning("Event Enqueued Time not present.")

    df = events.df

    if not df.empty:
        events_queue.append(df)


async def on_event_batch(partition_context: PartitionContext, events: list[EventData]) -> None:
    await process_batch(events)

    await partition_context.update_checkpoint()


async def on_partition_initialize(partition_context: PartitionContext) -> None:
    logger.info(f"Partition: {partition_context.partition_id} has been initialized.")


async def on_partition_close(partition_context: PartitionContext, reason: CloseReason) -> None:
    logger.warning(f"Partition: {partition_context.partition_id} has been closed, reason for closing: {reason}.")


async def on_error(partition_context: PartitionContext, error: Exception) -> None:
    if partition_context:
        logger.error(
            f"An exception: {partition_context.partition_id} occurred during receiving from Partition: {error}."
        )
    else:
        logger.error(f"An exception: {error} occurred during the load balance process.")


async def consume_events(client: EventHubConsumerClient, id: str) -> None:
    async with client:
        await client.receive_batch(
            on_event_batch=on_event_batch,
            on_error=on_error,
            on_partition_close=on_partition_close,
            on_partition_initialize=on_partition_initialize,
            starting_position=datetime.now(tz=timezone.utc),
            partition_id=id,
        )


async def main() -> None:
    checkpoint_store = BlobCheckpointStore.from_connection_string(  # type: ignore
        conn_str=os.environ["STORAGE_CONNECTION_STRING"], container_name="checkpoint"
    )

    client = EventHubConsumerClient.from_connection_string(
        conn_str=os.environ["EVENTHUB_CONNECTION_STRING"],
        consumer_group=os.environ["CONSUMER_GROUP"],
        eventhub_name=os.environ["EVENTHUB_NAME"],
        checkpoint_store=checkpoint_store,
    )

    ids = await client.get_partition_ids()

    tasks = []

    num_partition = int(os.environ["NUM_PARTITION"])
    total_partition = int(os.environ["TOTAL_PARTITION"])

    for id in np.array_split(ids, total_partition)[num_partition]:
        tasks.append(consume_events(client, id))

    await asyncio.gather(
        *tasks,
        write_events(
            os.environ["INFLUXDB_HOST"],
            os.environ["INFLUXDB_TOKEN"],
            os.environ["INFLUXDB_ORG"],
        ),
    )


if __name__ == "__main__":
    logger.info("Start Azure Eventhub Consumer")

    asyncio.run(main())
