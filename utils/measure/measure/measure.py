#!/usr/bin/env python3

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from typing import Any

import inquirer
from decouple import UndefinedValueError
from decouple import config as decouple_config
from inquirer.errors import ValidationError
from inquirer.questions import Question
from inquirer.render import ConsoleRender

from measure.config import MeasureConfig
from measure.const import PROJECT_DIR, QUESTION_DUMMY_LOAD, QUESTION_GENERATE_MODEL_JSON, QUESTION_MEASURE_DEVICE, QUESTION_MODEL_NAME, MeasureType
from measure.controller.light.errors import LightControllerError
from measure.powermeter.errors import PowerMeterError
from measure.powermeter.factory import PowerMeterFactory
from measure.powermeter.powermeter import PowerMeter
from measure.runner.average import AverageRunner
from measure.runner.charging import ChargingRunner
from measure.runner.light import LightRunner
from measure.runner.recorder import RecorderRunner
from measure.runner.runner import MeasurementRunner
from measure.runner.speaker import SpeakerRunner
from measure.util.measure_util import MeasureUtil

sys.path.append(os.path.dirname(os.path.realpath(__file__)))

with open(os.path.join(PROJECT_DIR, ".VERSION")) as f:
    _VERSION = f.read().strip()

config = MeasureConfig()

logging.basicConfig(
    level=logging.getLevelName(config.log_level),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(PROJECT_DIR, "measure.log")),
        logging.StreamHandler(),
    ],
)


MEASURE_TYPE_RUNNER = {
    MeasureType.LIGHT: LightRunner,
    MeasureType.SPEAKER: SpeakerRunner,
    MeasureType.RECORDER: RecorderRunner,
    MeasureType.AVERAGE: AverageRunner,
    MeasureType.CHARGING: ChargingRunner,
}


_LOGGER = logging.getLogger("measure")


