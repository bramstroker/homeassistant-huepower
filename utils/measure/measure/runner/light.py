from __future__ import annotations

import csv
import gzip
import logging
import os
import re
import shutil
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import datetime as dt
from typing import Any, TextIO

import inquirer

from measure.config import MeasureConfig
from measure.controller.light.const import ColorMode
from measure.controller.light.controller import LightInfo
from measure.controller.light.factory import LightControllerFactory
from measure.powermeter.errors import (
    OutdatedMeasurementError,
    PowerMeterError,
    ZeroReadingError,
)
from measure.runner.const import QUESTION_COLOR_MODE, QUESTION_GZIP, QUESTION_MULTIPLE_LIGHTS, QUESTION_NUM_LIGHTS
from measure.runner.errors import RunnerError
from measure.runner.runner import MeasurementRunner, RunnerResult
from measure.util.measure_util import MeasureUtil

CSV_HEADERS = {
    ColorMode.HS: ["bri", "hue", "sat", "watt"],
    ColorMode.COLOR_TEMP: ["bri", "mired", "watt"],
    ColorMode.BRIGHTNESS: ["bri", "watt"],
}

CSV_WRITE_BUFFER = 50
MAX_ALLOWED_0_READINGS = 50

_LOGGER = logging.getLogger("measure")


