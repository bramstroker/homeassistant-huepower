from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any, Callable

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import ATTR_STATE_CLASS
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_DOMAIN,
    CONF_ENTITIES,
    CONF_NAME,
    CONF_UNIQUE_ID,
    ENERGY_KILO_WATT_HOUR,
    ENERGY_MEGA_WATT_HOUR,
    ENERGY_WATT_HOUR,
    EVENT_HOMEASSISTANT_STOP,
    POWER_WATT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CoreState, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.json import JSONEncoder
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.singleton import singleton
from homeassistant.helpers.storage import Store
from homeassistant.util.unit_conversion import (
    BaseUnitConverter,
    EnergyConverter,
    PowerConverter,
)
from ..common import create_source_entity

from ..const import (
    ATTR_ENTITIES,
    ATTR_IS_GROUP,
    CONF_DISABLE_EXTENDED_ATTRIBUTES,
    CONF_ENERGY_SENSOR_PRECISION,
    CONF_ENERGY_SENSOR_UNIT_PREFIX,
    CONF_GROUP,
    CONF_GROUP_ENERGY_ENTITIES,
    CONF_GROUP_MEMBER_SENSORS,
    CONF_GROUP_POWER_ENTITIES,
    CONF_HIDE_MEMBERS,
    CONF_POWER_SENSOR_PRECISION,
    CONF_SENSOR_TYPE,
    CONF_SUB_GROUPS,
    DATA_STANDBY_POWER_SENSORS,
    DOMAIN,
    DUMMY_ENTITY_ID, ENTRY_DATA_ENERGY_ENTITY,
    ENTRY_DATA_POWER_ENTITY,
    SERVICE_RESET_ENERGY,
    SIGNAL_POWER_SENSOR_STATE_CHANGE,
    SensorType,
    UnitPrefix,
)
from .abstract import (
    BaseEntity,
    generate_energy_sensor_entity_id,
    generate_energy_sensor_name,
    generate_power_sensor_entity_id,
    generate_power_sensor_name,
)
from .energy import EnergySensor, create_energy_sensor
from .power import PowerSensor
from .utility_meter import create_utility_meters

ENTITY_ID_FORMAT = SENSOR_DOMAIN + ".{}"

_LOGGER = logging.getLogger(__name__)
STORAGE_KEY = "powercalc_group"
STORAGE_VERSION = 1
# How long between periodically saving the current states to disk
STATE_DUMP_INTERVAL = timedelta(minutes=10)


async def create_group_sensors(
    group_name: str,
    sensor_config: dict[str, Any],
    entities: list[Entity],
    hass: HomeAssistant,
    filters: list[Callable, None] = None,
) -> list[GroupedSensor]:
    """Create grouped power and energy sensors."""

    if filters is None:
        filters = []

    def _get_filtered_entity_ids_by_class(
        all_entities: list, default_filters: list[Callable], class_name
    ) -> list[str]:
        filter_list = default_filters.copy()
        filter_list.append(lambda elm: not isinstance(elm, GroupedSensor))
        filter_list.append(lambda elm: isinstance(elm, class_name))
        return [
            x.entity_id
            for x in filter(
                lambda x: all(f(x) for f in filter_list),
                all_entities,
            )
        ]

    group_sensors = []

    power_sensor_ids = _get_filtered_entity_ids_by_class(entities, filters, PowerSensor)
    power_sensor = create_grouped_power_sensor(
        hass, group_name, sensor_config, set(power_sensor_ids)
    )
    group_sensors.append(power_sensor)

    energy_sensor_ids = _get_filtered_entity_ids_by_class(
        entities, filters, EnergySensor
    )
    energy_sensor = create_grouped_energy_sensor(
        hass, group_name, sensor_config, set(energy_sensor_ids)
    )
    group_sensors.append(energy_sensor)

    group_sensors.extend(
        await create_utility_meters(
            hass, energy_sensor, sensor_config, net_consumption=True
        )
    )

    return group_sensors


