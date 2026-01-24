from __future__ import annotations

import asyncio
import logging
import os
import traceback

from homeassistant.core import HomeAssistant, ServiceCall, callback  # type: ignore[reportMissingImports]
from homeassistant.config_entries import ConfigEntry  # type: ignore[reportMissingImports]
from homeassistant.helpers import config_validation as cv  # type: ignore[reportMissingImports]
from homeassistant.helpers.storage import Store  # type: ignore[reportMissingImports]
from homeassistant.helpers.dispatcher import async_dispatcher_send, async_dispatcher_connect  # type: ignore[reportMissingImports]
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_state_change_event, async_track_time_interval  # type: ignore[reportMissingImports]
from homeassistant.util import dt as dt_util  # type: ignore[reportMissingImports]
from datetime import timedelta
from homeassistant.components.http import HomeAssistantView  # type: ignore[reportMissingImports]
from homeassistant.const import UnitOfTemperature  # type: ignore[reportMissingImports]
from typing import Any, Optional

from .const import DOMAIN, STORAGE_KEY, STORAGE_VERSION, SIGNAL_UPDATED, BACKUP_STORAGE_KEY

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


_LOGGER = logging.getLogger(__name__)


def _norm_instance_id(raw: Any) -> str:
    """Normalize an instance id used to namespace schedules/settings.

    Must be stable, JSON-safe and reasonably human editable.
    """
    try:
        s = str(raw or "").strip()
    except Exception:
        s = ""
    if not s:
        return "default"
    # Keep a conservative charset to avoid surprises in storage keys and URLs.
    out = []
    for ch in s:
        if ch.isalnum() or ch in ("_", "-", "."):
            out.append(ch)
        else:
            out.append("_")
    s2 = "".join(out)
    s2 = s2[:64]
    return s2 or "default"


def _ensure_instances_loaded(hass: HomeAssistant) -> None:
    """Ensure hass.data[DOMAIN] has instances + active_instance_id.

    This is defensive in case older installs call services before async_setup_entry finished.
    """
    try:
        data = hass.data.setdefault(DOMAIN, {})
        if not isinstance(data.get("instances"), dict):
            data["instances"] = {"default": {"schedules": {}, "weekdays": {}, "profiles": {}, "settings": {}, "colors": {}}}
        if not isinstance(data.get("active_instance_id"), str):
            data["active_instance_id"] = "default"
    except Exception:
        pass


def _c_to_f(c: float) -> float:
    return (float(c) * 9.0 / 5.0) + 32.0


def _f_to_c(f: float) -> float:
    return (float(f) - 32.0) * 5.0 / 9.0


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


