import json
import resource
from datetime import datetime
from datetime import timezone

from locust import events
from locust import task
from locust.argument_parser import LocustArgumentParser

from producer.mqtt_user import MqttUser

# Set soft open files ulimit
resource.setrlimit(resource.RLIMIT_NOFILE, (999999, 999999))


@events.init_command_line_parser.add_listener
def _(parser: LocustArgumentParser) -> None:
    parser.add_argument(
        "--run-id",
        type=str,
        env_var="RUN_ID",
        help="Set a Test Run ID. Default: YYYY-MM-DD HH:MM:SS",
        required=False,
        default=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


class User(MqttUser):
    def on_start(self) -> None:
        self.connect(
            ca_certs="path/to/ca",
            cert_file="path/to/cert",
            key_file="path/to/key",
        )

    @task
    def send_telemetry(self) -> None:
        self.client.publish("/", self.telemetry.encode(), qos=0)

    def on_stop(self) -> None:
        self.disconnect()

    @property
    def telemetry(self) -> str:
        return json.dumps(
            {
                "info": {
                    "run_id": self.environment.parsed_options.run_id,
                    "timestamp": datetime.now(tz=timezone.utc).timestamp(),
                },
                "context": {"user": repr(self)},
            }
        )