async def create_group_sensors_from_config_entry(
    hass: HomeAssistant, entry: ConfigEntry, sensor_config: dict
) -> list[GroupedSensor]:
    """Create group sensors based on a config_entry"""
    group_sensors = []

    group_name = entry.data.get(CONF_NAME)

    if CONF_UNIQUE_ID not in sensor_config:
        sensor_config[CONF_UNIQUE_ID] = entry.entry_id

    power_sensor_ids: set[str] = set(
        resolve_entity_ids_recursively(hass, entry, SensorDeviceClass.POWER)
    )
    if power_sensor_ids:
        power_sensor = create_grouped_power_sensor(
            hass, group_name, sensor_config, power_sensor_ids
        )
        group_sensors.append(power_sensor)

    energy_sensor_ids: set[str] = set(
        resolve_entity_ids_recursively(hass, entry, SensorDeviceClass.ENERGY)
    )
    if energy_sensor_ids:
        energy_sensor = create_grouped_energy_sensor(
            hass, group_name, sensor_config, energy_sensor_ids
        )
        group_sensors.append(energy_sensor)

        group_sensors.extend(
            await create_utility_meters(
                hass, energy_sensor, sensor_config, net_consumption=True
            )
        )

    return group_sensors


async def create_general_standby_sensors(hass: HomeAssistant, config: ConfigType) -> list[Entity]:
    sensor_config = config.copy()
    power_sensor = StandbyPowerSensor(hass, rounding_digits=sensor_config.get(CONF_POWER_SENSOR_PRECISION))
    power_sensor.entity_id = "sensor.all_standby_power"
    sensor_config[CONF_NAME] = "All standby"
    source_entity = await create_source_entity(DUMMY_ENTITY_ID, hass)
    energy_sensor = await create_energy_sensor(hass, sensor_config, power_sensor, source_entity)
    return [power_sensor, energy_sensor]


async def create_domain_group_sensor(hass: HomeAssistant, discovery_info: DiscoveryInfoType, config: ConfigType) -> list[Entity]:
    domain = discovery_info[CONF_DOMAIN]
    sensor_config = config.copy()
    sensor_config[
        CONF_UNIQUE_ID
    ] = f"powercalc_domaingroup_{discovery_info[CONF_DOMAIN]}"
    return await create_group_sensors(
        f"All {domain}", sensor_config, discovery_info[CONF_ENTITIES], hass
    )


async def remove_power_sensor_from_associated_groups(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> list[ConfigEntry]:
    """
    When the user remove a virtual power config entry we need to update all the groups which this sensor belongs to
    """
    group_entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data.get(CONF_SENSOR_TYPE) == SensorType.GROUP
        and config_entry.entry_id in (entry.data.get(CONF_GROUP_MEMBER_SENSORS) or [])
    ]

    for group_entry in group_entries:
        member_sensors = group_entry.data.get(CONF_GROUP_MEMBER_SENSORS) or []
        member_sensors.remove(config_entry.entry_id)

        hass.config_entries.async_update_entry(
            group_entry,
            data={**group_entry.data, CONF_GROUP_MEMBER_SENSORS: member_sensors},
        )

    return group_entries


async def remove_group_from_power_sensor_entry(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> list[ConfigEntry]:
    """
    When the user removes a group config entry we need to update all the virtual power sensors which reference this group
    """
    entries_to_update = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data.get(CONF_SENSOR_TYPE) == SensorType.VIRTUAL_POWER
        and entry.data.get(CONF_GROUP) == config_entry.entry_id
    ]

    for group_entry in entries_to_update:
        hass.config_entries.async_update_entry(
            group_entry,
            data={**group_entry.data, CONF_GROUP: None},
        )

    return entries_to_update


