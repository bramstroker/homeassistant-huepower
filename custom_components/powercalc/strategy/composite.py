from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import condition
from homeassistant.helpers.condition import ConditionCheckerType
from homeassistant.helpers.event import TrackTemplate

from .strategy_interface import PowerCalculationStrategyInterface

_LOGGER = logging.getLogger(__name__)


class CompositeStrategy(PowerCalculationStrategyInterface):
    def __init__(self, hass: HomeAssistant, strategies: list[SubStrategy]) -> None:
        self.hass = hass
        self.strategies = strategies

    async def calculate(self, entity_state: State) -> Decimal | None:
        for sub_strategy in self.strategies:
            if sub_strategy.condition and not sub_strategy.condition(self.hass, {"state": entity_state}):
                continue

            return await sub_strategy.strategy.calculate(entity_state)

        return None

    def get_entities_to_track(self) -> list[str | TrackTemplate]:
        track_templates: list[str | TrackTemplate] = []

        for sub_strategy in self.strategies:
            if sub_strategy.condition_config
        if isinstance(self._power, Template):
            track_templates.append(TrackTemplate(self._power, None, None))

        if self._per_state_power:
            for power in list(self._per_state_power.values()):
                if isinstance(power, Template):
                    track_templates.append(TrackTemplate(power, None, None))

        return track_templates


@dataclass
class SubStrategy:
    condition_config = dict | None
    condition: ConditionCheckerType | None
    strategy: PowerCalculationStrategyInterface
