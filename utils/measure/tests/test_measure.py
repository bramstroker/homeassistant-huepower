import csv
import logging
import os
import sys
from collections.abc import Iterator
from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest
from inquirer import events
from inquirer.render import ConsoleRender
from measure.config import MeasureConfig
from measure.const import PROJECT_DIR, MeasureType
from measure.controller.charging.const import ChargingDeviceType
from measure.measure import Measure
from measure.powermeter.dummy import DummyPowerMeter
from measure.util.measure_util import MeasureUtil
from readchar import key


class EventGenerator:
    def __init__(self, *args: str) -> None:
        self.iterator: Iterator[str] = args.__iter__()

    def next(self) -> events.KeyPressed:
        return events.KeyPressed(next(self.iterator))


def event_factory(*args: str) -> EventGenerator:
    return EventGenerator(*args)


def test_wizard(mock_config_factory) -> None:  # noqa: ANN001
    """Test the CLI wizard, can we actually select certain options"""
    mock_config = mock_config_factory(
        set_question_defaults=False,
    )
    measure = _create_measure_instance(
        mock_config,
        console_events=event_factory(
            # MEASURE_TYPE
            key.ENTER,
            # GENERATE_MODEL_JSON
            "y",
            # DUMMY_LOAD
            "n",
            # MODEL_NAME
            "a",
            key.ENTER,
            # MEASURE_DEVICE
            "a",
            key.ENTER,
            # COLOR_MODE
            key.DOWN,
            key.DOWN,
            key.ENTER,
            # GZIP
            key.ENTER,
            # MULTIPLE_LIGHTS
            "n",
        ),
    )

    with patch("builtins.input", return_value=""):
        measure.start()

    assert os.path.exists(os.path.join(PROJECT_DIR, "export/dummy/brightness.csv.gz"))
    assert os.path.exists(os.path.join(PROJECT_DIR, "export/dummy/model.json"))


def test_run_light(mock_config_factory) -> None:  # noqa: ANN001
    """Simulate a full run of the light measure using brightness mode"""
    mock_config = mock_config_factory()

    measure = _create_measure_instance(config=mock_config)
    measure.start()

    assert os.path.exists(os.path.join(PROJECT_DIR, "export/dummy/brightness.csv.gz"))
    assert os.path.exists(os.path.join(PROJECT_DIR, "export/dummy/model.json"))


@patch("builtins.input", return_value="")
def test_run_smart_speaker(mock_input, mock_config_factory) -> None:  # noqa: ANN001
    """Simulate a full run of the speaker measure"""
    mock_config = mock_config_factory(
        question_defaults={
            "selected_measure_type": MeasureType.SPEAKER,
        },
    )

    with (
        patch("measure.runner.speaker.SLEEP_PRE_MEASURE", 0),
        patch("measure.runner.speaker.SLEEP_MUTE", 0),
        patch.object(MeasureUtil, "take_average_measurement", return_value=1.5),
    ):
        measure = _create_measure_instance(config=mock_config)
        measure.start()

    assert os.path.exists(os.path.join(PROJECT_DIR, "export/speaker/model.json"))


@patch("builtins.input", return_value="")
def test_run_charging(mock_input, mock_config_factory) -> None:  # noqa: ANN001
    """Simulate a full run of the charging measure"""
    mock_config = mock_config_factory(
        question_defaults={
            "selected_measure_type": MeasureType.CHARGING,
            "charging_device_type": ChargingDeviceType.VACUUM_ROBOT,
        },
    )

    with patch.object(MeasureUtil, "take_average_measurement", return_value=1.5):
        measure = _create_measure_instance(config=mock_config)
        measure.start()

    assert os.path.exists(os.path.join(PROJECT_DIR, "export/charging/model.json"))


@patch("builtins.input", return_value="")
@patch("time.sleep", return_value=None)
def test_run_recorder(mock_input, mock_sleep, mock_config_factory) -> None:  # noqa: ANN001
    """Simulate a full run of the recorder measure"""
    mock_config = mock_config_factory(
        question_defaults={
            "selected_measure_type": MeasureType.RECORDER,
            "export_filename": "test.csv",
        },
    )

    def side_effect(_: Any) -> None:  # noqa: ANN401
        if side_effect.counter >= 5:
            raise KeyboardInterrupt
        side_effect.counter += 1

    side_effect.counter = 0

    # Mock take_measurement to call the side_effect function after 5 iterations
    with patch.object(MeasureUtil, "take_measurement", side_effect=side_effect):
        measure = _create_measure_instance(config=mock_config)
        measure.start()

    csv_filepath = os.path.join(PROJECT_DIR, "export/recorder/test.csv")
    assert os.path.exists(csv_filepath)

    with open(csv_filepath, newline="") as csv_file:
        reader = csv.reader(csv_file)
        lines = list(reader)

    assert len(lines) == 5


@patch("builtins.input", return_value="")
def test_run_average(mock_input, mock_config_factory, caplog: pytest.LogCaptureFixture) -> None:  # noqa: ANN001
    """Simulate a full run of the average measure"""
    caplog.set_level(logging.INFO)
    mock_config = mock_config_factory(
        question_defaults={
            "selected_measure_type": MeasureType.AVERAGE,
            "duration": 30,
        },
    )

    with patch.object(MeasureUtil, "take_average_measurement", return_value=1.5) as mock_take_measurement:
        measure = _create_measure_instance(config=mock_config)
        measure.start()

        mock_take_measurement.assert_called_once_with(30)


def _create_measure_instance(config: MeasureConfig, console_events: EventGenerator | None = None):  # noqa: ANN202
    """Create instance of the Measure class"""

    sys.stdin = StringIO()
    sys.stdout = StringIO()

    render = ConsoleRender(
        event_generator=console_events,
    )

    power_meter = DummyPowerMeter()
    return Measure(power_meter, config, render)
