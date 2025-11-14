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

from .const import DOMAIN, STORAGE_KEY, STORAGE_VERSION, SIGNAL_UPDATED, BACKUP_STORAGE_KEY
from homeassistant.helpers import entity_registry as er

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


_LOGGER = logging.getLogger(__name__)

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

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["store"] = store
    hass.data[DOMAIN]["schedules"] = data.get("schedules", {})
    hass.data[DOMAIN]["settings"] = data.get("settings", {})
    hass.data[DOMAIN]["version"] = int(data.get("version", 1))
    # Backup payloads
    hass.data[DOMAIN]["backup_schedules"] = backup_data.get("schedules", {})
    hass.data[DOMAIN]["backup_settings"] = backup_data.get("settings", {})
    hass.data[DOMAIN]["backup_version"] = int(backup_data.get("version", 1))
    hass.data[DOMAIN]["backup_last_ts"] = backup_data.get("last_backup_ts")
    # Track whether the backup is partial and which sections it includes
    hass.data[DOMAIN]["backup_partial_flags"] = backup_data.get("partial_flags")

    async def _save_and_broadcast():
        # Broadcast first so UI updates immediately
        async_dispatcher_send(hass, SIGNAL_UPDATED)
        # Proactively nudge the overview sensor to update its state in HA
        try:
            ent_reg = er.async_get(hass)
            sensor_eid = ent_reg.async_get_entity_id("sensor", DOMAIN, "thermostat_timeline_overview")
            if sensor_eid:
                await hass.services.async_call("homeassistant", "update_entity", {"entity_id": sensor_eid}, blocking=False)
        except Exception:
            pass
        try:
            await store.async_save({
                "schedules": hass.data[DOMAIN]["schedules"],
                "settings": hass.data[DOMAIN].get("settings", {}),
                "version": hass.data[DOMAIN]["version"],
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
        force = bool(call.data.get("force"))
        cur_sched = hass.data[DOMAIN].get("schedules", {})
        cur_set = hass.data[DOMAIN].get("settings", {})
        changed = False
        if "schedules" in call.data:
            schedules = call.data.get("schedules")
            if not isinstance(schedules, dict):
                _LOGGER.warning("%s.set_store: schedules must be an object when provided", DOMAIN)
                return
            if schedules != cur_sched:
                hass.data[DOMAIN]["schedules"] = schedules
                changed = True
        # Optional settings payload
        if "settings" in call.data:
            settings = call.data.get("settings")
            if isinstance(settings, dict) and settings != cur_set:
                hass.data[DOMAIN]["settings"] = settings
                changed = True
        if not changed and not force:
            # No-op: nothing changed and not forced; avoid spurious version bump/apply
            return
        hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN]["version"]) + 1
        await _save_and_broadcast()

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
            hass.data[DOMAIN]["settings"] = dict(backup_set)
            hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN].get("version", 1)) + 1
            await _save_and_broadcast()
            return

        do_merge = (mode == "merge") or (not mode and bool(flags))
        if not do_merge:
            # Explicit replace
            hass.data[DOMAIN]["schedules"] = dict(backup_sched)
            hass.data[DOMAIN]["settings"] = dict(backup_set)
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
        hass.data[DOMAIN]["settings"] = cur_set
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
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, ["sensor"])


class AutoApplyManager:
    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._unsub_timer = None
        self._unsub_persons = None
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
        async_dispatcher_connect(self.hass, SIGNAL_UPDATED, _on_store_update)
        # Watch person.* states if away mode is used
        self._reset_person_watch()

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
        await self._maybe_apply_now(force=True, boundary_only=(not self._next_is_resume))
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
