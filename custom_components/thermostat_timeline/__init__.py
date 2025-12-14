from __future__ import annotations

import asyncio
import logging
import os
import traceback

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.dispatcher import async_dispatcher_send, async_dispatcher_connect
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_state_change_event
from homeassistant.util import dt as dt_util
from datetime import timedelta
from homeassistant.components.http import HomeAssistantView

from .const import DOMAIN, STORAGE_KEY, STORAGE_VERSION, SIGNAL_UPDATED, BACKUP_STORAGE_KEY

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


_LOGGER = logging.getLogger(__name__)


def _dbg_write_sync(path: str, msg: str) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(msg + "\n")


def _dbg(msg: str) -> None:
    """Write lightweight debug info to local debug.log without blocking the event loop."""
    try:
        base = os.path.dirname(__file__)
        path = os.path.join(base, "debug.log")

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _dbg_write_sync(path, msg)
            return

        # Fire-and-forget file write in a worker thread.
        loop.create_task(asyncio.to_thread(_dbg_write_sync, path, msg))
    except Exception:
        # Debug logging must never interfere with the integration.
        pass


def build_state_payload(hass: HomeAssistant, versions_only: bool = False) -> dict:
    """Build a JSON-serializable snapshot of the current timeline state."""
    if not hass or DOMAIN not in hass.data:
        # Return safe defaults if integration not yet loaded
        _dbg("build_state_payload: hass or DOMAIN missing, returning defaults")
        return {
            "version": 1,
            "settings_version": 1,
            "colors_version": 1,
            "weekday_version": 1,
            "profile_version": 1,
            "backup_version": 1,
        } if versions_only else {
            "version": 1,
            "settings_version": 1,
            "colors_version": 1,
            "weekday_version": 1,
            "profile_version": 1,
            "backup_version": 1,
            "schedules": {},
            "weekdays": {},
            "profiles": {},
            "settings": {},
            "colors": {},
            "backup": {"slots": [], "slot_index": 0, "schedules": {}, "settings": {}, "weekdays": {}, "profiles": {}, "version": 1, "last_backup_ts": None, "partial_flags": None},
        }
    data = hass.data[DOMAIN]
    payload = {
        "version": int(data.get("version", 1)),
        "settings_version": int(data.get("settings_version", data.get("version", 1))),
        "colors_version": int(data.get("colors_version", data.get("version", 1))),
        "weekday_version": int(data.get("weekday_version", data.get("version", 1))),
        "profile_version": int(data.get("profile_version", data.get("version", 1))),
        "backup_version": int(data.get("backup_version", 1)),
    }
    if versions_only:
        return payload
    payload.update({
        "schedules": data.get("schedules", {}),
        "weekdays": data.get("weekday_schedules", {}),
        "profiles": data.get("profile_schedules", {}),
        "settings": data.get("settings", {}),
        "colors": data.get("colors", {}),
        "backup": {
            "slots": data.get("backup_slots", []),
            "slot_index": data.get("backup_slot_index", 0),
            "schedules": data.get("backup_schedules", {}),
            "settings": data.get("backup_settings", {}),
            "weekdays": data.get("backup_weekdays", {}),
            "profiles": data.get("backup_profiles", {}),
            "version": data.get("backup_version", 1),
            "last_backup_ts": data.get("backup_last_ts"),
            "partial_flags": data.get("backup_partial_flags"),
        },
    })
    return payload


class ThermostatTimelineStateView(HomeAssistantView):
    url = "/api/thermostat_timeline/state"
    name = "api:thermostat_timeline_state"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        self.hass = hass

    async def get(self, request):
        try:
            _dbg("API /state requested")
            payload = build_state_payload(self.hass, versions_only=False)
            _LOGGER.debug("API /state requested, version=%s", payload.get("version"))
            _dbg(f"API /state returning version={payload.get('version')}")
            return self.json(payload)
        except Exception as e:
            _LOGGER.error("API /state failed: %s", e, exc_info=True)
            _dbg("API /state failed: " + str(e) + "\n" + traceback.format_exc())
            return self.json({"error": str(e)}, status_code=500)


