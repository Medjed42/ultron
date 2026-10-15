from .device_discovery import DeviceDiscovery
from .device_aliases import DeviceAliases

class DeviceMatcher:
    def __init__(self, discovery: DeviceDiscovery, aliases: DeviceAliases):
        self.discovery = discovery
        self.aliases = aliases

    def match_device(self, query: str):
        if not query: return None
        query = query.lower().strip()
        
        # 1. Alias check
        target_name = self.aliases.get_target(query)
        
        devices = self.discovery.list_active_outputs()
        
        # 2. Exact match on alias target or query
        for d in devices:
            fname = d.FriendlyName.lower()
            if target_name and target_name.lower() == fname:
                return d
            if query == fname:
                return d
                
        # 3. Partial match (e.g. "televizor" matching "LG TV" or "slušalice" matching "Headphones")
        # Handle some built-in serbian translations if aliases aren't set
        translations = {
            "slusalice": "headphone",
            "slušalice": "headphone",
            "zvucnici": "speaker",
            "zvučnici": "speaker",
            "televizor": "tv",
            "monitor": "nvidia"
        }
        
        search_terms = [query]
        if query in translations:
            search_terms.append(translations[query])
            
        matches = [d for d in devices if any(term in d.FriendlyName.lower() or term in d.id.lower() for term in search_terms)]
        return matches[0] if len(matches) == 1 else None
