# Thermostat Pro Timeline Sync

Use together with **Thermostat Pro Timeline Card** (published separately).  
This integration **synchronizes the Thermostat Timeline** across users and devices in Home Assistant, so everyone sees and uses the same schedule.

## Installation (HACS)
1) HACS → Integrations → ⋯ → **Custom repositories**  
   URL: `https://github.com/qlerup/thermostat-pro-timeline-sync` • Category: **Integration`
2) Install **Thermostat Pro Timeline Sync** and restart Home Assistant.
3) Add the integration in **Settings → Devices & Services**.
4) Install the **Thermostat Pro Timeline Card** separately (it reads/writes the shared timeline via this integration).

## At a glance
- Central store for timeline data (shared by all users/devices)
- Updates are written once and shared immediately
- Domain: `thermostat_timeline` • Sensor: `sensor.thermostat_timeline` (version + `schedules`)