class ThermostatTimelineVersionView(HomeAssistantView):
    url = "/api/thermostat_timeline/version"
    name = "api:thermostat_timeline_version"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        self.hass = hass

    async def get(self, request):
        try:
            _dbg("API /version requested")
            payload = build_state_payload(self.hass, versions_only=True)
            _LOGGER.debug("API /version requested, returning: %s", payload)
            _dbg(f"API /version returning: {payload}")
            return self.json(payload)
        except Exception as e:
            _LOGGER.error("API /version failed: %s", e, exc_info=True)
            _dbg("API /version failed: " + str(e) + "\n" + traceback.format_exc())
            return self.json({"error": str(e)}, status_code=500)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    # YAML fallback: hvis brugeren har 'thermostat_timeline:' i configuration.yaml
    if DOMAIN in config:
        # Opret/indlæs config entry via IMPORT
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "import"}, data={}
            )
        )
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}.json")
    data = await store.async_load() or {}
    backup_store = Store(hass, STORAGE_VERSION, f"{BACKUP_STORAGE_KEY}.json")
    backup_data = await backup_store.async_load() or {}

    _dbg("async_setup_entry: loaded data version=" + str(data.get("version")))

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["store"] = store
    hass.data[DOMAIN]["schedules"] = data.get("schedules", {})
    hass.data[DOMAIN]["weekday_schedules"] = data.get("weekdays", {}) or {}
    hass.data[DOMAIN]["profile_schedules"] = data.get("profiles", {}) or {}
    hass.data[DOMAIN]["settings"] = data.get("settings", {})
    hass.data[DOMAIN]["colors"] = data.get("colors", {}) or {}
    hass.data[DOMAIN]["version"] = int(data.get("version", 1))
    # Track a separate version for settings-only changes (for the new settings sensor)
    hass.data[DOMAIN]["settings_version"] = int(data.get("settings_version", data.get("version", 1)))
    # Track a separate version for colors-only changes
    hass.data[DOMAIN]["colors_version"] = int(data.get("colors_version", data.get("version", 1)))
    # Track a separate version for weekday-only changes
    hass.data[DOMAIN]["weekday_version"] = int(data.get("weekday_version", data.get("version", 1)))
    # Track a separate version for profiles-only changes
    hass.data[DOMAIN]["profile_version"] = int(data.get("profile_version", data.get("version", 1)))
    # Backup payloads
    hass.data[DOMAIN]["backup_schedules"] = backup_data.get("schedules", {})
    hass.data[DOMAIN]["backup_settings"] = backup_data.get("settings", {})
    hass.data[DOMAIN]["backup_weekdays"] = backup_data.get("weekdays", {}) or {}
    hass.data[DOMAIN]["backup_profiles"] = backup_data.get("profiles", {}) or {}
    hass.data[DOMAIN]["backup_version"] = int(backup_data.get("version", 1))
    hass.data[DOMAIN]["backup_last_ts"] = backup_data.get("last_backup_ts")
    # Track whether the backup is partial and which sections it includes
    hass.data[DOMAIN]["backup_partial_flags"] = backup_data.get("partial_flags")
    slots = backup_data.get("slots", [])
    if not isinstance(slots, list):
        slots = []
    # Normalize: only dict entries or None
    norm_slots = []
    for entry in slots:
        if isinstance(entry, dict):
            norm_slots.append(entry)
        else:
            norm_slots.append(None)
    hass.data[DOMAIN]["backup_slots"] = norm_slots
    hass.data[DOMAIN]["backup_slot_index"] = len([s for s in norm_slots if s])

    _dbg("async_setup_entry: setup complete, version=" + str(hass.data[DOMAIN]["version"]))

    async def _save_and_broadcast():
        # Broadcast first so UI updates immediately
        async_dispatcher_send(hass, SIGNAL_UPDATED)
        try:
            await store.async_save({
                "schedules": hass.data[DOMAIN]["schedules"],
                "weekdays": hass.data[DOMAIN].get("weekday_schedules", {}),
                "profiles": hass.data[DOMAIN].get("profile_schedules", {}),
                "settings": hass.data[DOMAIN].get("settings", {}),
                "colors": hass.data[DOMAIN].get("colors", {}),
                "version": hass.data[DOMAIN]["version"],
                "settings_version": hass.data[DOMAIN].get("settings_version", hass.data[DOMAIN]["version"]),
                "colors_version": hass.data[DOMAIN].get("colors_version", hass.data[DOMAIN]["version"]),
                "weekday_version": hass.data[DOMAIN].get("weekday_version", hass.data[DOMAIN]["version"]),
                "profile_version": hass.data[DOMAIN].get("profile_version", hass.data[DOMAIN]["version"]),
            })
        except Exception:
            # Storage failure shouldn't block UI update
            _LOGGER.warning("%s: failed to save store after broadcast", DOMAIN)

    async def _save_backup():
        # Broadcast so backup sensor updates
        async_dispatcher_send(hass, SIGNAL_UPDATED)
        try:
            await backup_store.async_save({
                "schedules": hass.data[DOMAIN].get("backup_schedules", {}),
                "settings": hass.data[DOMAIN].get("backup_settings", {}),
                "weekdays": hass.data[DOMAIN].get("backup_weekdays", {}),
                "profiles": hass.data[DOMAIN].get("backup_profiles", {}),
                "version": hass.data[DOMAIN].get("backup_version", 1),
                "last_backup_ts": hass.data[DOMAIN].get("backup_last_ts"),
                "partial_flags": hass.data[DOMAIN].get("backup_partial_flags"),
                "slots": hass.data[DOMAIN].get("backup_slots", []),
                "slot_index": hass.data[DOMAIN].get("backup_slot_index", 0),
            })
        except Exception:
            _LOGGER.warning("%s: failed to save backup store", DOMAIN)

    def _latest_backup_ts(slots: list) -> str | None:
        """Find newest backup timestamp across slots (iso string)."""
        newest = None
        try:
            for slot in (slots or []):
                if not isinstance(slot, dict):
                    continue
                ts = slot.get("ts") or slot.get("created_at") or slot.get("created")
                if not ts:
                    continue
                try:
                    dt = dt_util.parse_datetime(ts)
                except Exception:
                    dt = None
                if dt:
                    if not newest or dt > newest[0]:
                        newest = (dt, ts)
                else:
                    # Fallback: keep first seen string when parse fails
                    if not newest:
                        newest = (None, ts)
        except Exception:
            return None
        return newest[1] if newest else None

    async def set_store(call: ServiceCall):
        force = bool(call.data.get("force"))
        cur_sched = hass.data[DOMAIN].get("schedules", {})
        cur_set = hass.data[DOMAIN].get("settings", {})
        cur_colors = hass.data[DOMAIN].get("colors", {})
        cur_week = hass.data[DOMAIN].get("weekday_schedules", {})
        cur_prof = hass.data[DOMAIN].get("profile_schedules", {})
        changed = False
        sched_changed = False
        settings_changed = False
        colors_changed = False
        weekday_changed = False
        profile_changed = False

        if "schedules" in call.data:
            schedules = call.data.get("schedules")
            if not isinstance(schedules, dict):
                _LOGGER.warning("%s.set_store: schedules must be an object when provided", DOMAIN)
                return
            if force or schedules != cur_sched:
                hass.data[DOMAIN]["schedules"] = schedules
                sched_changed = True
                changed = True
            if "weekdays" not in call.data:
                try:
                    new_week = {}
                    for eid, row in (schedules.items() if isinstance(schedules, dict) else []):
                        if not isinstance(row, dict):
                            continue
                        wk = {}
                        if "weekly" in row:
                            wk["weekly"] = row["weekly"]
                        if "weekly_modes" in row:
                            wk["weekly_modes"] = row["weekly_modes"]
                        if wk:
                            new_week[eid] = wk
                    if force or new_week != cur_week:
                        hass.data[DOMAIN]["weekday_schedules"] = new_week
                        weekday_changed = True
                        changed = True
                except Exception:
                    pass
            if "profiles" not in call.data:
                try:
                    new_prof = {}
                    for eid, row in (schedules.items() if isinstance(schedules, dict) else []):
                        if not isinstance(row, dict):
                            continue
                        pr = {}
                        if "profiles" in row:
                            pr["profiles"] = row["profiles"]
                        if "activeProfile" in row:
                            pr["activeProfile"] = row["activeProfile"]
                        if pr:
                            new_prof[eid] = pr
                    if force or new_prof != cur_prof:
                        hass.data[DOMAIN]["profile_schedules"] = new_prof
                        profile_changed = True
                        changed = True
                except Exception:
                    pass

        if "settings" in call.data:
            settings = call.data.get("settings")
            if isinstance(settings, dict):
                if force or settings != cur_set:
                    hass.data[DOMAIN]["settings"] = settings
                    settings_changed = True
                    changed = True
                c_ranges = settings.get("color_ranges")
                c_global = settings.get("color_global")
                if c_ranges is not None or c_global is not None:
                    hass.data[DOMAIN]["colors"] = {
                        "color_ranges": c_ranges if c_ranges is not None else cur_colors.get("color_ranges", {}),
                        "color_global": c_global if c_global is not None else cur_colors.get("color_global", False),
                    }
                    changed = True
                    settings_changed = True
                    colors_changed = True

        if "colors" in call.data:
            colors = call.data.get("colors")
            if isinstance(colors, dict):
                if force or colors != cur_colors:
                    hass.data[DOMAIN]["colors"] = colors
                    colors_changed = True
                    changed = True

        if "weekdays" in call.data:
            weekdays = call.data.get("weekdays")
            if isinstance(weekdays, dict):
                if force or weekdays != cur_week:
                    hass.data[DOMAIN]["weekday_schedules"] = weekdays
                    weekday_changed = True
                    changed = True
                try:
                    for eid, wk in (weekdays.items() if isinstance(weekdays, dict) else []):
                        if not isinstance(wk, dict):
                            continue
                        row = dict(hass.data[DOMAIN].get("schedules", {}).get(eid, {}))
                        if "weekly" in wk:
                            row["weekly"] = wk["weekly"]
                        if "weekly_modes" in wk:
                            row["weekly_modes"] = wk["weekly_modes"]
                        hass.data[DOMAIN].setdefault("schedules", {})[eid] = row
                        sched_changed = True
                except Exception:
                    pass

        if "profiles" in call.data:
            profiles = call.data.get("profiles")
            if isinstance(profiles, dict):
                if force or profiles != cur_prof:
                    hass.data[DOMAIN]["profile_schedules"] = profiles
                    profile_changed = True
                    changed = True
                try:
                    for eid, pdata in (profiles.items() if isinstance(profiles, dict) else []):
                        if not isinstance(pdata, dict):
                            continue
                        row = dict(hass.data[DOMAIN].get("schedules", {}).get(eid, {}))
                        if "profiles" in pdata:
                            row["profiles"] = pdata["profiles"]
                        if "activeProfile" in pdata:
                            row["activeProfile"] = pdata["activeProfile"]
                        hass.data[DOMAIN].setdefault("schedules", {})[eid] = row
                        sched_changed = True
                except Exception:
                    pass

        if not changed and not force:
            return

        if sched_changed:
            hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN]["version"]) + 1
        if settings_changed:
            hass.data[DOMAIN]["settings_version"] = int(hass.data[DOMAIN].get("settings_version", hass.data[DOMAIN].get("version", 1))) + 1
            if not sched_changed:
                hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN]["version"]) + 1
        if colors_changed:
            hass.data[DOMAIN]["colors_version"] = int(hass.data[DOMAIN].get("colors_version", hass.data[DOMAIN].get("version", 1))) + 1
            if not sched_changed:
                hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN]["version"]) + 1
        if weekday_changed:
            hass.data[DOMAIN]["weekday_version"] = int(hass.data[DOMAIN].get("weekday_version", hass.data[DOMAIN].get("version", 1))) + 1
            if not sched_changed:
                hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN]["version"]) + 1
        if profile_changed:
            hass.data[DOMAIN]["profile_version"] = int(hass.data[DOMAIN].get("profile_version", hass.data[DOMAIN].get("version", 1))) + 1
            if not sched_changed:
                hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN]["version"]) + 1
        if force and not sched_changed and not settings_changed:
            hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN]["version"]) + 1
            hass.data[DOMAIN]["settings_version"] = int(hass.data[DOMAIN].get("settings_version", hass.data[DOMAIN]["version"])) + 1
            hass.data[DOMAIN]["colors_version"] = int(hass.data[DOMAIN].get("colors_version", hass.data[DOMAIN]["version"])) + 1
            hass.data[DOMAIN]["weekday_version"] = int(hass.data[DOMAIN].get("weekday_version", hass.data[DOMAIN]["version"])) + 1
            hass.data[DOMAIN]["profile_version"] = int(hass.data[DOMAIN].get("profile_version", hass.data[DOMAIN]["version"])) + 1
        _LOGGER.info("set_store: version=%s, sched_changed=%s, settings_changed=%s", 
                     hass.data[DOMAIN]["version"], sched_changed, settings_changed)
        await _save_and_broadcast()

    async def clear_store(call: ServiceCall):
        # Explicit clear: wipe schedules + settings and bump both versions once
        hass.data[DOMAIN]["schedules"] = {}
        hass.data[DOMAIN]["weekday_schedules"] = {}
        hass.data[DOMAIN]["profile_schedules"] = {}
        hass.data[DOMAIN]["settings"] = {}
        hass.data[DOMAIN]["colors"] = {}
        hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN].get("version", 0)) + 1
        hass.data[DOMAIN]["settings_version"] = int(hass.data[DOMAIN].get("settings_version", hass.data[DOMAIN]["version"])) + 1
        hass.data[DOMAIN]["colors_version"] = int(hass.data[DOMAIN].get("colors_version", hass.data[DOMAIN]["version"])) + 1
        hass.data[DOMAIN]["weekday_version"] = int(hass.data[DOMAIN].get("weekday_version", hass.data[DOMAIN]["version"])) + 1
        hass.data[DOMAIN]["profile_version"] = int(hass.data[DOMAIN].get("profile_version", hass.data[DOMAIN]["version"])) + 1
        await _save_and_broadcast()

    async def backup_now(call: ServiceCall):
        """Create a backup of the current store.

        Supports selective sections via optional booleans:
          - main: base daily schedules (defaultTemp, blocks)
          - weekday: weekday schedules (weekly, weekly_modes)
          - presence: presence schedules (advanced away combos per room)
          - settings: editor/global settings (except color ranges)
          - holiday: holiday schedules per room
          - colors: color ranges and color mode
          - profiles: profile schedules (profiles, activeProfile)
        If no flags are provided, a full backup is made (backwards compatible).
        """
        want_main = bool(call.data.get("main", True))
        want_week = bool(call.data.get("weekday", True))
        want_presence = bool(call.data.get("presence", True))
        want_settings = bool(call.data.get("settings", True))
        want_holiday = bool(call.data.get("holiday", True))
        want_colors = bool(call.data.get("colors", True))
        want_profiles = bool(call.data.get("profiles", True))

        src_sched = hass.data[DOMAIN].get("schedules", {}) or {}
        src_week = hass.data[DOMAIN].get("weekday_schedules", {}) or {}
        src_prof = hass.data[DOMAIN].get("profile_schedules", {}) or {}
        src_set = hass.data[DOMAIN].get("settings", {}) or {}

        out_sched: dict = {}
        if any([want_main, want_week, want_presence, want_holiday, want_profiles]):
            for eid, row in (src_sched.items() if isinstance(src_sched, dict) else []):
                if not isinstance(row, dict):
                    continue
                new_row = {}
                if want_main:
                    if "defaultTemp" in row:
                        new_row["defaultTemp"] = row["defaultTemp"]
                    if "blocks" in row:
                        new_row["blocks"] = row["blocks"]
                if want_profiles:
                    prof_src = src_prof.get(eid, {}) if isinstance(src_prof, dict) else {}
                    if "profiles" in prof_src:
                        new_row["profiles"] = prof_src["profiles"]
                    elif "profiles" in row:
                        new_row["profiles"] = row["profiles"]
                    if "activeProfile" in prof_src:
                        new_row["activeProfile"] = prof_src["activeProfile"]
                    elif "activeProfile" in row:
                        new_row["activeProfile"] = row["activeProfile"]
                if want_week:
                    try:
                        wk_src = src_week.get(eid, {}) if isinstance(src_week, dict) else {}
                    except Exception:
                        wk_src = {}
                    if "weekly" in wk_src:
                        new_row["weekly"] = wk_src["weekly"]
                    elif "weekly" in row:
                        new_row["weekly"] = row["weekly"]
                    if "weekly_modes" in wk_src:
                        new_row["weekly_modes"] = wk_src["weekly_modes"]
                    elif "weekly_modes" in row:
                        new_row["weekly_modes"] = row["weekly_modes"]
                if want_presence and "presence" in row:
                    new_row["presence"] = row["presence"]
                if want_holiday and "holiday" in row:
                    new_row["holiday"] = row["holiday"]
                if new_row:
                    out_sched[eid] = new_row

        out_set: dict = {}
        if want_settings:
            for k, v in (src_set.items() if isinstance(src_set, dict) else []):
                if k in ("color_ranges", "color_global"):
                    continue
                out_set[k] = v
        if want_colors:
            if "color_ranges" in src_set:
                out_set["color_ranges"] = src_set["color_ranges"]
            if "color_global" in src_set:
                out_set["color_global"] = src_set["color_global"]

        legacy_full = (
            "main" not in call.data
            and "weekday" not in call.data
            and "presence" not in call.data
            and "settings" not in call.data
            and "holiday" not in call.data
            and "colors" not in call.data
            and "profiles" not in call.data
        )

        if legacy_full:
            hass.data[DOMAIN]["backup_schedules"] = dict(src_sched)
            hass.data[DOMAIN]["backup_settings"] = dict(src_set)
            hass.data[DOMAIN]["backup_profiles"] = dict(src_prof)
            hass.data[DOMAIN]["backup_weekdays"] = dict(src_week)
            hass.data[DOMAIN]["backup_partial_flags"] = None
        else:
            hass.data[DOMAIN]["backup_schedules"] = out_sched
            hass.data[DOMAIN]["backup_settings"] = out_set
            hass.data[DOMAIN]["backup_profiles"] = {k: v for k, v in (src_prof.items() if isinstance(src_prof, dict) else [])} if want_profiles else {}
            hass.data[DOMAIN]["backup_weekdays"] = {k: v for k, v in (src_week.items() if isinstance(src_week, dict) else [])}
            hass.data[DOMAIN]["backup_partial_flags"] = {
                "main": want_main,
                "weekday": want_week,
                "presence": want_presence,
                "settings": want_settings,
                "holiday": want_holiday,
                "colors": want_colors,
                "profiles": want_profiles,
            }

        # Maintain append-only backup list (unbounded)
        try:
            slots = hass.data[DOMAIN].get("backup_slots", [])
            if not isinstance(slots, list):
                slots = []
            # Colors come from live colors store (matches colors sensor)
            backup_colors = hass.data[DOMAIN].get("colors", {}) or {}
            entry = {
                "ts": dt_util.utcnow().isoformat(),
                "version": hass.data[DOMAIN].get("version", 1),
                "settings_version": hass.data[DOMAIN].get("settings_version", hass.data[DOMAIN].get("version", 1)),
                "weekday_version": hass.data[DOMAIN].get("weekday_version", hass.data[DOMAIN].get("version", 1)),
                "profile_version": hass.data[DOMAIN].get("profile_version", hass.data[DOMAIN].get("version", 1)),
                "colors_version": hass.data[DOMAIN].get("colors_version", hass.data[DOMAIN].get("version", 1)),
                "partial_flags": hass.data[DOMAIN].get("backup_partial_flags"),
                "sections": {
                    "schedules": hass.data[DOMAIN].get("backup_schedules", {}),
                    "settings": hass.data[DOMAIN].get("backup_settings", {}),
                    "weekdays": hass.data[DOMAIN].get("backup_weekdays", {}),
                    "profiles": hass.data[DOMAIN].get("backup_profiles", {}),
                    "colors": backup_colors,
                },
            }
            # Append new entry (skip None placeholders)
            slots = [s for s in slots if s is None or isinstance(s, dict)]
            slots.append(entry)
            hass.data[DOMAIN]["backup_slot_index"] = len(slots)
            hass.data[DOMAIN]["backup_slots"] = slots
        except Exception:
            pass

        hass.data[DOMAIN]["backup_version"] = int(hass.data[DOMAIN].get("backup_version", 1)) + 1
        try:
            hass.data[DOMAIN]["backup_last_ts"] = dt_util.utcnow().isoformat()
        except Exception:
            pass
        await _save_backup()

    async def delete_backup(call: ServiceCall):
        """Delete a specific backup slot or clear all backups when no slot is provided."""
        slots = hass.data[DOMAIN].get("backup_slots", [])
        if not isinstance(slots, list):
            slots = []

        slot_num = call.data.get("slot")
        changed = False

        if slot_num is not None:
            try:
                idx = int(slot_num) - 1
            except Exception:
                idx = -1
            if 0 <= idx < len(slots):
                try:
                    slots.pop(idx)
                except Exception:
                    pass
                else:
                    changed = True
        else:
            # Clear all backup payloads and slots
            hass.data[DOMAIN]["backup_schedules"] = {}
            hass.data[DOMAIN]["backup_settings"] = {}
            hass.data[DOMAIN]["backup_weekdays"] = {}
            hass.data[DOMAIN]["backup_profiles"] = {}
            hass.data[DOMAIN]["backup_partial_flags"] = None
            hass.data[DOMAIN]["backup_slot_index"] = 0
            hass.data[DOMAIN]["backup_last_ts"] = None
            slots = []
            changed = True

        if not changed:
            return

        hass.data[DOMAIN]["backup_slots"] = slots
        hass.data[DOMAIN]["backup_version"] = int(hass.data[DOMAIN].get("backup_version", 1)) + 1
        hass.data[DOMAIN]["backup_slot_index"] = len(slots)
        hass.data[DOMAIN]["backup_last_ts"] = _latest_backup_ts(slots)
        await _save_backup()

    async def restore_now(call: ServiceCall):
        """Restore from backup.

        Optional mode: 'replace' (default) or 'merge'.
        Optional section flags like backup_now: main, weekday, presence, settings, holiday, colors, profiles.
        Optional slot (1-based) to restore from rolling backup slots.
        If backup contains partial_flags and no mode is provided, merge is used by default.
        """
        slot_num = call.data.get("slot")
        slot_entry = None
        if slot_num is not None:
            try:
                idx = int(slot_num) - 1
                slots = hass.data[DOMAIN].get("backup_slots", []) if isinstance(hass.data[DOMAIN].get("backup_slots", []), list) else []
                if 0 <= idx < len(slots):
                    slot_entry = slots[idx]
            except Exception:
                slot_entry = None

        mode = str(call.data.get("mode", "")).lower().strip()
        flags = hass.data[DOMAIN].get("backup_partial_flags") or {}
        # Allow caller to override flags
        for key in ("main","weekday","presence","settings","holiday","colors","profiles"):
            if key in call.data:
                flags[key] = bool(call.data.get(key))

        sections = None
        if slot_entry and isinstance(slot_entry, dict):
            sections = slot_entry.get("sections", {}) if isinstance(slot_entry.get("sections"), dict) else {}
            flags = slot_entry.get("partial_flags") or flags

        backup_sched = (sections.get("schedules") if sections else None) or hass.data[DOMAIN].get("backup_schedules", {}) or {}
        backup_set = (sections.get("settings") if sections else None) or hass.data[DOMAIN].get("backup_settings", {}) or {}
        backup_week = (sections.get("weekdays") if sections else None) or hass.data[DOMAIN].get("backup_weekdays", {}) or {}
        backup_prof = (sections.get("profiles") if sections else None) or hass.data[DOMAIN].get("backup_profiles", {}) or {}

        # If no flags and mode empty, keep legacy behavior (replace)
        if not flags and mode not in ("merge", "replace"):
            hass.data[DOMAIN]["schedules"] = dict(backup_sched)
            hass.data[DOMAIN]["settings"] = dict(backup_set)
            hass.data[DOMAIN]["weekday_schedules"] = dict(backup_week)
            hass.data[DOMAIN]["profile_schedules"] = dict(backup_prof)
            hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN].get("version", 1)) + 1
            hass.data[DOMAIN]["settings_version"] = int(hass.data[DOMAIN].get("settings_version", 1)) + 1
            hass.data[DOMAIN]["weekday_version"] = int(hass.data[DOMAIN].get("weekday_version", 1)) + 1
            hass.data[DOMAIN]["profile_version"] = int(hass.data[DOMAIN].get("profile_version", 1)) + 1
            await _save_and_broadcast()
            return

        do_merge = (mode == "merge") or (not mode and bool(flags))
        if not do_merge:
            # Explicit replace
            hass.data[DOMAIN]["schedules"] = dict(backup_sched)
            hass.data[DOMAIN]["settings"] = dict(backup_set)
            hass.data[DOMAIN]["weekday_schedules"] = dict(backup_week)
            hass.data[DOMAIN]["profile_schedules"] = dict(backup_prof)
            hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN].get("version", 1)) + 1
            hass.data[DOMAIN]["settings_version"] = int(hass.data[DOMAIN].get("settings_version", 1)) + 1
            hass.data[DOMAIN]["weekday_version"] = int(hass.data[DOMAIN].get("weekday_version", 1)) + 1
            hass.data[DOMAIN]["profile_version"] = int(hass.data[DOMAIN].get("profile_version", 1)) + 1
            await _save_and_broadcast()
            return

        # Merge: only update selected sections/keys
        cur_sched = hass.data[DOMAIN].get("schedules", {}) or {}
        cur_set = hass.data[DOMAIN].get("settings", {}) or {}
        cur_week = hass.data[DOMAIN].get("weekday_schedules", {}) or {}
        cur_prof = hass.data[DOMAIN].get("profile_schedules", {}) or {}

        want_main = bool(flags.get("main", False))
        want_week = bool(flags.get("weekday", False))
        want_presence = bool(flags.get("presence", False))
        want_settings = bool(flags.get("settings", False))
        want_holiday = bool(flags.get("holiday", False))
        want_colors = bool(flags.get("colors", False))
        want_profiles = bool(flags.get("profiles", False))

        if any([want_main, want_week, want_presence, want_holiday, want_profiles]):
            for eid, brow in (backup_sched.items() if isinstance(backup_sched, dict) else []):
                if not isinstance(brow, dict):
                    continue
                row = dict(cur_sched.get(eid, {}))
                if want_main:
                    if "defaultTemp" in brow:
                        row["defaultTemp"] = brow["defaultTemp"]
                    if "blocks" in brow:
                        row["blocks"] = brow["blocks"]
                if want_profiles:
                    prof_src = backup_prof.get(eid, {}) if isinstance(backup_prof, dict) else {}
                    if "profiles" in prof_src:
                        row["profiles"] = prof_src["profiles"]
                    elif "profiles" in brow:
                        row["profiles"] = brow["profiles"]
                    if "activeProfile" in prof_src:
                        row["activeProfile"] = prof_src["activeProfile"]
                    elif "activeProfile" in brow:
                        row["activeProfile"] = brow["activeProfile"]
                if want_week:
                    wk_src = backup_week.get(eid, {}) if isinstance(backup_week, dict) else {}
                    if "weekly" in wk_src:
                        row["weekly"] = wk_src["weekly"]
                    elif "weekly" in brow:
                        row["weekly"] = brow["weekly"]
                    if "weekly_modes" in wk_src:
                        row["weekly_modes"] = wk_src["weekly_modes"]
                    elif "weekly_modes" in brow:
                        row["weekly_modes"] = brow["weekly_modes"]
                if want_presence and "presence" in brow:
                    row["presence"] = brow["presence"]
                if want_holiday and "holiday" in brow:
                    row["holiday"] = brow["holiday"]
                cur_sched[eid] = row

        if want_settings or want_colors:
            # Merge settings keys
            for k, v in (backup_set.items() if isinstance(backup_set, dict) else []):
                if (k in ("color_ranges", "color_global")) and not want_colors:
                    continue
                if (k not in ("color_ranges", "color_global")) and not want_settings:
                    continue
                cur_set[k] = v

        if want_week:
            hass.data[DOMAIN]["weekday_schedules"] = {k: v for k, v in (backup_week.items() if isinstance(backup_week, dict) else [])}
        else:
            hass.data[DOMAIN]["weekday_schedules"] = cur_week
        if want_profiles:
            hass.data[DOMAIN]["profile_schedules"] = {k: v for k, v in (backup_prof.items() if isinstance(backup_prof, dict) else [])}
        else:
            hass.data[DOMAIN]["profile_schedules"] = cur_prof

        hass.data[DOMAIN]["schedules"] = cur_sched
        hass.data[DOMAIN]["settings"] = cur_set
        hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN].get("version", 1)) + 1
        hass.data[DOMAIN]["settings_version"] = int(hass.data[DOMAIN].get("settings_version", 1)) + 1
        if want_week:
            hass.data[DOMAIN]["weekday_version"] = int(hass.data[DOMAIN].get("weekday_version", 1)) + 1
        if want_profiles:
            hass.data[DOMAIN]["profile_version"] = int(hass.data[DOMAIN].get("profile_version", 1)) + 1
        await _save_and_broadcast()

    async def patch_entity(call: ServiceCall):
        eid = str(call.data.get("entity_id","")).strip()
        d = call.data.get("data", {})
        if not eid or not isinstance(d, dict):
            _LOGGER.warning("%s.patch_entity: invalid args", DOMAIN)
            return
        cur = dict(hass.data[DOMAIN]["schedules"].get(eid, {}))
        if "defaultTemp" in d: cur["defaultTemp"] = d["defaultTemp"]
        if "blocks" in d and isinstance(d["blocks"], list): cur["blocks"] = d["blocks"]
        hass.data[DOMAIN]["schedules"][eid] = cur
        hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN]["version"]) + 1
        await _save_and_broadcast()

    async def clear(call: ServiceCall):
        hass.data[DOMAIN]["schedules"] = {}
        hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN]["version"]) + 1
        await _save_and_broadcast()

    hass.services.async_register(DOMAIN, "set_store", set_store)
    hass.services.async_register(DOMAIN, "clear_store", clear_store)
    hass.services.async_register(DOMAIN, "backup_now", backup_now)
    hass.services.async_register(DOMAIN, "delete_backup", delete_backup)
    hass.services.async_register(DOMAIN, "restore_now", restore_now)
    hass.services.async_register(DOMAIN, "patch_entity", patch_entity)
    hass.services.async_register(DOMAIN, "clear", clear)
    # no apply_now service (removed)

    # Expose lightweight HTTP views for clients (works over HA Cloud)
    try:
        hass.http.register_view(ThermostatTimelineStateView(hass))
        hass.http.register_view(ThermostatTimelineVersionView(hass))
    except Exception:
        _LOGGER.warning("%s: failed to register HTTP views", DOMAIN)

    # ---- Background Auto-Apply Manager ----
    mgr = AutoApplyManager(hass)
    hass.data[DOMAIN]["manager"] = mgr
    await mgr.async_start()
    # ---- Backup Manager (auto backup) ----
    bkm = BackupManager(hass, backup_cb=backup_now)
    hass.data[DOMAIN]["backup_manager"] = bkm
    await bkm.async_start()
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True