async def add_to_associated_group(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> ConfigEntry | None:
    """
    When the user has set a group on a virtual power config entry,
    we need to add this config entry to the group members sensors and update the group
    """
    sensor_type = config_entry.data.get(CONF_SENSOR_TYPE)
    if sensor_type != SensorType.VIRTUAL_POWER:
        return None

    if CONF_GROUP not in config_entry.data:
        return None

    group_entry_id = config_entry.data.get(CONF_GROUP)
    group_entry = hass.config_entries.async_get_entry(group_entry_id)

    if not group_entry:
        _LOGGER.warning(
            f"ConfigEntry {config_entry.title}: Cannot add/remove to group {group_entry_id}. It does not exist."
        )
        return None

    member_sensors = set(group_entry.data.get(CONF_GROUP_MEMBER_SENSORS) or [])
    if config_entry.entry_id not in member_sensors:
        member_sensors.add(config_entry.entry_id)

    hass.config_entries.async_update_entry(
        group_entry,
        data={**group_entry.data, CONF_GROUP_MEMBER_SENSORS: list(member_sensors)},
    )
    return group_entry


@callback
def resolve_entity_ids_recursively(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device_class: SensorDeviceClass,
    resolved_ids: list[str] | None = None,
) -> list[str]:
    """
    Get all the entity id's for the current group and all the subgroups
    """

    if resolved_ids is None:
        resolved_ids = []

    # Include the power/energy sensors for an existing Virtual Power config entry
    member_entry_ids = entry.data.get(CONF_GROUP_MEMBER_SENSORS) or []
    for member_entry_id in member_entry_ids:
        member_entry = hass.config_entries.async_get_entry(member_entry_id)
        key = (
            ENTRY_DATA_POWER_ENTITY
            if device_class == SensorDeviceClass.POWER
            else ENTRY_DATA_ENERGY_ENTITY
        )
        if key in member_entry.data:
            resolved_ids.extend([member_entry.data.get(key)])
        else:
            # Below is the old logic for entity resolving.
            # May be removed in the future when all config entries of users have been migrated
            # In the new situation we save the power and energy entity id's on the config entry
            # So we don't have to use a hacky way to get the entities from the entity registry anymore.
            if device_class == SensorDeviceClass.POWER:
                _LOGGER.warning("Using legacy resolve_entity_ids_recursively method")
            entity_reg = er.async_get(hass)
            state_class = (
                SensorStateClass.MEASUREMENT
                if device_class == SensorDeviceClass.POWER
                else SensorStateClass.TOTAL_INCREASING
            )
            entities = [
                entity_entry.entity_id
                for entity_entry in entity_reg.entities.values()
                if entity_entry.config_entry_id == member_entry_id
                and entity_entry.capabilities.get(ATTR_STATE_CLASS) in state_class
            ]
            sorted_entities = sorted(entities)
            resolved_ids.extend([sorted_entities[0]])

    # Include the additional power/energy sensors the user specified
    conf_key = (
        CONF_GROUP_POWER_ENTITIES
        if device_class == SensorDeviceClass.POWER
        else CONF_GROUP_ENERGY_ENTITIES
    )
    resolved_ids.extend(entry.data.get(conf_key) or [])

    # Include the entities from sub groups
    subgroups = entry.data.get(CONF_SUB_GROUPS)
    if not subgroups:
        return resolved_ids

    for subgroup_entry_id in subgroups:
        subgroup_entry = hass.config_entries.async_get_entry(subgroup_entry_id)
        if subgroup_entry is None:
            _LOGGER.error(f"Subgroup config entry not found: {subgroup_entry_id}")
            continue
        resolve_entity_ids_recursively(hass, subgroup_entry, device_class, resolved_ids)
    return resolved_ids


@callback
def create_grouped_power_sensor(
    hass: HomeAssistant,
    group_name: str,
    sensor_config: dict,
    power_sensor_ids: set[str],
) -> GroupedPowerSensor:
    name = generate_power_sensor_name(sensor_config, group_name)
    unique_id = sensor_config.get(CONF_UNIQUE_ID) or sensor_config.get(group_name)
    entity_id = generate_power_sensor_entity_id(
        hass, sensor_config, name=group_name, unique_id=unique_id
    )

    _LOGGER.debug("Creating grouped power sensor: %s (entity_id=%s)", name, entity_id)

    return GroupedPowerSensor(
        name=name,
        entities=power_sensor_ids,
        unique_id=unique_id,
        sensor_config=sensor_config,
        rounding_digits=sensor_config.get(CONF_POWER_SENSOR_PRECISION),
        entity_id=entity_id,
    )


@callback
def create_grouped_energy_sensor(
    hass: HomeAssistant,
    group_name: str,
    sensor_config: dict,
    energy_sensor_ids: set[str],
) -> GroupedEnergySensor:
    name = generate_energy_sensor_name(sensor_config, group_name)
    unique_id = sensor_config.get(CONF_UNIQUE_ID)
    energy_unique_id = None
    if unique_id:
        energy_unique_id = f"{unique_id}_energy"
    entity_id = generate_energy_sensor_entity_id(
        hass, sensor_config, name=group_name, unique_id=energy_unique_id
    )

    _LOGGER.debug("Creating grouped energy sensor: %s (entity_id=%s)", name, entity_id)

    return GroupedEnergySensor(
        name=name,
        entities=energy_sensor_ids,
        unique_id=energy_unique_id,
        sensor_config=sensor_config,
        rounding_digits=sensor_config.get(CONF_ENERGY_SENSOR_PRECISION),
        entity_id=entity_id,
    )


class GroupedSensor(BaseEntity, RestoreEntity, SensorEntity):
    """Base class for grouped sensors"""

    _attr_should_poll = False

    def __init__(
        self,
        name: str,
        entities: set[str],
        entity_id: str,
        sensor_config: dict[str, Any],
        unique_id: str | None = None,
        rounding_digits: int = 2,
    ):
        self._attr_name = name
        self._entities = entities
        if not sensor_config.get(CONF_DISABLE_EXTENDED_ATTRIBUTES):
            self._attr_extra_state_attributes = {
                ATTR_ENTITIES: self._entities,
                ATTR_IS_GROUP: True,
            }
        self._rounding_digits = rounding_digits
        self._sensor_config = sensor_config
        if unique_id:
            self._attr_unique_id = unique_id
        self.entity_id = entity_id
        self.unit_converter: BaseUnitConverter | None = None
        if hasattr(self, "get_unit_converter"):
            self.unit_converter = self.get_unit_converter()
        self._prev_state_store: PreviousStateStore | None = None

    async def async_added_to_hass(self) -> None:
        """Register state listeners."""
        await super().async_added_to_hass()

        if (state := await self.async_get_last_state()) is not None:
            self._attr_native_value = state.state

        self._prev_state_store = await PreviousStateStore.async_get_instance(self.hass)

        async_track_state_change_event(self.hass, self._entities, self.on_state_change)

        self._async_hide_members(self._sensor_config.get(CONF_HIDE_MEMBERS))

    async def async_will_remove_from_hass(self) -> None:
        """
        This will trigger when entity is about to be removed from HA
        Unhide the entities, when they where hidden before
        """
        if self._sensor_config.get(CONF_HIDE_MEMBERS) is True:
            self._async_hide_members(False)

    @callback
    def _async_hide_members(self, hide: True) -> None:
        """Hide/unhide group members"""
        registry = er.async_get(self.hass)
        for entity_id in self._entities:
            registry_entry = registry.async_get(entity_id)
            if not registry_entry:
                continue

            # We don't want to touch devices which are forced hidden by the user
            if registry_entry.hidden_by == er.RegistryEntryHider.USER:
                continue

            hidden_by = er.RegistryEntryHider.INTEGRATION if hide else None
            registry.async_update_entity(entity_id, hidden_by=hidden_by)

    @callback
    def on_state_change(self, event) -> None:
        """Triggered when one of the group entities changes state"""
        if self.hass.state != CoreState.running:  # pragma: no cover
            return

        all_states = [self.hass.states.get(entity_id) for entity_id in self._entities]
        states: list[State] = list(filter(None, all_states))
        available_states = [
            state
            for state in states
            if state and state.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE]
        ]
        unavailable_entities = [
            state.entity_id
            for state in states
            if state and state.state == STATE_UNAVAILABLE
        ]
        if unavailable_entities and isinstance(self, GroupedEnergySensor):
            for entity_id in unavailable_entities:
                prev_state = self._prev_state_store.get_entity_state(entity_id)
                if prev_state:
                    available_states.append(prev_state)
                    unavailable_entities.remove(entity_id)

            if unavailable_entities:
                _LOGGER.warning(
                    "%s: One or more members of the group are unavailable, setting group to unavailable (%s)",
                    self.entity_id,
                    ",".join(unavailable_entities),
                )
                self._attr_available = False
                self.async_schedule_update_ha_state(True)
                return

        if not available_states:
            self._attr_available = False
            self.async_schedule_update_ha_state(True)
            return

        summed = self.calculate_new_state(available_states)
        self._attr_native_value = round(summed, self._rounding_digits)
        self._attr_available = True
        self.async_schedule_update_ha_state(True)

    def _get_state_value_in_native_unit(self, state: State) -> Decimal | None:
        if state is None:
            return None

        value = float(state.state)
        unit_of_measurement = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if (
            unit_of_measurement
            and self._attr_native_unit_of_measurement != unit_of_measurement
        ):
            unit_converter = (
                EnergyConverter
                if isinstance(self, GroupedEnergySensor)
                else PowerConverter
            )
            value = unit_converter.convert(
                value, unit_of_measurement, self._attr_native_unit_of_measurement
            )
        return Decimal(value)


