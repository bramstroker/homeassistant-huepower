"""Platform for sensor integration."""

from __future__ import annotations

import logging
from typing import Final

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components import (
    binary_sensor,
    climate,
    device_tracker,
    fan,
    group,
    input_boolean,
    input_number,
    input_select,
    light,
    media_player,
    remote,
    sensor,
    switch,
    vacuum,
    water_heater,
)
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.components.utility_meter.const import METER_TYPES
from homeassistant.const import CONF_ENTITIES, CONF_ENTITY_ID, CONF_NAME
from homeassistant.core import split_entity_id
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import (
    ConfigType,
    DiscoveryInfoType,
    HomeAssistantType,
)
from homeassistant.helpers import (
    area_registry,
    device_registry,
    entity,
    entity_registry,
)

from .common import validate_name_pattern, create_source_entity
from .const import (
    CALCULATION_MODES,
    CONF_AREA,
    CONF_CREATE_ENERGY_SENSOR,
    CONF_CREATE_ENERGY_SENSORS,
    CONF_CREATE_GROUP,
    CONF_CREATE_UTILITY_METERS,
    CONF_CUSTOM_MODEL_DIRECTORY,
    CONF_DISABLE_STANDBY_POWER,
    CONF_DISABLE_STANDBY_USAGE,
    CONF_ENERGY_SENSOR_NAMING,
    CONF_FIXED,
    CONF_GROUP,
    CONF_INCLUDE,
    CONF_LINEAR,
    CONF_MANUFACTURER,
    CONF_MODE,
    CONF_MODEL,
    CONF_MULTIPLY_FACTOR,
    CONF_MULTIPLY_FACTOR_STANDBY,
    CONF_POWER_SENSOR_NAMING,
    CONF_STANDBY_POWER,
    CONF_STANDBY_USAGE,
    CONF_UTILITY_METER_TYPES,
    DATA_CONFIGURED_ENTITIES,
    DATA_DISCOVERED_ENTITIES,
    DISCOVERY_SOURCE_ENTITY,
    DOMAIN,
    DOMAIN_CONFIG,
)
from .errors import PowercalcSetupError, SensorConfigurationError
from .model_discovery import is_supported_model
from .sensors.energy import VirtualEnergySensor, create_energy_sensor
from .sensors.group import GroupedEnergySensor, GroupedPowerSensor, GroupedSensor
from .sensors.power import VirtualPowerSensor, create_power_sensor
from .sensors.utility_meter import create_utility_meters
from .strategy_fixed import CONFIG_SCHEMA as FIXED_SCHEMA
from .strategy_linear import CONFIG_SCHEMA as LINEAR_SCHEMA

_LOGGER = logging.getLogger(__name__)

SUPPORTED_ENTITY_DOMAINS = (
    light.DOMAIN,
    switch.DOMAIN,
    fan.DOMAIN,
    binary_sensor.DOMAIN,
    climate.DOMAIN,
    device_tracker.DOMAIN,
    remote.DOMAIN,
    media_player.DOMAIN,
    input_boolean.DOMAIN,
    input_number.DOMAIN,
    input_select.DOMAIN,
    sensor.DOMAIN,
    vacuum.DOMAIN,
    water_heater.DOMAIN,
)

SENSOR_CONFIG = {
    vol.Optional(CONF_NAME): cv.string,
    vol.Optional(CONF_ENTITY_ID): cv.entity_domain(SUPPORTED_ENTITY_DOMAINS),
    vol.Optional(CONF_MODEL): cv.string,
    vol.Optional(CONF_MANUFACTURER): cv.string,
    vol.Optional(CONF_MODE): vol.In(CALCULATION_MODES),
    vol.Optional(CONF_STANDBY_POWER): vol.Coerce(float),
    vol.Optional(CONF_DISABLE_STANDBY_POWER, default=False): cv.boolean,
    vol.Optional(CONF_STANDBY_USAGE): vol.Coerce(float),
    vol.Optional(CONF_DISABLE_STANDBY_USAGE, default=False): cv.boolean,
    vol.Optional(CONF_CUSTOM_MODEL_DIRECTORY): cv.string,
    vol.Optional(CONF_FIXED): FIXED_SCHEMA,
    vol.Optional(CONF_LINEAR): LINEAR_SCHEMA,
    vol.Optional(CONF_CREATE_ENERGY_SENSOR): cv.boolean,
    vol.Optional(CONF_CREATE_UTILITY_METERS): cv.boolean,
    vol.Optional(CONF_UTILITY_METER_TYPES): vol.All(
        cv.ensure_list, [vol.In(METER_TYPES)]
    ),
    vol.Optional(CONF_MULTIPLY_FACTOR): vol.Coerce(float),
    vol.Optional(CONF_MULTIPLY_FACTOR_STANDBY, default=False): cv.boolean,
    vol.Optional(CONF_POWER_SENSOR_NAMING): validate_name_pattern,
    vol.Optional(CONF_ENERGY_SENSOR_NAMING): validate_name_pattern,
}

