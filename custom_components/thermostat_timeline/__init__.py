from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.dispatcher import async_dispatcher_send, async_dispatcher_connect
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_state_change_event
from homeassistant.util import dt as dt_util
from datetime import timedelta
import json
import os
import threading

from .const import DOMAIN, STORAGE_KEY, STORAGE_VERSION, SIGNAL_UPDATED, BACKUP_STORAGE_KEY, SIGNAL_DECISION
from typing import Callable
from homeassistant.helpers import entity_registry as er

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_NAMES: tuple[str, ...] = (
    "set_store",
    "backup_now",
    "restore_now",
    "patch_entity",
    "clear",
    "early_start_set",
)


_LOGGER = logging.getLogger(__name__)


async def _async_debug_write(hass: HomeAssistant, payload: dict) -> None:
    """Persist debug payload outside the event loop via executor."""
    base = hass.config.path(os.path.join("custom_components", DOMAIN))

    def _write(base_path: str, body: dict) -> None:
        os.makedirs(base_path, exist_ok=True)
        path = os.path.join(base_path, "debug.log")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(body, ensure_ascii=True) + "\n")

    await hass.async_add_executor_job(_write, base, payload)


def _debug_log(hass: HomeAssistant, message: str, context: dict | None = None) -> None:
    """Queue debug logging without blocking the Home Assistant event loop."""
    try:
        payload = {
            "ts": dt_util.utcnow().isoformat(),
            "msg": message,
        }
        if context is not None:
            payload["ctx"] = context

        async def _ensure_task() -> None:
            try:
                await _async_debug_write(hass, payload)
            except Exception:
                pass

        hass.async_create_task(_ensure_task())
    except RuntimeError:
        # Loop not running (reload/startup). Write via background thread to avoid blocking.
        def _fallback_write() -> None:
            try:
                base = hass.config.path(os.path.join("custom_components", DOMAIN))
                os.makedirs(base, exist_ok=True)
                path = os.path.join(base, "debug.log")
                with open(path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
            except Exception:
                pass

        threading.Thread(target=_fallback_write, name="thermostat_debug_log", daemon=True).start()
    except Exception:
        # Never let logging attempts take the system down
        pass

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

    def _coerce_int(val):
        try:
            return int(round(float(val)))
        except (TypeError, ValueError):
            return val

    def _harmonize_ml_settings(settings_dict):
        if not isinstance(settings_dict, dict):
            return {}
        synced = dict(settings_dict)
        pairs = (
            ("early_start_enabled", "ml_enabled", lambda v: bool(v)),
            ("early_start_training_interval_min", "ml_training_interval_min", _coerce_int),
            ("early_start_min_cycles", "ml_min_samples", _coerce_int),
            ("early_start_history_days", "ml_history_days", _coerce_int),
            ("early_start_safety_margin_min", "ml_safety_margin_min", _coerce_int),
            ("early_start_weather_entity", "ml_weather_entity", lambda v: str(v or "")),
        )
        for primary, mirror, caster in pairs:
            has_primary = primary in synced
            has_mirror = mirror in synced
            if has_mirror and not has_primary:
                try:
                    synced[primary] = caster(synced[mirror])
                except Exception:
                    synced[primary] = synced[mirror]
            elif has_primary and not has_mirror:
                try:
                    synced[mirror] = caster(synced[primary])
                except Exception:
                    synced[mirror] = synced[primary]
        return synced

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["store"] = store
    hass.data[DOMAIN]["schedules"] = data.get("schedules", {})
    hass.data[DOMAIN]["settings"] = _harmonize_ml_settings(data.get("settings", {}))
    hass.data[DOMAIN]["version"] = int(data.get("version", 1))
    # ML (Early Start) persisted data (optional)
    hass.data[DOMAIN]["ml"] = data.get("ml", {}) or {}
    hass.data[DOMAIN]["ml_per_room"] = data.get("ml_per_room", {}) or {}
    # Runtime status mirrors (not necessarily persisted)
    hass.data[DOMAIN]["ml_status"] = data.get("ml_status")
    hass.data[DOMAIN]["ml_enabled"] = data.get("ml_enabled")
    hass.data[DOMAIN]["ml_trained_cycles"] = data.get("ml_trained_cycles")
    hass.data[DOMAIN]["ml_confidence_pct"] = data.get("ml_confidence_pct")
    hass.data[DOMAIN]["ml_last_trained_iso"] = data.get("ml_last_trained_iso")
    # Backup payloads
    hass.data[DOMAIN]["backup_schedules"] = backup_data.get("schedules", {})
    hass.data[DOMAIN]["backup_settings"] = backup_data.get("settings", {})
    hass.data[DOMAIN]["backup_version"] = int(backup_data.get("version", 1))
    hass.data[DOMAIN]["backup_last_ts"] = backup_data.get("last_backup_ts")
    # Track whether the backup is partial and which sections it includes
    hass.data[DOMAIN]["backup_partial_flags"] = backup_data.get("partial_flags")

    async def _save_and_broadcast():
        _debug_log(hass, "save_and_broadcast:start", {"version": hass.data[DOMAIN].get("version")})
        # Broadcast first so UI updates immediately
        try:
            async_dispatcher_send(hass, SIGNAL_UPDATED)
            _debug_log(hass, "save_and_broadcast:dispatch_ok", None)
        except Exception as err:
            _debug_log(hass, "save_and_broadcast:dispatch_failed", {"error": repr(err)})
            raise
        # Proactively nudge the overview sensor to update its state in HA
        try:
            ent_reg = er.async_get(hass)
            sensor_eid = ent_reg.async_get_entity_id("sensor", DOMAIN, "thermostat_timeline_overview")
            if sensor_eid:
                _debug_log(hass, "save_and_broadcast:update_entity", {"entity_id": sensor_eid})
                await hass.services.async_call("homeassistant", "update_entity", {"entity_id": sensor_eid}, blocking=False)
                _debug_log(hass, "save_and_broadcast:update_dispatched", {"entity_id": sensor_eid})
        except Exception:
            pass
        try:
            await store.async_save({
                "schedules": hass.data[DOMAIN]["schedules"],
                "settings": hass.data[DOMAIN].get("settings", {}),
                "version": hass.data[DOMAIN]["version"],
                # Persist ML related data
                "ml": hass.data[DOMAIN].get("ml", {}),
                "ml_per_room": hass.data[DOMAIN].get("ml_per_room", {}),
                "ml_status": hass.data[DOMAIN].get("ml_status"),
                "ml_enabled": hass.data[DOMAIN].get("ml_enabled"),
                "ml_trained_cycles": hass.data[DOMAIN].get("ml_trained_cycles"),
                "ml_confidence_pct": hass.data[DOMAIN].get("ml_confidence_pct"),
                "ml_last_trained_iso": hass.data[DOMAIN].get("ml_last_trained_iso"),
            })
            _debug_log(hass, "save_and_broadcast:store_saved", None)
        except Exception as err:
            # Storage failure shouldn't block UI update
            _LOGGER.warning("%s: failed to save store after broadcast", DOMAIN)
            _debug_log(hass, "save_and_broadcast:store_failed", {"error": repr(err)})

    async def _save_backup():
        # Broadcast so backup sensor updates
        async_dispatcher_send(hass, SIGNAL_UPDATED)
        try:
            await backup_store.async_save({
                "schedules": hass.data[DOMAIN].get("backup_schedules", {}),
                "settings": hass.data[DOMAIN].get("backup_settings", {}),
                "version": hass.data[DOMAIN].get("backup_version", 1),
                "last_backup_ts": hass.data[DOMAIN].get("backup_last_ts"),
                "partial_flags": hass.data[DOMAIN].get("backup_partial_flags"),
            })
            # Proactively nudge the backup sensor to update its state in HA
            try:
                ent_reg = er.async_get(hass)
                sensor_eid = ent_reg.async_get_entity_id("sensor", DOMAIN, "thermostat_timeline_backup")
                if sensor_eid:
                    await hass.services.async_call("homeassistant", "update_entity", {"entity_id": sensor_eid}, blocking=False)
            except Exception:
                pass
        except Exception:
            _LOGGER.warning("%s: failed to save backup store", DOMAIN)

    async def set_store(call: ServiceCall):
        try:
            await _set_store_internal(call)
        except Exception as err:
            _debug_log(hass, "set_store:exception", {"error": repr(err)})
            raise

    async def _set_store_internal(call: ServiceCall):
        force = bool(call.data.get("force"))
        cur_sched = hass.data[DOMAIN].get("schedules", {})
        cur_set = hass.data[DOMAIN].get("settings", {}) or {}
        cur_set_norm = _harmonize_ml_settings(cur_set)
        changed = False
        try:
            sched_payload = call.data.get("schedules")
            settings_payload = call.data.get("settings")
            ctx = {
                "force": force,
                "sched_rooms": len(sched_payload or {}) if isinstance(sched_payload, dict) else 0,
                "settings_keys": sorted((settings_payload or {}).keys())[:25]
            }
            _debug_log(hass, "set_store:start", ctx)
        except Exception:
            pass
        # Debug: record last received settings payload (for troubleshooting)
        try:
            if "settings" in call.data and isinstance(call.data.get("settings"), dict):
                preview = _harmonize_ml_settings(call.data.get("settings"))
                hass.data[DOMAIN]["debug_last_settings"] = {
                    "ts": dt_util.utcnow().isoformat(),
                    "payload": preview
                }
                # Track a short history of early_start_enabled values to diagnose overwrite
                try:
                    hist = hass.data[DOMAIN].get("debug_settings_history") or []
                    es_val = preview.get("early_start_enabled")
                    hist.append({
                        "ts": hass.data[DOMAIN]["debug_last_settings"]["ts"],
                        "early_start_enabled": es_val
                    })
                    hass.data[DOMAIN]["debug_settings_history"] = hist[-20:]
                except Exception:
                    pass
        except Exception:
            pass
        if "schedules" in call.data:
            schedules = call.data.get("schedules")
            if not isinstance(schedules, dict):
                _LOGGER.warning("%s.set_store: schedules must be an object when provided", DOMAIN)
                return
            if schedules != cur_sched:
                hass.data[DOMAIN]["schedules"] = schedules
                changed = True
        # Optional settings payload (merge keys instead of full replace)
        if "settings" in call.data:
            incoming = call.data.get("settings")
            if isinstance(incoming, dict):
                incoming = _harmonize_ml_settings(incoming)
                merged = dict(cur_set_norm)
                for k, v in incoming.items():
                    merged[k] = v
                merged = _harmonize_ml_settings(merged)
                if merged != cur_set_norm:
                    hass.data[DOMAIN]["settings"] = merged
                    changed = True
        if not changed and not force:
            # No-op: nothing changed and not forced; avoid spurious version bump/apply
            _debug_log(hass, "set_store:noop", {"version": hass.data[DOMAIN].get("version")})
            return
        hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN]["version"]) + 1
        try:
            await _save_and_broadcast()
            _debug_log(hass, "set_store:save_ok", {"version": hass.data[DOMAIN]["version"]})
        except Exception as err:
            _debug_log(hass, "set_store:save_failed", {"error": repr(err)})
            raise

    async def backup_now(call: ServiceCall):
        """Create a backup of the current store.

        Supports selective sections via optional booleans:
          - main: base daily schedules (defaultTemp, blocks, profiles)
          - weekday: weekday schedules (weekly, weekly_modes)
          - presence: presence schedules (advanced away combos per room)
          - settings: editor/global settings (except color ranges)
          - holiday: holiday schedules per room
          - colors: color ranges and color mode
        If no flags are provided, a full backup is made (backwards compatible).
        """
        # Determine requested sections (default: all True)
        want_main = bool(call.data.get("main", True))
        want_week = bool(call.data.get("weekday", True))
        want_presence = bool(call.data.get("presence", True))
        want_settings = bool(call.data.get("settings", True))
        want_holiday = bool(call.data.get("holiday", True))
        want_colors = bool(call.data.get("colors", True))

        src_sched = hass.data[DOMAIN].get("schedules", {}) or {}
        src_set = hass.data[DOMAIN].get("settings", {}) or {}

        # Build selective schedules
        out_sched: dict = {}
        if any([want_main, want_week, want_presence, want_holiday]):
            for eid, row in (src_sched.items() if isinstance(src_sched, dict) else []):
                if not isinstance(row, dict):
                    continue
                new_row = {}
                if want_main:
                    if "defaultTemp" in row:
                        new_row["defaultTemp"] = row["defaultTemp"]
                    if "blocks" in row:
                        new_row["blocks"] = row["blocks"]
                    # Include profiles data in main
                    if "profiles" in row:
                        new_row["profiles"] = row["profiles"]
                    if "activeProfile" in row:
                        new_row["activeProfile"] = row["activeProfile"]
                if want_week:
                    if "weekly" in row:
                        new_row["weekly"] = row["weekly"]
                    if "weekly_modes" in row:
                        new_row["weekly_modes"] = row["weekly_modes"]
                if want_presence and "presence" in row:
                    new_row["presence"] = row["presence"]
                if want_holiday and "holiday" in row:
                    new_row["holiday"] = row["holiday"]
                if new_row:
                    out_sched[eid] = new_row

        # Build selective settings
        out_set: dict = {}
        if want_settings:
            # Copy all settings except colors (handled below)
            for k, v in (src_set.items() if isinstance(src_set, dict) else []):
                if k in ("color_ranges", "color_global"):
                    continue
                out_set[k] = v
        if want_colors:
            if "color_ranges" in src_set:
                out_set["color_ranges"] = src_set["color_ranges"]
            if "color_global" in src_set:
                out_set["color_global"] = src_set["color_global"]

        # If no flags explicitly provided (legacy), store a full backup
        legacy_full = (
            "main" not in call.data
            and "weekday" not in call.data
            and "presence" not in call.data
            and "settings" not in call.data
            and "holiday" not in call.data
            and "colors" not in call.data
        )

        if legacy_full:
            hass.data[DOMAIN]["backup_schedules"] = dict(src_sched)
            hass.data[DOMAIN]["backup_settings"] = dict(src_set)
            hass.data[DOMAIN]["backup_partial_flags"] = None
        else:
            hass.data[DOMAIN]["backup_schedules"] = out_sched
            hass.data[DOMAIN]["backup_settings"] = out_set
            hass.data[DOMAIN]["backup_partial_flags"] = {
                "main": want_main,
                "weekday": want_week,
                "presence": want_presence,
                "settings": want_settings,
                "holiday": want_holiday,
                "colors": want_colors,
            }

        hass.data[DOMAIN]["backup_version"] = int(hass.data[DOMAIN].get("backup_version", 1)) + 1
        try:
            hass.data[DOMAIN]["backup_last_ts"] = dt_util.utcnow().isoformat()
        except Exception:
            pass
        await _save_backup()

    async def restore_now(call: ServiceCall):
        """Restore from backup.

        Optional mode: 'replace' (default) or 'merge'.
        Optional section flags like backup_now: main, weekday, presence, settings, holiday, colors.
        If backup contains partial_flags and no mode is provided, merge is used by default.
        """
        mode = str(call.data.get("mode", "")).lower().strip()
        flags = hass.data[DOMAIN].get("backup_partial_flags") or {}
        # Allow caller to override flags
        for key in ("main","weekday","presence","settings","holiday","colors"):
            if key in call.data:
                flags[key] = bool(call.data.get(key))

        backup_sched = hass.data[DOMAIN].get("backup_schedules", {}) or {}
        backup_set = hass.data[DOMAIN].get("backup_settings", {}) or {}

        # If no flags and mode empty, keep legacy behavior (replace)
        if not flags and mode not in ("merge", "replace"):
            hass.data[DOMAIN]["schedules"] = dict(backup_sched)
            hass.data[DOMAIN]["settings"] = _harmonize_ml_settings(dict(backup_set))
            hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN].get("version", 1)) + 1
            await _save_and_broadcast()
            return

        do_merge = (mode == "merge") or (not mode and bool(flags))
        if not do_merge:
            # Explicit replace
            hass.data[DOMAIN]["schedules"] = dict(backup_sched)
            hass.data[DOMAIN]["settings"] = _harmonize_ml_settings(dict(backup_set))
            hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN].get("version", 1)) + 1
            await _save_and_broadcast()
            return

        # Merge: only update selected sections/keys
        cur_sched = hass.data[DOMAIN].get("schedules", {}) or {}
        cur_set = hass.data[DOMAIN].get("settings", {}) or {}

        want_main = bool(flags.get("main", False))
        want_week = bool(flags.get("weekday", False))
        want_presence = bool(flags.get("presence", False))
        want_settings = bool(flags.get("settings", False))
        want_holiday = bool(flags.get("holiday", False))
        want_colors = bool(flags.get("colors", False))

        if any([want_main, want_week, want_presence, want_holiday]):
            for eid, brow in (backup_sched.items() if isinstance(backup_sched, dict) else []):
                if not isinstance(brow, dict):
                    continue
                row = dict(cur_sched.get(eid, {}))
                if want_main:
                    if "defaultTemp" in brow:
                        row["defaultTemp"] = brow["defaultTemp"]
                    if "blocks" in brow:
                        row["blocks"] = brow["blocks"]
                    if "profiles" in brow:
                        row["profiles"] = brow["profiles"]
                    if "activeProfile" in brow:
                        row["activeProfile"] = brow["activeProfile"]
                if want_week:
                    if "weekly" in brow:
                        row["weekly"] = brow["weekly"]
                    if "weekly_modes" in brow:
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

        hass.data[DOMAIN]["schedules"] = cur_sched
        hass.data[DOMAIN]["settings"] = _harmonize_ml_settings(cur_set)
        hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN].get("version", 1)) + 1
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
    hass.services.async_register(DOMAIN, "backup_now", backup_now)
    hass.services.async_register(DOMAIN, "restore_now", restore_now)
    hass.services.async_register(DOMAIN, "patch_entity", patch_entity)
    hass.services.async_register(DOMAIN, "clear", clear)
    
    async def early_start_set(call: ServiceCall):
        """Explicitly enable/disable Early Start (ML) regardless of editor behavior."""
        try:
            enabled = bool(call.data.get("enabled"))
            settings = hass.data[DOMAIN].get("settings", {})
            current_es = bool(settings.get("early_start_enabled"))
            current_ml = bool(settings.get("ml_enabled")) if "ml_enabled" in settings else current_es
            if current_es != enabled or current_ml != enabled:
                settings["early_start_enabled"] = enabled
                settings["ml_enabled"] = enabled
                hass.data[DOMAIN]["settings"] = _harmonize_ml_settings(settings)
                hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN]["version"]) + 1
                # Record in debug history
                try:
                    hist = hass.data[DOMAIN].get("debug_settings_history") or []
                    hist.append({"ts": dt_util.utcnow().isoformat(), "early_start_enabled": enabled, "source": "service"})
                    hass.data[DOMAIN]["debug_settings_history"] = hist[-20:]
                except Exception:
                    pass
                await _save_and_broadcast()
        except Exception:
            _LOGGER.warning("%s.early_start_set: failed", DOMAIN)
    hass.services.async_register(DOMAIN, "early_start_set", early_start_set)
    # no apply_now service (removed)

    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    # ---- Background Auto-Apply Manager ----
    mgr = AutoApplyManager(hass)
    hass.data[DOMAIN]["manager"] = mgr
    await mgr.async_start()
    # ---- Backup Manager (auto backup) ----
    bkm = BackupManager(hass, backup_cb=backup_now)
    hass.data[DOMAIN]["backup_manager"] = bkm
    await bkm.async_start()
    # ---- Early Start (ML) Manager ----
    esm = EarlyStartManager(hass, save_cb=_save_and_broadcast)
    hass.data[DOMAIN]["earlystart_manager"] = esm
    await esm.async_start()
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data.get(DOMAIN, {})
    mgr: AutoApplyManager | None = data.get("manager")
    bkm: BackupManager | None = data.get("backup_manager")
    esm: EarlyStartManager | None = data.get("earlystart_manager")

    for worker in (mgr, bkm, esm):
        if worker is None:
            continue
        stop = getattr(worker, "async_stop", None)
        if stop is not None:
            try:
                await stop()
            except Exception:
                _LOGGER.debug("%s: failed to stop %s during unload", DOMAIN, worker.__class__.__name__)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])

    for service in SERVICE_NAMES:
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)

    hass.data.pop(DOMAIN, None)
    return unload_ok