def build_state_payload(hass: HomeAssistant, versions_only: bool = False, instance_id: str | None = None) -> dict[str, Any]:
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
    _ensure_instances_loaded(hass)
    instances = data.get("instances") if isinstance(data.get("instances"), dict) else {}
    active_iid = _norm_instance_id(data.get("active_instance_id") or "default")
    if active_iid not in instances and instances:
        # Pick first available instance if active id is missing.
        try:
            active_iid = sorted(instances.keys())[0]
        except Exception:
            active_iid = "default"
    req_iid = _norm_instance_id(instance_id) if instance_id else active_iid
    if req_iid not in instances and instances:
        req_iid = active_iid
    inst = instances.get(req_iid, {}) if isinstance(instances.get(req_iid), dict) else {}
    payload: dict[str, Any] = {
        "version": int(data.get("version", 1)),
        "settings_version": int(data.get("settings_version", data.get("version", 1))),
        "colors_version": int(data.get("colors_version", data.get("version", 1))),
        "weekday_version": int(data.get("weekday_version", data.get("version", 1))),
        "profile_version": int(data.get("profile_version", data.get("version", 1))),
        "backup_version": int(data.get("backup_version", 1)),
        "active_instance_id": active_iid,
        "instance_id": req_iid,
        "instances": sorted(list(instances.keys())) if isinstance(instances, dict) else ["default"],
    }
    if versions_only:
        return payload
    payload.update({
        # Return data for the requested instance (or the active instance when not specified).
        "schedules": inst.get("schedules", {}) if isinstance(inst, dict) else data.get("schedules", {}),
        "weekdays": inst.get("weekdays", {}) if isinstance(inst, dict) else data.get("weekday_schedules", {}),
        "profiles": inst.get("profiles", {}) if isinstance(inst, dict) else data.get("profile_schedules", {}),
        "settings": inst.get("settings", {}) if isinstance(inst, dict) else data.get("settings", {}),
        "colors": inst.get("colors", {}) if isinstance(inst, dict) else data.get("colors", {}),
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
            iid = None
            try:
                iid = request.query.get("instance_id")
            except Exception:
                iid = None
            payload = build_state_payload(self.hass, versions_only=False, instance_id=iid)
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

    # Ensure Lovelace card JS is available and resource is registered (best-effort)
    try:
        from .frontend import ensure_frontend
        await ensure_frontend(hass)
    except Exception:
        _LOGGER.debug("%s: ensure_frontend failed", DOMAIN, exc_info=True)

    # --- Multi-instance store support (backwards compatible) ---
    instances: dict[str, Any] = {}
    active_iid = _norm_instance_id(data.get("active_instance_id") or data.get("active_instance") or "default")
    raw_instances = data.get("instances")
    if isinstance(raw_instances, dict):
        for k, v in raw_instances.items():
            if not isinstance(v, dict):
                continue
            iid = _norm_instance_id(k)
            instances[iid] = {
                "schedules": v.get("schedules", {}) if isinstance(v.get("schedules"), dict) else {},
                "weekdays": v.get("weekdays", {}) if isinstance(v.get("weekdays"), dict) else {},
                "profiles": v.get("profiles", {}) if isinstance(v.get("profiles"), dict) else {},
                "settings": v.get("settings", {}) if isinstance(v.get("settings"), dict) else {},
                "colors": v.get("colors", {}) if isinstance(v.get("colors"), dict) else {},
            }
    if not instances:
        # Legacy flat format: migrate into default instance.
        instances = {
            "default": {
                "schedules": data.get("schedules", {}) if isinstance(data.get("schedules"), dict) else {},
                "weekdays": data.get("weekdays", {}) if isinstance(data.get("weekdays"), dict) else {},
                "profiles": data.get("profiles", {}) if isinstance(data.get("profiles"), dict) else {},
                "settings": data.get("settings", {}) if isinstance(data.get("settings"), dict) else {},
                "colors": data.get("colors", {}) if isinstance(data.get("colors"), dict) else {},
            }
        }
        active_iid = "default"
    if active_iid not in instances:
        try:
            active_iid = sorted(instances.keys())[0]
        except Exception:
            active_iid = "default"

    hass.data[DOMAIN]["instances"] = instances
    hass.data[DOMAIN]["active_instance_id"] = active_iid

    # Keep legacy top-level keys pointing at the active instance for minimal change elsewhere.
    active_store = instances.get(active_iid, {}) if isinstance(instances.get(active_iid), dict) else {}
    hass.data[DOMAIN]["schedules"] = active_store.get("schedules", {})
    hass.data[DOMAIN]["weekday_schedules"] = active_store.get("weekdays", {}) or {}
    hass.data[DOMAIN]["profile_schedules"] = active_store.get("profiles", {}) or {}
    hass.data[DOMAIN]["settings"] = active_store.get("settings", {})
    hass.data[DOMAIN]["colors"] = active_store.get("colors", {}) or {}
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
            _ensure_instances_loaded(hass)
            instances = hass.data[DOMAIN].get("instances", {})
            active_iid = _norm_instance_id(hass.data[DOMAIN].get("active_instance_id") or "default")
            active_store = instances.get(active_iid, {}) if isinstance(instances, dict) else {}
            await store.async_save({
                # New multi-instance store
                "instances": instances,
                "active_instance_id": active_iid,
                # Legacy top-level mirror (active instance)
                "schedules": active_store.get("schedules", hass.data[DOMAIN].get("schedules", {})),
                "weekdays": active_store.get("weekdays", hass.data[DOMAIN].get("weekday_schedules", {})),
                "profiles": active_store.get("profiles", hass.data[DOMAIN].get("profile_schedules", {})),
                "settings": active_store.get("settings", hass.data[DOMAIN].get("settings", {})),
                "colors": active_store.get("colors", hass.data[DOMAIN].get("colors", {})),
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
        _ensure_instances_loaded(hass)
        instances = hass.data[DOMAIN].setdefault("instances", {})
        iid_in_call = "instance_id" in call.data
        iid = _norm_instance_id(call.data.get("instance_id")) if iid_in_call else _norm_instance_id(hass.data[DOMAIN].get("active_instance_id") or "default")

        inst = instances.get(iid)
        if not isinstance(inst, dict):
            inst = {"schedules": {}, "weekdays": {}, "profiles": {}, "settings": {}, "colors": {}}
            instances[iid] = inst

        cur_sched = inst.get("schedules", {}) if isinstance(inst.get("schedules"), dict) else {}
        cur_set = inst.get("settings", {}) if isinstance(inst.get("settings"), dict) else {}
        cur_colors = inst.get("colors", {}) if isinstance(inst.get("colors"), dict) else {}
        cur_week = inst.get("weekdays", {}) if isinstance(inst.get("weekdays"), dict) else {}
        cur_prof = inst.get("profiles", {}) if isinstance(inst.get("profiles"), dict) else {}
        changed = False
        sched_changed = False
        settings_changed = False
        colors_changed = False
        weekday_changed = False
        profile_changed = False

        prev_active = _norm_instance_id(hass.data[DOMAIN].get("active_instance_id") or "default")
        activate = False
        if "activate" in call.data:
            activate = bool(call.data.get("activate"))
        elif iid_in_call:
            # New clients that pass instance_id will default to activating that instance.
            activate = True

        if "schedules" in call.data:
            schedules = call.data.get("schedules")
            if not isinstance(schedules, dict):
                _LOGGER.warning("%s.set_store: schedules must be an object when provided", DOMAIN)
                return
            if force or schedules != cur_sched:
                inst["schedules"] = schedules
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
                        inst["weekdays"] = new_week
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
                        inst["profiles"] = new_prof
                        profile_changed = True
                        changed = True
                except Exception:
                    pass

        if "settings" in call.data:
            settings = call.data.get("settings")
            if isinstance(settings, dict):
                if force or settings != cur_set:
                    inst["settings"] = settings
                    settings_changed = True
                    changed = True
                c_ranges = settings.get("color_ranges")
                c_global = settings.get("color_global")
                if c_ranges is not None or c_global is not None:
                    inst["colors"] = {
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
                    inst["colors"] = colors
                    colors_changed = True
                    changed = True

        if "weekdays" in call.data:
            weekdays = call.data.get("weekdays")
            if isinstance(weekdays, dict):
                if force or weekdays != cur_week:
                    inst["weekdays"] = weekdays
                    weekday_changed = True
                    changed = True
                try:
                    for eid, wk in (weekdays.items() if isinstance(weekdays, dict) else []):
                        if not isinstance(wk, dict):
                            continue
                        row = dict(inst.get("schedules", {}).get(eid, {}))
                        if "weekly" in wk:
                            row["weekly"] = wk["weekly"]
                        if "weekly_modes" in wk:
                            row["weekly_modes"] = wk["weekly_modes"]
                        inst.setdefault("schedules", {})[eid] = row
                        sched_changed = True
                except Exception:
                    pass

        if "profiles" in call.data:
            profiles = call.data.get("profiles")
            if isinstance(profiles, dict):
                if force or profiles != cur_prof:
                    inst["profiles"] = profiles
                    profile_changed = True
                    changed = True
                try:
                    for eid, pdata in (profiles.items() if isinstance(profiles, dict) else []):
                        if not isinstance(pdata, dict):
                            continue
                        row = dict(inst.get("schedules", {}).get(eid, {}))
                        if "profiles" in pdata:
                            row["profiles"] = pdata["profiles"]
                        if "activeProfile" in pdata:
                            row["activeProfile"] = pdata["activeProfile"]
                        inst.setdefault("schedules", {})[eid] = row
                        sched_changed = True
                except Exception:
                    pass

        # Allow activation-only switches (no payload changes).
        if not changed and not force and not (activate and prev_active != iid):
            return

        # Handle activation and keep legacy pointers synced.
        if activate:
            hass.data[DOMAIN]["active_instance_id"] = iid

        if activate or _norm_instance_id(hass.data[DOMAIN].get("active_instance_id") or "default") == iid:
            hass.data[DOMAIN]["schedules"] = inst.get("schedules", {})
            hass.data[DOMAIN]["weekday_schedules"] = inst.get("weekdays", {})
            hass.data[DOMAIN]["profile_schedules"] = inst.get("profiles", {})
            hass.data[DOMAIN]["settings"] = inst.get("settings", {})
            hass.data[DOMAIN]["colors"] = inst.get("colors", {})

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

        # Switching active instance should also trigger a refresh.
        if activate and prev_active != iid:
            hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN]["version"]) + 1
        _LOGGER.info("set_store: version=%s, sched_changed=%s, settings_changed=%s", 
                     hass.data[DOMAIN]["version"], sched_changed, settings_changed)
        await _save_and_broadcast()

    async def select_instance(call: ServiceCall):
        """Select the active instance id.

        Optional: create the instance if missing, and optionally copy from current active.
        """
        _ensure_instances_loaded(hass)
        instances = hass.data[DOMAIN].setdefault("instances", {})
        target = _norm_instance_id(call.data.get("instance_id"))
        create_if_missing = bool(call.data.get("create_if_missing"))
        copy_from_active = bool(call.data.get("copy_from_active"))
        prev_active = _norm_instance_id(hass.data[DOMAIN].get("active_instance_id") or "default")

        if target not in instances:
            if not create_if_missing:
                _LOGGER.warning("%s.select_instance: instance_id '%s' does not exist", DOMAIN, target)
                return
            base = {}
            if copy_from_active and prev_active in instances and isinstance(instances.get(prev_active), dict):
                base = instances.get(prev_active) or {}
            instances[target] = {
                "schedules": (base.get("schedules") or {}) if isinstance(base, dict) else {},
                "weekdays": (base.get("weekdays") or {}) if isinstance(base, dict) else {},
                "profiles": (base.get("profiles") or {}) if isinstance(base, dict) else {},
                "settings": (base.get("settings") or {}) if isinstance(base, dict) else {},
                "colors": (base.get("colors") or {}) if isinstance(base, dict) else {},
            }

        hass.data[DOMAIN]["active_instance_id"] = target
        inst = instances.get(target, {}) if isinstance(instances.get(target), dict) else {}
        hass.data[DOMAIN]["schedules"] = inst.get("schedules", {})
        hass.data[DOMAIN]["weekday_schedules"] = inst.get("weekdays", {})
        hass.data[DOMAIN]["profile_schedules"] = inst.get("profiles", {})
        hass.data[DOMAIN]["settings"] = inst.get("settings", {})
        hass.data[DOMAIN]["colors"] = inst.get("colors", {})

        if prev_active != target:
            hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN].get("version", 0)) + 1
        await _save_and_broadcast()

    async def rename_instance(call: ServiceCall):
        """Rename an instance id (move data)."""
        _ensure_instances_loaded(hass)
        instances = hass.data[DOMAIN].setdefault("instances", {})
        old_id = _norm_instance_id(call.data.get("old_instance_id") or call.data.get("old_id"))
        new_id = _norm_instance_id(call.data.get("new_instance_id") or call.data.get("new_id"))
        if old_id == new_id:
            return
        if old_id not in instances:
            _LOGGER.warning("%s.rename_instance: old instance '%s' not found", DOMAIN, old_id)
            return
        if new_id in instances:
            _LOGGER.warning("%s.rename_instance: new instance '%s' already exists", DOMAIN, new_id)
            return
        instances[new_id] = instances.pop(old_id)
        if _norm_instance_id(hass.data[DOMAIN].get("active_instance_id") or "default") == old_id:
            hass.data[DOMAIN]["active_instance_id"] = new_id
            inst = instances.get(new_id, {}) if isinstance(instances.get(new_id), dict) else {}
            hass.data[DOMAIN]["schedules"] = inst.get("schedules", {})
            hass.data[DOMAIN]["weekday_schedules"] = inst.get("weekdays", {})
            hass.data[DOMAIN]["profile_schedules"] = inst.get("profiles", {})
            hass.data[DOMAIN]["settings"] = inst.get("settings", {})
            hass.data[DOMAIN]["colors"] = inst.get("colors", {})
        hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN].get("version", 0)) + 1
        await _save_and_broadcast()

    async def clear_store(call: ServiceCall):
        # Explicit clear: wipe ALL instances + active and bump versions.
        hass.data[DOMAIN]["instances"] = {"default": {"schedules": {}, "weekdays": {}, "profiles": {}, "settings": {}, "colors": {}}}
        hass.data[DOMAIN]["active_instance_id"] = "default"
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

    async def factory_reset(call: ServiceCall):
        """Factory reset thermostat_timeline.

        Deletes the underlying .storage JSON files and recreates them empty.
        This is useful when the store has become corrupted or needs a hard reset.
        """
        # 1) Remove persisted files (best-effort)
        try:
            await store.async_remove()
        except Exception:
            pass
        try:
            await backup_store.async_remove()
        except Exception:
            pass

        # 2) Reset runtime state (equivalent to clear_store + clearing backups)
        hass.data[DOMAIN]["instances"] = {"default": {"schedules": {}, "weekdays": {}, "profiles": {}, "settings": {}, "colors": {}}}
        hass.data[DOMAIN]["active_instance_id"] = "default"
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

        # Clear backups too
        hass.data[DOMAIN]["backup_schedules"] = {}
        hass.data[DOMAIN]["backup_settings"] = {}
        hass.data[DOMAIN]["backup_weekdays"] = {}
        hass.data[DOMAIN]["backup_profiles"] = {}
        hass.data[DOMAIN]["backup_partial_flags"] = None
        hass.data[DOMAIN]["backup_slot_index"] = 0
        hass.data[DOMAIN]["backup_last_ts"] = None
        hass.data[DOMAIN]["backup_slots"] = []
        hass.data[DOMAIN]["backup_version"] = int(hass.data[DOMAIN].get("backup_version", 1)) + 1

        # 3) Recreate empty files and broadcast
        await _save_and_broadcast()
        await _save_backup()

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
    hass.services.async_register(DOMAIN, "factory_reset", factory_reset)
    hass.services.async_register(DOMAIN, "select_instance", select_instance)
    hass.services.async_register(DOMAIN, "rename_instance", rename_instance)
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

    # ---- Background Auto-Apply Manager (multi-instance) ----
    mgr = MultiInstanceAutoApplyManager(hass)
    hass.data[DOMAIN]["manager"] = mgr
    await mgr.async_start()
    # ---- Open Window Detection (background, multi-instance) ----
    owd = MultiInstanceOpenWindowManager(hass, apply_multi=mgr)
    # Keep legacy key for compatibility (points to the multi wrapper).
    hass.data[DOMAIN]["open_window_manager"] = owd
    await owd.async_start()
    # ---- Backup Manager (auto backup) ----
    bkm = BackupManager(hass, backup_cb=backup_now)
    hass.data[DOMAIN]["backup_manager"] = bkm
    await bkm.async_start()
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True


