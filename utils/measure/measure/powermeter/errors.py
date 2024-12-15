class PowerMeterError(Exception):
    pass


class OutdatedMeasurementError(PowerMeterError):
    pass


class ZeroReadingError(PowerMeterError):
    pass


class ApiConnectionError(PowerMeterError):
    pass


class UnsupportedFeatureError(PowerMeterError):
    pass
