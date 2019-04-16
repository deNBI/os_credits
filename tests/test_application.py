from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from importlib import reload
from typing import Type

from pytest import fixture

import os_credits.perun.attributesManager
import os_credits.perun.groupsManager
from os_credits import settings
from os_credits.credits.base_models import UsageMeasurement

from .conftest import TEST_INITIAL_CREDITS_GRANTED
from .patches import (
    get_attributes,
    get_group_by_name,
    get_resource_bound_attributes,
    set_attributes,
    set_resource_bound_attributes,
)


@fixture(autouse=True)
def reload_conf_module():
    reload(settings)


@fixture(name="os_credits_offline")
def fixture_os_credits_offline(monkeypatch):

    monkeypatch.setattr(
        os_credits.perun.groupsManager,
        "get_resource_bound_attributes",
        get_resource_bound_attributes,
    )
    monkeypatch.setattr(
        os_credits.perun.groupsManager,
        "set_resource_bound_attributes",
        set_resource_bound_attributes,
    )
    monkeypatch.setattr(
        os_credits.perun.groupsManager, "get_attributes", get_attributes
    )
    monkeypatch.setattr(
        os_credits.perun.groupsManager, "set_attributes", set_attributes
    )
    monkeypatch.setattr(
        os_credits.perun.groupsManager.Group._retrieve_resource_bound_attributes,
        "__defaults__",
        (True,),
    )
    monkeypatch.setattr(
        os_credits.perun.groupsManager.Group.save, "__defaults__", (True,)
    )

    monkeypatch.setattr(
        os_credits.perun.groupsManager, "get_group_by_name", get_group_by_name
    )


async def test_settings(monkeypatch):
    monkeypatch.setenv("OS_CREDITS_PROJECT_WHITELIST", "ProjectA;ProjectB")
    monkeypatch.setenv("OS_CREDITS_PRECISION", "98")
    # necessary to pickup different environment variables
    reload(settings)
    from os_credits.settings import config

    assert config["OS_CREDITS_PROJECT_WHITELIST"] == {
        "ProjectA",
        "ProjectB",
    }, "Comma-separated list was not parsed correctly from environment"
    assert (
        config["OS_CREDITS_PRECISION"] == 98
    ), "Integer value was not parsed/converted correctly from environment"


async def test_startup(aiohttp_client):
    from os_credits.main import create_app

    app = await create_app()
    client = await aiohttp_client(app)
    resp = await client.get("/ping")
    text = await resp.text()
    assert resp.status == 200 and text == "Pong", "/ping endpoint failed"
    # check for correct parsing and processing of settings via env vars


async def test_credits_endpoint(aiohttp_client):
    from os_credits.main import create_app
    from os_credits.credits.base_models import Metric

    app = await create_app()
    client = await aiohttp_client(app)

    class _MetricA(Metric, measurement_name="metric_a", friendly_name="metric_a"):
        CREDITS_PER_VIRTUAL_HOUR = 1.3
        property_description = "Test metric A"

        @classmethod
        def api_information(cls):
            return {
                "type": "str",
                "description": cls.property_description,
                "measurement_name": cls.measurement_name,
            }

    class _MetricB(Metric, measurement_name="metric_b", friendly_name="metric_b"):
        CREDITS_PER_VIRTUAL_HOUR = 1

    resp = await client.get("/credits")
    measurements = await resp.json()
    assert resp.status == 200 and measurements["metric_a"] == {
        "description": "Test metric A",
        "type": "str",
        "measurement_name": "metric_a",
    }, "GET /credits returned wrong body"

    resp = await client.post("/credits", json={"DefinitelyNotExisting": "test"})
    assert resp.status == 404, "POST /credits accepted invalid data"

    resp = await client.post("/credits", json={"metric_a": 3, "metric_b": 2})
    json = await resp.json()
    assert (
        resp.status == 200 and json == 2 * 1 + 3 * 1.3
    ), "POST /credits returned wrong result"


async def test_whole_run(aiohttp_client, os_credits_offline, influx_client):
    from os_credits.main import create_app
    from os_credits.credits.base_models import Metric
    from os_credits.perun.groupsManager import Group

    start_date = datetime.now()

    test_measurent_name = "whole_run_test_1"
    test_group_name = "test_run_1"
    test_location_id = 1111
    test_group = Group(test_group_name, test_location_id)
    test_usage_delta = 5

    reload(settings)

    class _TestMetric(
        Metric, measurement_name=test_measurent_name, friendly_name=test_measurent_name
    ):
        CREDITS_PER_VIRTUAL_HOUR = 1
        property_description = "Test Metric 1 for whole run test"

    @dataclass(frozen=True)
    class _TestMeasurement(UsageMeasurement):
        metric: Type[Metric] = _TestMetric

    measurement1 = _TestMeasurement(
        measurement=test_measurent_name,
        time=start_date,
        location_id=test_location_id,
        project_name=test_group_name,
        value=100,
    )

    app = await create_app()
    client = await aiohttp_client(app)

    # simulate subscription behaviour where every entry gets mirrored to the application
    # /write endpoint
    await influx_client.write(measurement1)
    resp = await client.post("/write", data=measurement1.to_lineprotocol())
    assert resp.status == 202
    # wait until request has been processed, indicated by the task finally calling
    # `task_done`
    await app["task_queue"].join()
    await test_group.connect()
    assert (
        test_group.credits_granted.value
        == test_group.credits_current.value
        == TEST_INITIAL_CREDITS_GRANTED
    ), "Initial copy from credits_granted to credits_current failed"
    assert (
        test_group.credits_timestamps.value[test_measurent_name] == start_date
    ), "Timestamp from measurement was not stored correctly in group"
    measurement2 = replace(
        measurement1,
        time=start_date + timedelta(days=7),
        value=measurement1.value + test_usage_delta,
    )
    await influx_client.write(measurement2)
    resp = await client.post("/write", data=measurement2.to_lineprotocol())
    assert resp.status == 202
    # wait until request has been processed, indicated by the task finally calling
    # `task_done`
    await app["task_queue"].join()
    await test_group.connect()
    assert test_group.credits_timestamps.value[
        test_measurent_name
    ] == start_date + timedelta(
        days=7
    ), "Timestamp from measurement was not stored correctly in group"
    # since our CREDITS_PER_VIRTUAL_HOUR are 1
    assert (
        test_group.credits_current.value
        == TEST_INITIAL_CREDITS_GRANTED - test_usage_delta
    )
