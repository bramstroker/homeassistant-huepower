from enum import Enum


class PowerMeterType(str, Enum):
    DUMMY = "dummy"
    HASS = "hass"
    KASA = "kasa"
    MANUAL = "manual"
    OCR = "ocr"
    SHELLY = "shelly"
    TASMOTA = "tasmota"
    TUYA = "tuya"
    MYSTROM = "mystrom"


QUESTION_POWERMETER_ENTITY_ID = "powermeter_entity_id"
QUESTION_VOLTAGEMETER_ENTITY_ID = "voltagemeter_entity_id"