class AutoApplyManager:
    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._unsub_timer = None
        self._unsub_persons = None
        self._unsub_boiler = None
        self._last_applied = {}  # eid -> {"min": int, "temp": float}
        self._next_is_resume = False

    async def async_start(self):
        # Apply once on startup and schedule next if enabled
        await self._maybe_apply_now(force=True)
        await self._maybe_control_boiler(force=True)
        await self._schedule_next()
        # Re-apply on store updates
        @callback
        def _on_store_update():
            self.hass.async_create_task(self._on_store_changed())
        async_dispatcher_connect(self.hass, SIGNAL_UPDATED, _on_store_update)
        # Watch person.* states if away mode is used
        self._reset_person_watch()
        self._reset_boiler_watch()

    async def _on_store_changed(self):
        # Respect apply_on_edit toggle: only apply immediately on store changes when enabled
        _s, settings = self._get_data()
        if bool(settings.get("apply_on_edit", True)):
            await self._maybe_apply_now(force=True)
        await self._maybe_control_boiler(force=True)
        await self._schedule_next()
        self._reset_person_watch()
        self._reset_boiler_watch()

    def _get_data(self):
        d = self.hass.data.get(DOMAIN, {})
        schedules = d.get("schedules", {})
        settings = d.get("settings", {})
        return schedules, settings

    def _auto_apply_enabled(self) -> bool:
        _s, settings = self._get_data()
        if not bool(settings.get("auto_apply_enabled")):
            return False
        # Pause gates
        try:
            if bool(settings.get("pause_indef")):
                return False
            pu = settings.get("pause_until_ms")
            if isinstance(pu, (int, float)):
                # compare in ms
                now_ms = dt_util.utcnow().timestamp() * 1000.0
                if float(pu) > now_ms:
                    return False
        except Exception:
            pass
        return True

    def _paused_until_dt(self):
        _s, settings = self._get_data()
        try:
            if bool(settings.get("pause_indef")):
                return None
            pu = settings.get("pause_until_ms")
            if isinstance(pu, (int, float)) and float(pu) > 0:
                return dt_util.utc_from_timestamp(float(pu) / 1000.0)
        except Exception:
            pass
        return None

    def _now_min(self) -> int:
        now = dt_util.now()
        return now.hour * 60 + now.minute

    def _today_key(self) -> str:
        # Monday=0 ... Sunday=6 -> map to keys
        idx = (dt_util.now().weekday())  # 0..6
        return ["mon","tue","wed","thu","fri","sat","sun"][idx]

    def _effective_blocks_today(self, row: dict, settings: dict):
        # Presence (advanced away) overrides if enabled and an active combo exists
        try:
            away = settings.get("away") or {}
            if bool(away.get("advanced_enabled")):
                key = self._active_presence_combo_key(settings)
                if key:
                    pres = row.get("presence") or {}
                    blk = (pres.get(key) or {}).get("blocks")
                    if isinstance(blk, list) and len(blk) > 0:
                        return blk
                    # Presence combo is active but this room has no presence schedule:
                    # return a virtual all‑day block at Away temperature
                    try:
                        target = float(away.get("target_c")) if "target_c" in away else None
                        if target is not None:
                            return [{"id": "__presence_away__", "startMin": 0, "endMin": 1440, "temp": target}]
                    except Exception:
                        pass
        except Exception:
            pass
        # Profiles override take precedence when enabled (per-room activeProfile)
        try:
            if settings.get("profiles_enabled"):
                ap = row.get("activeProfile")
                if ap:
                    profs = row.get("profiles") or {}
                    blk = (profs.get(ap) or {}).get("blocks")
                    if isinstance(blk, list):
                        return blk
        except Exception:
            pass
        # If row has weekly structure, use today's day list, otherwise default blocks
        try:
            wk = row.get("weekly")
            if wk and isinstance(wk, dict):
                days = wk.get("days") or {}
                arr = days.get(self._today_key())
                if isinstance(arr, list):
                    return arr
        except Exception:
            pass
        return row.get("blocks") or []

    def _presence_persons(self, settings: dict) -> list[str]:
        try:
            away = settings.get("away") or {}
            persons = away.get("persons")
            return list(persons) if isinstance(persons, list) else []
        except Exception:
            return []

    def _presence_combo_key(self, home: list[str], away: list[str]) -> str:
        try:
            h = sorted([str(x) for x in (home or [])])
            a = sorted([str(x) for x in (away or [])])
            return f"H:{','.join(h)}|A:{','.join(a)}"
        except Exception:
            return "H:|A:"

    def _active_presence_combo_key(self, settings: dict) -> str | None:
        try:
            away = settings.get("away") or {}
            if not bool(away.get("advanced_enabled")):
                return None
            persons = self._presence_persons(settings)
            if not persons:
                return None
            home, not_home = [], []
            for p in persons:
                st = self.hass.states.get(p)
                if st and str(st.state).lower() == "home":
                    home.append(p)
                else:
                    not_home.append(p)
            key = self._presence_combo_key(home, not_home)
            combos = (away.get("combos") or {}) if isinstance(away, dict) else {}
            meta = combos.get(key)
            if meta and bool(meta.get("enabled")):
                return key
            return None
        except Exception:
            return None

    def _all_targets(self, schedules: dict, merges: dict) -> set[str]:
        out = set()
        for primary in schedules.keys():
            out.add(primary)
            linked = merges.get(primary) or []
            for e in linked:
                out.add(e)
        return out

    def _desired_for(self, eid: str, schedules: dict, settings: dict, now_min: int):
        # Resolve primary (merged)
        merges = settings.get("merges") or {}
        primary = None
        if eid in schedules:
            primary = eid
        else:
            # find primary that lists eid
            for p, lst in (merges.items() if isinstance(merges, dict) else []):
                try:
                    if eid in (lst or []):
                        primary = p
                        break
                except Exception:
                    continue
        if primary is None:
            primary = eid
        row = schedules.get(primary)
        if not isinstance(row, dict):
            return None
        # Determine which blocks are effective for today
        blocks = self._effective_blocks_today(row, settings)
        # Track if advanced presence is active globally and whether this room has a presence schedule for the active combo
        presence_key_active = self._active_presence_combo_key(settings)
        room_has_presence_blocks = False
        try:
            if presence_key_active:
                pres = row.get("presence") or {}
                bl = (pres.get(presence_key_active) or {}).get("blocks")
                room_has_presence_blocks = isinstance(bl, list) and len(bl) > 0
        except Exception:
            room_has_presence_blocks = False
        hit = None
        for b in blocks:
            try:
                if now_min >= int(b.get("startMin", -1)) and now_min < int(b.get("endMin", -1)):
                    hit = b
                    break
            except Exception:
                continue
        # Base desired setpoint
        if hit is not None:
            want = float(hit.get("temp"))
        else:
            # When advanced presence is active but this room has no presence schedule,
            # use Away temperature instead of the room default.
            if presence_key_active and not room_has_presence_blocks:
                try:
                    away = settings.get("away") or {}
                    if "target_c" in away:
                        want = float(away.get("target_c"))
                    else:
                        want = float(row.get("defaultTemp", 20))
                except Exception:
                    want = float(row.get("defaultTemp", 20))
            else:
                want = float(row.get("defaultTemp", 20))
        # Away override (disabled when advanced presence is enabled)
        try:
            away = settings.get("away") or {}
            if bool(away.get("advanced_enabled")):
                pass  # advanced presence replaces simple away clamp entirely
            elif away.get("enabled") and isinstance(away.get("persons"), list):
                anyone_home = False
                for p in away["persons"]:
                    st = self.hass.states.get(p)
                    if st and str(st.state).lower() == "home":
                        anyone_home = True
                        break
                if not anyone_home and ("target_c" in away):
                    target_c = float(away.get("target_c", want))
                    want = min(want, target_c)
        except Exception:
            pass
        # Clamp
        try:
            mn = settings.get("min_temp")
            mx = settings.get("max_temp")
            if mn is not None:
                want = max(want, float(mn))
            if mx is not None:
                want = min(want, float(mx))
        except Exception:
            pass
        return want

    async def _maybe_apply_now(self, force: bool = False, boundary_only: bool = False, reconcile: bool = False):
        if not self._auto_apply_enabled():
            return
        schedules, settings = self._get_data()
        merges = settings.get("merges") or {}
        now_min = self._now_min()
        targets = self._all_targets(schedules, merges)
        for eid in targets:
            # Supported targets: climate.* and input_number.*
            if not isinstance(eid, str) or not (eid.startswith("climate.") or eid.startswith("input_number.")):
                continue
            if not self.hass.states.get(eid):
                continue
            # If we are at a boundary tick (timer), only apply for entities that have a boundary now
            if boundary_only:
                try:
                    primary = None
                    if eid in schedules:
                        primary = eid
                    else:
                        for p, lst in (merges.items() if isinstance(merges, dict) else []):
                            try:
                                if eid in (lst or []):
                                    primary = p
                                    break
                            except Exception:
                                continue
                    if primary is None:
                        primary = eid
                    row = schedules.get(primary) or {}
                    if not self._entity_has_boundary_now(row, now_min):
                        continue
                except Exception:
                    # if in doubt, skip to avoid overriding manual changes unnecessarily
                    continue
            desired = self._desired_for(eid, schedules, settings, now_min)
            if desired is None:
                continue
            # Only apply when desired setpoint changes for this entity.
            # This prevents overriding manual thermostat changes just because
            # another room hits a new block (or any global refresh runs).
            #
            # Exceptions:
            # - On resume from pause we reconcile to the desired setpoint even
            #   if it hasn't changed (reconcile=True).
            last = self._last_applied.get(eid) or {}
            try:
                if (not reconcile) and ("temp" in last) and abs(float(last.get("temp")) - float(desired)) < 0.05:
                    continue
            except Exception:
                pass
            # If current equals desired, just update cache
            st = self.hass.states.get(eid)
            cur = None
            if eid.startswith("input_number."):
                try:
                    cur = float(st.state)
                except Exception:
                    cur = None
            else:
                for attr in ("temperature","target_temperature","target_temp"):
                    v = st.attributes.get(attr)
                    if isinstance(v, (int, float)):
                        cur = float(v)
                        break
            if cur is not None and abs(cur - float(desired)) < 0.05:
                self._last_applied[eid] = {"min": now_min, "temp": float(desired)}
                continue
            ok = await self._apply_setpoint(eid, float(desired))
            if ok:
                self._last_applied[eid] = {"min": now_min, "temp": float(desired)}
            else:
                _LOGGER.warning("Auto-apply failed for %s", eid)

    def _next_boundary_dt(self):
        schedules, settings = self._get_data()
        now = dt_util.now()
        now_min = self._now_min()
        best_delta = None
        # consider all block boundaries today
        for eid, row in schedules.items():
            try:
                blocks = self._effective_blocks_today(row, settings)
                for b in blocks:
                    for t in (int(b.get("startMin", -1)), int(b.get("endMin", -1))):
                        if t < 0:
                            continue
                        delta = (t - now_min) % 1440
                        if delta == 0:
                            delta = 1
                        if best_delta is None or delta < best_delta:
                            best_delta = delta
            except Exception:
                continue
        # always include midnight rollover
        delta_mid = (1440 - now_min) % 1440
        if delta_mid == 0:
            delta_mid = 1
        if best_delta is None or delta_mid < best_delta:
            best_delta = delta_mid
        # build dt in UTC
        return dt_util.as_utc(now + timedelta(minutes=best_delta or 1))

    async def _schedule_next(self):
        # cancel previous
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        if not self._auto_apply_enabled():
            # If paused indefinitely, do not schedule anything
            _s, settings = self._get_data()
            if bool(settings.get("pause_indef")):
                return
            # If paused until a time, schedule wake-up then
            wake = self._paused_until_dt()
            if not wake:
                return
            boundary = None
        else:
            wake = self._paused_until_dt()
            boundary = self._next_boundary_dt()
        # choose earliest available
        if wake and boundary:
            self._next_is_resume = wake <= boundary
            when = wake if self._next_is_resume else boundary
        elif wake:
            self._next_is_resume = True
            when = wake
        else:
            self._next_is_resume = False
            when = boundary
        @callback
        def _cb(_now):
            self.hass.async_create_task(self._timer_fire())
        self._unsub_timer = async_track_point_in_utc_time(self.hass, _cb, when)

    async def _timer_fire(self):
        # If this wake was caused by pause expiry, reconcile immediately across all entities.
        resume = bool(self._next_is_resume)
        await self._maybe_apply_now(force=True, boundary_only=(not resume), reconcile=resume)
        await self._maybe_control_boiler(force=True)
        self._next_is_resume = False
        await self._schedule_next()

    def _reset_boiler_watch(self):
        if self._unsub_boiler:
            self._unsub_boiler()
            self._unsub_boiler = None
        _schedules, settings = self._get_data()
        try:
            if not bool(settings.get("boiler_enabled")):
                return
            sens = settings.get("boiler_temp_sensor")
            if not isinstance(sens, str) or not sens:
                return

            @callback
            def _ch(_event):
                # React quickly when boiler sensor updates
                self.hass.async_create_task(self._maybe_control_boiler(force=True))

            self._unsub_boiler = async_track_state_change_event(self.hass, [sens], _ch)
        except Exception:
            self._unsub_boiler = None

    @staticmethod
    def _f_to_c(v: float) -> float:
        return (float(v) - 32.0) * 5.0 / 9.0

    async def _maybe_control_boiler(self, force: bool = False) -> None:
        """Control boiler switch using hysteresis based on boiler sensor.

        Rules:
        - If sensor temp < min -> switch ON
        - If sensor temp > max -> switch OFF
        - Otherwise do nothing

        Values in settings are stored in settings.temp_unit (C/F).
        Sensor is interpreted from unit_of_measurement when available.
        """
        try:
            if not self._auto_apply_enabled():
                return
            _schedules, settings = self._get_data()
            if not bool(settings.get("boiler_enabled")):
                return

            sw = settings.get("boiler_switch")
            sens = settings.get("boiler_temp_sensor")
            if not isinstance(sw, str) or not sw.startswith("switch."):
                return
            if not isinstance(sens, str) or "." not in sens:
                return

            st_sens = self.hass.states.get(sens)
            if not st_sens:
                return
            try:
                raw = float(st_sens.state)
            except Exception:
                return

            # Read thresholds (stored in settings temp unit)
            unit = str(settings.get("temp_unit") or "C").upper()
            bmin_raw = settings.get("boiler_min_temp")
            bmax_raw = settings.get("boiler_max_temp")

            if bmin_raw is None:
                min_c = 20.0
            else:
                min_v = float(bmin_raw)
                min_c = self._f_to_c(min_v) if unit == "F" else min_v

            if bmax_raw is None:
                max_c = 25.0
            else:
                max_v = float(bmax_raw)
                max_c = self._f_to_c(max_v) if unit == "F" else max_v

            lo = min(min_c, max_c)
            hi = max(min_c, max_c)

            # Sensor value -> °C if needed
            val_c = raw
            try:
                u = st_sens.attributes.get("unit_of_measurement")
                if isinstance(u, str) and ("°F" in u or u.strip().upper() == "F"):
                    val_c = self._f_to_c(raw)
            except Exception:
                pass

            want = None
            if val_c < lo:
                want = "on"
            elif val_c > hi:
                want = "off"
            else:
                return

            st_sw = self.hass.states.get(sw)
            cur = str(st_sw.state).lower() if st_sw else ""
            if want == "on" and cur == "on":
                return
            if want == "off" and cur == "off":
                return

            await self.hass.services.async_call(
                "switch",
                "turn_on" if want == "on" else "turn_off",
                {"entity_id": sw},
                blocking=False,
            )
        except Exception:
            # Boiler control must never interfere with normal schedule apply
            return

    def _reset_person_watch(self):
        # Recreate person watcher based on settings.away.persons (both simple away and advanced presence)
        if self._unsub_persons:
            self._unsub_persons()
            self._unsub_persons = None
        _schedules, settings = self._get_data()
        away = settings.get("away") or {}
        persons = away.get("persons") if isinstance(away, dict) else None
        if (away.get("enabled") or away.get("advanced_enabled")) and isinstance(persons, list) and persons:
            @callback
            def _ch(event):
                # Apply immediately when presence changes
                self.hass.async_create_task(self._maybe_apply_now(force=True))
                self.hass.async_create_task(self._schedule_next())
            self._unsub_persons = async_track_state_change_event(self.hass, persons, _ch)

    def _entity_has_boundary_now(self, row: dict, now_min: int) -> bool:
        """Return True if the entity has a block boundary at (or just before) now.

        The timer intentionally schedules a few seconds/minutes past the exact
        boundary to avoid immediate execution collisions. That means the check
        here must tolerate a small offset, otherwise we miss the boundary and
        never apply. We therefore consider the current minute and the previous
        minute as being "at the boundary".
        """
        try:
            _s, settings = self._get_data()
            blocks = self._effective_blocks_today(row, settings)
            # allow a 1‑minute grace window (handle wraparound at midnight)
            prev_min = (now_min - 1) % 1440
            for b in blocks:
                try:
                    s = int(b.get("startMin", -1))
                    e = int(b.get("endMin", -1))
                    if s in (now_min, prev_min) or e in (now_min, prev_min):
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    async def _apply_setpoint(self, eid: str, desired: float) -> bool:
        """Apply desired setpoint robustly across HVAC modes.

        Strategy:
        - If device exposes range (target_temp_low/high) and mode is heat_cool/auto, set a band around desired.
        - If preset supports a manual/hold, switch to it first.
        - If current mode is off/dry/fan_only, try switching to heat, else cool.
        - Fallback to single temperature set.
        """
        try:
            st = self.hass.states.get(eid)
            if not st:
                return False
            # input_number support: set value directly (no HVAC modes/presets)
            if isinstance(eid, str) and eid.startswith("input_number."):
                attrs = st.attributes or {}
                min_v = attrs.get("min")
                max_v = attrs.get("max")

                val = float(desired)
                try:
                    if isinstance(min_v, (int, float)):
                        val = max(val, float(min_v))
                    if isinstance(max_v, (int, float)):
                        val = min(val, float(max_v))
                except Exception:
                    pass
                await self.hass.services.async_call(
                    "input_number", "set_value", {"entity_id": eid, "value": float(val)}, blocking=False
                )
                return True
            attrs = st.attributes or {}
            hvac_mode = str(attrs.get("hvac_mode", st.state)).lower()
            hvac_modes = [str(x).lower() for x in (attrs.get("hvac_modes") or [])]
            preset_mode = str(attrs.get("preset_mode", "") or "").lower()
            preset_modes = [str(x).lower() for x in (attrs.get("preset_modes") or [])]
            min_t = attrs.get("min_temp")
            max_t = attrs.get("max_temp")

            def _clamp(val: float) -> float:
                try:
                    if isinstance(min_t, (int, float)):
                        val = max(val, float(min_t))
                    if isinstance(max_t, (int, float)):
                        val = min(val, float(max_t))
                except Exception:
                    pass
                return val

            # If preset has a manual/hold and not active, switch to it
            try:
                want_preset = None
                for cand in ("manual", "hold"):
                    if cand in preset_modes:
                        want_preset = cand
                        break
                if want_preset and preset_mode != want_preset:
                    await self.hass.services.async_call(
                        "climate", "set_preset_mode", {"entity_id": eid, "preset_mode": want_preset}, blocking=False
                    )
            except Exception:
                pass

            # If in a mode that ignores temperature, try switching to a workable one
            try:
                if hvac_mode in ("off", "dry", "fan_only"):
                    new_mode = None
                    if "heat" in hvac_modes:
                        new_mode = "heat"
                    elif "cool" in hvac_modes:
                        new_mode = "cool"
                    if new_mode:
                        await self.hass.services.async_call(
                            "climate", "set_hvac_mode", {"entity_id": eid, "hvac_mode": new_mode}, blocking=False
                        )
                        hvac_mode = new_mode
            except Exception:
                pass

            # Decide whether to use range or single setpoint
            has_low = isinstance(attrs.get("target_temp_low"), (int, float)) or ("target_temp_low" in attrs)
            has_high = isinstance(attrs.get("target_temp_high"), (int, float)) or ("target_temp_high" in attrs)
            use_range = (hvac_mode in ("heat_cool", "auto")) and has_low and has_high

            if use_range:
                # Build a narrow band around desired
                band = 1.0
                try:
                    # Allow global override via settings.range_band_c
                    _s, settings = self._get_data()
                    band = float(settings.get("range_band_c", 1.0))
                except Exception:
                    pass
                half = max(0.2, band / 2.0)
                low = _clamp(desired - half)
                high = _clamp(desired + half)
                if high <= low:
                    # Ensure valid ordering
                    high = min(_clamp(low + 0.5), _clamp(desired + 0.5))
                data = {"entity_id": eid, "target_temp_low": float(low), "target_temp_high": float(high)}
                await self.hass.services.async_call("climate", "set_temperature", data, blocking=False)
                return True

            # Fallback: single temperature
            data = {"entity_id": eid, "temperature": float(_clamp(desired))}
            await self.hass.services.async_call("climate", "set_temperature", data, blocking=False)
            return True
        except Exception:
            return False


