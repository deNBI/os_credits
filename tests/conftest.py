from asyncio import sleep
from logging import getLogger
from typing import Dict, List

from pytest import fixture

from aiohttp.client_exceptions import ClientOSError
from os_credits.influx.client import InfluxDBClient
from pytest_docker_compose import NetworkInfo

pytest_plugins = ["docker_compose"]

# how many credits does every group, created during test runs, have
TEST_INITIAL_CREDITS_GRANTED = 200


@fixture(name="influx_client")
async def fixture_influx_client(
    docker_network_info: Dict[str, List[NetworkInfo]], monkeypatch, loop
):

    influxdb_service = docker_network_info["test_influxdb"][0]
    monkeypatch.setenv("INFLUXDB_HOST", influxdb_service.hostname)
    monkeypatch.setenv("INFLUXDB_DB", "pytest")
    monkeypatch.setenv("INFLUXDB_USER", "")
    monkeypatch.setenv("INFLUXDB_USER_PASSWORD", "")
    influx_client = InfluxDBClient()
    influx_client.db = "pytest"
    getLogger("aioinflux").level = 0
    while True:
        # wait until InfluxDB is ready and up
        try:
            await influx_client.ping()
            break
        except ClientOSError:
            await sleep(1)
    yield influx_client
    await influx_client.close()
