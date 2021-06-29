import time

import pytest

from infection_monkey.telemetry.base_telem import BaseTelem
from infection_monkey.telemetry.batchable_telem_mixin import BatchableTelemMixin
from infection_monkey.telemetry.i_batchable_telem import IBatchableTelem
from infection_monkey.telemetry.messengers.batching_telemetry_messenger import (
    BatchingTelemetryMessenger,
)

PERIOD = 0.001


def release_GIL():
    time.sleep(PERIOD)


def advance_clock_to_next_period(monkeypatch):
    patch_time(monkeypatch, time.time() + (PERIOD * 1.01))


def patch_time(monkeypatch, new_time: float):
    monkeypatch.setattr(time, "time", lambda: new_time)


class NonBatchableTelemStub(BaseTelem):
    telem_category = "NonBatchableTelemStub"

    def send(self, log_data=True):
        raise NotImplementedError

    def get_data(self) -> dict:
        return {"1": {"i": "a", "ii": "b"}}

    def __eq__(self, other):
        return self.get_data() == other.get_data() and self.telem_category == other.telem_category


class BatchableTelemStub(BatchableTelemMixin, BaseTelem, IBatchableTelem):
    def __init__(self, value, telem_category="cat1"):
        self._telemetry_entries.append(value)
        self._telem_category = telem_category

    @property
    def telem_category(self):
        return self._telem_category

    def send(self, log_data=True):
        raise NotImplementedError

    def get_data(self) -> dict:
        return {"entries": self._telemetry_entries}


@pytest.fixture
def batching_telemetry_messenger(monkeypatch, telemetry_messenger_spy):
    patch_time(monkeypatch, 0)
    return BatchingTelemetryMessenger(telemetry_messenger_spy, period=0.001)


def test_send_immediately(batching_telemetry_messenger, telemetry_messenger_spy):
    telem = NonBatchableTelemStub()

    batching_telemetry_messenger.send_telemetry(telem)
    release_GIL()

    assert len(telemetry_messenger_spy.telemetries) == 1
    assert telemetry_messenger_spy.telemetries[0] == telem


def test_send_telem_batch(monkeypatch, batching_telemetry_messenger, telemetry_messenger_spy):
    expected_data = {"entries": [1, 2]}
    telem1 = BatchableTelemStub(1)
    telem2 = BatchableTelemStub(2)

    batching_telemetry_messenger.send_telemetry(telem1)
    batching_telemetry_messenger.send_telemetry(telem2)
    release_GIL()

    assert len(telemetry_messenger_spy.telemetries) == 0
    advance_clock_to_next_period(monkeypatch)
    release_GIL()

    assert len(telemetry_messenger_spy.telemetries) == 1
    assert telemetry_messenger_spy.telemetries[0].get_data() == expected_data


def test_send_different_telem_types(
    monkeypatch, batching_telemetry_messenger, telemetry_messenger_spy
):
    telem1 = BatchableTelemStub(1, "cat1")
    telem2 = BatchableTelemStub(2, "cat2")

    batching_telemetry_messenger.send_telemetry(telem1)
    batching_telemetry_messenger.send_telemetry(telem2)
    release_GIL()

    assert len(telemetry_messenger_spy.telemetries) == 0
    advance_clock_to_next_period(monkeypatch)
    release_GIL()

    assert len(telemetry_messenger_spy.telemetries) == 2
    assert telemetry_messenger_spy.telemetries[0] == telem1
    assert telemetry_messenger_spy.telemetries[1] == telem2


def test_send_two_batches(monkeypatch, batching_telemetry_messenger, telemetry_messenger_spy):
    telem1 = BatchableTelemStub(1, "cat1")
    telem2 = BatchableTelemStub(2, "cat1")

    batching_telemetry_messenger.send_telemetry(telem1)
    advance_clock_to_next_period(monkeypatch)
    release_GIL()

    batching_telemetry_messenger.send_telemetry(telem2)
    release_GIL()
    assert len(telemetry_messenger_spy.telemetries) == 1

    advance_clock_to_next_period(monkeypatch)
    release_GIL()

    assert len(telemetry_messenger_spy.telemetries) == 2
    assert telemetry_messenger_spy.telemetries[1] == telem2


def test_send_remaining_telem_after_stop(monkeypatch, telemetry_messenger_spy):
    patch_time(monkeypatch, 0)
    batching_telemetry_messenger = BatchingTelemetryMessenger(
        telemetry_messenger_spy, period=PERIOD
    )

    expected_data = {"entries": [1]}
    telem = BatchableTelemStub(1)

    batching_telemetry_messenger.send_telemetry(telem)
    release_GIL()

    assert len(telemetry_messenger_spy.telemetries) == 0
    del batching_telemetry_messenger

    assert len(telemetry_messenger_spy.telemetries) == 1
    assert telemetry_messenger_spy.telemetries[0].get_data() == expected_data