class AutoApplyManager:
    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._unsub_timer = None
        self._unsub_persons = None
        self._unsub_store = None
        self._last_applied = {}  # eid -> {"min": int, "temp": float}
        self._next_is_resume = False

    async def async_start(self):
        # Apply once on startup and schedule next if enabled
        await self._maybe_apply_now(force=True)
        await self._schedule_next()
        # Re-apply on store updates
        @callback
        def _on_store_update():
            self.hass.async_create_task(self._on_store_changed())
        self._unsub_store = async_dispatcher_connect(self.hass, SIGNAL_UPDATED, _on_store_update)
        # Watch person.* states if away mode is used
        self._reset_person_watch()

    async def async_stop(self):
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        if self._unsub_persons:
            self._unsub_persons()
            self._unsub_persons = None
        if self._unsub_store:
            self._unsub_store()
            self._unsub_store = None

    async def _on_store_changed(self):
        # Respect apply_on_edit toggle: only apply immediately on store changes when enabled
        _s, settings = self._get_data()
        if bool(settings.get("apply_on_edit", True)):
            await self._maybe_apply_now(force=True)
        await self._schedule_next()
        self._reset_person_watch()

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

    def _is_holiday_today(self, settings: dict) -> bool:
        """Return True if today is a holiday according to settings.

        Supports two sources:
          - calendar: an entity (binary/calendar/simple) that is 'on' on holidays
          - manual: list of ISO dates (YYYY-MM-DD) in settings.holidays_dates
        """
        try:
            if not bool(settings.get("holidays_enabled")):
                return False
            source = str(settings.get("holidays_source", "")).strip().lower()
            if source == "calendar":
                eid = settings.get("holidays_entity")
                if isinstance(eid, str) and eid:
                    st = self.hass.states.get(eid)
                    if st and str(st.state).lower() in ("on", "true", "holiday"):
                        return True
                return False
            if source == "manual":
                dates = settings.get("holidays_dates") or []
                if isinstance(dates, list) and dates:
                    today_iso = dt_util.now().date().isoformat()
                    return today_iso in dates
                return False
        except Exception:
            return False
        return False

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
        # Holiday schedule override (after presence & profiles, before weekly/default)
        try:
            if self._is_holiday_today(settings):
                h = row.get("holiday") or {}
                hb = h.get("blocks") if isinstance(h, dict) else None
                if isinstance(hb, list) and len(hb) > 0:
                    return hb
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

    async def _maybe_apply_now(self, force: bool = False, boundary_only: bool = False):
        if not self._auto_apply_enabled():
            return
        schedules, settings = self._get_data()
        merges = settings.get("merges") or {}
        now_min = self._now_min()
        targets = self._all_targets(schedules, merges)
        for eid in targets:
            # only climate.* entities
            if not isinstance(eid, str) or not eid.startswith("climate."):
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
            # skip if no change
            last = self._last_applied.get(eid) or {}
            if (not force) and last.get("min") == now_min and abs(float(last.get("temp", 9999)) - float(desired)) < 0.05:
                continue
            # If current equals desired, just update cache
            st = self.hass.states.get(eid)
            cur = None
            for attr in ("temperature","target_temperature","target_temp"):
                v = st.attributes.get(attr)
                if isinstance(v, (int,float)):
                    cur = float(v); break
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
        # If this wake was caused by pause expiry, apply immediately across all entities
        boundary_only = (not self._next_is_resume)
        # Before applying, compute which primaries are at boundary now for signaling
        at_primaries = []
        try:
            schedules, settings = self._get_data()
            now_min = self._now_min()
            for primary, row in (schedules.items() if isinstance(schedules, dict) else []):
                try:
                    if self._entity_has_boundary_now(row, now_min):
                        at_primaries.append(primary)
                except Exception:
                    continue
        except Exception:
            pass
        await self._maybe_apply_now(force=True, boundary_only=boundary_only)
        # Emit decision signal so EarlyStartManager can start measurement at same trigger
        try:
            async_dispatcher_send(self.hass, SIGNAL_DECISION, {"type": "boundary", "primaries": at_primaries})
        except Exception:
            pass
        self._next_is_resume = False
        await self._schedule_next()

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


