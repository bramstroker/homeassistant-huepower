[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/custom-components/hacs)
![Version](https://img.shields.io/github/v/release/bramstroker/homeassistant-powercalc)
![Downloads](https://img.shields.io/github/downloads/bramstroker/homeassistant-powercalc/total)

# homeassistant-powercalc
Custom component to calculate estimated power consumption of lights and other appliances.
Provides easy configuration to get virtual power consumption sensors in Home Assistant for all your devices which don't have a build in power meter.
This component estimates power usage by looking at brightness, hue/saturation and color temperature etc using different strategies. They are explained below.
Power sensors can be created for `light`, `switch`, `fan`, `binary_sensor`, `input_boolean`, `remote` and `media_player` entities 

![Preview](https://raw.githubusercontent.com/bramstroker/homeassistant-powercalc/master/assets/preview.gif)

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/bramski)

## TOC
- [Installation](#installation)
    - [HACS](#hacs)
    - [Manual](#manual)
- [Configuration](#configuration)
- [Calculation modes](#calculation-modes)
    - [LUT](#lut-mode)
    - [Linear](#linear-mode)
    - [Fixed](#fixed-mode)
- [Light model library](#light-model-library)
    - [LUT data files](#lut-data-files)
    - [Supported models](#supported-models)
- [Debug logging](#debug-logging)

## Installation

### HACS
This integration is part of the default HACS repository. Just click "Explore and add repository" to install

### Manual
Copy `custom_components/powercalc` into your Home Assistant `config` directory.

## Configuration

Each virtual power sensor have it's own configuration possibilities. They are as follows:

| Name                   | Type    | Requirement  | Description                                                                |
| ---------------------- | ------- | ------------ | -------------------------------------------------------------------------- |
| entity_id              | string  | **Required** | HA entity ID                                                               |
| manufacturer           | string  | **Optional** | Manufacturer, most of the time this can be automatically discovered        |
| model                  | string  | **Optional** | Model id, most of the time this can be automatically discovered            |
| standby_usage          | float   | **Optional** | Supply the wattage when the device is off                                  |
| disable_standby_usage  | boolean | **Optional** | Set to `true` to not show any power consumption when the device is standby |
| name                   | string  | **Optional** | Override the name                                                          |
| custom_model_directory | string  | **Optional** | Directory for a custom light model. Relative from the `config` directory   |
| mode                   | string  | **Optional** | Calculation mode, one of `lut`, `linear`, `fixed`                          |
| fixed                  | object  | **Optional** | [Fixed mode options](#fixed-mode)                                          |
| linear                 | object  | **Optional** | [Linear mode options](#linear-mode)                                        |

## Calculation modes

To calculate estimated power consumption different modes are supported, they are as follows:
- [LUT (lookup table)](#lut-mode)
- [Linear](#linear-mode)
- [Fixed](#fixed-mode)

### LUT mode
Supported domain: `light`

This is the most accurate mode.
For some models from the Philips Hue line measurements are taken using smart plugs. All this data is saved into CSV files. When you have the LUT mode activated the current brightness/hue/saturation of the light will be checked and closest matching line will be looked up in the CSV.
- [Supported models](#supported-models) for LUT mode
- [LUT file structure](#lut-data-files)

#### Configuration

```yaml
sensor:
  - platform: powercalc
    entity_id: light.livingroom_floorlamp
    manufacturer: signify
    model: LCT010
```

When you are using the official Philips Hue integration the manufacturer and model can automatically be discovered, so there is no need to supply those.

```yaml
sensor:
  - platform: powercalc
    entity_id: light.livingroom_floorlamp
```

### Linear mode
Supported domains: `light`, `fan`

The linear mode can be used for dimmable devices which don't have a lookup table available.
You need to supply the min and max power draw yourself, by either looking at the datasheet or measuring yourself with a smart plug / power meter.
Power consumpion is calculated by ratio. So when you have your fan running at 50% speed and define watt range 2 - 6, than the estimated consumption will be 4 watt.

#### Configuration options
| Name              | Type    | Requirement  | Description                                 |
| ----------------- | ------- | ------------ | ------------------------------------------- |
| min_power         | float   | **Optional** | Power usage for lowest brightness level     |
| max_power         | float   | **Optional** | Power usage for highest brightness level    |
| calibrate         | string  | **Optional** | Calibration values                          |

#### Example configuration

```yaml
sensor:
  - platform: powercalc
    entity_id: light.livingroom_floorlamp
    linear:
      min_power: 0.5
      max_power: 8
```

#### Advanced precision calibration

With the `calibrate` setting you can supply more than one power value for multiple brightness/percentage levels.
This allows for a more accurate estimation because not all lights are straight linear.

```yaml
sensor:
  - platform: powercalc
    entity_id: light.livingroom_floorlamp
    linear:
      calibrate:
        - 1 -> 0.3
        - 10 -> 1.25
        - 50 -> 3.50
        - 100 -> 6.8
        - 255 -> 15.3
```

> Note: For lights the supplied values must be in brightness range 1-255, when you select 1 in lovelace UI slider this is actually brightness level 3.
> For fan speeds the range is 1-100 (percentage)

### Fixed mode
Supported domains: `light`, `fan`, `switch`, `binary_sensor`, `remote`, `media_player`, `input_boolean`

When you have an appliance which only can be set on and off you can use this mode.
You need to supply a single watt value in the configuration which will be used when the device is ON

#### Configuration options
| Name              | Type    | Requirement  | Description                                 |
| ----------------- | ------- | ------------ | ------------------------------------------- |
| power             | float   | **Optional** | Power usage when the appliance is turned on |

```yaml
sensor:
  - platform: powercalc
    entity_id: light.nondimmabled_bulb
    fixed:
      power: 20
```

## Configuration examples

### Linear mode with additional standby usage

```yaml
sensor:
  - platform: powercalc
    entity_id: light.livingroom_floorlamp
    linear:
      min_power: 0.5
      max_power: 8
    standby_usage: 0.2
    name: My amazing power meter
```

<hr>

## Light model library

The component ships with predefined light measurements for some light models.
This library will keep extending by the effort of community users.

These models are located in `custom_components/powercalc/data` directory.
Each light model has it's own subdirectory `{manufacturer}/{modelid}`

Every model MUST contain a `model.json` file which defines the supported calculation modes and other configuration.
When LUT mode is supported also [LUT data files](#lut-data-files) must be provided.

Example lut mode:

```json
{
    "name": "Hue White and Color Ambiance A19 E26 (Gen 5)",
    "standby_usage": 0.4,
    "supported_modes": [
        "lut"
    ]
}
```

Example linear mode

```json
{
    "name": "Hue Go",
    "supported_modes": [
        "linear"
    ],
    "standby_usage": 0.2,
    "linear_config": {
        "min_power": 0,
        "max_power": 6
    }
}
```

### LUT data files

To calculate power consumption a lookup is done into CSV data files.

Depending on the supported color modes of the light the integration expects multiple CSV files here:
 - hs.csv.gz (hue/saturation, colored lamps)
 - color_temp.csv.gz (color temperature)
 - brightness.csv.gz (brightness only lights)

Some lights support two color modes (both hs and color_temp), so there must be two CSV files.

The files are gzipped to keep the repository footprint small, and installation fast.

Example:

```
- signify
  - LCT010
    - hs.csv.gz
    - color_temp.csv.gz
```

#### Expected file structure

- The file MUST contain a header row.
- The data rows in the CSV files MUST have the following column order:

**hs.csv**
```csv
bri,hue,sat,watt
```

**color_temp.csv**
```csv
bri,mired,watt
```

**brightness.csv**
```csv
bri,watt
```

***Ranges***:
- brightness (0-255)
- hue (0-65535)
- saturation (0-255)
- mired (0-500)  min value depending on min mired value of the light model

#### Creating LUT files

New files are created by taking measurements using a smartplug (i.e. Shelly plug) and changing the light to all kind of different variations using the Hue API.
An example script is available `utils/measure/measure.py`.
I am using the "Shelly Plug S"

### Supported models

See the [list](docs/supported_models.md) of supported lights which don't need any manual configuration

## Debug logging

Add the following to configuration.yaml:

```yaml
logger:
  default: warning
  logs:
    custom_components.powercalc: debug
```
