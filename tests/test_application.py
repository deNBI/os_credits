"""
Contains test which launch the whole application and simulate incoming data or external
requests against it. They all require the aiohttp_client and influx_client fixture, the
latter even if they do not the InfluxDB functionality since the app does check the
existence of certain databases at startup.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from importlib import reload
from typing import Type

from pytest import fixture

import os_credits.perun.attributesManager
import os_credits.perun.groupsManager
from os_credits.credits.base_models import Metric, TotalUsageMetric, UsageMeasurement
from os_credits.credits.models import BillingHistory
from os_credits.perun.groupsManager import Group

from . import patches
from .conftest import TEST_INITIAL_CREDITS_GRANTED
from .patches import (
    get_attributes,
    get_group_by_name,
    get_resource_bound_attributes,
    set_attributes,
    set_resource_bound_attributes,
)


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
    yield
    # reset internal storage of group values by reloading the module
    reload(patches)


async def test_startup(aiohttp_client, influx_client):
    "Test startup of the application and try to connect to its `/ping` endpoint"
    from os_credits.main import create_app

    app = await create_app()
    client = await aiohttp_client(app)
    resp = await client.get("/ping")
    text = await resp.text()
    assert (200, "Pong") == (resp.status, text), "/ping endpoint failed"


async def test_credits_endpoint(aiohttp_client, influx_client):
    """Test the `/api/credits` endpoint responsible for calculating expected costs per
    hour of given resources"""
    from os_credits.main import create_app
    from os_credits.credits.base_models import TotalUsageMetric

    app = await create_app()
    client = await aiohttp_client(app)

    class _MetricA(
        TotalUsageMetric, measurement_name="metric_a", friendly_name="metric_a"
    ):
        CREDITS_PER_VIRTUAL_HOUR = Decimal("1.3")
        property_description = "Test metric A"

        @classmethod
        def api_information(cls):
            return {
                "type": "str",
                "description": cls.property_description,
                "measurement_name": cls.measurement_name,
            }

    class _MetricB(
        TotalUsageMetric, measurement_name="metric_b", friendly_name="metric_b"
    ):
        CREDITS_PER_VIRTUAL_HOUR = Decimal("1")

    resp = await client.get("/api/credits")
    measurements = await resp.json()
    assert resp.status == 200 and measurements["metric_a"] == {
        "description": "Test metric A",
        "type": "str",
        "measurement_name": "metric_a",
    }, "GET /api/credits returned wrong body"

    resp = await client.post("/api/credits", json={"DefinitelyNotExisting": "test"})
    assert resp.status == 404, "POST /api/credits accepted invalid data"

    resp = await client.post("/api/credits", json={"metric_a": 3, "metric_b": 2})
    assert (
        resp.status == 200 and await resp.json() == 2 * 1 + 3 * 1.3
    ), "POST /api/credits returned wrong result"


test_measurent_name = "whole_run_test_1"
test_group_name = "test_run_1"
test_location_id = 1111
test_group = Group(test_group_name, test_location_id)
test_usage_delta = 5
start_date = datetime.now()


class _TestMetric(
    TotalUsageMetric,
    measurement_name=test_measurent_name,
    friendly_name=test_measurent_name,
):
    CREDITS_PER_VIRTUAL_HOUR = Decimal("1")
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


async def test_regular_run(aiohttp_client, os_credits_offline, influx_client):
    """Tests the complete workflow of the application without any expected errors.

    Incoming data of the InfluxDB are simulated two times to trigger different
    scenarios (first measurement vs second measurement)"""
    from os_credits.main import create_app

    app = await create_app()
    http_client = await aiohttp_client(app)

    # simulate subscription behaviour where every entry gets mirrored to the application
    # /write endpoint
    await influx_client.write(measurement1)
    resp = await http_client.post("/write", data=measurement1.to_lineprotocol())
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
    # let's send the second measurement
    measurement2 = replace(
        measurement1,
        time=start_date + timedelta(days=7),
        value=measurement1.value + test_usage_delta,
    )
    billing_point = BillingHistory(
        measurement=test_group_name,
        time=measurement2.time,
        credits=TEST_INITIAL_CREDITS_GRANTED - test_usage_delta,
        metric_name=measurement2.metric.measurement_name,
        metric_friendly_name=measurement2.metric.friendly_name,
    )
    await influx_client.write(measurement2)
    resp = await http_client.post("/write", data=measurement2.to_lineprotocol())
    assert resp.status == 202
    # wait until request has been processed, indicated by the task finally calling
    # `task_done`
    await app["task_queue"].join()
    await test_group.connect()
    billing_points = [
        p async for p in await influx_client.query_billing_history(test_group_name)
    ]
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
    assert [billing_point] == billing_points


async def test_50_percent_notification(
    aiohttp_client, os_credits_offline, influx_client, smtpserver
):
    """Tests the complete workflow of the application without any expected errors but
    let the group fall under 50% of its granted credits.
    """
    from os_credits.main import create_app
    from os_credits.settings import config
    from os_credits.notifications import HalfOfCreditsLeft

    app = await create_app()
    http_client = await aiohttp_client(app)

    # simulate subscription behaviour where every entry gets mirrored to the application
    # /write endpoint
    await influx_client.write(measurement1)
    resp = await http_client.post("/write", data=measurement1.to_lineprotocol())
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
    # let's send the second measurement
    measurement2 = replace(
        measurement1,
        time=start_date + timedelta(days=7),
        value=measurement1.value + test_usage_delta,
    )
    half_of_granted_credits = Decimal(TEST_INITIAL_CREDITS_GRANTED / 2)
    billing_point = BillingHistory(
        measurement=test_group_name,
        time=measurement2.time,
        credits=half_of_granted_credits - test_usage_delta,
        metric_name=measurement2.metric.measurement_name,
        metric_friendly_name=measurement2.metric.friendly_name,
    )
    test_group.credits_current.value = half_of_granted_credits
    await test_group.save()
    await influx_client.write(measurement2)
    resp = await http_client.post("/write", data=measurement2.to_lineprotocol())
    assert resp.status == 202
    # wait until request has been processed, indicated by the task finally calling
    # `task_done`
    await app["task_queue"].join()
    await test_group.connect()
    billing_points = [
        p async for p in await influx_client.query_billing_history(test_group_name)
    ]
    assert test_group.credits_timestamps.value[
        test_measurent_name
    ] == start_date + timedelta(
        days=7
    ), "Timestamp from measurement was not stored correctly in group"
    # since our CREDITS_PER_VIRTUAL_HOUR are 1
    assert (
        test_group.credits_current.value == half_of_granted_credits - test_usage_delta
    )
    assert [
        billing_point
    ] == billing_points, "Billing history has been stored incorrectly"
    assert len(smtpserver.outbox) == 1, "No notification has been send"


async def test_measurement_from_the_past(
    aiohttp_client, os_credits_offline, influx_client
):
    """Tests the correct behaviour in case of incoming measurements whose timestamps are
    not older than the stored one, which should never happen... But you never know"""
    from os_credits.main import create_app

    app = await create_app()
    http_client = await aiohttp_client(app)

    # simulate subscription behaviour where every entry gets mirrored to the application
    # /write endpoint
    await influx_client.write(measurement1)
    resp = await http_client.post("/write", data=measurement1.to_lineprotocol())
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
    # let's send the second measurement
    measurement2 = replace(measurement1, value=measurement1.value + test_usage_delta)
    await influx_client.write(measurement2)
    resp = await http_client.post("/write", data=measurement2.to_lineprotocol())
    assert resp.status == 202
    # wait until request has been processed, indicated by the task finally calling
    # `task_done`
    await app["task_queue"].join()
    await test_group.connect()
    billing_points = [
        p async for p in await influx_client.query_billing_history(test_group_name)
    ]
    assert (
        test_group.credits_timestamps.value[test_measurent_name] == start_date
    ), "Timestamp of metric was updated although the measurement was invalid"
    assert test_group.credits_current.value == TEST_INITIAL_CREDITS_GRANTED
    assert [] == billing_points


async def test_equal_usage_values(aiohttp_client, os_credits_offline, influx_client):
    """In contrast to :func:`test_regular_run` the second measurement does not have a
    higher usage value than the first one"""
    from os_credits.main import create_app

    app = await create_app()
    http_client = await aiohttp_client(app)

    await influx_client.write(measurement1)
    resp = await http_client.post("/write", data=measurement1.to_lineprotocol())
    assert resp.status == 202
    # wait until request has been processed, indicated by the task finally calling
    # `task_done`
    await app["task_queue"].join()
    await test_group.connect()
    assert (
        TEST_INITIAL_CREDITS_GRANTED
        == test_group.credits_granted.value
        == test_group.credits_current.value
    ), "Initial copy from credits_granted to credits_current failed"
    assert (
        test_group.credits_timestamps.value[test_measurent_name] == start_date
    ), "Timestamp from measurement was not stored correctly in group"
    # let's send the second measurement
    measurement2 = replace(measurement1, time=start_date + timedelta(days=7))
    await influx_client.write(measurement2)
    resp = await http_client.post("/write", data=measurement2.to_lineprotocol())
    assert 202 == resp.status
    # wait until request has been processed, indicated by the task finally calling
    # `task_done`
    await app["task_queue"].join()
    await test_group.connect()
    assert (
        test_group.credits_timestamps.value[test_measurent_name] == start_date
    ), "Timestamp was updated although the measurement did not cause any billing"
    assert (
        test_group.credits_current.value == TEST_INITIAL_CREDITS_GRANTED
    ), "Group has been billed incorrectly, no changes expected"


async def test_no_billing_due_to_rounding(
    aiohttp_client, os_credits_offline, influx_client
):
    """The measurements are all valid but no credits are billed and no timestamps
    updated when the second measurement is processed since the costs of the metric are
    so low they get lost when rounding according to given precision.
    
    Depending on the chosen rounding strategy multiple measurements have to processed
    before their accumulated usage delta leads to a billing."""
    from os_credits.main import create_app
    from os_credits.settings import config

    test_measurent_name = "whole_run_test_cheap_1"

    class _TestMetricCheap(
        TotalUsageMetric,
        measurement_name=test_measurent_name,
        friendly_name=test_measurent_name,
    ):
        # by setting the costs per hour this way we can be sure that the first billings
        # will be rounded to zero
        CREDITS_PER_VIRTUAL_HOUR = config["OS_CREDITS_PRECISION"] * Decimal("10") ** -1
        property_description = "Test Metric 1 for whole run test"

    @dataclass(frozen=True)
    class _TestMeasurementCheap(UsageMeasurement):
        metric: Type[Metric] = _TestMetricCheap

    measurement = _TestMeasurementCheap(
        measurement=test_measurent_name,
        time=start_date,
        location_id=test_location_id,
        project_name=test_group_name,
        value=100,
    )
    test_usage_delta = 1

    app = await create_app()
    http_client = await aiohttp_client(app)

    # simulate subscription behaviour where every entry gets mirrored to the application
    # /write endpoint
    await influx_client.write(measurement)
    resp = await http_client.post("/write", data=measurement.to_lineprotocol())
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
    # with default rounding Strategy ROUND_TO_HALF_EVEN
    # https://en.wikipedia.org/wiki/Rounding#Round_half_to_even
    # the following measurements will not cause any bills
    # choosing the ranges according to the amount of the credits to bill they accumulate
    for i in range(1, 6):
        measurement = replace(
            measurement,
            time=measurement.time + timedelta(days=7),
            value=measurement.value + test_usage_delta,
        )
        await influx_client.write(measurement)
        resp = await http_client.post("/write", data=measurement.to_lineprotocol())
        assert resp.status == 202
        # wait until request has been processed, indicated by the task finally calling
        # `task_done`
        await app["task_queue"].join()
        await test_group.connect()
        billing_points = [
            p async for p in await influx_client.query_billing_history(test_group_name)
        ]
        assert (
            test_group.credits_timestamps.value[test_measurent_name] == start_date
        ), "Timestamp from measurement was updated although no credits were billed"
        # since our CREDITS_PER_VIRTUAL_HOUR are 1
        assert (
            test_group.credits_current.value == TEST_INITIAL_CREDITS_GRANTED
        ), """Credits were billed although this should not have happened given the required
        precision"""
        assert billing_points == []
    # this measurement should to a bill
    measurement = replace(
        measurement,
        time=measurement.time + timedelta(days=7),
        value=measurement.value + test_usage_delta,
    )
    await influx_client.write(measurement)
    resp = await http_client.post("/write", data=measurement.to_lineprotocol())
    assert resp.status == 202
    # wait until request has been processed, indicated by the task finally calling
    # `task_done`
    await app["task_queue"].join()
    await test_group.connect()
    billing_points = [
        p async for p in await influx_client.query_billing_history(test_group_name)
    ]
    expected_credits_left = (
        Decimal(TEST_INITIAL_CREDITS_GRANTED) - config["OS_CREDITS_PRECISION"]
    )
    billing_point = BillingHistory(
        measurement=test_group_name,
        time=measurement.time,
        credits=expected_credits_left,
        metric_name=measurement.metric.measurement_name,
        metric_friendly_name=measurement.metric.friendly_name,
    )
    assert (
        test_group.credits_timestamps.value[test_measurent_name] == measurement.time
    ), "Timestamp from measurement was updated although no credits were billed"
    assert (
        test_group.credits_current.value == expected_credits_left
    ), """Credits were billed although this should not have happened given the required
    precision"""
    assert billing_points == [billing_point]


async def test_missing_previous_values(
    aiohttp_client, os_credits_offline, influx_client
):
    """Tests the complete workflow of the application without any expected errors.

    Incoming data of the InfluxDB are simulated two times to trigger different
    scenarios (first measurement vs second measurement)"""
    from os_credits.main import create_app

    app = await create_app()
    http_client = await aiohttp_client(app)

    # do not store the measurement in the InfluxDB (in contrast to `test_regular_run`)
    # since we want to test the behaviour where the entry corresponding to the timestamp
    # stored inside the group does not exist inside the InfluxDB (anymore)
    resp = await http_client.post("/write", data=measurement1.to_lineprotocol())
    assert resp.status == 202
    # wait until request has been processed, indicated by the task finally calling
    # `task_done`
    await app["task_queue"].join()
    await test_group.connect()
    assert (
        TEST_INITIAL_CREDITS_GRANTED
        == test_group.credits_granted.value
        == test_group.credits_current.value
    ), "Initial copy from credits_granted to credits_current failed"
    assert (
        test_group.credits_timestamps.value[test_measurent_name] == start_date
    ), "Timestamp from measurement was not stored correctly in group"
    # let's send the second measurement
    measurement2 = replace(
        measurement1,
        time=start_date + timedelta(days=7),
        value=measurement1.value + test_usage_delta,
    )
    await influx_client.write(measurement2)
    resp = await http_client.post("/write", data=measurement2.to_lineprotocol())
    assert 202 == resp.status
    # wait until request has been processed, indicated by the task finally calling
    # `task_done`
    await app["task_queue"].join()
    await test_group.connect()
    assert test_group.credits_timestamps.value[
        test_measurent_name
    ] == start_date + timedelta(
        days=7
    ), "Timestamp from measurement was not stored correctly in group"
    assert (
        test_group.credits_current.value == TEST_INITIAL_CREDITS_GRANTED
    ), """Group has been billed although the values of the previous measurement could
    not be retrieved"""
