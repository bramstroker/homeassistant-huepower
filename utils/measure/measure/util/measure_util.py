from __future__ import annotations

import logging
import os
import time
from datetime import datetime as dt
from statistics import mean

import numpy as np

from measure.config import MeasureConfig
from measure.const import PROJECT_DIR, Trend, dummy_load_measurement_count, dummy_load_measurements_duration
from measure.powermeter.errors import (
    OutdatedMeasurementError,
    PowerMeterError,
    ZeroReadingError,
)
from measure.powermeter.powermeter import PowerMeasurementResult, PowerMeter

_LOGGER = logging.getLogger("measure")


class MeasureUtil:
    def __init__(self, power_meter: PowerMeter, config: MeasureConfig) -> None:
        self.power_meter = power_meter
        self.dummy_load_value: float | None = None
        self.config = config

    def take_average_measurement(self, duration: int, measure_resistance: bool = False) -> float:
        """
        Measure the average power consumption or resistance for a given time period in seconds.

        This function calculates the average power or resistance by taking multiple readings over
        the specified duration. If `measure_resistance` is True, the function computes resistance
        using the formula R = V^2 / P, where V is the voltage and P is the power. If a dummy load
        resistive value is set, the power consumption of the dummy load calculated for each
        measurement, depending on the current voltage and is subtracted from the power
        measurements.

        Args:
            duration (int): The time duration (in seconds) over which to take measurements.
            measure_resistance (bool): Whether to measure resistance instead of power. Defaults to False.

        Returns:
            float: The average resistance (in Ω) or power (in W) over the measurement duration.

        Raises:
            UnsupportedFeatureError: If `measure_resistance` is True but the power meter does not
                                      support voltage measurements.

        Error handling:
            - If voltage measurements are not supported, the program will terminate with an appropriate
              error message, if measuring power consumption and a dummy load resistive value is set.
            - For resistance measurements, voltage values below 1 are treated as invalid, and the program
              exits to avoid incorrect calculations.
            - Ignores single measurements of <= 0 W.
        """
        _LOGGER.info("Measuring average %s over %s seconds", "resistance" if measure_resistance else "power", duration)
        start_time = time.time()
        readings: list[float] = []

        first_measurement = True

        while (time.time() - start_time) < duration:
            if first_measurement:
                first_measurement = False
            else:
                # sleep time exceeds duration
                if not ((time.time() - start_time + self.config.sleep_time) < duration):
                    break
                time.sleep(self.config.sleep_time)

            if measure_resistance:
                result = self.power_meter.get_power(include_voltage=True)
                power, voltage = result.power, result.voltage

                if voltage < 1:
                    _LOGGER.error("Error during measurement: Voltage measurement returned zero. Aborting measurement.")
                    exit(1)

                if round(power, 2) == 0:
                    _LOGGER.warning("Invalid measurement: power: %.2f W, voltage: %.2f", power, voltage)
                    continue

                resistance = round((voltage**2) / power, 4)
                readings.append(resistance)
                _LOGGER.debug(
                    "Measured resistance: %.2f Ω; measured power: %.2f W, voltage: %.2f",
                    resistance,
                    power,
                    voltage,
                )
                continue

            if self.dummy_load_value:  # measurement with dummy load
                # Validate power meter is capable of measuring voltage
                self._validate_voltage_support()

                result = self.power_meter.get_power(include_voltage=True)

                power, voltage = result.power, result.voltage

                if voltage < 1:
                    _LOGGER.error("Error during measurement: Voltage measurement returned zero. Aborting measurement.")
                    exit(1)

                dummy_power = (voltage**2) / self.dummy_load_value
                power -= dummy_power

                if round(power, 2) <= 0:
                    _LOGGER.warning(
                        "Invalid measurement after subtracting dummy load consumption. Calculated consumption: %.2f W; ignoring",
                        power,
                    )
                    continue

                readings.append(power)
                _LOGGER.info("Measured power: %.2f W", power)
                continue

            # measurement without dummy load
            power = self.power_meter.get_power().power
            if round(power, 2) == 0:
                _LOGGER.warning("Invalid measurement. Consumption: %.2f W; ignoring", power)

                continue
            readings.append(power)
            _LOGGER.info("Measured power: %.2f W", power)

        if not readings:
            _LOGGER.error("No valid readings were recorded.")
            exit(1)

        average = round(mean(readings), 2)
        _LOGGER.info("Average of %d measurements: %.2f %s", len(readings), average, "Ω" if measure_resistance else "W")
        return average

    def take_measurement(
        self,
        start_timestamp: float | None = None,
        retry_count: int = 0,
    ) -> float:
        """Get a measurement from the powermeter, take multiple samples and calculate the average"""
        measurements = []
        # Take multiple samples to reduce noise
        for i in range(1, self.config.sample_count + 1):
            _LOGGER.debug("Taking sample %d", i)
            error = None
            measurement: PowerMeasurementResult | None = None

            try:
                if self.dummy_load_value:
                    measurement = self.power_meter.get_power(include_voltage=True)
                else:
                    measurement = self.power_meter.get_power()

                updated_at = dt.fromtimestamp(measurement.updated).strftime(
                    "%d-%m-%Y, %H:%M:%S",
                )
                _LOGGER.debug("Measurement received (update_time=%s)", updated_at)
            except PowerMeterError as err:
                error = err

            if measurement:
                # Check if measurement is not outdated
                if start_timestamp and measurement.updated < start_timestamp:
                    error = OutdatedMeasurementError(
                        "Power measurement is outdated. Aborting after %d successive retries",
                        self.config.max_retries,
                    )

                power = measurement.power

                # Check if we not have a 0 measurement
                if round(power, 2) <= 0:
                    error = ZeroReadingError("0 watt was read from the power meter")

                if self.dummy_load_value:
                    voltage = measurement.voltage
                    if voltage < 1:
                        error = ZeroReadingError("0 Volt was read from the power meter")
                    else:
                        dummy_power = (voltage**2) / self.dummy_load_value
                        power -= dummy_power

                        if round(power, 2) <= 0:
                            error = ZeroReadingError("0 watt was read from the power meter, after substracting the dummy load")

            if error:
                # Prevent endless recursion. Throw error when max retries is reached
                if retry_count == self.config.max_retries:
                    raise error
                retry_count += 1
                time.sleep(self.config.sleep_time)
                return self.take_measurement(start_timestamp, retry_count)

            measurements.append(power)
            if self.config.sample_count > 1:
                time.sleep(self.config.sleep_time_sample)

        # Determine Average PM reading
        if not measurements:
            _LOGGER.error("No valid readings were recorded.")
            exit(1)

        average = mean(measurements)
        _LOGGER.info("Average measurement: %.3f W", average)
        return average

    def initialize_dummy_load(self) -> float:
        """Get the previously measured dummy load resistance, or take a new measurement if it doesn't exist"""

        dummy_load_file = os.path.join(
            PROJECT_DIR,
            ".persistent/dummy_load_resistance",
        )
        if os.path.exists(dummy_load_file):
            with open(dummy_load_file) as f:
                value = float(f.read())
            _LOGGER.info("Dummy load was already measured before, value: %s Ω", value)

            print("You need to preheat the dummy load, so it's consumption can stablize.")
            print("If you're unsure the dummy load is sufficently preheated or you're using a different one, remeasure.")
            print()
            inquirer = input("Do you want to measure the dummy load again? (y/n): ")
            if inquirer.lower() == "n":
                self.dummy_load_value = value
                return self.dummy_load_value

        print()
        print("Tip: Use a dummy load with constant power consumption. Stick to resistive loads for the best results!")
        print("Important: Connect only the dummy load to your smart plug—not the device you're measuring.")
        print()
        print("The script will now measure the dummy load and continue once it's consumption has stablized.")
        print("Depending on the dummy load this may take 2 hours.")
        print()
        input("Ready to begin measuring the dummy load? Hit Enter ")

        self.dummy_load_value = self._measure_dummy_load(dummy_load_file)
        return self.dummy_load_value

    def _measure_dummy_load(self, file_path: str) -> float:
        """Measure the dummy load and persist the value for future measurement session"""

        print(
            "Measuring and checking dummy load... this will take at least %.0f minutes."
            % (dummy_load_measurement_count / 60 * dummy_load_measurements_duration),
        )

        # Validate power meter is capable of measuring voltage
        self._validate_voltage_support()

        while True:
            averages = [
                self.take_average_measurement(dummy_load_measurements_duration, measure_resistance=True) for _ in range(dummy_load_measurement_count)
            ]

            trend = self._check_trend(averages)

            if not trend:
                _LOGGER.error("Error during measurement: No trend could be calculated")
                exit(1)

            if trend == Trend.STEADY:
                break

            print(f"Dummy load resistance has not yet stablized and is {trend}, repeating.")

        average = round(mean(averages), 2)

        _LOGGER.info("Dummy load measurement completed. Resistance: %s Ω", average)

        with open(file_path, "w") as f:
            f.write(str(average))
        return average

    def _check_trend(self, averages: list[float]) -> Trend | None:
        """
        Checks if the resistance readings of a dummy load are increasing, decreasing, or steady (fluctuating).

        Returns:
            str: "increasing", "decreasing", or "steady" based on the trends.
            None: if not enough samples were supplied
        """
        if len(averages) < 20:
            return None

        mid = len(averages) // 2  # Calculate the midpoint

        first_half = averages[:mid]
        second_half = averages[mid:]

        # Helper function to calculate trend
        def calc_trend(values: list[float]) -> float:
            # Perform a linear regression to estimate the trend
            x = np.arange(len(values))
            coeffs = np.polyfit(x, values, 1)  # Linear fit: y = mx + c
            return coeffs[0]  # Extract the slope (m)

        first_trend = calc_trend(first_half)
        second_trend = calc_trend(second_half)

        def trend_direction(slope: float, threshold: float = 0.01) -> Trend:
            if slope > threshold:
                return Trend.INCREASING
            if slope < -threshold:
                return Trend.DECREASING
            return Trend.STEADY

        first_trend = trend_direction(first_trend)
        second_trend = trend_direction(second_trend)

        if first_trend == second_trend and first_trend != Trend.STEADY:
            return first_trend
        return Trend.STEADY

    def _validate_voltage_support(self) -> None:
        """Check if the power meter supports voltage readings."""

        if not self.power_meter.has_voltage_support:
            print("The selected power meter does not support voltage measurements, required to measure dummy loads.")
            exit(1)
