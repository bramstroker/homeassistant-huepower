"""Config flow for Adaptive Lighting integration."""

from __future__ import annotations
import logging
import copy
from numpy import isin

import voluptuous as vol

from typing import Any
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import (
    CONF_ATTRIBUTE,
    CONF_ENTITY_ID,
    CONF_IF,
    CONF_NAME,
    CONF_UNIQUE_ID,
    CONF_UNIT_OF_MEASUREMENT,
    ENERGY_KILO_WATT_HOUR,
    POWER_WATT,
)
from homeassistant.helpers import selector
from homeassistant.config_entries import data_entry_flow, ConfigEntry, OptionsFlow
import homeassistant.helpers.config_validation as cv
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_CREATE_UTILITY_METERS,
    CONF_DAILY_FIXED_ENERGY,
    CONF_FIXED,
    CONF_FIXED_RAW,
    CONF_POWER_TEMPLATE,
    CONF_CALIBRATE,
    CONF_LINEAR,
    CONF_MIN_POWER,
    CONF_MAX_POWER,
    CONF_GAMMA_CURVE,
    CONF_SENSOR_TYPE,
    CONF_ON_TIME,
    CONF_START_TIME,
    CONF_VALUE_TEMPLATE,
    CONF_UPDATE_FREQUENCY,
    CONF_WLED,
    DOMAIN,
    CONF_MODE,
    CONF_VALUE,
    CONF_STANDBY_POWER,
    CALCULATION_MODES,
    MODE_FIXED,
    CONF_CREATE_ENERGY_SENSOR,
    CONF_POWER,
    MODE_LINEAR,
    MODE_WLED,
    SensorType
)
from .common import SourceEntity, create_source_entity
from .sensors.daily_energy import DEFAULT_DAILY_UPDATE_FREQUENCY
from .strategy.wled import CONFIG_SCHEMA as SCHEMA_POWER_WLED

_LOGGER = logging.getLogger(__name__)

SENSOR_TYPE_MENU = {
    SensorType.DAILY_ENERGY: "Daily energy",
    SensorType.VIRTUAL_POWER: "Virtual power",
    #SensorType.GROUP: "Group"
}

SCHEMA_DAILY_ENERGY_OPTIONS = vol.Schema(
    {
        vol.Optional(CONF_VALUE): vol.Coerce(float),
        vol.Optional(CONF_VALUE_TEMPLATE): selector.TemplateSelector(),
        vol.Optional(CONF_UNIT_OF_MEASUREMENT, default=ENERGY_KILO_WATT_HOUR): vol.In(
            [ENERGY_KILO_WATT_HOUR, POWER_WATT]
        ),
        vol.Optional(CONF_ON_TIME): selector.DurationSelector(selector.DurationSelectorConfig(enable_day=False)),
        #vol.Optional(CONF_START_TIME): selector.TimeSelector(),
        vol.Optional(
            CONF_UPDATE_FREQUENCY, default=DEFAULT_DAILY_UPDATE_FREQUENCY
        ): vol.Coerce(int),
    }
)
SCHEMA_DAILY_ENERGY = vol.Schema(
    {
        vol.Required(CONF_NAME): str,
        vol.Optional(CONF_UNIQUE_ID): cv.string,
    }
).extend(SCHEMA_DAILY_ENERGY_OPTIONS.schema)

SCHEMA_POWER_OPTIONS = vol.Schema({
    vol.Optional(CONF_STANDBY_POWER): vol.Coerce(float),
    vol.Optional(CONF_CREATE_ENERGY_SENSOR, default=True): cv.boolean,
    vol.Optional(CONF_CREATE_UTILITY_METERS, default=False): cv.boolean
})

SCHEMA_POWER = vol.Schema({
    vol.Required(CONF_ENTITY_ID): selector.EntitySelector(),
    vol.Optional(CONF_NAME): str,
    vol.Optional(CONF_UNIQUE_ID): cv.string,
    vol.Optional(CONF_MODE, default=MODE_FIXED): selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=CALCULATION_MODES,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    ),
}).extend(SCHEMA_POWER_OPTIONS.schema)

SCHEMA_POWER_FIXED = vol.Schema({
    vol.Optional(CONF_POWER): vol.Coerce(float),
    vol.Optional(CONF_POWER_TEMPLATE): selector.TemplateSelector(),
    vol.Optional(CONF_FIXED_RAW): selector.ObjectSelector()
})