class LightRunner(MeasurementRunner):
    """
    This class is responsible for measuring the power usage of a light. It uses a LightController to control the light, and a PowerMeter
    to measure the power usage. The measurements are exported as CSV files in export/<model_id>/<color_mode>.csv (or .csv.gz). The
    model_id is retrieved from the LightController and color mode can be selected by user input or self.config.file (.env). The CSV files
    contain one row per variation, where each column represents one property of that variation (e.g., brightness, hue, saturation). The last
    column contains the measured power value in watt.
    If you want to generate model JSON files for the LUT model, you can do so by answering yes to the question "Do you want to generate
    model.json?".

    # CSV file export/<model-id>/hs.csv will be created with measurements for HS
    color mode (e.g., hue and saturation). The last column contains the measured
    power value in watt.
    """

    def __init__(self, measure_util: MeasureUtil, config: MeasureConfig) -> None:
        self.light_controller = LightControllerFactory(config).create()
        self.measure_util = measure_util
        self.color_modes: set[ColorMode] | None = None
        self.num_lights: int = 1
        self.num_0_readings: int = 0
        self.light_info: LightInfo | None = None
        self.config = config

    def prepare(self, answers: dict[str, Any]) -> None:
        self.light_controller.process_answers(answers)
        self.color_modes = set(answers[QUESTION_COLOR_MODE])
        self.num_lights = int(answers.get(QUESTION_NUM_LIGHTS) or 1)
        self.light_info = self.light_controller.get_light_info()

    def get_export_directory(self) -> str:
        return f"{self.light_info.model_id}"

    def run(self, answers: dict[str, Any], export_directory: str) -> RunnerResult | None:
        measurements_to_run = [self.prepare_measurements_for_color_mode(export_directory, color_mode) for color_mode in self.color_modes]

        all_variations: list[Variation] = []
        for measurement in measurements_to_run:
            all_variations.extend(measurement.variations)
        left_variations = all_variations.copy()

        [self.run_color_mode(answers, measurement_info, all_variations, left_variations) for measurement_info in measurements_to_run]

        return RunnerResult(
            model_json_data={"calculation_strategy": "lut"},
        )

    def prepare_measurements_for_color_mode(self, export_directory: str, color_mode: ColorMode) -> MeasurementRunInput:
        """Fetch all variations for the given color mode and prepare the measurement session."""

        csv_file_path = f"{export_directory}/{color_mode.value}.csv"

        resume_at = None
        if self.should_resume(csv_file_path):
            resume_at = self.get_resume_variation(csv_file_path, color_mode)

        variations = list(self.get_variations(color_mode, resume_at))
        return MeasurementRunInput(
            color_mode=color_mode,
            csv_file=csv_file_path,
            variations=variations,
            is_resuming=bool(resume_at),
        )

    def run_color_mode(
        self,
        answers: dict[str, Any],
        measurement_info: MeasurementRunInput,
        all_variations: list[Variation],
        left_variations: list[Variation],
    ) -> None:
        """Run the measurement session for lights"""

        color_mode = measurement_info.color_mode
        file_write_mode = "w"
        write_header_row = True
        if measurement_info.is_resuming:
            _LOGGER.info("Resuming measurements")
            file_write_mode = "a"
            write_header_row = False

        variations = measurement_info.variations

        _LOGGER.info(
            "Starting measurements. Estimated duration: %s",
            self.calculate_time_left(all_variations, left_variations),
        )

        with open(measurement_info.csv_file, file_write_mode, newline="") as csv_file:
            csv_writer = CsvWriter(csv_file, color_mode, write_header_row, self.config)

            # To avoid bugs in some lights, when set to low brightness initially
            # where they turn off again. And also bugs where lights will turn off
            # again, after they recevied two turn-off commands, followed by a single
            # turn on command, we set them to maximum brightness, twice here.
            # See issue #2598
            self.set_light_to_maximum_brightness(color_mode)

            # Initially wait longer so the smartplug can settle
            _LOGGER.info(
                "Start taking measurements for color mode: %s",
                color_mode.value,
            )
            _LOGGER.info("Waiting %d seconds...", self.config.sleep_initial)
            time.sleep(self.config.sleep_initial)

            previous_variation = None
            for count, variation in enumerate(variations):
                if count % 10 == 0:
                    time_left = self.calculate_time_left(all_variations, left_variations, variation)
                    progress_percentage = ((len(all_variations) - len(left_variations)) / len(all_variations)) * 100
                    _LOGGER.info(
                        "Progress: %d%%, Estimated time left: %s",
                        progress_percentage,
                        time_left,
                    )
                _LOGGER.info("Changing light to: %s", variation)
                variation_start_time = time.time()
                self.light_controller.change_light_state(
                    color_mode,
                    on=True,
                    **asdict(variation),
                )

                if previous_variation and isinstance(variation, ColorTempVariation) and variation.ct < previous_variation.ct:
                    _LOGGER.info("Extra waiting for significant CT change...")
                    time.sleep(self.config.sleep_time_ct)

                if previous_variation and isinstance(variation, HsVariation) and variation.sat < previous_variation.sat:
                    _LOGGER.info("Extra waiting for significant SAT change...")
                    time.sleep(self.config.sleep_time_sat)

                if previous_variation and isinstance(variation, HsVariation) and variation.hue < previous_variation.hue:
                    _LOGGER.info("Extra waiting for significant HUE change...")
                    time.sleep(self.config.sleep_time_hue)

                previous_variation = variation
                time.sleep(self.config.sleep_time)
                try:
                    power = self.take_power_measurement(variation_start_time)
                except OutdatedMeasurementError:
                    power = self.nudge_and_remeasure(color_mode, variation)
                except ZeroReadingError as error:
                    self.num_0_readings += 1
                    _LOGGER.warning("Discarding measurement: %s", error)
                    if self.num_0_readings > MAX_ALLOWED_0_READINGS:
                        _LOGGER.error(
                            "Aborting measurement session. Received too many 0 readings",
                        )
                        return
                    continue
                except PowerMeterError as error:
                    _LOGGER.error("Aborting: %s", error)
                    return
                _LOGGER.info("Measured power: %.2f", power)
                csv_writer.write_measurement(variation, power)
                left_variations.remove(variation)

            csv_file.close()
            _LOGGER.info(
                "Hooray! measurements finished. Exported CSV file %s",
                measurement_info.csv_file,
            )

            self.light_controller.change_light_state(ColorMode.BRIGHTNESS, on=False)
            _LOGGER.info("Turning off the light")

        if bool(answers.get(QUESTION_GZIP, True)):
            self.gzip_csv(measurement_info.csv_file)

    def set_light_to_maximum_brightness(self, color_mode: ColorMode) -> None:
        """
        Set the light to maximum brightness twice to avoid that bugs in some lights will
        cause them to be off
        """
        _LOGGER.info("Turning on light with maximum brightness")
        # Turn the light on twice to ensure it's in the correct state
        for _ in range(2):
            if color_mode == ColorMode.HS:
                self.light_controller.change_light_state(
                    color_mode,
                    on=True,
                    bri=255,
                    hue=0,  # Set default hue
                    sat=1,  # Set default saturation
                )
            elif color_mode == ColorMode.COLOR_TEMP:
                self.light_controller.change_light_state(
                    color_mode,
                    on=True,
                    bri=255,
                    ct=self.light_info.min_mired,  # Set to minimum mired value for color temp
                )
            else:
                self.light_controller.change_light_state(
                    color_mode,
                    on=True,
                    bri=255,
                )
            time.sleep(self.config.sleep_time)  # Wait for the light to process

    def get_variations(
        self,
        color_mode: ColorMode,
        resume_at: Variation | None = None,
    ) -> Iterator[Variation]:
        """Get all the light settings where the measure script needs to cycle through"""
        if color_mode == ColorMode.HS:
            variations = self.get_hs_variations()
        elif color_mode == ColorMode.COLOR_TEMP:
            variations = self.get_ct_variations()
        else:
            variations = self.get_brightness_variations()

        if resume_at:
            include_variation = False
            for variation in variations:
                if include_variation:
                    yield variation

                # Current variation is the one we need to resume at.
                # Set include_variation flag so it every variation from now on will be yielded next iteration
                if variation == resume_at:
                    include_variation = True
        else:
            yield from variations

    def get_ct_variations(self) -> Iterator[ColorTempVariation]:
        """Get color_temp variations"""
        min_mired = round(self.light_info.min_mired)
        max_mired = round(self.light_info.max_mired)
        for bri in self.inclusive_range(
            self.config.min_brightness,
            self.config.max_brightness,
            self.config.ct_bri_steps,
        ):
            for mired in self.inclusive_range(
                min_mired,
                max_mired,
                self.config.ct_mired_steps,
            ):
                yield ColorTempVariation(bri=bri, ct=mired)

    def get_hs_variations(self) -> Iterator[HsVariation]:
        """Get hue/sat variations"""
        for bri in self.inclusive_range(
            self.config.min_brightness,
            self.config.max_brightness,
            self.config.hs_bri_steps,
        ):
            for sat in self.inclusive_range(
                self.config.min_sat,
                self.config.max_sat,
                self.config.hs_sat_steps,
            ):
                for hue in self.inclusive_range(
                    self.config.min_hue,
                    self.config.max_hue,
                    self.config.hs_hue_steps,
                ):
                    yield HsVariation(bri=bri, hue=hue, sat=sat)

    def get_brightness_variations(self) -> Iterator[Variation]:
        """Get brightness variations"""
        for bri in self.inclusive_range(
            self.config.min_brightness,
            self.config.max_brightness,
            self.config.bri_bri_steps,
        ):
            yield Variation(bri=bri)

    @staticmethod
    def inclusive_range(start: int, end: int, step: int) -> Iterator[int]:
        """Get an iterator including the min and max, with steps in between"""
        i = start
        while i < end:
            yield i
            i += step
        yield end

    def calculate_time_left(
        self,
        all_variations: list[Variation],
        left_variations: list[Variation],
        current_variation: Variation | None = None,
    ) -> str:
        """Try to guess the remaining time left. This will not account for measuring errors / retries obviously"""
        num_variations_left = len(left_variations)
        num_variations = len(all_variations)
        progress = num_variations - num_variations_left
        current_color_mode = self.get_color_mode(current_variation)

        # Account estimated seconds for the light_controller and power_meter to process
        estimated_step_delay = 0.15

        time_left = 0
        if progress == 0:
            time_left += self.config.sleep_standby + self.config.sleep_initial
        time_left += num_variations_left * (self.config.sleep_time + estimated_step_delay)
        if self.config.sample_count > 1:
            time_left += num_variations_left * self.config.sample_count * (self.config.sleep_time_sample + estimated_step_delay)

        color_mode_time_calculation = {
            ColorMode.HS: self.calculate_hs_time_left,
            ColorMode.COLOR_TEMP: self.calculate_ct_time_left,
            ColorMode.BRIGHTNESS: lambda _: 0,
        }

        time_left += color_mode_time_calculation[current_color_mode](current_variation)

        # Add timings for color modes which needs to be fully measured
        left_color_modes = {self.get_color_mode(variation) for variation in left_variations}

        time_left += sum(color_mode_time_calculation[mode](None) for mode in left_color_modes if mode not in current_color_mode)

        return self.format_time_left(time_left)

    @staticmethod
    def get_color_mode(variation: Variation) -> ColorMode:
        """Get the color mode of the variation"""
        if isinstance(variation, HsVariation):
            return ColorMode.HS
        if isinstance(variation, ColorTempVariation):
            return ColorMode.COLOR_TEMP
        return ColorMode.BRIGHTNESS

    def calculate_hs_time_left(self, current_variation: HsVariation | None) -> float:
        """Calculate the time left for the HS color mode."""
        brightness = current_variation.bri if current_variation else self.config.min_brightness
        sat_steps_left = (
            round(
                (self.config.max_brightness - brightness) / self.config.hs_bri_steps,
            )
            - 1
        )
        time_left = sat_steps_left * self.config.sleep_time_sat
        hue_steps_left = round(
            self.config.max_hue / self.config.hs_hue_steps * sat_steps_left,
        )
        time_left += hue_steps_left * self.config.sleep_time_hue
        return time_left

    def calculate_ct_time_left(self, current_variation: ColorTempVariation | None) -> float:
        """Calculate the time left for the HS color mode."""
        brightness = current_variation.bri if current_variation else self.config.min_brightness
        ct_steps_left = (
            round(
                (self.config.max_brightness - brightness) / self.config.ct_bri_steps,
            )
            - 1
        )
        return ct_steps_left * self.config.sleep_time_ct

    @staticmethod
    def format_time_left(time_left: float) -> str:
        """Format the time left in a human readable format"""
        if time_left < 0:
            time_left = 0
        if time_left > 3600:
            formatted_time = f"{round(time_left / 3600, 1)}h"
        elif time_left > 60:
            formatted_time = f"{round(time_left / 60, 1)}m"
        else:
            formatted_time = f"{round(time_left, 1)}s"

        return formatted_time

    def nudge_and_remeasure(
        self,
        color_mode: str,
        variation: Variation,
    ) -> float | None:
        nudge_count = 0
        for nudge_count in range(self.config.max_nudges):  # noqa: B007
            try:
                # Likely not significant enough change for PM to detect. Try nudging it
                _LOGGER.warning("Measurement is stuck, Nudging")
                # If brightness is low, set brightness high. Else, turn light off
                self.light_controller.change_light_state(
                    ColorMode.BRIGHTNESS,
                    on=(variation.bri < 128),
                    bri=255,
                )
                time.sleep(self.config.pulse_time_nudge)
                variation_start_time = time.time()
                self.light_controller.change_light_state(
                    color_mode,
                    on=True,
                    **asdict(variation),
                )
                # Wait a longer amount of time for the PM to settle
                time.sleep(self.config.sleep_time_nudge)
                return self.take_power_measurement(variation_start_time)
            except OutdatedMeasurementError:
                continue
            except ZeroReadingError as error:
                self.num_0_readings += 1
                _LOGGER.warning("Discarding measurement: %s", error)
                if self.num_0_readings > MAX_ALLOWED_0_READINGS:
                    _LOGGER.error(
                        "Aborting measurement session. Received too many 0 readings",
                    )
                    return None
                continue
        raise OutdatedMeasurementError(
            f"Power measurement is outdated. Aborting after {nudge_count + 1} nudged retries",
        )

    def should_resume(self, csv_file_path: str) -> bool:
        """This method checks if a CSV file already exists for the current color mode.

        If so, it asks the user if he wants to resume measurements or start over.

        Parameters
        ----------
        csv_file_path : str
            The path of the CSV file that should be checked

        Returns
        -------
        bool
            True if we should resume measurements, False otherwise.

        Raises
        ------
        Exception
            When something goes wrong with reading/writing files.

        UndefinedValueError
            When no value is defined in .env for RESUME key.

        ValueError
            When an invalid value is defined in .env for RESUME key (not 'true' or 'false').

        """
        if not os.path.exists(csv_file_path):
            return False

        size = os.path.getsize(csv_file_path)
        if size == 0:
            return False

        with open(csv_file_path) as csv_file:
            rows = csv.reader(csv_file)
            if len(list(rows)) == 1:
                return False

        should_resume = self.config.resume
        if should_resume is None:
            return inquirer.confirm(
                message=f"CSV File {csv_file_path} already exists. Do you want to resume measurements?",
                default=True,
            )
        return should_resume

    def get_resume_variation(self, csv_file_path: str, color_mode: ColorMode) -> Variation | None:
        """This method returns the variation to resume at.

        It reads the last row from the CSV file and converts it into a Variation object.

        Parameters
        ----------
        csv_file_path : str
            The path to the CSV file

        Returns
        -------
        Variation:
            The variation to resume at. None if no resuming is needed.

        Raises
        -------
        FileNotFoundError, Exception, ZeroDivisionError, ValueError, TypeError, IndexError

        Examples
        --------
        >>> get_resume_variation("/home/user/export/LCT001/hs.csv") -> HsVariation(bri=254, hue=0, sat=0)

        See Also
        -------
        get_variations()

        Notes
        -------
        This method will raise an exception when something goes wrong while reading or parsing the CSV file or when an unsupported color
        mode is used in the CSV file.
        """

        with open(csv_file_path) as csv_file:
            rows = csv.reader(csv_file)
            last_row = list(rows)[-1]

        if color_mode == ColorMode.BRIGHTNESS:
            return Variation(bri=int(last_row[0]))

        if color_mode == ColorMode.COLOR_TEMP:
            return ColorTempVariation(bri=int(last_row[0]), ct=int(last_row[1]))

        if color_mode == ColorMode.HS:
            return HsVariation(
                bri=int(last_row[0]),
                hue=int(last_row[1]),
                sat=int(last_row[2]),
            )

        raise RunnerError(f"Color mode {color_mode} not supported")

    def take_power_measurement(
        self,
        start_timestamp: float,
        retry_count: int = 0,
    ) -> float:
        """Request a power reading from the self.config.red power meter"""
        value = self.measure_util.take_measurement(start_timestamp, retry_count)

        # Determine per load power consumption
        value /= self.num_lights

        return round(value, 2)

    @staticmethod
    def gzip_csv(csv_file_path: str) -> None:
        """Gzip the CSV file"""
        with (
            open(csv_file_path, "rb") as csv_file,
            gzip.open(
                f"{csv_file_path}.gz",
                "wb",
            ) as gzip_file,
        ):
            shutil.copyfileobj(csv_file, gzip_file)

    def measure_standby_power(self) -> float:
        """Measures the standby power (when the light is OFF)"""
        self.light_controller.change_light_state(ColorMode.BRIGHTNESS, on=False)
        start_time = time.time()
        _LOGGER.info(
            "Measuring standby power. Waiting for %d seconds...",
            self.config.sleep_standby,
        )
        time.sleep(self.config.sleep_standby)
        try:
            return self.take_power_measurement(start_time)
        except OutdatedMeasurementError:
            self.nudge_and_remeasure(ColorMode.BRIGHTNESS, Variation(0))
        except ZeroReadingError:
            _LOGGER.error(
                "Measured 0 watt as standby usage, continuing now, "
                "but you probably need to have a look into measuring multiple lights at the same time "
                "or using a dummy load.",
            )
            return 0

    def get_questions(self) -> list[inquirer.questions.Question]:
        """Get questions to ask for the light runner"""
        questions = [
            inquirer.List(
                name=QUESTION_COLOR_MODE,
                message="Select the color mode",
                choices=[
                    (ColorMode.HS, {ColorMode.HS}),
                    (ColorMode.COLOR_TEMP, {ColorMode.COLOR_TEMP}),
                    (ColorMode.BRIGHTNESS, {ColorMode.BRIGHTNESS}),
                    ("hs + color_temp", {ColorMode.HS, ColorMode.COLOR_TEMP}),
                ],
                default=ColorMode.HS,
            ),
            inquirer.Confirm(
                name=QUESTION_GZIP,
                message="Do you want to gzip CSV files?",
                default=True,
            ),
            inquirer.Confirm(
                name=QUESTION_MULTIPLE_LIGHTS,
                message="Are you measuring multiple lights. In some situations it helps to connect multiple lights to "
                "be able to measure low currents.",
                default=False,
            ),
            inquirer.Text(
                name=QUESTION_NUM_LIGHTS,
                message="How many lights are you measuring?",
                ignore=lambda answers: not answers.get(QUESTION_MULTIPLE_LIGHTS),
                validate=lambda _, current: re.match(r"\d+", current),
            ),
        ]
        questions.extend(self.light_controller.get_questions())
        return questions


