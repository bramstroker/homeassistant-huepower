from __future__ import annotations

from decimal import Decimal
from typing import Optional, Union

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components import climate, vacuum
from homeassistant.core import State
from homeassistant.helpers.event import TrackTemplate
from homeassistant.helpers.template import Template

from ..common import SourceEntity
from ..const import CONF_POWER, CONF_STATES_POWER
from ..errors import StrategyConfigurationError
from ..helpers import evaluate_power
from .strategy_interface import PowerCalculationStrategyInterface

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_POWER): vol.Any(vol.Coerce(float), cv.template),
        vol.Optional(CONF_STATES_POWER): vol.Schema(
            {cv.string: vol.Any(vol.Coerce(float), cv.template)}
        ),
    }
)

STATE_BASED_ENTITY_DOMAINS = [
    climate.DOMAIN,
    vacuum.DOMAIN,
]


class FixedStrategy(PowerCalculationStrategyInterface):
    def __init__(
        self,
        source_entity: SourceEntity,
        power: Optional[Union[Template, float]],
        per_state_power: Optional[dict[str, Union[float, Template]]],
    ) -> None:
        self._source_entity = source_entity
        self._power = power
        self._per_state_power = per_state_power

    async def calculate(self, entity_state: State) -> Decimal | None:
        if self._per_state_power is not None:
            # Lookup by state
            if entity_state.state in self._per_state_power:
                return await evaluate_power(
                    self._per_state_power.get(entity_state.state)
                )
            else:
                # Lookup by state attribute (attribute|value)
                for state_key, power in self._per_state_power.items():
                    if "|" in state_key:
                        attribute, value = state_key.split("|", 2)
                        if str(entity_state.attributes.get(attribute)) == value:
                            return await evaluate_power(power)

        if self._power is None:
            return None

        return await evaluate_power(self._power)

    async def validate_config(self) -> None:
        """Validate correct setup of the strategy"""
        if self._power is None and self._per_state_power is None:
            raise StrategyConfigurationError(
                "You must supply one of 'states_power' or 'power'", "fixed_mandatory"
            )

        if (
            self._source_entity.domain in STATE_BASED_ENTITY_DOMAINS
            and self._per_state_power is None
        ):
            raise StrategyConfigurationError(
                "This entity can only work with 'states_power' not 'power'",
                "fixed_states_power_only",
            )

    def get_entities_to_track(self) -> list[Union[str | TrackTemplate]]:
        track_templates = []

        if isinstance(self._power, Template):
            track_templates.append(TrackTemplate(self._power, None))

        if self._per_state_power:
            for power in list(self._per_state_power.values()):
                if isinstance(power, Template):
                    track_templates.append(TrackTemplate(power, None))

        return track_templates
