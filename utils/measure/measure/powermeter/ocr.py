from __future__ import annotations

import os
from typing import Any

from measure.powermeter.errors import UnsupportedFeatureError
from measure.powermeter.powermeter import PowerMeasurementResult, PowerMeter


class OcrPowerMeter(PowerMeter):
    def __init__(self) -> None:
        filepath = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "../ocr/ocr_results.txt",
        )

        self.file = open(filepath, "rb")  # noqa: SIM115
        super().__init__()

    def get_power(self, include_voltage: bool = False) -> PowerMeasurementResult:
        """Get a new power reading via OCR."""
        if include_voltage:
            raise UnsupportedFeatureError("Voltage measurement are not supported for OCR mode.")

        last_line = self.read_last_line()
        (timestamp, power) = last_line.strip().split(";")
        power = float(power)
        timestamp = float(timestamp)
        return PowerMeasurementResult(power=power, updated=timestamp)

    def read_last_line(self) -> str:
        try:
            self.file.seek(-2, os.SEEK_END)
            while self.file.read(1) != b"\n":
                self.file.seek(-2, os.SEEK_CUR)
        except OSError:
            self.file.seek(0)
        return self.file.readline().decode()

    def has_voltage_support(self) -> bool:
        return False

    def process_answers(self, answers: dict[str, Any]) -> None:
        pass
