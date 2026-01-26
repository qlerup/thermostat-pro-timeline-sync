# Thermostat Pro Timeline (Integration + Card) 🔥🕒

[![hacs_badge](https://img.shields.io/badge/HACS-Default-blue.svg)](https://hacs.xyz)

A Lovelace card + integration for Home Assistant that lets you plan temperatures on an easy timeline — and automatically apply them to your `climate.*` (or `input_number.*`) entities.

Card type: `custom:thermostat-timeline-card`
Default resource (auto-registered by the integration): `/local/thermostat-pro-timeline.js`

<img width="2288" height="476" alt="image" src="https://github.com/user-attachments/assets/95a17e9d-e404-4bad-ba93-5af0a6cff6d5" />

---

## Support this project ☕

If you find this project useful, you can support me on Ko-fi 💙

[![Buy me some debugging time on Ko-fi](https://img.shields.io/badge/%F0%9F%90%9E_Buy_me_some_debugging_time_on-Ko--fi-2ea44f?style=for-the-badge)](https://ko-fi.com/qlerup)

---

## At-a-glance features ✨

- 🧊 Per-thermostat or merged “room” timelines (control multiple thermostats as one)
- 🎨 Color ranges per room or global (`color_ranges` + `color_global`)
- 🗓️ Weekdays schedules: weekday/weekend, weekday+Sat+Sun, or all 7 days; two views
- 🎉 Holidays: dedicated holiday plan (calendar/binary_sensor or manual dates/ranges)
- 🧾 Profiles: multiple named profiles per room with activation
- 🏡 Away / Presence: auto away when nobody’s home; resume when someone returns
- 👥 Advanced presence: who’s-home combinations + per-room presence sensor with on/off delays
- ⏱️ Away delay option before applying changes; live presence chips in header
- ⏸️ Pause: pause indefinitely or until time; optional pause via a `binary_sensor.*`
- 🪟 Open Window Detection: suspend heating while windows/doors are open; resume after close (with delays)
- 🔥 Boiler control: drive a boiler switch by offsets vs target, or legacy temperature-sensor mode
- 🌡️ Room-temperature bubble + `temp_sensors` mapping per room
- 🔢 Input-number mode per row (drive external automations)
- 🔌 Turn-on sequencing for thermostats (send climate.turn_on before/after set_temperature, with delay)
- 🔧 Supports both `climate.*` and `input_number.*` targets
- 🏷️ Custom room labels and thermostat merges
- 🕒 Time format (12/24h), time source (browser/HA), and temperature unit auto-detect
- 📍 “Now” indicators (cursor and optional top header line)
- 💾 Shared storage + background control via integration (`storage_enabled: true`)
- 🧬 Multiple configurations via instances (namespaces) and instance switching
- 🧰 Backup/restore: slots, selective sections, auto backups, and delete backups
- 🎨 Color ranges per room or global with persistent storage
- 🌍 Localization: English, Danish, Swedish, Norwegian, German, Spanish, French, Finnish, Czech
- 🧹 Storage migration tools and clear-data menu (browser/storage)

---

## Storage and integration mode 💾


The integration (domain `thermostat_timeline`) provides:

- Shared schedules/settings persisted in HA `.storage`
- A backend API for the card + background “auto-apply”, Open Window Detection, and auto-backups

`storage_enabled`

- `true` (default): use the integration’s shared store (recommended)
- `false`: store locally in the browser (legacy/standalone)

Switching between true/false changes where data lives; timelines/settings won’t automatically migrate unless you use the built-in migration.

---

## Installation 📦

Recommended: HACS (Integration)

1) Install the Thermostat Timeline integration via HACS (as Integration)
2) Restart Home Assistant
3) The integration deploys `/config/www/thermostat-pro-timeline.js` and auto-registers a Lovelace resource

If the resource does not appear, add it manually under Settings → Dashboards → Resources:

- URL: `/local/thermostat-pro-timeline.js`
- Type: JavaScript Module

Manual (no HACS)

1) Copy `thermostat-pro-timeline.js` to `/config/www/`
2) Add the resource as above

---

## Card configuration (YAML) 🧩


Minimum

```yaml
type: custom:thermostat-timeline-card
entities:
  - climate.living_room
  - climate.bedroom
```

Global options (commonly used)

