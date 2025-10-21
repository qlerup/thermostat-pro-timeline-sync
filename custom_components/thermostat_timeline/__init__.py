from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.dispatcher import async_dispatcher_send, async_dispatcher_connect
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_state_change
from homeassistant.util import dt as dt_util
from datetime import timedelta

from .const import DOMAIN, STORAGE_KEY, STORAGE_VERSION, SIGNAL_UPDATED

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

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["store"] = store
    hass.data[DOMAIN]["schedules"] = data.get("schedules", {})
    hass.data[DOMAIN]["settings"] = data.get("settings", {})
    hass.data[DOMAIN]["version"] = int(data.get("version", 1))

    async def _save_and_broadcast():
        await store.async_save({
            "schedules": hass.data[DOMAIN]["schedules"],
            "settings": hass.data[DOMAIN].get("settings", {}),
            "version": hass.data[DOMAIN]["version"],
        })
        async_dispatcher_send(hass, SIGNAL_UPDATED)

    async def set_store(call: ServiceCall):
        if "schedules" in call.data:
            schedules = call.data.get("schedules")
            if not isinstance(schedules, dict):
                _LOGGER.warning("%s.set_store: schedules must be an object when provided", DOMAIN)
                return
            hass.data[DOMAIN]["schedules"] = schedules
        # Optional settings payload
        settings = call.data.get("settings")
        if isinstance(settings, dict):
            hass.data[DOMAIN]["settings"] = settings
        hass.data[DOMAIN]["version"] = int(hass.data[DOMAIN]["version"]) + 1
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
    hass.services.async_register(DOMAIN, "patch_entity", patch_entity)
    hass.services.async_register(DOMAIN, "clear", clear)

    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    # ---- Background Auto-Apply Manager ----
    mgr = AutoApplyManager(hass)
    hass.data[DOMAIN]["manager"] = mgr
    await mgr.async_start()
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, ["sensor"])


class AutoApplyManager:
    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._unsub_timer = None
        self._unsub_persons = None
        self._last_applied = {}  # eid -> {"min": int, "temp": float}

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
        return bool(settings.get("auto_apply_enabled"))

    def _now_min(self) -> int:
        now = dt_util.now()
        return now.hour * 60 + now.minute

    def _today_key(self) -> str:
        # Monday=0 ... Sunday=6 -> map to keys
        idx = (dt_util.now().weekday())  # 0..6
        return ["mon","tue","wed","thu","fri","sat","sun"][idx]

    def _effective_blocks_today(self, row: dict):
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
        blocks = self._effective_blocks_today(row)
        hit = None
        for b in blocks:
            try:
                if now_min >= int(b.get("startMin", -1)) and now_min < int(b.get("endMin", -1)):
                    hit = b
                    break
            except Exception:
                continue
        want = float(hit.get("temp")) if hit is not None else float(row.get("defaultTemp", 20))
        # Away override
        try:
            away = settings.get("away") or {}
            if away.get("enabled") and isinstance(away.get("persons"), list):
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

    async def _maybe_apply_now(self, force: bool = False):
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
            try:
                await self.hass.services.async_call(
                    "climate", "set_temperature", {"entity_id": eid, "temperature": float(desired)}, blocking=False
                )
                self._last_applied[eid] = {"min": now_min, "temp": float(desired)}
            except Exception:
                _LOGGER.warning("Auto-apply failed for %s", eid)

    def _next_boundary_dt(self):
        schedules, settings = self._get_data()
        now = dt_util.now()
        now_min = self._now_min()
        best_delta = None
        # consider all block boundaries today
        for eid, row in schedules.items():
            try:
                blocks = self._effective_blocks_today(row)
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
            return
        when = self._next_boundary_dt()
        @callback
        def _cb(_now):
            self.hass.async_create_task(self._timer_fire())
        self._unsub_timer = async_track_point_in_utc_time(self.hass, _cb, when)

    async def _timer_fire(self):
        await self._maybe_apply_now(force=True)
        await self._schedule_next()

    def _reset_person_watch(self):
        # Recreate person watcher based on settings.away.persons
        if self._unsub_persons:
            self._unsub_persons()
            self._unsub_persons = None
        _schedules, settings = self._get_data()
        away = settings.get("away") or {}
        persons = away.get("persons") if isinstance(away, dict) else None
        if away.get("enabled") and isinstance(persons, list) and persons:
            @callback
            def _ch(_entity, _old, _new):
                # Apply immediately when presence changes
                self.hass.async_create_task(self._maybe_apply_now(force=True))
                self.hass.async_create_task(self._schedule_next())
            self._unsub_persons = async_track_state_change(self.hass, persons, _ch)
