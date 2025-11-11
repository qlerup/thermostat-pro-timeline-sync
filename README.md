# Thermostat Pro Timeline Sync

[![hacs\_badge](https://img.shields.io/badge/HACS-Default-blue.svg)](https://hacs.xyz)

Use together with **Thermostat Pro Timeline Card** ([Link](https://github.com/qlerup/lovelace-thermostat-pro-timeline)).  
This integration **synchronizes the Thermostat Timeline** across users and devices in Home Assistant, so everyone sees and uses the same schedule.

<img width="767" height="932" alt="yimeline_sync" src="https://github.com/user-attachments/assets/f8661142-9504-49fc-829f-e19a7d8067c6" />


## Installation (HACS)

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=qlerup&repository=thermostat-pro-timeline-sync)


1) HACS → Integrations → ⋯ → **Custom repositories**  
   URL: `https://github.com/qlerup/thermostat-pro-timeline-sync` • Category: **Integration`
2) Install **Thermostat Pro Timeline Sync** and restart Home Assistant.
3) Add the integration in **Settings → Devices & Services**.
4) Install the **Thermostat Pro Timeline Card** separately (it reads/writes the shared timeline via this integration).

## At a glance
- Central store for timeline data (shared by all users/devices)
- Updates are written once and shared immediately
- Domain: `thermostat_timeline` • Sensor: `sensor.thermostat_timeline` (version + `schedules`)