```yaml
title: "Heating – Overview"
row_height: 64
default_temp: 20
min_temp: 5
max_temp: 25

temp_unit: auto      # 'C' | 'F' | 'auto'
time_12h: auto       # true | false | 'auto'
time_source: browser # 'browser' | 'ha'

now_update_ms: 60000
show_top_now: false
now_extend_px: 76

auto_apply: true
apply_on_edit: true
apply_on_default_change: true
```

🏷️ Labels (optional)

```yaml
labels:
  climate.living_room: Living Room
  climate.bedroom: Bedroom
```

🔗 Merges (control multiple thermostats as one row)

```yaml
merges:
  climate.living_room:
    - climate.living_radiator_left
    - climate.living_radiator_right
```

🔌 Turn on (send turn_on in addition to set_temperature)

```yaml
turn_on:
  climate.living_room:
    enabled: true
    order: before   # 'before' | 'after'
```

🌡️ Room temperature bubble + external sensors

```yaml
show_room_temp: true
temp_sensors:
  climate.living_room: sensor.living_temperature
```

🔢 Input number mode (per row, matches entities[] index)

```yaml
room_use_input_number:
  - true
  - false
```

⏸️ Pause controls

```yaml
show_pause_button: true
pause_sensor_enabled: true
pause_sensor_entity: binary_sensor.heating_pause
```

🪟 Open Window Detection


```yaml
open_window:
  enabled: true
  open_delay_min: 2
  close_delay_min: 5
  sensors:
    climate.living_room:
      - binary_sensor.window_living
      - binary_sensor.patio_door
```

🗓️ Weekdays


```yaml
weekdays_enabled: true
weekdays_mode: weekday_weekend      # 'weekday_weekend' | 'weekday_sat_sun' | 'all_7'
weekdays_view: all_rooms_one_day    # 'all_rooms_one_day' | 'one_room_all_days'
weekdays_view_switch_in_timeline: false
weekdays_selected_room: ''
```

🏡 Away / Presence

```yaml
away:
  enabled: true
  persons:
    - person.alex
    - person.jamie
  target_c: 17
  advanced_enabled: false   # enable combos-based presence schedules
  combos: {}

presence_live_header: true  # show presence chips
```

🎉 Holidays

<!-- IMAGE: Holidays editor – manual dates and date ranges. -->
<!-- IMAGE: Timeline on an active holiday (holiday badge/notice + holiday blocks). -->

```yaml
holidays_enabled: true
holidays_source: calendar   # 'calendar' | 'manual'
holidays_entity: calendar.public_holidays  # or a binary_sensor (on/off)
holidays_dates:
  - '2026-12-24'
  - '2026-12-25'
```

🧾 Profiles

<!-- IMAGE: Profiles dialog – list of profiles, activate/deactivate, rename, delete. -->
<!-- IMAGE: Editing blocks within a profile + Active profile label. -->

```yaml
profiles_enabled: true
```

🔥 Boiler control

<!-- IMAGE: Boiler control editor – switch entity, offsets (on/off), room selection. -->
<!-- IMAGE: Boiler status chip/indicator while heating demand is ON. -->

Recommended (switch mode)

```yaml
boiler_enabled: true
boiler_switch_domain: switch        # 'switch' | 'input_boolean'
boiler_switch: switch.boiler
boiler_rooms: null                  # null = all rooms, or list of climate.*
boiler_on_offset: 0                 # ON when current < target - on_offset
boiler_off_offset: 0                # OFF when current >= target + off_offset
```

Legacy temperature-sensor mode

```yaml
boiler_temp_sensor: sensor.boiler_temp
boiler_min_temp: 20
boiler_max_temp: 25
```

🎨 Color ranges

<!-- IMAGE: Color ranges editor – per-room intervals. -->
<!-- IMAGE: Color ranges editor – global mode with single set applied to all rooms. -->
<!-- IMAGE: Timeline with colored blocks across different temperature bands. -->

Per room

```yaml
color_global: false
color_ranges:
  climate.living_room:
    - { from: 5,  to: 18, color: "#87cefa" }
    - { from: 18, to: 21, color: "#90ee90" }
    - { from: 21, to: 25, color: "#ffb347" }
```

Global (applies to all rooms)

```yaml
color_global: true
color_ranges:
  "*":
    - { from: 5,  to: 18, color: "#87cefa" }
    - { from: 18, to: 21, color: "#90ee90" }
    - { from: 21, to: 25, color: "#ffb347" }
```

🧬 Instances (namespaced configurations)