SCHEMA_POWER_LINEAR = vol.Schema({
    # vol.Optional(CONF_CALIBRATE): vol.All(
    #     cv.ensure_list, [vol.Match("^[0-9]+ -> ([0-9]*[.])?[0-9]+$")]
    # ),
    vol.Optional(CONF_MIN_POWER): vol.Coerce(float),
    vol.Optional(CONF_MAX_POWER): vol.Coerce(float),
    vol.Optional(CONF_GAMMA_CURVE): vol.Coerce(float),
    vol.Optional(CONF_CALIBRATE): selector.TextSelector(selector.TextSelectorConfig(multiline=True))
})

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Adaptive Lighting."""

    VERSION = 1

    def __init__(self):
        """Initialize options flow."""
        self.sensor_config: dict[str, Any] = dict()
        self.selected_sensor_type: str = None
        self.name: str = None
        self.source_entity: SourceEntity = None
        self.entity_id: str = None
    
    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle the initial step."""

        return self.async_show_menu(
            step_id="user", menu_options=SENSOR_TYPE_MENU
        )

    async def async_step_virtual_power(self, user_input: dict[str,str] = None) -> FlowResult:
        if user_input is not None:
            self.entity_id = user_input[CONF_ENTITY_ID]
            self.source_entity = await create_source_entity(self.entity_id, self.hass)
            unique_id = user_input.get(CONF_UNIQUE_ID) or self.source_entity.unique_id or self.entity_id

            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            self.name = user_input.get(CONF_NAME) or self.source_entity.name
            self.selected_sensor_type = SensorType.VIRTUAL_POWER
            self.sensor_config.update(user_input)

            if user_input.get(CONF_MODE) == MODE_FIXED:
                return await self.async_step_fixed()
            
            if user_input.get(CONF_MODE) == MODE_LINEAR:
                return await self.async_step_linear()
            
            if user_input.get(CONF_MODE) == MODE_WLED:
                return await self.async_step_wled()
            
        return self.async_show_form(
            step_id="virtual_power",
            data_schema=SCHEMA_POWER,
            errors={},
        )
    
    async def async_step_daily_energy(self, user_input: dict[str,str] = None) -> FlowResult:
        errors = _validate_daily_energy_input(user_input)

        if user_input is not None and not errors:
            unique_id = user_input.get(CONF_UNIQUE_ID) or user_input.get(CONF_NAME)
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            self.selected_sensor_type = SensorType.DAILY_ENERGY
            self.name = user_input.get(CONF_NAME)

            self.sensor_config.update({CONF_DAILY_FIXED_ENERGY: _build_daily_energy_config(user_input)})
            return self.create_config_entry()
            
        return self.async_show_form(
            step_id="daily_energy",
            data_schema=SCHEMA_DAILY_ENERGY,
            errors=errors,
        )
    
    async def async_step_fixed(self, user_input: dict[str,str] = None) -> FlowResult:
        if user_input is not None:
            if CONF_FIXED_RAW in user_input:
                fixed_config = user_input[CONF_FIXED_RAW]
            else:
                power = user_input.get(CONF_POWER) or user_input.get(CONF_POWER_TEMPLATE)
                fixed_config = {CONF_POWER: power}

            self.sensor_config.update({CONF_FIXED: fixed_config})
            return self.create_config_entry()

        return self.async_show_form(
            step_id="fixed",
            data_schema=SCHEMA_POWER_FIXED,
            errors={},
        )
    
    async def async_step_linear(self, user_input: dict[str,str] = None) -> FlowResult:
        errors = _validate_linear_input(user_input)

        if user_input is not None and not errors:
            linear_config = user_input
            self.sensor_config.update({CONF_LINEAR: linear_config})
            return self.create_config_entry()

        config_schema = SCHEMA_POWER_LINEAR.extend(
            {
                vol.Optional(CONF_ATTRIBUTE): selector.AttributeSelector(selector.AttributeSelectorConfig(entity_id=self.entity_id))
            }
        )
        return self.async_show_form(
            step_id="linear",
            data_schema=config_schema,
            errors=errors,
        )
    
    async def async_step_wled(self, user_input: dict[str,str] = None) -> FlowResult:
        if user_input is not None:
            self.sensor_config.update({CONF_WLED: user_input})
            return self.create_config_entry()

        return self.async_show_form(
            step_id="wled",
            data_schema=SCHEMA_POWER_WLED,
            errors={},
        )
    
    @callback
    def create_config_entry(self):
        self.sensor_config.update({"sensor_type": self.selected_sensor_type})
        if self.name:
            self.sensor_config.update({CONF_NAME: self.name})
        if self.entity_id:
            self.sensor_config.update({CONF_ENTITY_ID: self.entity_id})
        if self.unique_id:
            self.sensor_config.update({CONF_UNIQUE_ID: self.unique_id})
        return self.async_create_entry(
            title=self.name, data=self.sensor_config
        )