GROUPED_SENSOR_CONFIG = {
    vol.Optional(CONF_CREATE_GROUP): cv.string,
    vol.Optional(CONF_INCLUDE, default={}): vol.Schema({
        vol.Optional(CONF_AREA): cv.string,
        vol.Optional(CONF_GROUP): cv.string
    }),
    vol.Optional(CONF_ENTITIES, None): vol.All(cv.ensure_list, [SENSOR_CONFIG]),
}

PLATFORM_SCHEMA: Final = vol.All(
    cv.has_at_least_one_key(CONF_ENTITY_ID, CONF_ENTITIES, CONF_INCLUDE),
    cv.deprecated(
        CONF_DISABLE_STANDBY_USAGE, replacement_key=CONF_DISABLE_STANDBY_POWER
    ),
    cv.deprecated(CONF_STANDBY_USAGE, replacement_key=CONF_STANDBY_POWER),
    PLATFORM_SCHEMA.extend(
        {
            **SENSOR_CONFIG,
            **GROUPED_SENSOR_CONFIG,
        }
    ),
)

ENTITY_ID_FORMAT = SENSOR_DOMAIN + ".{}"


async def async_setup_platform(
    hass: HomeAssistantType,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
):
    """Set up the virtual power sensors."""

    try:
        entities = await create_sensors(hass, config, discovery_info)
    except SensorConfigurationError as err:
        _LOGGER.error(err)
        return

    if entities:
        async_add_entities(entities)


def get_merged_sensor_configuration(*configs: dict) -> dict:
    """Merges configuration from multiple levels (sensor, group, global) into a single dict"""

    merged_config = {}
    for config in configs:
        merged_config.update(config)

    if CONF_STANDBY_USAGE in merged_config:
        merged_config[CONF_STANDBY_POWER] = merged_config[CONF_STANDBY_USAGE]
    if CONF_DISABLE_STANDBY_USAGE in merged_config:
        merged_config[CONF_DISABLE_STANDBY_POWER] = merged_config[
            CONF_DISABLE_STANDBY_USAGE
        ]

    if not CONF_CREATE_ENERGY_SENSOR in merged_config:
        merged_config[CONF_CREATE_ENERGY_SENSOR] = merged_config.get(
            CONF_CREATE_ENERGY_SENSORS
        )

    if not CONF_ENTITY_ID in merged_config:
        raise SensorConfigurationError(
            "You must supply an entity_id in the configuration, see the README"
        )

    return merged_config


async def create_sensors(
    hass: HomeAssistantType, config: ConfigType, discovery_info: DiscoveryInfoType | None = None,
) -> list[SensorEntity]:
    """Main routine to create all sensors (power, energy, utility, group) for a given entity"""

    global_config = hass.data[DOMAIN][DOMAIN_CONFIG]

    # Setup a power sensor for one single appliance. Either by manual configuration or discovery
    if CONF_ENTITY_ID in config or discovery_info is not None:
        if discovery_info:
            config[CONF_ENTITY_ID] = discovery_info[CONF_ENTITY_ID]
        merged_sensor_config = get_merged_sensor_configuration(
            global_config, config
        )
        return await create_individual_sensors(hass, merged_sensor_config, discovery_info)
    
    # Setup power sensors for multiple appliances in one config entry
    created_sensors = []
    sensor_configs = {}
    if CONF_ENTITIES in config:
        sensor_configs = {conf[CONF_ENTITY_ID]: conf for conf in config.get(CONF_ENTITIES)}
    
    if CONF_INCLUDE in config:

        # Include entities from a certain area
        if CONF_AREA in config.get(CONF_INCLUDE):
            area_id = config.get(CONF_INCLUDE)[CONF_AREA]
            _LOGGER.debug("Loading entities from area: %s", area_id)
            sensor_configs = {
                entity.entity_id: {CONF_ENTITY_ID: entity.entity_id}
                for entity 
                in await get_area_entities(hass, area_id)
                if await is_supported_model(hass, entity)
            } | sensor_configs
        
        # Include entities from a certain group
        if CONF_GROUP in config.get(CONF_INCLUDE):
            group_id = config.get(CONF_INCLUDE)[CONF_GROUP]
            _LOGGER.debug("Loading entities from group: %s", group_id)
            sensor_configs = {
                entity.entity_id: {CONF_ENTITY_ID: entity.entity_id}
                for entity 
                in await get_group_entities(hass, group_id)
                if await is_supported_model(hass, entity)
            } | sensor_configs
            
    # Create sensors for each entity
    for sensor_config in sensor_configs.values():
        merged_sensor_config = get_merged_sensor_configuration(
            global_config, config, sensor_config
        )
        created_sensors.extend(
            await create_individual_sensors(hass, merged_sensor_config)
        )

    # Create group sensors (power, energy, utility)
    if CONF_CREATE_GROUP in config:
        group_name = config.get(CONF_CREATE_GROUP)
        if not created_sensors:
            _LOGGER.error("Could not create group %s, no entities resolved", group_name)
        group_sensors = create_group_sensors(
            group_name, merged_sensor_config, created_sensors, hass=hass
        )
        created_sensors.extend(group_sensors)
    
    return created_sensors


