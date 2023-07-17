import selectors
import ssl
import time
from contextlib import suppress
from typing import Any
from typing import NamedTuple
from typing import Optional
from uuid import uuid4

from gevent.event import Event
from locust import User
from locust.env import Environment
from paho.mqtt.client import Client
from paho.mqtt.client import MQTT_ERR_CONN_LOST
from paho.mqtt.client import MQTT_ERR_SUCCESS
from paho.mqtt.client import MQTT_ERR_UNKNOWN

REQUEST_TYPE = "MQTT"

RESULT_CODES = {
    1: "incorrect protocol version",
    2: "invalid client identifier",
    3: "server unavailable",
    4: "bad username or password",
    5: "not authorised",
}


class DeviceDisconnected(Exception):
    pass


class LostAcknowledgement(Exception):
    pass


def _generate_mqtt_event_name(event_type: str, qos: int, topic: str) -> str:
    return f"{event_type}:{qos}:{topic}"


def _generate_mqtt_error_event_name(event_type: str, qos: int, topic: str, error_code: int) -> str:
    return f"{event_type}:{error_code}:{qos}:{topic}"


class PublishedMessageContext(NamedTuple):

    qos: int
    topic: str
    start_time: float
    payload_size: int


class MqttUser(User):
    abstract = True
    port = 8883
    client_id = None

    def __init__(self, environment: Environment):
        super().__init__(environment)

        self.client: MqttClient = MqttClient(
            environment=self.environment,
            client_id=self.client_id,
        )

    def connect(self, ca_certs: str, cert_file: str, key_file: str) -> None:
        self.client.tls_set(
            ca_certs=ca_certs,
            certfile=cert_file,
            keyfile=key_file,
            cert_reqs=ssl.CERT_REQUIRED,
            tls_version=ssl.PROTOCOL_TLSv1_2,
            ciphers=None,
        )
        self.client.tls_insecure_set(False)

        self.client.reconnect_delay_set(1, 120)

        self.client.connected.clear()

        if self.client.environment.parsed_options:
            self.client.connect_async(
                host=self.client.environment.host,
                port=self.port,
                keepalive=120,
            )

        self.client.loop_start()

        self.client.connected.wait(240)

    def disconnect(self) -> None:

        if self.client.connected.is_set():

            self.client.disconnect()

        self.client.loop_stop()


