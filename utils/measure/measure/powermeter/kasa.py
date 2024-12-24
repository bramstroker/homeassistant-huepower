from __future__ import annotations

import asyncio
import time
from typing import Any

from kasa import SmartPlug

from measure.powermeter.errors import UnsupportedFeatureError
from measure.powermeter.powermeter import PowerMeasurementResult, PowerMeter


class KasaPowerMeter(PowerMeter):
    def __init__(self, device_ip: str) -> None:
        self._smartplug = SmartPlug(device_ip)

    def get_power(self, include_voltage: bool = False) -> PowerMeasurementResult:
        """Get a new power reading from the Kasa device. Optionally include voltage (FIXME: not yet implemented)."""
        if include_voltage:
            # FIXME: Not yet implemented # noqa: FIX001
            raise UnsupportedFeatureError("Voltage measurement is not yet implemented for Kasa devices.")

        loop = asyncio.get_event_loop()
        power = loop.run_until_complete(self.async_read_power_meter())

        return PowerMeasurementResult(power=power, updated=time.time())

    async def async_read_power_meter(self) -> None:
        await self._smartplug.update()
        return self._smartplug.emeter_realtime["power"]

    def has_voltage_support(self) -> bool:
        return False

    def process_answers(self, answers: dict[str, Any]) -> None:
        pass
