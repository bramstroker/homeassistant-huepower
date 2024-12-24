from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

import requests

from measure.powermeter.errors import ApiConnectionError, UnsupportedFeatureError
from measure.powermeter.powermeter import PowerMeasurementResult, PowerMeter

_LOGGER = logging.getLogger("measure")


class ShellyApi(ABC):
    @property
    @abstractmethod
    def endpoint(self) -> str: ...

    @abstractmethod
    def parse_json(self, json: dict) -> PowerMeasurementResult: ...


class ShellyApiGen1(ShellyApi):
    @property
    def endpoint(self) -> str:
        return "/status"

    def parse_json(self, json: dict) -> PowerMeasurementResult:
        meter = json["meters"][0]
        return PowerMeasurementResult(power=float(meter["power"]), updated=float(meter["timestamp"]))


class ShellyApiGen2Plus(ShellyApi):
    def __init__(self, ip_address: str, timeout: int) -> None:
        self.ip_address = ip_address
        self.timeout = timeout
        self._endpoint = "/rpc/Switch.GetStatus?id=0"

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def parse_json(self, json: dict, include_voltage: bool = False) -> PowerMeasurementResult:
        if include_voltage:
            return PowerMeasurementResult(power=float(json["apower"]), voltage=float(json["voltage"]), updated=time.time())
        return PowerMeasurementResult(power=float(json["apower"]), updated=time.time())

    def check_gen2_plus_endpoints(self) -> None:
        """
        Checking the endpoint for Gen2+ devices.

        Shelly Gen2+ devices come with different capabilities, and depending on the device type, they may have different API endpoints.
        By default, we try to use the "/rpc/Switch.GetStatus?id=0" endpoint, which is suitable for devices that support switching.
        However, some Gen2+ devices are designed purely for power measurement without any relay (so they can't act as a switch).
        For those devices, the endpoint "/rpc/PM1.GetStatus?id=0" is used instead.
        """
        endpoints = ["/rpc/Switch.GetStatus?id=0", "/rpc/PM1.GetStatus?id=0"]
        for endpoint in endpoints:
            if self._check_endpoint_availability(endpoint):
                self._endpoint = endpoint
                return
        raise ApiConnectionError("Could not find available Shelly Gen2+ endpoint")

    def _check_endpoint_availability(self, endpoint: str) -> bool:
        """Check if the endpoint is available on the Shelly device"""
        try:
            uri = f"http://{self.ip_address}{endpoint}"
            _LOGGER.debug("Checking Gen2+ endpoint: %s", uri)
            response = requests.get(uri, timeout=self.timeout)
            if response.status_code != 200:
                _LOGGER.debug("Problem checking Shelly Gen2+ endpoint, invalid statusCode: %s", response.status_code)
                return False
        except requests.RequestException as e:
            _LOGGER.error("Problem checking Shelly Gen2+ endpoint: %s", e)
            return False

        return True


class ShellyPowerMeter(PowerMeter):
    def __init__(self, shelly_ip: str, timeout: int = 5) -> None:
        self.timeout = timeout
        self.ip_address = shelly_ip
        api_version = self._detect_api_version()
        if api_version == 1:
            self.api = ShellyApiGen1()
        else:
            self.api = ShellyApiGen2Plus(self.ip_address, self.timeout)
            self.api.check_gen2_plus_endpoints()

    def get_power(self, include_voltage: bool = False) -> PowerMeasurementResult:
        """Get a new power reading from the Shelly device. Optionally include voltage."""
        try:
            r = requests.get(
                f"http://{self.ip_address}{self.api.endpoint}",
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            _LOGGER.error("Problem connecting to Shelly plug: %s", e)
            raise ApiConnectionError("Could not connect to Shelly Plug") from e

        json = r.json()

        if include_voltage:
            if isinstance(self.api, ShellyApiGen1):
                raise UnsupportedFeatureError("Voltage measurement is not supported on Shelly Gen1 devices")
            if isinstance(self.api, ShellyApiGen2Plus):
                return self.api.parse_json(json, include_voltage=True)

        return self.api.parse_json(json)

    def _detect_api_version(self) -> int:
        """Check the generation / supported API version. All shelly's should implement the /shelly endpoint"""
        try:
            uri = f"http://{self.ip_address}/shelly"
            _LOGGER.debug("Checking API connection: %s", uri)
            response = requests.get(uri, timeout=self.timeout)
        except requests.RequestException as ex:
            raise ApiConnectionError("Could not connect to Shelly Plug") from ex

        if response.status_code != 200:
            raise ApiConnectionError(
                "Could not connect to Shelly Plug, invalid statusCode",
            )

        json = response.json()
        gen = json.get("gen", 1)
        _LOGGER.debug("Shelly API version %d detected", gen)
        return int(gen)

    def has_voltage_support(self) -> bool:
        return isinstance(self.api, ShellyApiGen2Plus)

    def process_answers(self, answers: dict[str, Any]) -> None:
        pass