class MqttClient(Client):
    def __init__(
        self,
        *args: tuple,
        environment: Environment,
        client_id: Optional[str] = None,
        **kwargs: dict,
    ) -> None:

        self.client_id = client_id or f"locust-{uuid4()}"

        super().__init__(
            *args,
            client_id=self.client_id,
            protocol=4,
            **kwargs,
        )
        self.environment = environment
        self.on_publish = self._on_publish_cb
        self.on_disconnect = self._on_disconnect_cb
        self.on_connect = self._on_connect_cb

        # Device connection status

        self.connected: Event = Event()

        self._published_messages: dict[int, PublishedMessageContext] = {}

    # Override Paho Client method to use selectors instead of select

    def _loop(self, timeout: float = 1.0) -> int:
        if timeout < 0.0:
            raise ValueError("Invalid timeout.")

        sel = selectors.DefaultSelector()

        eventmask = selectors.EVENT_READ

        with suppress(IndexError):
            packet = self._out_packet.popleft()
            self._out_packet.appendleft(packet)
            eventmask = selectors.EVENT_WRITE | eventmask

        pending_bytes = 0
        if hasattr(self._sock, "pending"):
            pending_bytes = self._sock.pending()

        if pending_bytes > 0:
            timeout = 0.0

        try:
            if self._sockpairR is None:
                sel.register(self._sock, eventmask)
            else:
                sel.register(self._sock, eventmask)
                sel.register(self._sockpairR, selectors.EVENT_READ)

            events = sel.select(timeout)

        except TypeError:
            return int(MQTT_ERR_CONN_LOST)
        except ValueError:
            return int(MQTT_ERR_CONN_LOST)
        except Exception:
            return int(MQTT_ERR_UNKNOWN)

        socklist: list[list] = [[], []]

        for key, _event in events:
            if key.events & selectors.EVENT_READ:
                socklist[0].append(key.fileobj)

            if key.events & selectors.EVENT_WRITE:
                socklist[1].append(key.fileobj)

        if self._sock in socklist[0] or pending_bytes > 0:
            rc = self.loop_read()
            if rc or self._sock is None:
                return int(rc)

        if self._sockpairR and self._sockpairR in socklist[0]:
            socklist[1].insert(0, self._sock)
            with suppress(BlockingIOError):
                self._sockpairR.recv(10000)

        if self._sock in socklist[1]:
            rc = self.loop_write()
            if rc or self._sock is None:
                return int(rc)

        sel.close()

        return int(self.loop_misc())

    def _on_publish_cb(
        self,
        client: Client,
        userdata: Any,
        mid: int,
    ) -> None:
        cb_time = time.time()
        try:
            message_context = self._published_messages.pop(mid)

        except KeyError:
            """With QoS 0, the MQTT Broker does not resend the acknowledgement
            on re-connect. Method `.pop(..)` will always find the message mid.

            With QoS 1 or 2, the MQTT Broker resends the acknowledgement
            on re-connect. Method `.pop(..)` will not always find the
            message mid. The mid was popped before the disconnection.
            """

            # Logic implementation depends on QoS
            #
            # self.environment.events.request.fire(
            #     request_type=REQUEST_TYPE,
            #     name=_generate_mqtt_event_name(
            #         "publish", message_context.qos, message_context.topic
            #     ),
            #     response_time=0,
            #     response_length=0,
            #     response=None,
            #     exception=LostAcknowledgement(),
            #     context={
            #         "client_id": self.client_id,
            #         "mid": mid,
            #     },
            # )

        else:
            self.environment.events.request.fire(
                request_type=REQUEST_TYPE,
                name=_generate_mqtt_event_name("publish", message_context.qos, message_context.topic),
                response_time=(cb_time - message_context.start_time) * 1000,
                response_length=message_context.payload_size,
                exception=None,
                response=None,
                context={
                    "client_id": self.client_id,
                    **message_context._asdict(),
                },
            )

    def _on_disconnect_cb(
        self,
        client: Client,
        userdata: Any,
        rc: int,
    ) -> None:
        if rc != 0:
            self.environment.events.request.fire(
                request_type=REQUEST_TYPE,
                name="disconnect",
                response_time=0,
                response_length=0,
                response=None,
                exception=Exception(
                    f"Disconnection FAIL with error {rc} {RESULT_CODES[rc]}"
                    if rc in RESULT_CODES
                    else f"Disconnection FAIL with unknown error: {rc}",
                ),
                context={
                    "client_id": self.client_id,
                },
            )
        else:
            self.environment.events.request.fire(
                request_type=REQUEST_TYPE,
                name="disconnect",
                response_time=0,
                response_length=0,
                response=None,
                exception=None,
                context={
                    "client_id": self.client_id,
                },
            )

        if not client.is_connected():
            self.connected.clear()

    def _on_connect_cb(
        self,
        client: Client,
        userdata: Any,
        flags: dict[str, int],
        rc: int,
    ) -> None:
        if rc != 0:
            self.environment.events.request.fire(
                request_type=REQUEST_TYPE,
                name="connect",
                response_time=0,
                response_length=0,
                response=None,
                exception=Exception(
                    f"Connection FAIL with error {rc} {RESULT_CODES[rc]}"
                    if rc in RESULT_CODES
                    else f"Connection FAIL with unknown error: {rc}",
                ),
                context={
                    "client_id": self.client_id,
                },
            )
        else:
            self.environment.events.request.fire(
                request_type=REQUEST_TYPE,
                name="connect",
                response_time=0,
                response_length=0,
                response=None,
                exception=None,
                context={
                    "client_id": self.client_id,
                },
            )

            self.connected.set()

    def publish(
        self,
        topic: str,
        payload: Optional[bytes] = None,
        qos: int = 1,
        retain: bool = False,
    ) -> None:

        message_context = PublishedMessageContext(
            qos=qos,
            topic=topic,
            start_time=time.time(),
            payload_size=len(payload) if payload else 0,
        )

        if not self.connected.is_set():
            self.environment.events.request.fire(
                request_type=REQUEST_TYPE,
                name=_generate_mqtt_event_name("publish", message_context.qos, message_context.topic),
                response_time=0,
                response_length=0,
                exception=DeviceDisconnected("Device is not Connected."),
                response=None,
                context={
                    "client_id": self.client_id,
                    **message_context._asdict(),
                },
            )

            return

        publish_info = super().publish(topic, payload=payload, qos=qos, retain=retain)

        if publish_info.rc != MQTT_ERR_SUCCESS:
            self.environment.events.request.fire(
                request_type=REQUEST_TYPE,
                name=_generate_mqtt_error_event_name(
                    "publish",
                    message_context.qos,
                    message_context.topic,
                    publish_info.rc,
                ),
                response_time=0,
                response_length=0,
                exception=Exception(
                    f"Publish FAIL with error {publish_info.rc} {RESULT_CODES[publish_info.rc]}"
                    if publish_info.rc in RESULT_CODES
                    else f"Publish FAIL with unknown error: {publish_info.rc}",
                ),
                response=None,
                context={
                    "client_id": self.client_id,
                    **message_context._asdict(),
                },
            )
        else:
            self._published_messages[publish_info.mid] = message_context