class GroupedPowerSensor(GroupedSensor, PowerSensor):
    """Grouped power sensor. Sums all values of underlying individual power sensors"""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = POWER_WATT

    def calculate_new_state(self, member_states: list[State]) -> Decimal:
        return sum(
            [self._get_state_value_in_native_unit(state) for state in member_states]
        )


class GroupedEnergySensor(GroupedSensor, EnergySensor):
    """Grouped energy sensor. Sums all values of underlying individual energy sensors"""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(
        self,
        name: str,
        entities: set[str],
        entity_id: str,
        sensor_config: dict[str, Any],
        unique_id: str | None = None,
        rounding_digits: int = 2,
    ):
        super().__init__(
            name, entities, entity_id, sensor_config, unique_id, rounding_digits
        )
        unit_prefix = sensor_config.get(CONF_ENERGY_SENSOR_UNIT_PREFIX)
        if unit_prefix == UnitPrefix.KILO:
            self._attr_native_unit_of_measurement = ENERGY_KILO_WATT_HOUR
        elif unit_prefix == UnitPrefix.NONE:
            self._attr_native_unit_of_measurement = ENERGY_WATT_HOUR
        elif unit_prefix == UnitPrefix.MEGA:
            self._attr_native_unit_of_measurement = ENERGY_MEGA_WATT_HOUR

    @callback
    def async_reset(self) -> None:
        _LOGGER.debug(f"{self.entity_id}: Reset grouped energy sensor")
        for entity_id in self._entities:
            _LOGGER.debug(f"Resetting {entity_id}")
            self.hass.async_create_task(
                self.hass.services.async_call(
                    DOMAIN,
                    SERVICE_RESET_ENERGY,
                    {ATTR_ENTITY_ID: entity_id},
                )
            )
            self._prev_state_store.set_entity_state(entity_id, State(entity_id, "0.00"))
        self._attr_native_value = 0
        self._attr_last_reset = dt_util.utcnow()
        self.async_write_ha_state()

    def calculate_new_state(self, member_states: list[State]) -> Decimal:
        """
        Calculate the new group energy sensor state
        For each member sensor we calculate the delta by looking at the previous known state and compare it to the current.
        """
        if self.state is None:
            group_sum = Decimal(0)
        else:
            group_sum = Decimal(self.state)
        _LOGGER.debug(f"Current energy group value {self.entity_id}: {group_sum}")
        for entity_state in member_states:
            prev_state = self._prev_state_store.get_entity_state(entity_state.entity_id)
            cur_state = self._get_state_value_in_native_unit(entity_state)
            if prev_state:
                prev_state = self._get_state_value_in_native_unit(prev_state)
            else:
                prev_state = cur_state if self.state else Decimal(0)
            self._prev_state_store.set_entity_state(
                entity_state.entity_id, entity_state
            )

            delta = cur_state - prev_state
            _LOGGER.debug(f"delta for entity {entity_state.entity_id}: {delta}")
            if delta < 0:
                _LOGGER.warning(
                    f"skipping state for {entity_state.entity_id}, probably erroneous value or sensor was reset"
                )
                continue

            group_sum += delta

        _LOGGER.debug(f"New energy group value {self.entity_id}: {group_sum}")
        return group_sum


