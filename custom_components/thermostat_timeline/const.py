DOMAIN = "thermostat_timeline"
STORAGE_KEY = DOMAIN
STORAGE_VERSION = 1
SIGNAL_UPDATED = f"{DOMAIN}_updated"
SIGNAL_DECISION = f"{DOMAIN}_decision"  # emitted when a schedule/early-start apply decision is taken
BACKUP_STORAGE_KEY = f"{DOMAIN}_backup"