<!-- IMAGE: Configuration ID field in editor + example of switching instances (winter/summer). -->

```yaml
instance_enabled: true
instance_id: winter
```

🧰 Backups (auto)

<!-- IMAGE: Backup tab – “New backup”, slot selector, last backup timestamp. -->
<!-- IMAGE: Restore dialog – select sections (main, weekday, presence, settings, holiday, colors, profiles), merge vs replace. -->
<!-- IMAGE: Delete backup confirmation + Auto backup interval setting. -->

```yaml
backup_auto_enabled: true
backup_interval_days: 1
```

🗄️ Storage

<!-- IMAGE: Integration connection status (Connected/Not connected). -->
<!-- IMAGE: Storage migration dialog (browser → storage and storage → browser). -->
<!-- IMAGE: Clear data menu – All data, Local only, Storage only. -->

```yaml
storage_enabled: true
storage_entity: sensor.thermostat_timeline
```

---

## Integration services (YAML examples) 🛠️

Domain: `thermostat_timeline`

- 🗃️ set_store — replace the store (schedules/settings) and bump version

```yaml
service: thermostat_timeline.set_store
data:
  instance_id: default          # optional
  activate: true                # optional, when instance_id provided
  schedules:
    climate.kitchen:
      defaultTemp: 20
      blocks:
        - { from: "06:00", to: "08:00", temp: 21 }
  settings:
    min_temp: 5
    max_temp: 25
    auto_apply_enabled: true
```

- 🧭 select_instance — switch active instance (for background control and API)

```yaml
service: thermostat_timeline.select_instance
data:
  instance_id: winter
  create_if_missing: true
  copy_from_active: true
```

- ✏️ rename_instance — rename an instance id

```yaml
service: thermostat_timeline.rename_instance
data:
  old_instance_id: winter
  new_instance_id: winter_2026
```

- 🩹 patch_entity — merge data into a single climate entity

```yaml
service: thermostat_timeline.patch_entity
data:
  entity_id: climate.kitchen
  data:
    defaultTemp: 21
    blocks:
      - { from: "17:00", to: "22:00", temp: 22 }
```

- 🧹 clear — clear all schedules and bump version

```yaml
service: thermostat_timeline.clear
```

- 🧨 factory_reset — delete storage files and recreate them empty

```yaml
service: thermostat_timeline.factory_reset
```

- 💾 backup_now — create a backup (select sections)

```yaml
service: thermostat_timeline.backup_now
data:
  main: true
  weekday: true
  presence: true
  settings: true
  holiday: true
  colors: true
```

- ♻️ restore_now — restore from backup (merge or replace)

```yaml
service: thermostat_timeline.restore_now
data:
  mode: merge          # merge | replace
  main: true
  weekday: true
  presence: true
  settings: true
  holiday: true
  colors: true
```

- 🗑️ delete_backup — delete one backup slot or all when no slot given

```yaml
service: thermostat_timeline.delete_backup
data:
  slot: 3   # optional (1-based). Omit to delete all backups.
```

---

## Troubleshooting 🧯

<!-- IMAGE: Resources list showing duplicate entries (highlight duplicates to remove). -->

Custom element already defined / card loads twice

Typically happens if the resource is added more than once (e.g., both `/local/thermostat-pro-timeline.js` and an old `/hacsfiles/...`).

- Check Settings → Dashboards → Resources has a single entry
- Hard-refresh your browser afterwards

Resource missing / 404

- Verify `/config/www/thermostat-pro-timeline.js` exists
- If the integration didn’t auto-register the resource, add it manually (see Installation)

Switching `storage_enabled` looks odd

Expected: `storage_enabled: false` uses browser LocalStorage; `true` uses the integration’s shared store. Use the migration tools to transfer data.

---

## Localization 🌍

<!-- IMAGE: Card UI shown in two languages side-by-side (e.g., English and Danish) to demonstrate localization. -->

Supported: Danish, Swedish, Norwegian, English, German, Spanish, French, Finnish, Czech

Defaults to English when a translation is missing.

---

## Advanced ⚙️

API endpoints (require auth):

- GET /api/thermostat_timeline/state
- GET /api/thermostat_timeline/version

These return the current schedules/settings snapshot and version info.

---

## Advanced settings (card → Settings tab) 🧪

<!-- IMAGE: Advanced settings controls – range_band_c slider/input and turn_on_delay_s input in the editor. -->

These are optional expert toggles you may add to your YAML or configure via the editor UI:

```yaml
# Heat/Cool band around desired setpoint (°C). Default 1.0, min 0.2
range_band_c: 1.0

# Delay (seconds) between set_temperature and optional climate.turn_on
# Applies when turn_on.<room>.enabled is true. Default 1.0, max 5.0
turn_on_delay_s: 1.0
```

Notes

- The card maps UI options to backend settings automatically; you rarely need to set these manually.
- Presence sensor overrides are configured per room in the editor (temperature + on/off delays).

---

## Example: full card config 📘

<!-- IMAGE: Final dashboard card in use with colors, weekdays, presence chips, OWD icon, and boiler chip visible. -->

```yaml
type: custom:thermostat-timeline-card
title: Heating – Ground Floor
entities:
  - climate.living_room
  - climate.bedroom

row_height: 64
default_temp: 20
min_temp: 5
max_temp: 25
temp_unit: auto
time_12h: auto
time_source: ha
now_update_ms: 60000
show_top_now: false
now_extend_px: 76
auto_apply: true
apply_on_edit: true
apply_on_default_change: true

labels:
  climate.living_room: Living Room
  climate.bedroom: Bedroom

merges:
  climate.living_room:
    - climate.living_radiator_left
    - climate.living_radiator_right

turn_on:
  climate.living_room:
    enabled: true
    order: before

show_room_temp: true
temp_sensors:
  climate.living_room: sensor.living_temperature

room_use_input_number:
  - false
  - true

weekdays_enabled: true
weekdays_mode: weekday_sat_sun
weekdays_view: all_rooms_one_day

open_window:
  enabled: true
  open_delay_min: 2
  close_delay_min: 5
  sensors:
    climate.living_room:
      - binary_sensor.window_living

away:
  enabled: true
  persons: [person.alex, person.jamie]
  target_c: 17
presence_live_header: true

holidays_enabled: true
holidays_source: calendar
holidays_entity: calendar.public_holidays

profiles_enabled: true

boiler_enabled: true
boiler_switch_domain: switch
boiler_switch: switch.boiler
boiler_rooms: null
boiler_on_offset: 0
boiler_off_offset: 0

color_global: true
color_ranges:
  "*":
    - { from: 5,  to: 18, color: "#87cefa" }
    - { from: 18, to: 21, color: "#90ee90" }
    - { from: 21, to: 25, color: "#ffb347" }

instance_enabled: true
instance_id: winter

backup_auto_enabled: true
backup_interval_days: 1

storage_enabled: true
storage_entity: sensor.thermostat_timeline
```

---

Legacy docs (Danish) below

## Support this project

If you find this project useful, you can support me on Ko-fi 💙