class Measure:
    """
    Main class to measure the power usage of a device.

    If you answered yes to generating a model JSON file, a model.json will be created in export/<model-id>
    """

    def __init__(
        self,
        power_meter: PowerMeter,
        config: MeasureConfig,
        console_render: ConsoleRender | None = None,
    ) -> None:
        """This class measures the power consumption of a device.

        Parameters
        ----------
        power_meter : PowerMeter
            The power meter to use.
        console_render : ConsoleRender, optional
            The console renderer to use, by default None
        """
        self.power_meter = power_meter
        self.runner: MeasurementRunner | None = None
        self.measure_type: MeasureType = MeasureType.LIGHT
        self.console_render = console_render
        self.config = config

    def start(self) -> None:
        """Starts the measurement session.

        This method asks the user for the required information, sets up the runner and power meter and starts the measurement
        session.

        Notes
        -----
        This method is the main entry point for the measurement
        session.
        It selects a runner based on the selected device type
        This runner is responsible for executing the measurement session for the given device type

        Examples
        --------
        >>> measure = Measure()
        >>> measure.start()
        """

        _LOGGER.info("Selected powermeter: %s", self.config.selected_power_meter)
        if self.measure_type == MeasureType.LIGHT:
            _LOGGER.info(
                "Selected light controller: %s",
                self.config.selected_light_controller,
            )
        if self.measure_type == MeasureType.SPEAKER:
            _LOGGER.info(
                "Selected media controller: %s",
                self.config.selected_media_controller,
            )

        if self.config.selected_measure_type:
            self.measure_type = MeasureType(self.config.selected_measure_type)
        else:
            self.measure_type = inquirer.list_input(
                "What kind of measurement session do you want to run?",
                choices=[cls.value for cls in MeasureType],
                render=self.console_render,
            )

        measure_util = MeasureUtil(self.power_meter, self.config)
        self.runner = MEASURE_TYPE_RUNNER[self.measure_type](measure_util, self.config)

        answers = self.ask_questions(self.get_questions())
        self.power_meter.process_answers(answers)
        self.runner.prepare(answers)

        if answers.get(QUESTION_DUMMY_LOAD, False):
            measure_util.initialize_dummy_load()

        export_directory = None
        runner_export_directory = self.runner.get_export_directory()
        if runner_export_directory:
            export_directory = os.path.join(
                PROJECT_DIR,
                "export",
                self.runner.get_export_directory(),
            )
            if not os.path.exists(export_directory):
                os.makedirs(export_directory)

        if answers.get(QUESTION_DUMMY_LOAD, False):
            input("Please connect the appliance you want to measure in parallel to the dummy load and press enter to start measurement session...")
        runner_result = self.runner.run(answers, export_directory)
        if not runner_result:
            _LOGGER.error("Some error occurred during the measurement session")

        generate_model_json: bool = answers.get(QUESTION_GENERATE_MODEL_JSON, False) and export_directory

        if generate_model_json:
            try:
                standby_power = self.runner.measure_standby_power()
            except PowerMeterError as error:
                _LOGGER.error("Aborting: %s", error)
                return

            self.write_model_json(
                directory=export_directory,
                standby_power=standby_power,
                name=answers[QUESTION_MODEL_NAME],
                measure_device=answers[QUESTION_MEASURE_DEVICE],
                extra_json_data=runner_result.model_json_data,
            )

        if export_directory and (generate_model_json or isinstance(self.runner, LightRunner)):
            _LOGGER.info(
                "Measurement session finished. Files exported to %s",
                export_directory,
            )

    def write_model_json(
        self,
        directory: str,
        standby_power: float,
        name: str,
        measure_device: str,
        extra_json_data: dict | None = None,
    ) -> None:
        """Write model.json manifest file"""
        json_data = {
            "created_at": datetime.now().isoformat(),
            "measure_device": measure_device,
            "measure_method": "script",
            "measure_description": "Measured with utils/measure script",
            "measure_settings": {
                "VERSION": _VERSION,
                "SAMPLE_COUNT": self.config.sample_count,
                "SLEEP_TIME": self.config.sleep_time,
            },
            "name": name,
            "standby_power": standby_power,
        }
        if extra_json_data:
            json_data.update(extra_json_data)

        json_string = json.dumps(
            json_data,
            indent=2,
            sort_keys=True,
        )
        with open(os.path.join(directory, "model.json"), "w") as json_file:
            json_file.write(json_string)

    def get_questions(self) -> list[Question]:
        """
        Build list of questions to ask.
        Returns generic questions which are asked regardless of the choosen device type
        Additionally the configured runner and power_meter can also provide further questions
        """
        if self.measure_type in [MeasureType.LIGHT, MeasureType.SPEAKER, MeasureType.CHARGING]:
            questions = [
                inquirer.Confirm(
                    name=QUESTION_GENERATE_MODEL_JSON,
                    message="Do you want to generate model.json?",
                    default=True,
                ),
                inquirer.Confirm(
                    name=QUESTION_DUMMY_LOAD,
                    message="Do you want to use a dummy load? This can help to be able to measure standby power and low brightness levels correctly",
                    default=False,
                ),
                inquirer.Text(
                    name=QUESTION_MODEL_NAME,
                    message=f"Specify the full {self.measure_type} model name",
                    ignore=lambda answers: not answers.get(QUESTION_GENERATE_MODEL_JSON),
                    validate=validate_required,
                ),
                inquirer.Text(
                    name=QUESTION_MEASURE_DEVICE,
                    message="Which powermeter (manufacturer, model) do you use to take the measurement?",
                    ignore=lambda answers: not answers.get(QUESTION_GENERATE_MODEL_JSON),
                    validate=validate_required,
                ),
            ]
        else:
            questions = []

        questions.extend(self.runner.get_questions())
        questions.extend(self.power_meter.get_questions())

        return questions

    def ask_questions(self, questions: list[Question]) -> dict[str, Any]:
        """
        Ask question and return a dictionary with the answers.
        It will also check if any predefined answers are defined in .env, and will skip asking these
        """

        # Only ask questions which answers are not predefined in .env file
        questions_to_ask = list(questions)

        predefined_answers = {}
        for question in questions:
            question_name = str(question.name)
            env_var = question_name.upper()
            conf_value = self.config.get_conf_value(env_var)
            if conf_value is not None:
                if isinstance(question, inquirer.Confirm):
                    conf_value = bool(str_to_bool(conf_value))
                predefined_answers[question_name] = conf_value
                questions_to_ask.remove(question)

        answers = inquirer.prompt(questions_to_ask, answers=predefined_answers, render=self.console_render)
        answers.update(predefined_answers)

        _LOGGER.debug("Answers: %s", answers)

        return answers


def config_key_exists(key: str) -> bool:
    """Check whether a certain configuration exists in dot env file"""
    try:
        decouple_config(key)
        return True
    except UndefinedValueError:
        return False


def validate_required(_: Any, val: str) -> bool:  # noqa: ANN401
    """Validation function for the inquirer question, checks if the input has a not empty value"""
    if len(val) == 0:
        raise ValidationError(
            "",
            reason="This question cannot be empty, please put in a value",
        )
    return True


def str_to_bool(value: Any) -> bool:  # noqa: ANN401
    """Return whether the provided string (or any value really) represents true."""
    if not value:
        return False
    return str(value).lower() in ("y", "yes", "t", "true", "on", "1")


def main() -> None:
    print(f"Powercalc measure: {_VERSION}\n")

    try:
        power_meter = PowerMeterFactory(config).create()
        measure = Measure(power_meter, config)

        args = sys.argv[1:]
        if len(args) > 0 and args[0] == "average":
            try:
                duration = int(args[1])
            except IndexError:
                duration = 60
            questions = power_meter.get_questions()
            if questions:
                answers = measure.ask_questions(questions)
                power_meter.process_answers(answers)
            measure_util = MeasureUtil(power_meter, config)
            measure_util.take_average_measurement(duration)
            exit(0)

        measure.start()
        exit(0)
    except (PowerMeterError, LightControllerError, KeyboardInterrupt) as e:
        _LOGGER.error("Aborting: %s", e)
        exit(1)


if __name__ == "__main__":
    main()