class AutoApplyManager:
    def __init__(self, hass: HomeAssistant, instance_id: str = "default"):
        self.hass = hass
        self._instance_id = _norm_instance_id(instance_id)
        self._unsub_timer = None
        self._unsub_persons = None
        self._unsub_boiler = None
        self._unsub_boiler_interval = None
        self._unsub_pause_sensor = None
        self._last_pause_sensor_on: bool | None = None
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
        self._reset_boiler_interval()
        self._reset_pause_sensor_watch()

    async def _on_store_changed(self):
        # Respect apply_on_edit toggle: only apply immediately on store changes when enabled
        _s, settings = self._get_data()
        if bool(settings.get("apply_on_edit", True)):
            await self._maybe_apply_now(force=True)
        await self._maybe_control_boiler(force=True)
        await self._schedule_next()
        self._reset_person_watch()
        self._reset_boiler_watch()
        self._reset_boiler_interval()
        self._reset_pause_sensor_watch()

    def _pause_sensor_on(self, settings: dict) -> bool:
        """Return True when pause is requested via binary sensor."""
        try:
            if not bool(settings.get("pause_sensor_enabled")):
                return False
            eid = str(settings.get("pause_sensor_entity") or "").strip()
            if not eid or not eid.startswith("binary_sensor."):
                return False
            st = self.hass.states.get(eid)
            s = str(st.state if st else "").lower().strip()
            return s == "on"
        except Exception:
            return False

    def _get_data(self):
        d = self.hass.data.get(DOMAIN, {})
        try:
            instances = d.get("instances") if isinstance(d.get("instances"), dict) else None
            if isinstance(instances, dict):
                inst = instances.get(self._instance_id)
                if isinstance(inst, dict):
                    schedules = inst.get("schedules", {}) if isinstance(inst.get("schedules"), dict) else {}
                    settings = inst.get("settings", {}) if isinstance(inst.get("settings"), dict) else {}
                    return schedules, settings
        except Exception:
            pass
        # Fallback to legacy active-instance pointers
        schedules = d.get("schedules", {})
        settings = d.get("settings", {})
        return schedules, settings

    def _owd_mgr(self):
        """Return the Open Window manager for this instance (if any)."""
        try:
            d = self.hass.data.get(DOMAIN, {})
            m = d.get("open_window_managers")
            if isinstance(m, dict):
                owd = m.get(self._instance_id)
                if owd is not None:
                    return owd
            return d.get("open_window_manager")
        except Exception:
            return None

    def _auto_apply_enabled(self) -> bool:
        _s, settings = self._get_data()
        if not bool(settings.get("auto_apply_enabled")):
            return False
        # Pause via binary sensor (indefinite while sensor is on)
        try:
            if self._pause_sensor_on(settings):
                return False
        except Exception:
            pass
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
                # Ignore expired timestamps to avoid scheduling timers in the past,
                # which can create an immediate-fire feedback loop.
                now_ms = dt_util.utcnow().timestamp() * 1000.0
                if float(pu) <= (now_ms + 250.0):
                    return None
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

    def _today_iso(self) -> str:
        """Return today's date in HA local timezone as YYYY-MM-DD."""
        try:
            return dt_util.now().date().isoformat()
        except Exception:
            return ""

    def _is_holiday_active(self, settings: dict) -> bool:
        """Determine whether Holiday mode is active.

        Uses settings written by the frontend card:
          - holidays_enabled: bool
          - holidays_source: 'calendar' | 'manual'
          - holidays_entity: entity id (calendar.* or binary_sensor.*)
          - holidays_dates: ['YYYY-MM-DD', ...]
        """
        try:
            if not bool(settings.get("holidays_enabled")):
                return False
            src = str(settings.get("holidays_source") or "calendar").lower().strip()
            if src == "manual":
                dates = settings.get("holidays_dates")
                if not isinstance(dates, list) or not dates:
                    return False
                today = self._today_iso()
                return any(str(d) == today for d in dates)

            # calendar/binary_sensor entity
            eid = str(settings.get("holidays_entity") or "").strip()
            if not eid:
                return False
            st = self.hass.states.get(eid)
            s = str(st.state if st else "").lower()
            return s == "on"
        except Exception:
            return False

    def _effective_blocks_today(self, row: dict, settings: dict):
        # Holidays override have highest precedence when enabled and active.
        # Note: only apply holiday override when this room actually has holiday blocks.
        try:
            if self._is_holiday_active(settings):
                hol = row.get("holiday") or {}
                blk = hol.get("blocks") if isinstance(hol, dict) else None
                if isinstance(blk, list) and len(blk) > 0:
                    return blk
        except Exception:
            pass
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
                        val = (away.get("target_c") if isinstance(away, dict) else None)
                        target = float(val) if isinstance(val, (int, float, str)) else None
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
                    # Treat empty profile blocks as "no override".
                    # If a manual profile is active globally but a room has no
                    # blocks defined for that profile, the room must follow its
                    # normal schedule instead of falling back to defaultTemp.
                    if isinstance(blk, list) and len(blk) > 0:
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

    def _desired_for(self, eid: str, schedules: dict, settings: dict, now_min: int) -> Optional[float]:
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
            v = hit.get("temp") if isinstance(hit, dict) else None
            if isinstance(v, (int, float, str)):
                want = float(v)
            else:
                want = float(row.get("defaultTemp", 20))
        else:
            # When advanced presence is active but this room has no presence schedule,
            # use Away temperature instead of the room default.
            if presence_key_active and not room_has_presence_blocks:
                try:
                    away = settings.get("away") or {}
                    val = away.get("target_c")
                    if isinstance(val, (int, float, str)):
                        want = float(val)
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
            # Open Window Detection: skip applying schedules while a room is suspended
            try:
                owd = self._owd_mgr()
                if owd and hasattr(owd, "is_entity_suspended") and owd.is_entity_suspended(eid):
                    continue
            except Exception:
                pass
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
                lt = last.get("temp")
                last_temp = float(lt) if isinstance(lt, (int, float, str)) else None
            except Exception:
                last_temp = None
            if (not reconcile) and (last_temp is not None) and abs(last_temp - float(desired)) < 0.05:
                continue
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

    async def async_apply_entity_now(self, eid: str, reconcile: bool = False) -> None:
        """Apply the current desired setpoint for a single entity (best-effort).

        Used by other managers (e.g. Open Window Detection) to reconcile a room
        back to schedule without forcing a global re-apply.
        """
        try:
            if not isinstance(eid, str):
                return
            if not (eid.startswith("climate.") or eid.startswith("input_number.")):
                return
            if not self.hass.states.get(eid):
                return
            if not self._auto_apply_enabled():
                return
            # Respect Open Window Detection suspension
            try:
                owd = self._owd_mgr()
                if owd and hasattr(owd, "is_entity_suspended") and owd.is_entity_suspended(eid):
                    return
            except Exception:
                pass

            schedules, settings = self._get_data()
            now_min = self._now_min()
            desired = self._desired_for(eid, schedules, settings, now_min)
            if desired is None:
                return

            if not reconcile:
                last = self._last_applied.get(eid) or {}
                try:
                    lt = last.get("temp")
                    last_temp = float(lt) if isinstance(lt, (int, float, str)) else None
                except Exception:
                    last_temp = None
                if (last_temp is not None) and abs(last_temp - float(desired)) < 0.05:
                    return

            ok = await self._apply_setpoint(eid, float(desired))
            if ok:
                self._last_applied[eid] = {"min": now_min, "temp": float(desired)}
        except Exception:
            return

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

        if not when:
            return

        # Never schedule in the past (HA will execute immediately), which can
        # otherwise turn stale timestamps into a hot loop. Timers are minute-
        # granularity here, so clamp to at least +1 minute.
        try:
            when = dt_util.as_utc(when)
        except Exception:
            pass
        try:
            now_utc = dt_util.utcnow()
            if when <= now_utc:
                when = now_utc + timedelta(minutes=1)
        except Exception:
            pass
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
            # Backwards compat: if the new schedule-driven keys are absent,
            # keep the legacy sensor-based behavior.
            use_schedule_mode = (
                ("boiler_rooms" in settings)
                or ("boiler_on_offset" in settings)
                or ("boiler_off_offset" in settings)
            )

            if use_schedule_mode:
                schedules, _settings = self._get_data()
                merges = (settings.get("merges") or {}) if isinstance(settings, dict) else {}

                rooms = None
                br = settings.get("boiler_rooms")
                if isinstance(br, list):
                    rooms = [str(x).strip() for x in br if str(x).strip()]
                    if len(rooms) == 0:
                        return
                if rooms is None:
                    rooms = sorted(list(self._all_targets(schedules if isinstance(schedules, dict) else {}, merges)))

                watch = []
                for eid in rooms:
                    if isinstance(eid, str) and eid.startswith("climate."):
                        watch.append(eid)
                if not watch:
                    return

                @callback
                def _ch(_event):
                    self.hass.async_create_task(self._maybe_control_boiler(force=True))

                self._unsub_boiler = async_track_state_change_event(self.hass, watch, _ch)
                return

            # Legacy mode: react when boiler sensor updates
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

    def _reset_boiler_interval(self) -> None:
        if self._unsub_boiler_interval:
            try:
                self._unsub_boiler_interval()
            except Exception:
                pass
            self._unsub_boiler_interval = None

        _schedules, settings = self._get_data()
        try:
            if not bool(settings.get("boiler_enabled")):
                return

            @callback
            def _tick(_now):
                # Periodic sanity check (requested): independent of UI being open.
                self.hass.async_create_task(self._maybe_control_boiler(force=True))

            self._unsub_boiler_interval = async_track_time_interval(self.hass, _tick, timedelta(minutes=10))
        except Exception:
            self._unsub_boiler_interval = None

    def _reset_pause_sensor_watch(self) -> None:
        if self._unsub_pause_sensor:
            try:
                self._unsub_pause_sensor()
            except Exception:
                pass
            self._unsub_pause_sensor = None
        _schedules, settings = self._get_data()
        try:
            if not bool(settings.get("pause_sensor_enabled")):
                self._last_pause_sensor_on = None
                return
            eid = str(settings.get("pause_sensor_entity") or "").strip()
            if not eid or not eid.startswith("binary_sensor."):
                self._last_pause_sensor_on = None
                return

            # Prime last state
            self._last_pause_sensor_on = self._pause_sensor_on(settings)

            @callback
            def _ch(event):
                try:
                    ns = event.data.get("new_state")
                    s = str(ns.state if ns else "").lower().strip()
                    now_on = s == "on"
                except Exception:
                    return
                if self._last_pause_sensor_on is not None and now_on == self._last_pause_sensor_on:
                    return
                self._last_pause_sensor_on = now_on

                async def _handle() -> None:
                    # When pausing: just reschedule (cancels timers).
                    # When resuming: reconcile once and schedule.
                    if now_on:
                        await self._schedule_next()
                        return
                    await self._maybe_apply_now(force=True, reconcile=True)
                    await self._maybe_control_boiler(force=True)
                    await self._schedule_next()

                self.hass.async_create_task(_handle())

            self._unsub_pause_sensor = async_track_state_change_event(self.hass, [eid], _ch)
        except Exception:
            self._unsub_pause_sensor = None
            self._last_pause_sensor_on = None

    @staticmethod
    def _f_to_c(v: float) -> float:
        return (float(v) - 32.0) * 5.0 / 9.0

    async def _maybe_control_boiler(self, force: bool = False) -> None:
        """Control boiler switch (switch.* or input_boolean.*).

        New (schedule-driven) mode:
        - ON if any included room current temp is below its desired schedule temp minus boiler_on_offset.
        - OFF when all included rooms are at/above desired schedule temp plus boiler_off_offset.

        Offsets are stored as °C deltas (independent of HA display unit).

        Backwards compat:
        - If schedule-driven keys are not present, fall back to legacy boiler sensor min/max logic.
        """
        try:
            if not self._auto_apply_enabled():
                return
            _schedules, settings = self._get_data()
            if not bool(settings.get("boiler_enabled")):
                return

            sw = settings.get("boiler_switch")
            if not isinstance(sw, str) or "." not in sw:
                return
            sw_domain = sw.split(".", 1)[0]
            if sw_domain not in ("switch", "input_boolean"):
                return

            use_schedule_mode = (
                ("boiler_rooms" in settings)
                or ("boiler_on_offset" in settings)
                or ("boiler_off_offset" in settings)
            )

            # Determine current boiler state up-front (used by both modes)
            st_sw = self.hass.states.get(sw)
            cur = str(st_sw.state).lower().strip() if st_sw else ""

            if use_schedule_mode:
                schedules, settings = self._get_data()
                if not isinstance(schedules, dict):
                    return

                # HA display unit for climate attributes
                def _ha_is_f() -> bool:
                    try:
                        u = self.hass.config.units.temperature_unit
                        return u == UnitOfTemperature.FAHRENHEIT or "F" in str(u).upper()
                    except Exception:
                        return False

                ha_is_f = _ha_is_f()

                def _to_c_from_ha(val_ha: float) -> float:
                    return _f_to_c(val_ha) if ha_is_f else float(val_ha)

                try:
                    on_offset_c = float(settings.get("boiler_on_offset", 0.0) or 0.0)
                except Exception:
                    on_offset_c = 0.0
                try:
                    off_offset_c = float(settings.get("boiler_off_offset", 0.0) or 0.0)
                except Exception:
                    off_offset_c = 0.0

                merges = settings.get("merges") or {}
                rooms = None
                br = settings.get("boiler_rooms")
                if isinstance(br, list):
                    rooms = [str(x).strip() for x in br if str(x).strip()]
                    if len(rooms) == 0:
                        return
                if rooms is None:
                    rooms = sorted(list(self._all_targets(schedules, merges if isinstance(merges, dict) else {})))

                now_min = self._now_min()
                any_below = False
                all_above = True
                compared = 0
                missing = 0

                for eid in rooms:
                    try:
                        if not (isinstance(eid, str) and eid.startswith("climate.")):
                            missing += 1
                            continue

                        desired_c = self._desired_for(eid, schedules, settings, now_min)
                        if desired_c is None:
                            missing += 1
                            continue

                        st = self.hass.states.get(eid)
                        if not st:
                            missing += 1
                            continue

                        attrs = st.attributes or {}
                        cur_ha = attrs.get("current_temperature")
                        if cur_ha is None:
                            # some devices only expose "temperature"
                            cur_ha = attrs.get("temperature")
                        if cur_ha is None:
                            missing += 1
                            continue
                        cur_c = _to_c_from_ha(float(cur_ha))

                        compared += 1
                        if cur_c < (float(desired_c) - on_offset_c):
                            any_below = True
                            all_above = False
                        elif cur_c < (float(desired_c) + off_offset_c):
                            all_above = False
                    except Exception:
                        missing += 1
                        continue

                if compared <= 0:
                    return

                want = None
                if any_below:
                    want = "on"
                else:
                    # Conservative OFF: require complete data for all included rooms.
                    if missing > 0:
                        return
                    if all_above:
                        want = "off"
                    else:
                        return

                if want == "on" and cur == "on":
                    return
                if want == "off" and cur == "off":
                    return

                await self.hass.services.async_call(
                    sw_domain,
                    "turn_on" if want == "on" else "turn_off",
                    {"entity_id": sw},
                    blocking=False,
                )
                return

            # ---- Legacy mode (sensor min/max hysteresis) ----
            sens = settings.get("boiler_temp_sensor")
            if not isinstance(sens, str) or "." not in sens:
                return

            st_sens = self.hass.states.get(sens)
            if not st_sens:
                return
            try:
                raw = float(st_sens.state)
            except Exception:
                return

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

            if want == "on" and cur == "on":
                return
            if want == "off" and cur == "off":
                return

            await self.hass.services.async_call(
                sw_domain,
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
            # Store contract: temperatures are canonical °C.
            # Backwards compat: older clients may have stored °F when temp_unit == 'F'.
            try:
                schedules, settings = self._get_data()
            except Exception:
                settings = {}
                schedules = {}

            # Optional: for some thermostats we need to send a climate.turn_on command as well.
            # This is configured per primary room entity (merged entities inherit the primary room setting).
            do_turn_on = False
            turn_on_order = "before"  # 'before' | 'after'
            turn_on_delay_s = 1.0
            if isinstance(eid, str) and eid.startswith("climate."):
                try:
                    merges = settings.get("merges") or {}
                    primary = None
                    if isinstance(schedules, dict) and eid in schedules:
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

                    cfg_map = settings.get("turn_on") or {}
                    cfg = cfg_map.get(primary) if isinstance(cfg_map, dict) else None
                    if isinstance(cfg, dict):
                        do_turn_on = bool(cfg.get("enabled"))
                        o = str(cfg.get("order") or "before").lower().strip()
                        turn_on_order = "after" if o == "after" else "before"
                    # Allow optional global delay override (seconds)
                    try:
                        v = float(settings.get("turn_on_delay_s", turn_on_delay_s))
                        # Keep it short and safe
                        turn_on_delay_s = max(0.0, min(5.0, v))
                    except Exception:
                        turn_on_delay_s = 1.0
                except Exception:
                    do_turn_on = False

            async def _turn_on_and_delay() -> None:
                if not (do_turn_on and isinstance(eid, str) and eid.startswith("climate.")):
                    return
                try:
                    await self.hass.services.async_call("climate", "turn_on", {"entity_id": eid}, blocking=False)
                except Exception:
                    return
                try:
                    if turn_on_delay_s:
                        await asyncio.sleep(turn_on_delay_s)
                except Exception:
                    return

            def _ha_is_f() -> bool:
                try:
                    u = self.hass.config.units.temperature_unit
                    return u == UnitOfTemperature.FAHRENHEIT or "F" in str(u).upper()
                except Exception:
                    return False

            ha_is_f = _ha_is_f()

            def _to_ha_from_c(val_c: float) -> float:
                return _c_to_f(val_c) if ha_is_f else float(val_c)

            def _to_c_from_ha(val_ha: float) -> float:
                return _f_to_c(val_ha) if ha_is_f else float(val_ha)

            def _store_to_c(val: float) -> float:
                v = float(val)
                su = str((settings or {}).get("storage_temp_unit") or "").upper().strip()
                if su == "F":
                    return _f_to_c(v)
                if su == "C":
                    return v
                # Legacy heuristic: only treat as °F when display unit is F *and* values look like °F.
                legacy_u = str((settings or {}).get("temp_unit") or "").upper().strip()
                if legacy_u == "F" and v > 45.0:
                    return _f_to_c(v)
                return v

            desired_c = _store_to_c(desired)

            st = self.hass.states.get(eid)
            if not st:
                return False
            # input_number support: set value directly (no HVAC modes/presets)
            if isinstance(eid, str) and eid.startswith("input_number."):
                attrs = st.attributes or {}
                min_v = attrs.get("min")
                max_v = attrs.get("max")

                # input_number min/max are in HA's unit system
                val = float(_to_ha_from_c(desired_c))
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

            min_c = None
            max_c = None
            try:
                if isinstance(min_t, (int, float)):
                    min_c = _to_c_from_ha(float(min_t))
                if isinstance(max_t, (int, float)):
                    max_c = _to_c_from_ha(float(max_t))
            except Exception:
                min_c = None
                max_c = None

            def _clamp_c(val_c: float) -> float:
                try:
                    if isinstance(min_c, (int, float)):
                        val_c = max(val_c, float(min_c))
                    if isinstance(max_c, (int, float)):
                        val_c = min(val_c, float(max_c))
                except Exception:
                    pass
                return val_c

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
                low_c = _clamp_c(desired_c - half)
                high_c = _clamp_c(desired_c + half)
                if high_c <= low_c:
                    # Ensure valid ordering
                    high_c = min(_clamp_c(low_c + 0.5), _clamp_c(desired_c + 0.5))
                data = {
                    "entity_id": eid,
                    "target_temp_low": float(_to_ha_from_c(low_c)),
                    "target_temp_high": float(_to_ha_from_c(high_c)),
                }
                if turn_on_order == "before":
                    await _turn_on_and_delay()
                await self.hass.services.async_call("climate", "set_temperature", data, blocking=False)
                if turn_on_order == "after":
                    # Delay should be between set_temp and turn_on
                    try:
                        if turn_on_delay_s:
                            await asyncio.sleep(turn_on_delay_s)
                    except Exception:
                        pass
                    try:
                        if do_turn_on:
                            await self.hass.services.async_call("climate", "turn_on", {"entity_id": eid}, blocking=False)
                    except Exception:
                        pass
                return True

            # Fallback: single temperature
            data = {"entity_id": eid, "temperature": float(_to_ha_from_c(_clamp_c(desired_c)))}
            if turn_on_order == "before":
                await _turn_on_and_delay()
            await self.hass.services.async_call("climate", "set_temperature", data, blocking=False)
            if turn_on_order == "after":
                try:
                    if turn_on_delay_s:
                        await asyncio.sleep(turn_on_delay_s)
                except Exception:
                    pass
                try:
                    if do_turn_on:
                        await self.hass.services.async_call("climate", "turn_on", {"entity_id": eid}, blocking=False)
                except Exception:
                    pass
            return True
        except Exception:
            return False


class MultiInstanceAutoApplyManager:
    """Run auto-apply/boiler control for all known instance_id namespaces.

    This makes the card's "Configuration ID" feature work fully in the background:
    each instance gets its own timers/watchers and runs independently.
    """

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._managers: dict[str, AutoApplyManager] = {}
        self._unsub = None

    def get(self, instance_id: str) -> AutoApplyManager | None:
        try:
            return self._managers.get(_norm_instance_id(instance_id))
        except Exception:
            return None

    async def async_start(self) -> None:
        await self._sync()

        @callback
        def _on_update():
            # New instances can appear via set_store; ensure they get background managers.
            self.hass.async_create_task(self._sync())

        async_dispatcher_connect(self.hass, SIGNAL_UPDATED, _on_update)

    async def _sync(self) -> None:
        try:
            _ensure_instances_loaded(self.hass)
            d = self.hass.data.get(DOMAIN, {})
            instances = d.get("instances") if isinstance(d.get("instances"), dict) else {}
            for iid in (instances.keys() if isinstance(instances, dict) else []):
                try:
                    iidn = _norm_instance_id(iid)
                    if iidn in self._managers:
                        continue
                    mgr = AutoApplyManager(self.hass, instance_id=iidn)
                    self._managers[iidn] = mgr
                    await mgr.async_start()
                except Exception:
                    continue
            # Expose for other components
            d["instance_managers"] = self._managers
        except Exception:
            return


class MultiInstanceOpenWindowManager:
    """Run Open Window Detection independently per instance."""

    def __init__(self, hass: HomeAssistant, apply_multi: MultiInstanceAutoApplyManager):
        self.hass = hass
        self._apply_multi = apply_multi
        self._managers: dict[str, OpenWindowManager] = {}
        self._unsub = None

    def get(self, instance_id: str) -> 'OpenWindowManager' | None:
        try:
            return self._managers.get(_norm_instance_id(instance_id))
        except Exception:
            return None

    async def async_start(self) -> None:
        await self._sync()

        @callback
        def _on_update():
            self.hass.async_create_task(self._sync())

        async_dispatcher_connect(self.hass, SIGNAL_UPDATED, _on_update)

    async def _sync(self) -> None:
        try:
            _ensure_instances_loaded(self.hass)
            d = self.hass.data.get(DOMAIN, {})
            instances = d.get("instances") if isinstance(d.get("instances"), dict) else {}
            for iid in (instances.keys() if isinstance(instances, dict) else []):
                try:
                    iidn = _norm_instance_id(iid)
                    if iidn in self._managers:
                        continue
                    mgr = self._apply_multi.get(iidn)
                    if not mgr:
                        continue
                    owd = OpenWindowManager(self.hass, apply_mgr=mgr, instance_id=iidn)
                    self._managers[iidn] = owd
                    await owd.async_start()
                except Exception:
                    continue
            d["open_window_managers"] = self._managers
        except Exception:
            return


class OpenWindowManager:
    """Background Open Window Detection.

    Uses settings.open_window from the shared store.
    """

    def __init__(self, hass: HomeAssistant, apply_mgr: AutoApplyManager, instance_id: str = "default"):
        self.hass = hass
        self._mgr = apply_mgr
        self._instance_id = _norm_instance_id(instance_id)
        self._unsub = None
        self._room_sensors: dict[str, list[str]] = {}
        self._open_delay_s: int = 0
        self._close_delay_s: int = 0
        self._room_tasks: dict[tuple[str, str], asyncio.Task] = {}  # (room, kind)
        self._suspended_entities: set[str] = set()

    def is_entity_suspended(self, eid: str) -> bool:
        try:
            return isinstance(eid, str) and eid in self._suspended_entities
        except Exception:
            return False

    def _settings(self) -> dict:
        try:
            d = self.hass.data.get(DOMAIN, {}) or {}
            insts = d.get("instances") if isinstance(d.get("instances"), dict) else None
            if isinstance(insts, dict):
                inst = insts.get(self._instance_id)
                if isinstance(inst, dict):
                    s = inst.get("settings")
                    if isinstance(s, dict):
                        return s
        except Exception:
            pass
        return (self.hass.data.get(DOMAIN, {}) or {}).get("settings", {}) or {}

    def _enabled(self, settings: dict | None = None) -> bool:
        try:
            settings = settings if isinstance(settings, dict) else self._settings()
            ow = settings.get("open_window") or {}
            if not (isinstance(ow, dict) and bool(ow.get("enabled"))):
                return False
            # Only run when background control is active
            if not bool(settings.get("auto_apply_enabled")):
                return False
            # Respect pause via binary sensor
            try:
                if bool(settings.get("pause_sensor_enabled")):
                    eid = str(settings.get("pause_sensor_entity") or "").strip()
                    if eid and eid.startswith("binary_sensor."):
                        st = self.hass.states.get(eid)
                        if str(st.state if st else "").lower().strip() == "on":
                            return False
            except Exception:
                pass
            # Respect pause gates
            if bool(settings.get("pause_indef")):
                return False
            pu = settings.get("pause_until_ms")
            if isinstance(pu, (int, float)):
                now_ms = dt_util.utcnow().timestamp() * 1000.0
                if float(pu) > now_ms:
                    return False
            return True
        except Exception:
            return False

    @staticmethod
    def _clamp_int(v, default: int, lo: int, hi: int) -> int:
        try:
            n = int(float(v))
        except Exception:
            n = int(default)
        return max(lo, min(hi, n))

    @staticmethod
    def _is_open_state(state: str) -> bool:
        s = str(state or "").strip().lower()
        return s in ("on", "open", "true", "1")

    def _sensor_open(self, eid: str) -> bool:
        try:
            st = self.hass.states.get(eid)
            return self._is_open_state(st.state if st else "")
        except Exception:
            return False

    def _entities_for_room(self, room_eid: str, settings: dict) -> list[str]:
        out = [room_eid]
        try:
            merges = settings.get("merges") or {}
            linked = merges.get(room_eid) or []
            if isinstance(linked, list):
                out.extend([x for x in linked if isinstance(x, str) and x])
        except Exception:
            pass
        seen = set()
        uniq = []
        for e in out:
            if e in seen:
                continue
            seen.add(e)
            uniq.append(e)
        return uniq

    def _cancel_task(self, room: str, kind: str) -> None:
        t = self._room_tasks.pop((room, kind), None)
        if t:
            try:
                t.cancel()
            except Exception:
                pass

    async def async_start(self) -> None:
        @callback
        def _on_update():
            self.hass.async_create_task(self._rebuild())
        async_dispatcher_connect(self.hass, SIGNAL_UPDATED, _on_update)
        await self._rebuild()

    async def _rebuild(self) -> None:
        # Unsubscribe old watcher
        try:
            if self._unsub:
                self._unsub()
                self._unsub = None
        except Exception:
            self._unsub = None

        # Cancel pending tasks
        for k, t in list(self._room_tasks.items()):
            try:
                t.cancel()
            except Exception:
                pass
        self._room_tasks.clear()

        settings = self._settings()
        ow = settings.get("open_window") or {}
        self._open_delay_s = self._clamp_int(ow.get("open_delay_min"), 2, 0, 1440) * 60
        self._close_delay_s = self._clamp_int(ow.get("close_delay_min"), 5, 0, 1440) * 60

        sensors_map = ow.get("sensors") if isinstance(ow, dict) else None
        room_sensors: dict[str, list[str]] = {}
        flat: list[str] = []
        if isinstance(sensors_map, dict):
            for room, arr in sensors_map.items():
                if not isinstance(room, str) or not room:
                    continue
                if not isinstance(arr, list) or not arr:
                    continue
                clean = [x for x in arr if isinstance(x, str) and x.startswith("binary_sensor.")]
                if not clean:
                    continue
                room_sensors[room] = clean
                flat.extend(clean)

        self._room_sensors = room_sensors

        # If disabled, resume anything we previously turned off
        if not self._enabled(settings):
            await self._resume_all(settings)
            return

        flat = sorted(set(flat))
        if flat:
            @callback
            def _ch(_event):
                self.hass.async_create_task(self._evaluate_all())
            self._unsub = async_track_state_change_event(self.hass, flat, _ch)

        await self._evaluate_all()

    async def _evaluate_all(self) -> None:
        try:
            settings = self._settings()
            if not self._enabled(settings):
                await self._resume_all(settings)
                return
            for room in list(self._room_sensors.keys()):
                await self._evaluate_room(room, settings)
        except Exception:
            return

    async def _evaluate_room(self, room: str, settings: dict) -> None:
        sensors = self._room_sensors.get(room) or []
        if not sensors:
            await self._resume_room(room, settings)
            return

        open_now = any(self._sensor_open(s) for s in sensors)
        is_susp = any(e in self._suspended_entities for e in self._entities_for_room(room, settings))

        if open_now:
            self._cancel_task(room, "resume")
            if not is_susp and (room, "suspend") not in self._room_tasks:
                self._room_tasks[(room, "suspend")] = self.hass.async_create_task(self._delayed_suspend(room))
        else:
            self._cancel_task(room, "suspend")
            if is_susp and (room, "resume") not in self._room_tasks:
                self._room_tasks[(room, "resume")] = self.hass.async_create_task(self._delayed_resume(room))

    async def _delayed_suspend(self, room: str) -> None:
        try:
            if self._open_delay_s:
                await asyncio.sleep(self._open_delay_s)
            settings = self._settings()
            if not self._enabled(settings):
                return
            sensors = self._room_sensors.get(room) or []
            if sensors and any(self._sensor_open(s) for s in sensors):
                await self._suspend_room(room, settings)
        except asyncio.CancelledError:
            return
        except Exception:
            return

    async def _delayed_resume(self, room: str) -> None:
        try:
            if self._close_delay_s:
                await asyncio.sleep(self._close_delay_s)
            settings = self._settings()
            if not self._enabled(settings):
                return
            sensors = self._room_sensors.get(room) or []
            if sensors and any(self._sensor_open(s) for s in sensors):
                return
            await self._resume_room(room, settings)
        except asyncio.CancelledError:
            return
        except Exception:
            return

    async def _suspend_room(self, room: str, settings: dict) -> None:
        try:
            entities = self._entities_for_room(room, settings)
            for eid in entities:
                self._suspended_entities.add(eid)
            for eid in entities:
                if isinstance(eid, str) and eid.startswith("climate."):
                    await self.hass.services.async_call("climate", "turn_off", {"entity_id": eid}, blocking=False)
        except Exception:
            return

    async def _resume_room(self, room: str, settings: dict) -> None:
        try:
            entities = self._entities_for_room(room, settings)
            for eid in entities:
                try:
                    self._suspended_entities.discard(eid)
                except Exception:
                    pass
            for eid in entities:
                if isinstance(eid, str) and eid.startswith("climate."):
                    await self.hass.services.async_call("climate", "turn_on", {"entity_id": eid}, blocking=False)
                try:
                    await self._mgr.async_apply_entity_now(eid, reconcile=True)
                except Exception:
                    pass
        except Exception:
            return

    async def _resume_all(self, settings: dict) -> None:
        try:
            if not self._suspended_entities:
                return
            to_resume = list(self._suspended_entities)
            self._suspended_entities.clear()
            for eid in to_resume:
                if isinstance(eid, str) and eid.startswith("climate."):
                    await self.hass.services.async_call("climate", "turn_on", {"entity_id": eid}, blocking=False)
                try:
                    await self._mgr.async_apply_entity_now(eid, reconcile=True)
                except Exception:
                    pass
        except Exception:
            return


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
