from __future__ import annotations

import time
from typing import Any

import tuyapower

from measure.powermeter.errors import PowerMeterError, UnsupportedFeatureError
from measure.powermeter.powermeter import PowerMeasurementResult, PowerMeter

STATUS_OK = "OK"


class TuyaPowerMeter(PowerMeter):
    def __init__(
        self,
        device_id: str,
        device_ip: str,
        device_key: str,
        device_version: str = "3.3",
    ) -> None:
        self._device_id = device_id
        self._device_ip = device_ip
        self._device_key = device_key
        self._device_version = device_version

    def get_power(self, include_voltage: bool = False) -> PowerMeasurementResult:
        """Get a new power reading from the Tuya device. Optionally include voltage (FIXME: not yet implemented)."""
        if include_voltage:
            # FIXME: Not yet implemented # noqa: FIX001
            raise UnsupportedFeatureError("Voltage measurement is not yet implemented for Tuya devices.")

        (_, w, _, _, err) = tuyapower.deviceInfo(
            self._device_id,
            self._device_ip,
            self._device_key,
            self._device_version,
        )

        if err != STATUS_OK:
            raise PowerMeterError("Could not get a successful power reading")

        return PowerMeasurementResult(power=w, updated=time.time())

    def has_voltage_support(self) -> bool:
        return False

    def process_answers(self, answers: dict[str, Any]) -> None:
        pass