class OptionsFlowHandler(OptionsFlow):
    """Handle an option flow for PowerCalc."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        self.current_config: dict = dict(config_entry.data)
        self.sensor_type: SensorType = self.current_config.get("sensor_type") or SensorType.VIRTUAL_POWER

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle options flow."""

        self.current_config = dict(self.config_entry.data)

        if user_input is not None:
            self.save_options(user_input)
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=self.build_options_schema(),
            errors={},
        )

    def save_options(self, user_input):
        if self.sensor_type == SensorType.DAILY_ENERGY:
            daily_energy_config = _build_daily_energy_config(user_input)
            self.current_config.update({CONF_DAILY_FIXED_ENERGY: daily_energy_config})
        
        if self.sensor_type == SensorType.VIRTUAL_POWER:
            self.current_config.update(
                {
                    CONF_CREATE_ENERGY_SENSOR: user_input.get(CONF_CREATE_ENERGY_SENSOR),
                    CONF_CREATE_UTILITY_METERS: user_input.get(CONF_CREATE_UTILITY_METERS),
                    CONF_STANDBY_POWER: user_input.get(CONF_STANDBY_POWER)
                }
            )
            strategy_schema = self.get_strategy_schema()
            strategy_config_key = self.get_strategy_config_key()
            strategy_options = {}
            for key in strategy_schema.schema.keys():
                strategy_options[str(key)] = user_input.get(key)

            self.current_config.update({strategy_config_key: strategy_options})

        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data=self.current_config
        )

    def get_strategy_schema(self) -> vol.Schema:
        strategy = self.current_config.get(CONF_MODE)
        if strategy == MODE_FIXED:
            return SCHEMA_POWER_FIXED
        if strategy == MODE_LINEAR:
            return SCHEMA_POWER_LINEAR
        if strategy == MODE_WLED:
            return SCHEMA_POWER_WLED
        return SCHEMA_POWER_FIXED
    
    def get_strategy_config_key(self) -> str:
        strategy = self.current_config.get(CONF_MODE)
        if strategy == MODE_FIXED:
            return CONF_FIXED
        if strategy == MODE_LINEAR:
            return CONF_LINEAR
        if strategy == MODE_WLED:
            return CONF_WLED
        return CONF_FIXED

    def build_options_schema(self) -> vol.Schema:
        """Build the options schema. depending on the selected sensor type"""

        strategy_options = {}
        if self.sensor_type == SensorType.VIRTUAL_POWER:
            base_power_schema = SCHEMA_POWER_OPTIONS
            strategy_schema = self.get_strategy_schema()
            data_schema = base_power_schema.extend(strategy_schema.schema)
            strategy_config_key = self.get_strategy_config_key()
            strategy_options = self.current_config.get(strategy_config_key)

        if self.sensor_type == SensorType.DAILY_ENERGY:
            data_schema = SCHEMA_DAILY_ENERGY_OPTIONS
            strategy_options = self.current_config[CONF_DAILY_FIXED_ENERGY]
        
        data_schema = _fill_schema_defaults(data_schema, self.current_config | strategy_options)
        return data_schema


def _build_daily_energy_config(user_input: dict[str,str] = None) -> dict[str, Any]:
    config = user_input
    config.update({CONF_VALUE: user_input.get(CONF_VALUE, user_input.get(CONF_VALUE_TEMPLATE))})
    return config

def _validate_daily_energy_input(user_input: dict[str, str] = None) -> dict:
    if not user_input:
        return {}
    errors = {}

    if not CONF_VALUE in user_input and not CONF_VALUE_TEMPLATE in user_input:
        errors["base"] = "daily_energy_mandatory"
    
    return errors

def _validate_linear_input(linear_input: dict[str, str] = None) -> dict:
    if not linear_input:
        return {}
    errors = {}

    if not CONF_MAX_POWER in linear_input and not CONF_CALIBRATE in linear_input:
        errors["base"] = "linear_mandatory"
    
    return errors

def _fill_schema_defaults(data_schema: vol.Schema, options: dict[str, str]):
    # Make a copy of the schema with suggested values set to saved options
    schema = {}
    for key, val in data_schema.schema.items():
        new_key = key
        if key in options:
            if isinstance(key, vol.Marker):
                if isinstance(key, vol.Optional) and callable(key.default) and key.default():
                    new_key = vol.Optional(key.schema, default=options.get(key))
                else:
                    new_key = copy.copy(key)
                    new_key.description = {"suggested_value": options.get(key)}
        schema[new_key] = val
    data_schema = vol.Schema(schema)
    return data_schema