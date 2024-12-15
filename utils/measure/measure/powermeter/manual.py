from __future__ import annotations

import time
from typing import Any

from measure.powermeter.powermeter import PowerMeasurementResult, PowerMeter


class ManualPowerMeter(PowerMeter):
    def get_power(self, include_voltage: bool = False) -> PowerMeasurementResult:
        """Manually enter power readings. Optionally enter voltage readings as well."""
        if include_voltage:
            power = input("Input power measurement: ")
            voltage = input("Input voltage measurement: ")

            return PowerMeasurementResult(
                power=float(power),
                voltage=float(voltage),
                updated=time.time(),
            )

        power = input("Input power measurement: ")
        return PowerMeasurementResult(power=float(power), updated=time.time())

    def has_voltage_support(self) -> bool:
        return True

    def process_answers(self, answers: dict[str, Any]) -> None:
        pass