class BackupManager:
    def __init__(self, hass: HomeAssistant, backup_cb):
        self.hass = hass
        self._unsub_timer = None
        self._backup_cb = backup_cb

    def _settings(self):
        d = self.hass.data.get(DOMAIN, {})
        return d.get("settings", {})

    def _enabled(self) -> bool:
        s = self._settings()
        return bool(s.get("backup_auto_enabled", False))

    def _interval_min(self) -> int:
        s = self._settings()
        # Prefer days; fallback to minutes for backward compat
        try:
            if "backup_interval_days" in s:
                d = max(1, min(365, int(s.get("backup_interval_days", 1))))
                return d * 1440
        except Exception:
            pass
        try:
            v = int(s.get("backup_interval_min", 1440))
            return max(1440, min(365*1440, v))
        except Exception:
            return 1440

    async def async_start(self):
        await self._schedule_next()
        # Re-schedule on any store update
        @callback
        def _on_update():
            self.hass.async_create_task(self._schedule_next())
        async_dispatcher_connect(self.hass, SIGNAL_UPDATED, _on_update)

    async def _schedule_next(self):
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        if not self._enabled():
            return
        mins = self._interval_min()
        when = dt_util.utcnow() + timedelta(minutes=mins)
        @callback
        def _cb(_now):
            self.hass.async_create_task(self._do_backup())
        self._unsub_timer = async_track_point_in_utc_time(self.hass, _cb, when)

    async def _do_backup(self):
        try:
            await self._backup_cb(ServiceCall(DOMAIN, "backup_now", {}))
        except Exception:
            _LOGGER.warning("%s: auto backup failed", DOMAIN)
        await self._schedule_next()
