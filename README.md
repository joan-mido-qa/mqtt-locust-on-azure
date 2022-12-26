# MQTT Locust on Azure

# Consumer

## Install

```bash
$ pip install -r consumer/requirements.txt
```

## Configuration

Set up the followinf environment variables before starting the consumer:

```bash
$ export STORAGE_CONNECTION_STRING=""
$ export EVENTHUB_CONNECTION_STRING=""
$ export CONSUMER_GROUP=""
$ export EVENTHUB_NAME=""
$ export INFLUXDB_HOST=""
$ export INFLUXDB_TOKEN=""
$ export INFLUXDB_ORG=""
$ export NUM_PARTITION=1 # Consumer partition. ex: [0, 1] -> 1.
$ export TOTAL_PARTITION=2 # Total number of Consumers. ex: 2.
```

### Release

Uncomment the **cd.yaml** workflow to release the consumer docker.

### Deploy

Run the **deploy_consumer.yaml** workflow to deploy the consumer to Azure Container Instances.
