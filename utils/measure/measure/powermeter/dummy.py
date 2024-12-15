from __future__ import annotations

import time
from typing import Any

from measure.powermeter.powermeter import PowerMeasurementResult, PowerMeter


class DummyPowerMeter(PowerMeter):
    def get_power(self, include_voltage: bool = False) -> PowerMeasurementResult:
        if include_voltage:
            return PowerMeasurementResult(
                power=20.5,
                voltage=233.0,
                updated=time.time(),
            )
        return PowerMeasurementResult(power=20.5, updated=time.time())

    def has_voltage_support(self) -> bool:
        return True

    def process_answers(self, answers: dict[str, Any]) -> None:
        pass