async def create_individual_sensors(
    hass: HomeAssistantType, sensor_config: dict, discovery_info: DiscoveryInfoType | None = None,
) -> list[SensorEntity]:
    """Create entities (power, energy, utility_meters) which track the appliance."""

    if discovery_info:
        source_entity = discovery_info.get(DISCOVERY_SOURCE_ENTITY)
    else:
        source_entity = await create_source_entity(sensor_config[CONF_ENTITY_ID], hass)

    if source_entity.entity_id in hass.data[DOMAIN][DATA_CONFIGURED_ENTITIES]:
        # Display an error when a power sensor was already configured for the same entity by the user
        # No log entry will be shown when the entity was auto discovered, we can silently continue
        if not discovery_info:
            _LOGGER.error("%s: This entity has already configured a power sensor", source_entity.entity_id)
        return []

    try:
        power_sensor = await create_power_sensor(hass, sensor_config, source_entity, discovery_info)
    except PowercalcSetupError as err:
        return []

    entities_to_add = [power_sensor]

    if sensor_config.get(CONF_CREATE_ENERGY_SENSOR):
        energy_sensor = await create_energy_sensor(
            hass, sensor_config, power_sensor, source_entity
        )
        entities_to_add.append(energy_sensor)
        entities_to_add.extend(
            create_utility_meters(hass, energy_sensor, sensor_config)
        )

    if discovery_info:
        hass.data[DOMAIN][DATA_DISCOVERED_ENTITIES].append(source_entity.entity_id)
    else:
        hass.data[DOMAIN][DATA_CONFIGURED_ENTITIES].append(source_entity.entity_id)

    return entities_to_add


def create_group_sensors(
    group_name: str,
    sensor_config: dict,
    entities: list[SensorEntity],
    hass: HomeAssistantType,
) -> list[GroupedSensor]:
    """Create grouped power and energy sensors."""

    group_sensors = []

    power_sensors = list(
        filter(lambda elm: isinstance(elm, VirtualPowerSensor), entities)
    )
    power_sensor_ids = list(map(lambda x: x.entity_id, power_sensors))
    name_pattern = sensor_config.get(CONF_POWER_SENSOR_NAMING)
    name = name_pattern.format(group_name)
    group_sensors.append(GroupedPowerSensor(name, power_sensor_ids, hass))
    _LOGGER.debug("Creating grouped power sensor: %s", name)

    energy_sensors = list(
        filter(lambda elm: isinstance(elm, VirtualEnergySensor), entities)
    )
    energy_sensor_ids = list(map(lambda x: x.entity_id, energy_sensors))
    name_pattern = sensor_config.get(CONF_ENERGY_SENSOR_NAMING)
    name = name_pattern.format(group_name)
    group_energy_sensor = GroupedEnergySensor(
        name, energy_sensor_ids, hass, rounding_digits=4
    )
    group_sensors.append(group_energy_sensor)
    _LOGGER.debug("Creating grouped energy sensor: %s", name)

    group_sensors.extend(
        create_utility_meters(hass, group_energy_sensor, sensor_config)
    )

    return group_sensors


async def get_area_entities(hass: HomeAssistantType, area_id: str) -> list[entity_registry.RegistryEntry]:
    """Get a listing of al entities in a given area"""
    area_reg = area_registry.async_get(hass)
    if area_reg.async_get_area(area_id) is None:
        raise SensorConfigurationError(f"No area with id '{area_id}' found in your HA instance")

    entity_reg = entity_registry.async_get(hass)

    entities = entity_registry.async_entries_for_area(entity_reg, area_id)

    device_reg = device_registry.async_get(hass)
    # We also need to add entities tied to a device in the area that don't themselves
    # have an area specified since they inherit the area from the device.
    entities.extend(
        [
            entity
            for device in device_registry.async_entries_for_area(device_reg, area_id)
            for entity in entity_registry.async_entries_for_device(entity_reg, device.id)
            if entity.area_id is None
        ]
    )
    return entities


async def get_group_entities(hass: HomeAssistantType, entity_id: str) -> list[entity_registry.RegistryEntry]:
    """Get a listing of all entities in a given group"""
    entity_reg = entity_registry.async_get(hass)

    group_state = hass.states.get(entity_id)
    if not group_state:
        _LOGGER.error("Group %s does not exist, make sure you set the correct entity id", entity_id)

    entity_ids = group.get_entity_ids(hass, entity_id, light.DOMAIN)
    entities = []
    return entities