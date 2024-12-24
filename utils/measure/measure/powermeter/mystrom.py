from __future__ import annotations

import time
from typing import Any

import requests

from measure.powermeter.errors import PowerMeterError, UnsupportedFeatureError
from measure.powermeter.powermeter import PowerMeasurementResult, PowerMeter


class MyStromPowerMeter(PowerMeter):
    def __init__(self, device_ip: str) -> None:
        self._device_ip = device_ip

    def get_power(self, include_voltage: bool = False) -> PowerMeasurementResult:
        """Get a new power reading from the MyStrom device. Optionally include voltage (FIXME: not yet implemented)."""
        if include_voltage:
            # FIXME: Not yet implemented # noqa: FIX001
            raise UnsupportedFeatureError("Voltage measurement is not yet implemented for MyStrom devices.")

        r = requests.get(
            f"http://{self._device_ip}/report",
            timeout=10,
        )
        json = r.json()

        try:
            power = json["power"]
        except KeyError as error:
            raise PowerMeterError("Unexpected JSON response format") from error

        return PowerMeasurementResult(power=float(power), updated=time.time())

    def has_voltage_support(self) -> bool:
        return False

    def process_answers(self, answers: dict[str, Any]) -> None:
        pass