[![Buy me some debugging time on Ko-fi](https://img.shields.io/badge/%F0%9F%90%9E_Buy_me_some_debugging_time_on-Ko--fi-2ea44f?style=for-the-badge)](https://ko-fi.com/qlerup)

---

## ✨ Features

- 🧊 Per-termostat timeline eller **merged “rum”** (flere thermostat’er i én række)
- 🎨 **Color ranges** pr. rum eller globalt (`color_ranges` + `color_global`)
- 🗓️ **Weekdays** (weekday/weekend, weekday/sat/sun, alle 7) + 2 visninger
- 🗓️ **Holidays**: separat helligdags-skema (kalender/binary_sensor eller manuel dato/range)
- 🧾 **Profiles**: flere profiler pr. rum + aktiv profil
- 🏡 **Away / Presence**: auto “away” når alle er væk, resume når nogen kommer hjem
- 🧩 **Avanceret presence**: kombinationer + per-rum presence-sensor med on/off delay
- ⏸️ **Pause**: pause uendeligt / til tidspunkt + valgfri pause via `binary_sensor.*`
- 🪟 **Open Window Detection**: sluk varme ved åbent vindue og genoptag ved lukket (med delays)
- 🔥 **Boiler control**: styr kedel-switch baseret på behov (offsets) eller legacy temp-sensor mode
- 🌡️ Rumtemp-visning + **temp_sensors** mapping pr. rum
- 🔢 **input_number mode** pr. rum (hvis du vil drive anden automation)
- 💾 **Shared storage via integration** (`storage_enabled: true`) + multi-instance
- 🧰 **Backups/restore** med slots + merge/replace og sektioner

---

## 💾 Storage og “integration-mode”

Løsningen er samlet i en **integration** (domain: `thermostat_timeline`), som:

- gemmer shared schedules/settings i HA `.storage`
- udstiller API til kortet
- kører background “auto apply” / open-window detection / auto-backups

### `storage_enabled`

- `storage_enabled: true` (default): brug integrationens shared store (anbefalet)
- `storage_enabled: false`: gem lokalt i browserens LocalStorage (legacy/standalone)

Bemærk: skift mellem `true/false` ændrer hvor data ligger, så forvent ikke at data automatisk “følger med”.

---

## ⚙️ Installation

### ✅ Anbefalet: via HACS (Integration)

1. Installér integrationen via HACS (som **Integration**).
2. Genstart Home Assistant.
3. Integrationens frontend-del sørger for at JS ligger i `/config/www/thermostat-pro-timeline.js` og forsøger at auto-registrere resource.

Hvis resource ikke auto-dukker op, tilføj den manuelt:

- **Settings → Dashboards → Resources**
  - URL: `/local/thermostat-pro-timeline.js`
  - Type: **JavaScript Module**

### 📦 Manuelt (uden HACS)

1. Læg `thermostat-pro-timeline.js` i `/config/www/`.
2. Tilføj resource som ovenfor.

---

## 🧩 Card config (YAML)

Minimum:

```yaml
type: custom:thermostat-timeline-card
entities:
  - climate.stue
  - climate.sovevaerelse
```

### Typiske globale options

```yaml
title: "Heating – Overview"
row_height: 64
default_temp: 20
min_temp: 5
max_temp: 25

temp_unit: auto      # 'C' | 'F' | 'auto'
time_12h: auto       # true | false | 'auto'
time_source: browser # 'browser' | 'ha'

now_update_ms: 60000
show_top_now: false
now_extend_px: 76

auto_apply: true
apply_on_edit: true
apply_on_default_change: true
```

---

## 🎨 Colors (`color_ranges`)

Pr. rum:

```yaml
color_global: false
color_ranges:
  climate.stue:
    - { from: 5,  to: 18, color: "#87cefa" }
    - { from: 18, to: 21, color: "#90ee90" }
    - { from: 21, to: 25, color: "#ffb347" }
```

Globalt (gælder alle rum):

```yaml
color_global: true
color_ranges:
  "*":
    - { from: 5,  to: 18, color: "#87cefa" }
    - { from: 18, to: 21, color: "#90ee90" }
    - { from: 21, to: 25, color: "#ffb347" }
```

---

## 🔗 Merges (flere thermostat’er i én række)

```yaml
merges:
  climate.stue:
    - climate.stue_radiator_hojre
    - climate.stue_radiator_venstre
```

---

## 🔌 Turn on (til enheder der skal “tændes”)

```yaml
turn_on:
  climate.stue:
    enabled: true
    order: before   # 'before' | 'after'
```

---

## 🌡️ Rumtemp + eksterne sensorer

```yaml
show_room_temp: true
temp_sensors:
  climate.stue: sensor.stue_temperature
```

---

## 🔢 input_number mode (pr. række)

Slås til pr. række (matcher `entities[]`-index):

```yaml
room_use_input_number:
  - true
  - false
```

---

## ⏸️ Pause

```yaml
show_pause_button: true
pause_sensor_enabled: true
pause_sensor_entity: binary_sensor.varme_pause
```

---

## 🪟 Open Window Detection

```yaml
open_window:
  enabled: true
  open_delay_min: 2
  close_delay_min: 5
  sensors:
    climate.stue:
      - binary_sensor.stue_vindue
```

---

## 🗓️ Weekdays

```yaml
weekdays_enabled: true
weekdays_mode: weekday_weekend      # 'weekday_weekend' | 'weekday_sat_sun' | 'all_7'
weekdays_view: all_rooms_one_day    # 'all_rooms_one_day' | 'one_room_all_days'
weekdays_view_switch_in_timeline: false
weekdays_selected_room: ''
```

---

## 🏡 Away / Presence

Basis “away”:

```yaml
away:
  enabled: true
  persons:
    - person.mor
    - person.far
  target_c: 17
  advanced_enabled: false
  combos: {}
```

Live presence chips i header:

```yaml
presence_live_header: true
```

---

## 🗓️ Holidays

```yaml
holidays_enabled: true
holidays_source: calendar   # 'calendar' | 'manual'
holidays_entity: calendar.helligdage   # eller binary_sensor.* (on/off)
holidays_dates:
  - '2026-12-24'
  - '2026-12-25'
```

---

## 🧾 Profiles

```yaml
profiles_enabled: true
```

---

## 🔥 Boiler control

Switch-mode (anbefalet):

```yaml
boiler_enabled: true
boiler_switch_domain: switch        # 'switch' | 'input_boolean'
boiler_switch: switch.boiler
boiler_rooms: null                  # null = alle rum, eller liste af climate.*
boiler_on_offset: 0                 # ON når current < target - on_offset
boiler_off_offset: 0                # OFF når current >= target + off_offset
```

Legacy temperatur-sensor mode:

```yaml
boiler_temp_sensor: sensor.boiler_temp
boiler_min_temp: 20
boiler_max_temp: 25
```

---

## 🧩 Instances

Hvis du vil have flere uafhængige “stores” (fx “vinter” vs “sommer”), kan du namespace pr. kort:

```yaml
instance_enabled: true
instance_id: winter
```

---

## 🧰 Backups

```yaml
backup_auto_enabled: true
backup_interval_days: 1
```

Integration-services (avanceret):

- `thermostat_timeline.backup_now` (sektioner: `main`, `weekday`, `presence`, `settings`, `holiday`, `colors`, `profiles`)
- `thermostat_timeline.restore_now` (mode `merge|replace`, optional `slot`)
- `thermostat_timeline.delete_backup` (optional `slot`)
- `thermostat_timeline.select_instance` / `thermostat_timeline.rename_instance`
- `thermostat_timeline.clear_store` / `thermostat_timeline.factory_reset`

---

## 🧯 Troubleshooting

### “Custom element already defined” / kortet loader dobbelt

Det sker næsten altid hvis du har tilføjet JS resource flere gange (fx både `/local/thermostat-pro-timeline.js` og en gammel `/hacsfiles/...`).

- Gå i **Settings → Dashboards → Resources** og sørg for at der kun er én entry.
- Genindlæs browser (hard refresh) efter ændring.

### Resource mangler / 404

- Tjek at `/config/www/thermostat-pro-timeline.js` findes.
- Hvis integrationen ikke auto-registrer resource, tilføj den manuelt som beskrevet i Installation.

### Sync opfører sig “mærkeligt” efter skift af `storage_enabled`

Det er forventet: `storage_enabled: false` bruger LocalStorage; `storage_enabled: true` bruger integrationens shared store.

---

## 🌍 Localization

| Language       | Supported |
| -------------- | --------- |
| 🇩🇰 Danish    | ✅         |
| 🇸🇪 Swedish   | ✅         |
| 🇳🇴 Norwegian | ✅         |
| 🇬🇧 English   | ✅         |
| 🇩🇪 German    | ✅         |
| 🇪🇸 Spanish   | ✅         |
| 🇫🇷 French    | ✅         |
| 🇫🇮 Finnish   | ✅         |
| 🇨🇿 Czech     | ✅         |

Defaults to **English** if not translated.
Want to help? Open an issue titled `Locale request: <language>`.
# Thermostat Timeline Card 🔥🕒

[![hacs\_badge](https://img.shields.io/badge/HACS-Default-blue.svg)](https://hacs.xyz) [![Downloads](https://img.shields.io/github/downloads/qlerup/lovelace-thermostat-pro-timeline/total)](https://github.com/qlerup/lovelace-thermostat-pro-timeline/releases)


## Support this project

If you find this project useful, you can support me on Ko-fi 💙  

[![Buy me some debugging time on Ko-fi](https://img.shields.io/badge/%F0%9F%90%9E_Buy_me_some_debugging_time_on-Ko--fi-2ea44f?style=for-the-badge)](https://ko-fi.com/qlerup)








A **Lovelace card** for **Home Assistant** that lets you plan temperatures on a simple timeline🏡🔕️️ — and automatically apply them to your `climate.*` entities.

<img width="2288" height="476" alt="image" src="https://github.com/user-attachments/assets/95a17e9d-e404-4bad-ba93-5af0a6cff6d5" />


---

## ✨ Features

* 🧊 Per-thermostat **or merged room-based timeline**
* 🌡️➕🌡️ **Merge multiple thermostats** into one room – control them together via a single timeline 🏠🕒
* 📱 Tablet freindly
* 🎨 **Color Blocks** — visually highlight temperature ranges with custom colors 🌈
* 🏡 **Away From Home** — auto-set your “away” temperature when nobody’s home, resume schedule when someone returns 🚶‍♂️🏠
* 🔥 Default temperature per row
* 🕓 **Temperature Units** — choose **°C or °F**, or let it auto-detect from Home Assistant 🌡️
* ⏰ **12/24-Hour Clock Toggle** — display times using your preferred format 🕒
* 🕰️ **AM/PM Picker Fixed** — smooth, reliable time selection ✅
* 🗆️ **Weekday Timeline Modes** — set up flexible daily schedules:

  * Weekdays + Weekend (Mon–Fri, Sat–Sun)
  * Weekdays + Saturday + Sunday
  * Individual days per week 🧩
* 🔁 Double-click / double-tap to edit blocks
* ⏱️ “Now” indicator on the timeline
* 🤖 Optional auto-apply via `climate.set_temperature`
* 🦭 Clean, modern **Editor UI**
* 🥳 Lovelace GUI editor support
* 🔁 **Storage sensor toggle** — choose between synced sensor or local-only mode
* 🔄 **Data migration** — easily transfer data from browser to storage sensor 🔁
* 🙈 **Storage sensor auto-hides** when disabled — cleaner UI ✨
* 🥳 **Granular data clearing options** — choose exactly what to wipe:

  * 🥈 All data (sensor + browser)
  * 🥽 Local only (browser)
  * 🗄️ Storage sensor only
  * 🎨 Clear color block data (browser only, storange only or all)
* 🌡️⛔ **Max temperature limit** — prevent overheating with an upper bound 🚡️

---

## 🚷 Storage & Sync

* **With integration (`thermostat-pro-timeline-sync`)**: Keeps data synced across browsers/devices.
  → [Integration repo](https://github.com/qlerup/thermostat-pro-timeline-sync)
* **Without integration**: Data stored locally (browser LocalStorage).
* 🔁 **Toggle per card**: Switch between local-only and sensor-based storage.
* 🔄 **Data migration tool**: Transfer browser data → storage sensor data 🔁
* 🙈 **Auto-hide sensor** when disabled — reduces UI clutter
* 🥳 **Clear data menu**:

  * 🥈 All data — clears both sensor + browser
  * 🥽 Local only — clears browser cache
  * 🗄️ Storage sensor only — clears persistent store

> ℹ️ Mixing modes may cause timelines not to carry over — expected behavior.

---

## 🌍 Localization

| Language       | Supported |
| -------------- | --------- |
| 🇩🇰 Danish    | ✅         |
| 🇸🇪 Swedish   | ✅         |
| 🇳🇴 Norwegian | ✅         |
| 🇬🇧 English   | ✅         |
| 🇩🇪 German    | ✅         |
| 🇪🇸 Spanish   | ✅         |
| 🇫🇷 French    | ✅         |
| 🇫🇮 Finnish   | ✅         |
| 🇨🇿 Czech     | ✅         |


💡 Defaults to **English** if not translated.
Want to help? Open an issue titled `Locale request: <language>`.

---

## ⚙️ Installation (via HACS – as a custom repository)

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=qlerup&repository=lovelace-thermostat-pro-timeline)

1. Upload/publish this repo as `lovelace-thermostat-pro-timeline`.
2. In Home Assistant → **HACS** → ⋯ → **Custom repositories** → https://github.com/qlerup/lovelace-thermostat-pro-timeline, **Category: Dashboard**.
3. Install via HACS.

   * Resource URL: `/hacsfiles/lovelace-thermostat-pro-timeline/thermostat-pro-timeline.js`

### 🦙 Manual installation

1. Copy `thermostat-pro-timeline.js` to `/config/www/`.
2. Add the resource in **Settings → Dashboards → Resources**:

   * URL: `/local/thermostat-pro-timeline.js`
   * Type: **JavaScript Module**

---

## 🏷️ Title

Display title for the card. If omitted, a localized default title is used automatically.

```yaml
title: "Heating – Overview"
```

---

## 🧩 Entities

Lists the thermostats (climate.*) to show. Order in the list = order in the UI.

```yaml
entities:
  - climate.stue
  - climate.sovevaerelse
```

---

## 📏 Row height

Row height in pixels. Typical range 40–120, default 64.

```yaml
row_height: 64
```

---
## 🌡️ Default temp

Fallback temperature (°C) used for periods without blocks.

```yaml
default_temp: 20
```

---

## 📉 Min temp

Global minimum for temperature pickers and blocks (°C).

```yaml
min_temp: 5
```

---

## 📈 Max temp

Global maximum for temperature pickers and blocks (°C).

```yaml
max_temp: 25
```

---

## 🔁 Temp unit

Controls display unit. 'C', 'F', or 'auto' (detects preference).

```yaml
temp_unit: auto
```

---

## ⏰ Time 12h

12‑hour clock format. true (AM/PM), false (24‑hour) or 'auto'.

```yaml
time_12h: auto
```

---
## ⏱ Now update ms

How often the UI re‑renders and moves the "now" cursor (ms).

```yaml
now_update_ms: 60000
```

---
## 📍 Show top now

```yaml
show_top_now: true
```

---

## ↕️ Now extend px

How far the top "now" line extends (px).

```yaml
now_extend_px: 76
```

---

## ⚙️ Auto apply

Automatically apply the desired temperature to the current climate entity (and any merges) on load and at the next time boundary.

```yaml
auto_apply: true
```

---

## ✏️ Apply on edit

If an edit changes the current period, apply the new temperature immediately.

```yaml
apply_on_edit: true
```

---

## 🗓️ Weekdays enabled

Enable per‑day schedules (weekday/weekend or all days).

```yaml
weekdays_enabled: true
```

---

## 🗂️ Weekdays mode

Choose the grouping: 'weekday_weekend', 'weekday_sat_sun', or 'all_7'.

```yaml
weekdays_mode: weekday_weekend
```

---

## 🏷️ Labels

Custom display name per entity (left label in the UI).

```yaml
labels:
  climate.stue: Living Room
  climate.sovevaerelse: Bedroom
```

---

## 🔗 Merges

Merge secondary climate entities into a primary so setpoints hit them all at once.

```yaml
merges:
  climate.stue:
    - climate.stue_radiator_hojre
    - climate.stue_radiator_venstre
```

---

## 🎨 Color Blocks

Bring color to your comfort! 🌈

Define visual temperature ranges to make your schedule instantly readable.

```yaml
color_blocks:
  - from: 18
    to: 21
    color: "#00f0ec"
  - from: 21
    to: 24
    color: "#ffb347"
```

* Each range colors any block that fits inside it.
* Helps you spot comfort vs eco zones quickly 🔍
* Order ranges thoughtfully to avoid overlap confusion.

---

## 💾 Storage enabled

Store shared schedules/settings in a sensor (integration) across dashboards/users.

```yaml
storage_enabled: true
```

---

## 🗄️ Storage entity

The sensor used as the shared store (from the integration), e.g. sensor.thermostat_timeline.

```yaml
storage_entity: sensor.thermostat_timeline
```

---

## 🏡 Away From Home (Presence-Aware Mode)

Keep temperatures smart and efficient while you’re out 💡

```yaml
away:
  enabled: true
  persons:
    - person.mor
    - person.far
  target_c: 17
```



* Automatically applies the **away temperature** when everyone is away.
* When someone returns home, your schedule resumes automatically 🙌
* Works seamlessly with your existing timelines.

---

## 🤪 Usage

```yaml
type: custom:thermostat-timeline-card
title: Varme – Stue & Soveværelse
entities:
  - climate.stue
  - climate.sovevaerelse

row_height: 64
default_temp: 20
min_temp: 5
max_temp: 25

temp_unit: auto         # 'C' | 'F' | 'auto'
time_12h: auto          # true | false | 'auto'

now_update_ms: 60000
show_top_now: false
now_extend_px: 76

auto_apply: true
apply_on_edit: true
apply_on_default_change: true

weekdays_enabled: true
weekdays_mode: weekday_sat_sun

labels:
  climate.stue: Stue
  climate.sovevaerelse: Soveværelse

merges:
  climate.stue:
    - climate.traevaerk_termostat

color_ranges:
  climate.stue:
    - { from: 5,  to: 18, color: "#87cefa" }
    - { from: 18, to: 21, color: "#90ee90" }
    - { from: 21, to: 25, color: "#ffb347" }

storage_enabled: false
storage_entity: sensor.thermostat_timeline

away:
  enabled: true
  persons:
    - person.mor
    - person.far
  target_c: 17

```

---

🎉 **Enjoy your smarter, safer, and now even more colorful thermostat control!**
🌡️🔥 With **Color Blocks**, **Away Mode**, and fixed **AM/PM Picker** — your timeline just got both smarter *and* prettier 💫
