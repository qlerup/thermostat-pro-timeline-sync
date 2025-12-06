from __future__ import annotations
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util import dt as dt_util
from .const import DOMAIN, SIGNAL_UPDATED, BACKUP_SLOT_MAX

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    ent_main = ThermostatTimelineOverview(hass)
    ent_settings = ThermostatTimelineSettings(hass)
    ent_colors = ThermostatTimelineColors(hass)
    ent_weekdays = ThermostatTimelineWeekdays(hass)
    ent_profiles = ThermostatTimelineProfiles(hass)
    ent_backup = ThermostatTimelineBackup(hass)
    entities = [ent_main, ent_settings, ent_colors, ent_weekdays, ent_profiles, ent_backup]
    # Rolling backup slot sensors (per section, per slot)
    for idx in range(BACKUP_SLOT_MAX):
        for section in ("schedules", "weekdays", "profiles", "settings", "colors"):
            entities.append(ThermostatTimelineBackupSlot(hass, section, idx + 1))
    async_add_entities(entities, True)

class ThermostatTimelineOverview(SensorEntity):
    _attr_name = "Schedules"
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
            # Expose settings on the main sensor so dashboards that only point to
            # the schedules entity can still pick up config toggles (e.g. profiles_enabled).
            "settings": self.hass.data[DOMAIN].get("settings", {}),
            "version": int(self.hass.data[DOMAIN].get("version", 1)),
        }


class ThermostatTimelineSettings(SensorEntity):
    _attr_name = "Thermostat Timeline Settings"
    _attr_icon = "mdi:cog"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._attr_unique_id = "thermostat_timeline_settings"

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
        # Dedicated settings version counter
        return int(self.hass.data[DOMAIN].get("settings_version", self.hass.data[DOMAIN].get("version", 1)))

    @property
    def extra_state_attributes(self):
        return {
            "settings": self.hass.data[DOMAIN].get("settings", {}),
            "version": int(self.hass.data[DOMAIN].get("settings_version", self.hass.data[DOMAIN].get("version", 1))),
        }


class ThermostatTimelineColors(SensorEntity):
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
        return int(self.hass.data[DOMAIN].get("colors_version", self.hass.data[DOMAIN].get("version", 1)))

    @property
    def extra_state_attributes(self):
        colors = self.hass.data[DOMAIN].get("colors", {}) or {}
        return {
            "colors": colors,
            "version": int(self.hass.data[DOMAIN].get("colors_version", self.hass.data[DOMAIN].get("version", 1))),
        }


class ThermostatTimelineWeekdays(SensorEntity):
    _attr_name = "Weekdays schedules"
    _attr_icon = "mdi:calendar-week"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._attr_unique_id = "thermostat_timeline_weekdays"

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
        return int(self.hass.data[DOMAIN].get("weekday_version", self.hass.data[DOMAIN].get("version", 1)))

    @property
    def extra_state_attributes(self):
        weekdays = self.hass.data[DOMAIN].get("weekday_schedules", {}) or {}
        return {
            "weekdays": weekdays,
            "version": int(self.hass.data[DOMAIN].get("weekday_version", self.hass.data[DOMAIN].get("version", 1))),
        }


class ThermostatTimelineProfiles(SensorEntity):
    _attr_name = "Profile schedules"
    _attr_icon = "mdi:account-box-multiple"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._attr_unique_id = "thermostat_timeline_profiles"

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
        return int(self.hass.data[DOMAIN].get("profile_version", self.hass.data[DOMAIN].get("version", 1)))

    @property
    def extra_state_attributes(self):
        profiles = self.hass.data[DOMAIN].get("profile_schedules", {}) or {}
        return {
            "profiles": profiles,
            "version": int(self.hass.data[DOMAIN].get("profile_version", self.hass.data[DOMAIN].get("version", 1))),
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
            "profiles": self.hass.data[DOMAIN].get("backup_profiles", {}),
            "version": int(self.hass.data[DOMAIN].get("backup_version", 1)),
            "last_backup_ts": self.hass.data[DOMAIN].get("backup_last_ts"),
            "partial_flags": self.hass.data[DOMAIN].get("backup_partial_flags"),
            "slots": self.hass.data[DOMAIN].get("backup_slots", []),
            "slot_index": self.hass.data[DOMAIN].get("backup_slot_index", 0),
        }


class ThermostatTimelineBackupSlot(SensorEntity):
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, section: str, slot_index: int):
        self.hass = hass
        self._section = section
        self._slot = slot_index  # 1-based for display
        self._attr_unique_id = f"thermostat_timeline_{section}_backup_slot_{slot_index}"
        icon_map = {
            "schedules": "mdi:timeline-clock-outline",
            "weekdays": "mdi:calendar-week",
            "profiles": "mdi:account-box-multiple",
            "settings": "mdi:cog",
            "colors": "mdi:palette",
        }
        name_map = {
            "schedules": "Schedules",
            "weekdays": "Weekdays",
            "profiles": "Profile schedules",
            "settings": "Settings",
            "colors": "Colors",
        }
        base = name_map.get(section, section.title())
        self._attr_name = f"{base} Backup #{slot_index}"
        self._attr_icon = icon_map.get(section, "mdi:archive")

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._refresh))
        await self._refresh()

    async def _refresh(self):
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "thermostat_timeline_backups")},
            name="Thermostat Timeline Backups",
            manufacturer="Custom",
        )

    @property
    def native_value(self):
        data = self._slot_data()
        if not data:
            return None
        ts = data.get("ts")
        pretty = None
        try:
            if ts:
                dt = dt_util.parse_datetime(ts)
                if dt:
                    pretty = dt_util.as_local(dt).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pretty = None
        return pretty or ts or data.get("version")

    def _slot_data(self):
        try:
            slots = self.hass.data.get(DOMAIN, {}).get("backup_slots", [])
            if not isinstance(slots, list):
                return None
            if len(slots) < self._slot:
                return None
            return slots[self._slot - 1]
        except Exception:
            return None

    @property
    def extra_state_attributes(self):
        data = self._slot_data()
        if not data:
            return {}
        sections = data.get("sections", {}) if isinstance(data, dict) else {}
        sec = sections.get(self._section) if isinstance(sections, dict) else {}
        if sec is None:
            sec = {}
        attrs = dict(sec) if isinstance(sec, dict) else {}
        # Only expose section content plus minimal context
        # Provide a readable local timestamp alongside the raw created_at
        created = data.get("ts")
        pretty = None
        try:
            if created:
                dt = dt_util.parse_datetime(created)
                if dt:
                    pretty = dt_util.as_local(dt).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pretty = None
        attrs.update({
            "created_at": data.get("ts"),
            "created_at_local": pretty,
            "slot": self._slot,
            "section": self._section,
        })
        return attrs
