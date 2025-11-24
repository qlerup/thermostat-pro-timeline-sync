from __future__ import annotations
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN, SIGNAL_UPDATED


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    sensors = [
        ThermostatTimelineSettingsSensor(hass),
        ThermostatTimelineColorsSensor(hass),
        ThermostatTimelineSchedulesSensor(hass),
        ThermostatTimelineWeekdaysSensor(hass),
        ThermostatTimelineBackup(hass),
    ]
    async_add_entities(sensors, True)


class _BaseTimelineSensor(SensorEntity):
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant):
        self.hass = hass

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._refresh))
        await self._refresh()

    async def _refresh(self):
        self.async_write_ha_state()

    @property
    def _versions(self) -> dict:
        return self.hass.data[DOMAIN].get("versions", {}) or {}

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "thermostat_timeline")},
            name="Thermostat Timeline",
            manufacturer="Custom",
        )


class ThermostatTimelineSettingsSensor(_BaseTimelineSensor):
    _attr_name = "Thermostat Timeline (Settings)"
    _attr_icon = "mdi:cog"

    def __init__(self, hass: HomeAssistant):
        super().__init__(hass)
        # Reuse legacy unique_id so existing entity_id keeps working
        self._attr_unique_id = "thermostat_timeline_overview"

    @property
    def native_value(self):
        return int(self._versions.get("settings", self.hass.data[DOMAIN].get("version", 1)))

    @property
    def extra_state_attributes(self):
        return {
            "settings": self.hass.data[DOMAIN].get("settings_section", {}),
            "tt_section": "settings",
            "version": self._versions.get("settings", self.hass.data[DOMAIN].get("version", 1)),
        }


class ThermostatTimelineColorsSensor(_BaseTimelineSensor):
    _attr_name = "Thermostat Timeline (Colors)"
    _attr_icon = "mdi:palette"

    def __init__(self, hass: HomeAssistant):
        super().__init__(hass)
        self._attr_unique_id = "thermostat_timeline_colors"

    @property
    def native_value(self):
        return int(self._versions.get("colors", self.hass.data[DOMAIN].get("version", 1)))

    @property
    def extra_state_attributes(self):
        colors = self.hass.data[DOMAIN].get("colors_section") or {"color_ranges": {}, "color_global": False}
        return {
            "colors": colors,
            "tt_section": "colors",
            "version": self._versions.get("colors", self.hass.data[DOMAIN].get("version", 1)),
        }


class ThermostatTimelineSchedulesSensor(_BaseTimelineSensor):
    _attr_name = "Thermostat Timeline (Schedules)"
    _attr_icon = "mdi:timeline-clock-outline"

    def __init__(self, hass: HomeAssistant):
        super().__init__(hass)
        self._attr_unique_id = "thermostat_timeline_schedules"

    @property
    def native_value(self):
        return int(self._versions.get("schedules_main", self.hass.data[DOMAIN].get("version", 1)))

    @property
    def extra_state_attributes(self):
        settings = self.hass.data[DOMAIN].get("settings", {}) or {}
        return {
            "schedules": self.hass.data[DOMAIN].get("schedules_main", {}),
            "tt_section": "schedules_main",
            "version": self._versions.get("schedules_main", self.hass.data[DOMAIN].get("version", 1)),
            "temp_unit": settings.get("temp_unit"),
        }


class ThermostatTimelineWeekdaysSensor(_BaseTimelineSensor):
    _attr_name = "Thermostat Timeline (Weekdays)"
    _attr_icon = "mdi:calendar-week"

    def __init__(self, hass: HomeAssistant):
        super().__init__(hass)
        self._attr_unique_id = "thermostat_timeline_weekdays"

    @property
    def native_value(self):
        return int(self._versions.get("schedules_weekday", self.hass.data[DOMAIN].get("version", 1)))

    @property
    def extra_state_attributes(self):
        settings = self.hass.data[DOMAIN].get("settings", {}) or {}
        return {
            "schedules": self.hass.data[DOMAIN].get("schedules_weekday", {}),
            "tt_section": "schedules_weekday",
            "version": self._versions.get("schedules_weekday", self.hass.data[DOMAIN].get("version", 1)),
            "temp_unit": settings.get("temp_unit"),
        }


class ThermostatTimelineBackup(SensorEntity):
    _attr_name = "Thermostat Timeline Backup"
    _attr_icon = "mdi:archive"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._attr_unique_id = "thermostat_timeline_backup"

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._refresh))
        await self._refresh()

    async def _refresh(self):
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "thermostat_timeline")},
            name="Thermostat Timeline",
            manufacturer="Custom",
        )

    @property
    def native_value(self):
        return int(self.hass.data[DOMAIN].get("backup_version", 1))

    @property
    def extra_state_attributes(self):
        return {
            "schedules": self.hass.data[DOMAIN].get("backup_schedules", {}),
            "settings": self.hass.data[DOMAIN].get("backup_settings", {}),
            "version": int(self.hass.data[DOMAIN].get("backup_version", 1)),
            "last_backup_ts": self.hass.data[DOMAIN].get("backup_last_ts"),
            "partial_flags": self.hass.data[DOMAIN].get("backup_partial_flags"),
        }