class StandbyPowerSensor(SensorEntity, PowerSensor):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = POWER_WATT
    _attr_has_entity_name = True
    _attr_unique_id = "powercalc_standby_group"

    @property
    def name(self):
        """Name of the entity."""
        return "All standby power"

    def __init__(self, hass: HomeAssistant, rounding_digits: int = 2):
        self.standby_sensors: dict[str, Decimal] = hass.data[DOMAIN][DATA_STANDBY_POWER_SENSORS]
        self._rounding_digits = rounding_digits

    async def async_added_to_hass(self) -> None:
        """Register state listeners."""
        await super().async_added_to_hass()
        async_dispatcher_connect(self.hass, SIGNAL_POWER_SENSOR_STATE_CHANGE, self._recalculate)

    async def _recalculate(self) -> None:
        """Calculate sum of all power sensors in standby, and update the state of the sensor."""

        if self.standby_sensors:
            self._attr_native_value = round(sum(self.standby_sensors.values()), self._rounding_digits)
        else:
            self._attr_native_value = STATE_UNKNOWN
        self.async_schedule_update_ha_state(True)


class PreviousStateStore:
    @staticmethod
    @singleton("powercalc_group_storage")
    async def async_get_instance(hass: HomeAssistant) -> PreviousStateStore:
        """Get the singleton instance of this data helper."""
        instance = PreviousStateStore(hass)

        try:
            _LOGGER.debug("Load previous energy sensor states from store")
            stored_states = await instance.store.async_load()
        except HomeAssistantError as exc:
            _LOGGER.error("Error loading previous energy sensor states", exc_info=exc)
            stored_states = None

        if stored_states is None:
            instance.states = {}
        else:
            instance.states = {
                entity_id: State.from_dict(json_state)
                for (entity_id, json_state) in stored_states.items()
            }

        instance.async_setup_dump()

        return instance

    def __init__(self, hass: HomeAssistant):
        self.store: Store = Store(
            hass, STORAGE_VERSION, STORAGE_KEY, encoder=JSONEncoder
        )
        self.states: dict[str, State] = {}
        self.hass = hass

    def get_entity_state(self, entity_id) -> State | None:
        """Retrieve the previous state"""
        return self.states.get(entity_id)

    def set_entity_state(self, entity_id, state: State) -> None:
        """Set the state for an energy sensor"""
        self.states[entity_id] = state

    async def persist_states(self) -> None:
        """Save the current states to storage."""
        try:
            await self.store.async_save(self.states)
        except HomeAssistantError as exc:
            _LOGGER.error("Error saving current states", exc_info=exc)

    @callback
    def async_setup_dump(self) -> None:
        """Set up the listeners for persistence."""

        async def _async_dump_states(*_: Any) -> None:
            await self.persist_states()

        # Dump states periodically
        cancel_interval = async_track_time_interval(
            self.hass,
            _async_dump_states,
            STATE_DUMP_INTERVAL,
        )

        async def _async_dump_states_at_stop(*_: Any) -> None:
            cancel_interval()
            await self.persist_states()

        # Dump states when stopping hass
        self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, _async_dump_states_at_stop
        )
