from __future__ import annotations
import logging
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.storage import Store
from homeassistant.helpers.dispatcher import async_dispatcher_send
from .const import DOMAIN, STORAGE_KEY, STORAGE_VERSION, SIGNAL_UPDATED

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
    hass.data[DOMAIN]["version"] = int(data.get("version", 1))

    async def _save_and_broadcast():
        await store.async_save({
            "schedules": hass.data[DOMAIN]["schedules"],
            "version": hass.data[DOMAIN]["version"],
        })
        async_dispatcher_send(hass, SIGNAL_UPDATED)

    async def set_store(call: ServiceCall):
        schedules = call.data.get("schedules", {})
        if not isinstance(schedules, dict):
            _LOGGER.warning("%s.set_store: schedules must be an object", DOMAIN)
            return
        hass.data[DOMAIN]["schedules"] = schedules
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
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, ["sensor"])
