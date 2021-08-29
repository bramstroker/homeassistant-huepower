from __future__ import annotations

from phue import (
    Bridge,
    PhueRegistrationException
)
from .controller import LightController, LightInfo

NAME = "hue"

class HueLightController(LightController):
    def __init__(self, bridge_ip: str):
        self.bridge = self.initialize_hue_bridge(bridge_ip)

    def change_light_state(self, color_mode: str, on: bool = True, **kwargs):
        kwargs["on"] = on
        self.bridge.set_light(self.light_id, kwargs)

    def get_light_info(self) -> LightInfo:
        light = self.bridge.get_light(self.light_id)
        lightinfo = LightInfo(
            model_id=light["modelid"],
        )

        if "ct" in light["capabilities"]["control"]:
            lightinfo.min_mired = light["capabilities"]["control"]["ct"]["min"]
            lightinfo.max_mired = light["capabilities"]["control"]["ct"]["max"]

        return lightinfo

    def initialize_hue_bridge(self, bridge_ip: str) -> Bridge:
        try:
            bridge = Bridge(bridge_ip)
        except PhueRegistrationException as err:
            print("Please click the link button on the bridge, than hit enter..")
            input()
            bridge = Bridge(bridge_ip)

        return bridge

    def get_questions(self) -> list[dict]:
        light_list = []
        for light in self.bridge.lights:
            light_list.append({"key": light.light_id, "value": light.light_id, "name": light.name})

        return [
            {
                'type': 'list',
                'name': 'light',
                'message': 'Select the light?',
                'choices': light_list
            },
        ]

    def process_answers(self, answers):
        self.light_id = answers["light"]