@dataclass(frozen=True)
class Variation:
    bri: int

    def to_csv_row(self) -> list:
        return [self.bri]


@dataclass(frozen=True)
class HsVariation(Variation):
    hue: int
    sat: int

    def to_csv_row(self) -> list:
        return [self.bri, self.hue, self.sat]

    def is_hue_changed(self, other_variation: HsVariation) -> bool:
        return self.hue != other_variation.hue

    def is_sat_changed(self, other_variation: HsVariation) -> bool:
        return self.sat != other_variation.sat


@dataclass(frozen=True)
class ColorTempVariation(Variation):
    ct: int

    def to_csv_row(self) -> list:
        return [self.bri, self.ct]

    def is_ct_changed(self, other_variation: ColorTempVariation) -> bool:
        return self.ct != other_variation.ct


@dataclass(frozen=True)
class MeasurementRunInput:
    color_mode: ColorMode
    csv_file: str
    variations: list[Variation]
    is_resuming: bool


class CsvWriter:
    def __init__(
        self,
        csv_file: TextIO,
        color_mode: ColorMode,
        add_header: bool,
        config: MeasureConfig,
    ) -> None:
        self.csv_file = csv_file
        self.config = config
        self.writer = csv.writer(csv_file)
        self.rows_written = 0
        if add_header:
            header_row = CSV_HEADERS[color_mode]
            if self.config.csv_add_datetime_column:
                header_row.append("time")
            self.writer.writerow(header_row)

    def write_measurement(self, variation: Variation, power: float) -> None:
        """Write row with measurement to the CSV"""
        row = variation.to_csv_row()
        row.append(power)
        if self.config.csv_add_datetime_column:
            row.append(dt.now().strftime("%Y%m%d%H%M%S"))
        self.writer.writerow(row)
        self.rows_written += 1
        if self.rows_written % CSV_WRITE_BUFFER == 1:
            self.csv_file.flush()
            _LOGGER.debug("Flushing CSV buffer")