class EarlyStartManager:
    """Collects heating cycle samples and computes simple preheat predictions.

    This manager runs in the background and does NOT send setpoints; it only
    observes climate state, ambient/target temps and updates ML attributes.
    """
    def __init__(self, hass: HomeAssistant, save_cb):
        self.hass = hass
        self._save_cb = save_cb
        self._unsub_sample = None
        self._unsub_retrain = None
        self._unsub_call = None
        self._unsub_persons = None
        self._unsub_store = None
        self._unsub_decision = None
        self._preheat_timers: dict[str, Callable[[], None]] = {}
        self._active: dict[str, dict] = {}  # primary -> {start, target_c, ambient_start_c, outside_start_c, samples: [(iso, c)]}
        self._away_active_prev: bool | None = None
        self._handling_store_signal = False  # prevents recursive SIGNAL_UPDATED storms
        # Debug fields (transient, not persisted): last begin attempt & last cancel
        d = self.hass.data.setdefault(DOMAIN, {})
        d.setdefault("ml_debug_last_begin_attempt", None)
        d.setdefault("ml_debug_last_cancel_reason", None)

    def _data(self):
        d = self.hass.data.get(DOMAIN, {})
        schedules = d.get("schedules", {}) or {}
        settings = d.get("settings", {}) or {}
        ml = d.get("ml", {}) or {}
        ml_per_room = d.get("ml_per_room", {}) or {}
        return schedules, settings, ml, ml_per_room

    def _settings(self):
        return (self.hass.data.get(DOMAIN, {}) or {}).get("settings", {}) or {}

    def _merges(self):
        s = self._settings(); return (s.get("merges") or {}) if isinstance(s.get("merges"), dict) else {}

    def _temp_sensors(self):
        s = self._settings(); return (s.get("temp_sensors") or {}) if isinstance(s.get("temp_sensors"), dict) else {}

    def _min_samples(self) -> int:
        try: return max(1, int(self._settings().get("early_start_min_cycles", 10)))
        except Exception: return 10

    def _hist_days(self) -> int:
        try: return max(1, int(self._settings().get("early_start_history_days", 90)))
        except Exception: return 90

    def _enabled(self) -> bool:
        try: return bool(self._settings().get("early_start_enabled", False))
        except Exception: return False

    def _weather_eid(self) -> str | None:
        try:
            v = str(self._settings().get("early_start_weather_entity") or "").strip()
            return v or None
        except Exception:
            return None

    def _away_cfg(self) -> dict:
        try:
            s = self._settings()
            return (s.get("away") or {}) if isinstance(s.get("away"), dict) else {}
        except Exception:
            return {}

    def _away_persons(self) -> list[str]:
        try:
            a = self._away_cfg()
            ppl = a.get("persons")
            return [str(x) for x in (ppl or [])] if isinstance(ppl, list) else []
        except Exception:
            return []

    def _adv_presence_active(self) -> bool:
        """Return True if advanced presence combo is currently active globally."""
        try:
            a = self._away_cfg()
            if not bool(a.get("advanced_enabled")):
                return False
            persons = self._away_persons()
            if not persons:
                return False
            home, not_home = [], []
            for p in persons:
                st = self.hass.states.get(p)
                if st and str(st.state).lower() == "home":
                    home.append(p)
                else:
                    not_home.append(p)
            key = self._presence_combo_key(home, not_home)
            combos = (a.get("combos") or {}) if isinstance(a, dict) else {}
            meta = combos.get(key)
            return bool(meta and meta.get("enabled"))
        except Exception:
            return False

    def _simple_away_active(self) -> bool:
        try:
            a = self._away_cfg()
            if not bool(a.get("enabled")):
                return False
            persons = self._away_persons()
            if not persons:
                return False
            # active when none are home
            for p in persons:
                st = self.hass.states.get(p)
                if st and str(st.state).lower() == "home":
                    return False
            return True
        except Exception:
            return False

    def _away_active(self) -> bool:
        # Advanced presence takes precedence
        if self._adv_presence_active():
            return True
        return self._simple_away_active()

    def _presence_combo_key(self, home: list[str], away: list[str]) -> str:
        try:
            h = sorted([str(x) for x in (home or [])])
            a = sorted([str(x) for x in (away or [])])
            return f"H:{','.join(h)}|A:{','.join(a)}"
        except Exception:
            return "H:|A:"

    def _now_iso(self) -> str:
        try: return dt_util.utcnow().isoformat()
        except Exception: return ""

    def _ha_is_f(self) -> bool:
        try:
            u = str(self.hass.config.units.temperature or "")
            return u.upper().find("F") >= 0
        except Exception:
            return False

    def _to_c(self, v: float) -> float:
        try:
            return (v - 32.0) * 5.0 / 9.0 if self._ha_is_f() else v
        except Exception:
            return v

    def _ambient_for_entity_c(self, eid: str) -> float | None:
        # Prefer temp_sensors mapping, else climate.current_temperature
        try:
            st = self.hass.states.get(eid)
            if not st:
                return None
            # If mapped sensor exists for primary EID, use it (handled by caller for primary only)
            v = st.attributes.get("current_temperature")
            if isinstance(v, (int, float)):
                return float(self._to_c(float(v)))
        except Exception:
            pass
        return None

    def _room_ambient_c(self, primary: str) -> float | None:
        vals = []
        # Sensor override on primary
        try:
            sensors = self._temp_sensors(); sid = sensors.get(primary)
            if sid:
                st = self.hass.states.get(sid)
                if st:
                    try:
                        vv = float(st.state)
                        vals.append(self._to_c(vv))
                    except Exception:
                        pass
        except Exception:
            pass
        merges = self._merges(); group = [primary] + list(merges.get(primary) or [])
        for eid in group:
            c = self._ambient_for_entity_c(eid)
            if isinstance(c, (int, float)):
                vals.append(float(c))
        if not vals:
            return None
        return sum(vals) / len(vals)

    def _room_target_c(self, primary: str) -> float | None:
        st = self.hass.states.get(primary)
        if st:
            a = st.attributes or {}
            for k in ("temperature", "target_temperature", "target_temp"):
                v = a.get(k)
                if isinstance(v, (int, float)):
                    return float(self._to_c(float(v)))
            # heat_cool band -> use high
            vh = a.get("target_temp_high"); vl = a.get("target_temp_low")
            if isinstance(vh, (int, float)):
                return float(self._to_c(float(vh)))
            if isinstance(vl, (int, float)):
                return float(self._to_c(float(vl)))
        # Fallback: schedule desired now
        try:
            schedules, settings, _ml, _mlpr = self._data()
            aam = AutoApplyManager(self.hass)  # temporary helper usage
            # Inject data into helper instance
            def _get_data_override():
                return schedules, settings
            aam._get_data = _get_data_override  # type: ignore
            now_min = int(dt_util.now().hour) * 60 + int(dt_util.now().minute)
            return float(aam._desired_for(primary, schedules, settings, now_min))
        except Exception:
            return None

    def _outside_now_c(self) -> float | None:
        eid = self._weather_eid()
        if not eid:
            return None
        st = self.hass.states.get(eid)
        if not st:
            return None
        # Prefer state if numeric; else attributes.temperature
        try:
            v = float(st.state)
            return self._to_c(v)
        except Exception:
            pass
        try:
            v = st.attributes.get("temperature")
            if isinstance(v, (int, float)):
                return self._to_c(float(v))
        except Exception:
            pass
        return None

    async def async_start(self):
        # Schedule sampling and retraining if enabled; also react to store updates
        @callback
        def _on_update():
            self.hass.async_create_task(self._on_store_changed())
        self._unsub_store = async_dispatcher_connect(self.hass, SIGNAL_UPDATED, _on_update)
        # Listen to decision points from AutoApplyManager (boundary hits)
        @callback
        def _on_decision(payload):
            try:
                if not self._enabled():
                    return
                if not isinstance(payload, dict):
                    return
                primaries = payload.get("primaries") or []
                for p in primaries:
                    self.hass.async_create_task(self._begin_session(str(p), target_override=None))
            except Exception:
                return
            self._unsub_decision = async_dispatcher_connect(self.hass, SIGNAL_DECISION, _on_decision)
        await self._on_store_changed()
        # Listen for climate.set_temperature service calls (from card OR integration)
        @callback
        def _on_call(event):
            try:
                data = event.data or {}
                if data.get("domain") != "climate" or data.get("service") != "set_temperature":
                    return
                svcdata = data.get("service_data") or {}
                eids = svcdata.get("entity_id")
                if isinstance(eids, str):
                    eids = [eids]
                if not isinstance(eids, list):
                    return
                # Resolve target temperature from payload
                target = None
                for k in ("temperature", "target_temperature"):
                    v = svcdata.get(k)
                    if isinstance(v, (int, float)):
                        target = float(self._to_c(float(v)))
                        break
                if target is None:
                    # Range mode: use high side for heating
                    vh = svcdata.get("target_temp_high"); vl = svcdata.get("target_temp_low")
                    if isinstance(vh, (int, float)):
                        target = float(self._to_c(float(vh)))
                    elif isinstance(vl, (int, float)):
                        target = float(self._to_c(float(vl)))
                for eid in eids:
                    try:
                        pe = self._primary_of(eid)
                        if not pe:
                            continue
                        self.hass.async_create_task(self._begin_session(pe, target))
                    except Exception:
                        continue
            except Exception:
                return
        if self._unsub_call:
            self._unsub_call()
        self._unsub_call = self.hass.bus.async_listen("call_service", _on_call)
        # Watch person states to cancel ongoing ML sessions when away becomes active
        await self._reset_person_watch()

    async def async_stop(self):
        if self._unsub_store:
            self._unsub_store()
            self._unsub_store = None
        if self._unsub_decision:
            self._unsub_decision()
            self._unsub_decision = None
        if self._unsub_call:
            self._unsub_call()
            self._unsub_call = None
        if self._unsub_persons:
            self._unsub_persons()
            self._unsub_persons = None
        if self._unsub_sample:
            self._unsub_sample()
            self._unsub_sample = None
        if self._unsub_retrain:
            self._unsub_retrain()
            self._unsub_retrain = None
        for unsub in list(self._preheat_timers.values()):
            try:
                unsub()
            except Exception:
                pass
        self._preheat_timers.clear()
        await self._cancel_all_sessions()

    async def _on_store_changed(self):
        if self._handling_store_signal:
            return
        self._handling_store_signal = True
        try:
            # Refresh enablement and timers
            await self._ensure_timers()
            await self._ensure_preheat_schedules()
            # Update top-level flags for sensor consumption
            d = self.hass.data[DOMAIN]
            d["ml_enabled"] = bool(self._enabled())
            if d.get("ml_status") is None:
                d["ml_status"] = "disabled"
            async_dispatcher_send(self.hass, SIGNAL_UPDATED)
            # Rewire person watchers when settings change
            await self._reset_person_watch()
        finally:
            self._handling_store_signal = False

    async def _ensure_timers(self):
        # Sampling timer (every 2 minutes)
        if self._unsub_sample:
            self._unsub_sample(); self._unsub_sample = None
        if not self._enabled():
            return
        @callback
        def _sample_cb(_now):
            self.hass.async_create_task(self._do_sample())
            # reschedule in 2 minutes
            next_dt = dt_util.utcnow() + timedelta(minutes=2)
            self._unsub_sample = async_track_point_in_utc_time(self.hass, _sample_cb, next_dt)
        first = dt_util.utcnow() + timedelta(minutes=2)
        self._unsub_sample = async_track_point_in_utc_time(self.hass, _sample_cb, first)
        # Retraining timer
        if self._unsub_retrain:
            self._unsub_retrain(); self._unsub_retrain = None
        try:
            mins = max(5, int(self._settings().get("early_start_training_interval_min", 30)))
        except Exception:
            mins = 30
        @callback
        def _retrain_cb(_now):
            self.hass.async_create_task(self._retrain())
            next_dt = dt_util.utcnow() + timedelta(minutes=mins)
            self._unsub_retrain = async_track_point_in_utc_time(self.hass, _retrain_cb, next_dt)
        first_r = dt_util.utcnow() + timedelta(minutes=mins)
        self._unsub_retrain = async_track_point_in_utc_time(self.hass, _retrain_cb, first_r)

    async def _reset_person_watch(self):
        # Subscribe to person.* listed in away config
        if self._unsub_persons:
            self._unsub_persons(); self._unsub_persons = None
        persons = self._away_persons()
        if not persons:
            self._away_active_prev = None
            return
        self._away_active_prev = self._away_active()
        @callback
        def _ch(event):
            now_active = self._away_active()
            prev = self._away_active_prev
            self._away_active_prev = now_active
            # If away just became active, cancel all ongoing sessions
            if now_active and (prev is False or prev is None):
                self.hass.async_create_task(self._cancel_all_sessions())
        self._unsub_persons = async_track_state_change_event(self.hass, persons, _ch)

    async def _cancel_all_sessions(self):
        for primary in list(self._active.keys()):
            try:
                await self._cancel_session(primary)
            except Exception:
                continue

    async def _cancel_session(self, primary: str):
        # Remove without recording to history
        self._active.pop(primary, None)
        # Keep status as-is; we don't broadcast every cancel, to reduce churn
        try:
            d = self.hass.data[DOMAIN]
            d["ml_debug_last_cancel_reason"] = {
                "ts": self._now_iso(),
                "primary": primary,
                "reason": "cancel",
                "active_remaining": list(self._active.keys()),
            }
            async_dispatcher_send(self.hass, SIGNAL_UPDATED)
        except Exception:
            pass

    async def _ensure_preheat_schedules(self):
        # Clear existing preheat timers
        for k, unsub in list(self._preheat_timers.items()):
            try:
                unsub()
            except Exception:
                pass
            self._preheat_timers.pop(k, None)
        if not self._enabled():
            return
        d = self.hass.data[DOMAIN]
        mlpr = d.get("ml_per_room") or {}
        min_needed = self._min_samples()
        try:
            safety = float(self._settings().get("early_start_safety_margin_min", 5))
        except Exception:
            safety = 5.0
        # For each primary with enough data, schedule one preheat event for the next starting block today
        schedules, settings, _ml, _mlpr = self._data()
        now = dt_util.now()
        now_min = now.hour * 60 + now.minute
        for primary, info in (mlpr.items() if isinstance(mlpr, dict) else []):
            try:
                cycles = int(info.get("cycles", 0))
                avg_pre = float(info.get("avg_preheat_min", 0.0)) if cycles >= 1 else None
                if cycles < min_needed or not isinstance(avg_pre, (int, float)) or avg_pre <= 0:
                    continue
                row = schedules.get(primary)
                if not isinstance(row, dict):
                    continue
                blocks = AutoApplyManager(self.hass)._effective_blocks_today(row, settings)
                # Find the next block that starts after now
                nxt = None
                for b in blocks:
                    s = int(b.get("startMin", -1))
                    if s > now_min and (nxt is None or s < nxt[0]):
                        # Compute the desired setpoint as if at the block start (includes away/min/max rules)
                        aam = AutoApplyManager(self.hass)
                        # Provide data to helper
                        def _gd():
                            return schedules, settings
                        aam._get_data = _gd  # type: ignore
                        want = None
                        try:
                            want = float(aam._desired_for(primary, schedules, settings, s))
                        except Exception:
                            try:
                                want = float(b.get("temp"))
                            except Exception:
                                want = None
                        nxt = (s, want)
                if not nxt:
                    continue
                start_min, desired_at_start = nxt
                if not isinstance(desired_at_start, (int, float)):
                    continue
                preheat_total = float(avg_pre) + float(safety)
                trigger_min = start_min - int(round(preheat_total))
                # Compute trigger datetime today
                delta_min = trigger_min - now_min
                if delta_min <= 0:
                    continue
                trigger_dt = dt_util.as_utc(now + timedelta(minutes=delta_min))
                @callback
                def _ph_cb(_ts):
                    self.hass.async_create_task(self._on_preheat_trigger(primary, float(desired_at_start)))
                unsub = async_track_point_in_utc_time(self.hass, _ph_cb, trigger_dt)
                self._preheat_timers[primary] = unsub
            except Exception:
                continue

    async def _on_preheat_trigger(self, primary: str, desired_c: float):
        # Apply upcoming block target early and start measurement; also emit decision
        try:
            mgr: AutoApplyManager = self.hass.data[DOMAIN].get("manager")
            if mgr and isinstance(desired_c, (int, float)):
                # Respect the same gating (pause/auto_apply) as normal path
                if not mgr._auto_apply_enabled():
                    raise Exception("auto_apply disabled or paused; skip early start apply")
                # Apply to primary and its merges
                schedules, settings, _ml, _mlpr = self._data()
                merges = settings.get("merges") or {}
                group = [primary] + list(merges.get(primary) or [])
                for eid in group:
                    await mgr._apply_setpoint(eid, float(desired_c))
        except Exception:
            pass
        try:
            async_dispatcher_send(self.hass, SIGNAL_DECISION, {"type": "earlystart", "primaries": [primary]})
        except Exception:
            pass
        await self._begin_session(primary, target_override=float(desired_c))

    async def _do_sample(self):
        if not self._enabled():
            return
        schedules, settings, _ml, _mlpr = self._data()
        merges = settings.get("merges") or {}
        # For each primary room (keys of schedules)
        for primary in list(schedules.keys()):
            amb = self._room_ambient_c(primary)
            tgt = self._room_target_c(primary)
            if amb is None or tgt is None:
                continue
            # Only continue if a session is active (start is triggered by set_temperature service call)
            session = self._active.get(primary)
            if session is None:
                continue
            # Append sample
            try:
                session["samples"].append((self._now_iso(), float(amb)))
            except Exception:
                pass
            # Check target reached
            if amb + 0.1 >= tgt:
                await self._close_session(primary, success=True)
            # If target far below ambient (cooling/off), abort session
            elif (amb - tgt) > 1.0:
                await self._close_session(primary, success=False)

    def _primary_of(self, eid: str) -> str | None:
        # If eid is a primary in schedules, return it; else find primary that lists it in merges
        schedules, settings, _ml, _mlpr = self._data()
        if eid in schedules:
            return eid
        merges = settings.get("merges") or {}
        for p, lst in (merges.items() if isinstance(merges, dict) else []):
            try:
                if eid in (lst or []):
                    return p
            except Exception:
                continue
        return None

    async def _begin_session(self, primary: str, target_override: float | None):
        def _record(reason: str, amb, tgt):
            try:
                d = self.hass.data[DOMAIN]
                d["ml_debug_last_begin_attempt"] = {
                    "ts": self._now_iso(),
                    "primary": primary,
                    "reason": reason,
                    "ambient_c": amb if isinstance(amb, (int, float)) else None,
                    "target_c": tgt if isinstance(tgt, (int, float)) else None,
                    "override_c": target_override if isinstance(target_override, (int, float)) else None,
                    "enabled": self._enabled(),
                    "already_active": bool(self._active.get(primary)),
                }
                async_dispatcher_send(self.hass, SIGNAL_UPDATED)
            except Exception:
                pass
        if not self._enabled():
            _record("skipped_disabled", None, None)
            return
        if self._active.get(primary):
            _record("skipped_already_active", None, None)
            return
        amb = self._room_ambient_c(primary)
        tgt = target_override if isinstance(target_override, (int, float)) else self._room_target_c(primary)
        if amb is None:
            _record("skipped_no_ambient", amb, tgt)
            return
        if tgt is None:
            _record("skipped_no_target", amb, tgt)
            return
        self._active[primary] = {
            "start_iso": self._now_iso(),
            "target_c": float(tgt),
            "ambient_start_c": float(amb),
            "outside_start_c": self._outside_now_c(),
            "samples": []
        }
        # Update status to learning
        d = self.hass.data[DOMAIN]
        d["ml_status"] = "learning"
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)
        _record("started", amb, tgt)

    async def _close_session(self, primary: str, success: bool):
        sess = self._active.pop(primary, None)
        if not sess:
            return
        try:
            start = dt_util.parse_datetime(sess.get("start_iso"))
            end = dt_util.utcnow()
            dur_min = max(0.0, (end - start).total_seconds() / 60.0)
        except Exception:
            dur_min = None
        # Persist into ml_per_room history
        d = self.hass.data[DOMAIN]
        mlpr = d.get("ml_per_room") or {}
        room = mlpr.get(primary) or {"cycles": 0, "samples_count": 0}
        sample = {
            "start_iso": sess.get("start_iso"),
            "target_c": sess.get("target_c"),
            "ambient_start_c": sess.get("ambient_start_c"),
            "duration_min": dur_min,
            "success": bool(success),
            "outside_start_c": sess.get("outside_start_c"),
        }
        hist = room.get("history") or []
        hist.append(sample)
        # Prune by history days (~ cap by count: keep last N=5000 as safety)
        room["history"] = hist[-5000:]
        room["samples_count"] = len(room["history"])  # total
        # Update cycles count for successful runs
        if success and isinstance(dur_min, (int, float)) and dur_min > 0:
            room["cycles"] = int(room.get("cycles", 0)) + 1
        mlpr[primary] = room
        d["ml_per_room"] = mlpr
        # Touch counters and broadcast; persist lazily via save_cb
        d["ml_trained_cycles"] = sum(int(v.get("cycles", 0)) for v in mlpr.values())
        d["ml_status"] = "learning"
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)
        # Save snapshot (not on every 2-min sample, only when closing a session)
        await self._save_cb()

    async def _retrain(self):
        # Compute simple preheat averages and confidence
        d = self.hass.data[DOMAIN]
        mlpr = d.get("ml_per_room") or {}
        total_cycles = 0
        for primary, info in list(mlpr.items()):
            hist = [x for x in (info.get("history") or []) if bool(x.get("success")) and isinstance(x.get("duration_min"), (int, float))]
            # prune by date based on history days
            try:
                days = self._hist_days()
                cutoff = dt_util.utcnow() - timedelta(days=days)
                def _ok(s):
                    try:
                        t = dt_util.parse_datetime(str(s.get("start_iso")))
                        return t is None or t >= cutoff
                    except Exception:
                        return True
                hist = [s for s in hist if _ok(s)]
            except Exception:
                pass
            if hist:
                avg = sum(float(s.get("duration_min")) for s in hist) / float(len(hist))
                info["avg_preheat_min"] = avg
                info["samples_count"] = len(hist)
                total_cycles += int(info.get("cycles", 0))
            mlpr[primary] = info
        d["ml_per_room"] = mlpr
        d["ml_trained_cycles"] = total_cycles
        min_needed = self._min_samples()
        d["ml_enabled"] = self._enabled()
        d["ml_status"] = ("active" if total_cycles >= min_needed and self._enabled() else ("learning" if self._enabled() else "disabled"))
        try:
            conf = 100.0 * float(total_cycles) / float(min_needed or 1)
            d["ml_confidence_pct"] = max(0.0, min(100.0, conf))
        except Exception:
            d["ml_confidence_pct"] = None
        try:
            d["ml_last_trained_iso"] = dt_util.utcnow().isoformat()
        except Exception:
            pass
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)
        await self._save_cb()

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
        self._unsub_store = None

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
        self._unsub_store = async_dispatcher_connect(self.hass, SIGNAL_UPDATED, _on_update)

    async def async_stop(self):
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        if self._unsub_store:
            self._unsub_store()
            self._unsub_store = None

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
