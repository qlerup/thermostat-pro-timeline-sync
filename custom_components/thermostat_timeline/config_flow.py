from homeassistant import config_entries
from .const import DOMAIN

class ThermostatTimelineConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    async def async_step_user(self, user_input=None):
        # Kun én instans
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="Thermostat Timeline", data={})
        return self.async_show_form(step_id="user")

    async def async_step_import(self, user_input=None):
        """Understøt YAML: thermostat_timeline: i configuration.yaml."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_create_entry(title="Thermostat Timeline", data={})
