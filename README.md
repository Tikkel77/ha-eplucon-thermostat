# Eplucon Thermostat for Home Assistant

Custom Home Assistant integration for Eplucon / TECH Sterowniki zone thermostats.

## Features

- **Climate entity** per zone with temperature control
- **Sensors**: temperature, humidity, signal strength, battery level, operating mode
- **Binary sensors**: heating active, window open, zone alarm, parameters updating
- **Modes**: Constant temperature, Time limit, Schedule (local/global)
- Automatic detection of all zones
- Uses the same cloud API as the Eplucon portal

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu (top right) > "Custom repositories"
3. Add `https://github.com/Tikkel77/ha-eplucon-thermostat` as "Integration"
4. Search for "Eplucon Thermostat" and install
5. Restart Home Assistant

### Manual

1. Copy `custom_components/eplucon_thermostat/` to your HA `custom_components/` folder
2. Restart Home Assistant

## Configuration

1. Go to Settings > Devices & Services > Add Integration
2. Search for "Eplucon Thermostat"
3. Enter your credentials:
   - **Email**: your Eplucon portal email
   - **Password**: your Eplucon portal password
   - **API key**: found under "My Account" > "API" in the Eplucon portal

## Entities

### Climate (per zone)
- Current temperature
- Target temperature
- Humidity
- HVAC modes: Off, Heat, Cool, Auto (schedule)
- Preset modes: Constant, Time limit, Schedule
- HVAC action: Heating, Cooling, Idle

### Sensors (per zone)
- Current temperature (°C)
- Set temperature (°C)
- Humidity (%)
- Signal strength (%)
- Battery level (%)
- Time limit remaining (min)
- Operating mode

### Binary Sensors (per zone)
- Heating active
- Window open
- Zone alarm
- Parameters updating

## Notes

- Polling interval: 5 minutes (to avoid overloading the Eplucon portal)
- The Eplucon portal is slow; temperature changes may take 10-30 seconds to process
- Schedule times are rounded to 15-minute intervals (portal limitation)
