from __future__ import annotations
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN, SIGNAL_UPDATED
from . import _debug_log

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    ent_main = ThermostatTimelineOverview(hass)
    ent_backup = ThermostatTimelineBackup(hass)
    ent_schedules = ThermostatTimelineSchedules(hass)
    ent_settings = ThermostatTimelineSettings(hass)
    ent_colors = ThermostatTimelineColors(hass)
    async_add_entities([ent_main, ent_backup, ent_schedules, ent_settings, ent_colors], True)

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
        try:
            data = self.hass.data.get(DOMAIN, {}) or {}
            ctx = {
                "version": int(data.get("version", 1)),
                "ml_keys": list((data.get("ml") or {}).keys())[:5],
            }
            _debug_log(self.hass, "sensor:overview_refresh", ctx)
        except Exception:
            pass
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
        d = self.hass.data.get(DOMAIN, {})
        attrs = {
            "version": int(d.get("version", 1)),
        }
        # Expose Early Start (ML) runtime/status if available
        ml = d.get("ml") or {}
        if isinstance(ml, dict):
            # Top-level status mirrors for convenience
            if "ml_status" in d:
                attrs["ml_status"] = d.get("ml_status")
            if "ml_enabled" in d:
                attrs["ml_enabled"] = d.get("ml_enabled")
            if "ml_trained_cycles" in d:
                attrs["ml_trained_cycles"] = d.get("ml_trained_cycles")
            if "ml_confidence_pct" in d:
                attrs["ml_confidence_pct"] = d.get("ml_confidence_pct")
            if "ml_last_trained_iso" in d:
                attrs["ml_last_trained_iso"] = d.get("ml_last_trained_iso")
            if "ml_per_room" in d:
                attrs["ml_per_room"] = d.get("ml_per_room")
            if "ml_debug_last_begin_attempt" in d and d.get("ml_debug_last_begin_attempt"):
                attrs["ml_debug_last_begin_attempt"] = d.get("ml_debug_last_begin_attempt")
            if "ml_debug_last_cancel_reason" in d and d.get("ml_debug_last_cancel_reason"):
                attrs["ml_debug_last_cancel_reason"] = d.get("ml_debug_last_cancel_reason")
        return attrs


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
        try:
            data = self.hass.data.get(DOMAIN, {}) or {}
            ctx = {
                "backup_version": int(data.get("backup_version", 1)),
                "sched_rooms": len((data.get("backup_schedules") or {}).keys()),
            }
            _debug_log(self.hass, "sensor:backup_refresh", ctx)
        except Exception:
            pass
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


class ThermostatTimelineSchedules(SensorEntity):
    """Separate sensor for schedules only - prevents exceeding 16KB limit"""
    _attr_name = "Thermostat Timeline Schedules"
    _attr_icon = "mdi:calendar-clock"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._attr_unique_id = "thermostat_timeline_schedules"

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._refresh))
        await self._refresh()

    async def _refresh(self):
        try:
            data = self.hass.data.get(DOMAIN, {}) or {}
            sched = data.get("schedules") or {}
            ctx = {
                "rooms": len(sched.keys()),
                "total_blocks": sum(len((row or {}).get("blocks", [])) for row in sched.values() if isinstance(row, dict))
            }
            _debug_log(self.hass, "sensor:schedules_refresh", ctx)
        except Exception:
            pass
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
        return int(self.hass.data[DOMAIN].get("version", 1))

    @property
    def extra_state_attributes(self):
        d = self.hass.data.get(DOMAIN, {})
        return {
            "schedules": d.get("schedules", {}),
            "version": int(d.get("version", 1)),
        }


class ThermostatTimelineSettings(SensorEntity):
    """Separate sensor for settings only - prevents exceeding 16KB limit"""
    _attr_name = "Thermostat Timeline Settings"
    _attr_icon = "mdi:cog-outline"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._attr_unique_id = "thermostat_timeline_settings"

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._refresh))
        await self._refresh()

    async def _refresh(self):
        try:
            data = self.hass.data.get(DOMAIN, {}) or {}
            settings = data.get("settings") or {}
            ctx = {
                "keys": sorted(settings.keys())[:20],
                "count": len(settings.keys())
            }
            _debug_log(self.hass, "sensor:settings_refresh", ctx)
        except Exception:
            pass
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
        return int(self.hass.data[DOMAIN].get("version", 1))

    @property
    def extra_state_attributes(self):
        d = self.hass.data.get(DOMAIN, {})
        raw_settings = d.get("settings", {}) or {}
        trimmed = raw_settings if not isinstance(raw_settings, dict) else {
            k: v for k, v in raw_settings.items() if k not in ("color_ranges", "color_global")
        }
        attrs = {
            "settings": trimmed,
            "version": int(d.get("version", 1)),
        }
        # Expose last set_store payload for debugging settings writes
        try:
            if d.get("debug_last_settings"):
                attrs["debug_last_settings"] = d.get("debug_last_settings")
        except Exception:
            pass
        try:
            if d.get("debug_settings_history"):
                attrs["debug_settings_history"] = d.get("debug_settings_history")
        except Exception:
            pass
        return attrs


class ThermostatTimelineColors(SensorEntity):
    """Expose color configuration separately to stay well below 16KB per sensor."""

    _attr_name = "Thermostat Timeline Colors"
    _attr_icon = "mdi:palette"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._attr_unique_id = "thermostat_timeline_colors"

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._refresh))
        await self._refresh()

    async def _refresh(self):
        try:
            data = self.hass.data.get(DOMAIN, {}) or {}
            settings = data.get("settings") or {}
            if not isinstance(settings, dict):
                settings = {}
            ranges = settings.get("color_ranges") if isinstance(settings, dict) else None
            ctx = {
                "has_ranges": bool(ranges),
                "global": settings.get("color_global") if isinstance(settings, dict) else None,
            }
            _debug_log(self.hass, "sensor:colors_refresh", ctx)
        except Exception:
            pass
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
        return int(self.hass.data[DOMAIN].get("version", 1))

    @property
    def extra_state_attributes(self):
        d = self.hass.data.get(DOMAIN, {})
        settings = d.get("settings", {}) or {}
        if not isinstance(settings, dict):
            settings = {}
        return {
            "color_ranges": settings.get("color_ranges", {}),
            "color_global": settings.get("color_global"),
            "version": int(d.get("version", 1)),
        }
