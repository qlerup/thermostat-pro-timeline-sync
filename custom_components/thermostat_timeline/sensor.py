from __future__ import annotations
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN, SIGNAL_UPDATED

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    ent_main = ThermostatTimelineOverview(hass)
    ent_backup = ThermostatTimelineBackup(hass)
    async_add_entities([ent_main, ent_backup], True)

class ThermostatTimelineOverview(SensorEntity):
    _attr_name = "Thermostat Timeline"
    _attr_icon = "mdi:timeline-clock-outline"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._attr_unique_id = "thermostat_timeline_overview"

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
        # Brug version som "state" – ændrer sig ved hver gem
        return int(self.hass.data[DOMAIN].get("version", 1))

    @property
    def extra_state_attributes(self):
        return {
            "schedules": self.hass.data[DOMAIN].get("schedules", {}),
            "settings": self.hass.data[DOMAIN].get("settings", {}),
            "version": int(self.hass.data[DOMAIN].get("version", 1)),
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
        }
