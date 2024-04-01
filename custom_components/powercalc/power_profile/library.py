from __future__ import annotations

import logging
import os
import re
from typing import NamedTuple

from homeassistant.core import HomeAssistant

from custom_components.powercalc.aliases import MANUFACTURER_DIRECTORY_MAPPING
from custom_components.powercalc.const import DATA_PROFILE_LIBRARY, DOMAIN

from .error import LibraryError
from .loader.composite import CompositeLoader
from .loader.local import LocalLoader
from .loader.protocol import Loader
from .loader.remote import RemoteLoader
from .power_profile import DOMAIN_DEVICE_TYPE, PowerProfile

BUILT_IN_DATA_DIRECTORY = os.path.join(os.path.dirname(__file__), "../data")
CUSTOM_DATA_DIRECTORY = "powercalc-custom-models"

_LOGGER = logging.getLogger(__name__)


class ProfileLibrary:
    def __init__(self, hass: HomeAssistant, loader: Loader) -> None:
        self._hass = hass
        self._loader = loader
        self._profiles: dict[str, list[PowerProfile]] = {}
        self._manufacturer_models: dict[str, list[str]] = {}
        self._manufacturer_device_types: dict[str, list] = {}

    async def initialize(self) -> None:
        await self._loader.initialize()

    @staticmethod
    async def factory(hass: HomeAssistant) -> ProfileLibrary:
        """Creates and loads the profile library
        Makes sure it is only loaded once and instance is saved in hass data registry.
        """
        if DOMAIN not in hass.data:
            hass.data[DOMAIN] = {}

        if DATA_PROFILE_LIBRARY in hass.data[DOMAIN]:
            return hass.data[DOMAIN][DATA_PROFILE_LIBRARY]  # type: ignore

        loader = CompositeLoader(
            [
                LocalLoader(hass),
                RemoteLoader(hass),
            ],
        )
        library = ProfileLibrary(hass, loader)
        await library.initialize()
        hass.data[DOMAIN][DATA_PROFILE_LIBRARY] = library
        return library

    async def get_manufacturer_listing(self, entity_domain: str | None = None) -> list[str]:
        """Get listing of available manufacturers."""
        device_type = DOMAIN_DEVICE_TYPE.get(entity_domain) if entity_domain else None
        manufacturers = await self._loader.get_manufacturer_listing(device_type)
        return sorted(manufacturers)

    async def get_model_listing(self, manufacturer: str, entity_domain: str | None = None) -> list[str]:
        """Get listing of available models for a given manufacturer."""
        device_type = DOMAIN_DEVICE_TYPE.get(entity_domain) if entity_domain else None
        cache_key = f"{manufacturer}/{device_type}"
        cached_models = self._manufacturer_models.get(cache_key)
        if cached_models:
            return cached_models
        models = await self._loader.get_model_listing(manufacturer, device_type)
        self._manufacturer_models[cache_key] = sorted(models)
        return self._manufacturer_models[cache_key]

    async def get_profile(
        self,
        model_info: ModelInfo,
        custom_directory: str | None = None,
    ) -> PowerProfile | None:
        """Get a power profile for a given manufacturer and model."""
        # Support multiple LUT in subdirectories
        sub_profile = None
        if "/" in model_info.model:
            (model, sub_profile) = model_info.model.split("/", 1)
            model_info = ModelInfo(model_info.manufacturer, model)

        profile = await self.create_power_profile(model_info, custom_directory)

        if not profile:
            return None

        if sub_profile:
            profile.select_sub_profile(sub_profile)

        return profile

    async def create_power_profile(
        self,
        model_info: ModelInfo,
        custom_directory: str | None = None,
    ) -> PowerProfile | None:
        """Create a power profile object from the model JSON data."""

        manufacturer = model_info.manufacturer
        if manufacturer in MANUFACTURER_DIRECTORY_MAPPING:
            manufacturer = str(MANUFACTURER_DIRECTORY_MAPPING.get(manufacturer))
        manufacturer = manufacturer.lower()

        try:
            resolved_model: str | None = model_info.model
            if not custom_directory:
                resolved_model = await self.find_model(manufacturer, model_info.model)

            if not resolved_model:
                return None

            result = await self._loader.load_model(manufacturer, resolved_model, custom_directory)
            if not result:
                raise LibraryError(f"Model {manufacturer} {resolved_model} not found")
        except LibraryError as e:
            _LOGGER.error("Problem loading model: %s", e)
            return None

        profile = PowerProfile(
            self._hass,
            manufacturer=manufacturer,
            model=resolved_model,
            directory=result[1],
            json_data=result[0],
        )
        # When the power profile supplies multiple sub profiles we select one by default
        if not profile.sub_profile and profile.sub_profile_select:
            profile.select_sub_profile(profile.sub_profile_select.default)

        return profile

    async def find_model(self, manufacturer: str, model: str) -> str | None:
        """Check whether this power profile supports a given model ID.
        Also looks at possible aliases.
        """

        search = {
            model,
            model.replace("#slash#", "/"),
            model.lower(),
            model.lower().replace("#slash#", "/"),
            re.sub(r"^(.*)\(([^()]+)\)$", r"\2", model),
        }

        return await self._loader.find_model(manufacturer, search)


class ModelInfo(NamedTuple):
    manufacturer: str
    model: str
