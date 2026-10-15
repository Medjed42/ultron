import os
import sys
import threading
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import midea_beautiful
    from midea_beautiful.cloud import MideaCloud
    from midea_beautiful.lan import LanDevice
    from midea_beautiful import connect_to_cloud, appliance_state
except ImportError:
    print("[MIDEA] Error: midea-beautiful-air is not installed.")

class MideaManager:
    def __init__(self):
        self.cloud: Optional[MideaCloud] = None
        self.device: Optional[LanDevice] = None
        self.status: str = "INITIALIZING" 
        self.lock = threading.Lock()
        
    def start_background_init(self):
        t = threading.Thread(target=self._init_cloud, daemon=True)
        t.start()
        
    def _init_cloud(self):
        with self.lock:
            email = os.getenv("MIDEA_EMAIL")
            password = os.getenv("MIDEA_PASSWORD")
            
            if not email or not password:
                self.status = "MISSING_CONFIG"
                print("[MIDEA] Missing MIDEA_EMAIL or MIDEA_PASSWORD in env.")
                return
                
            try:
                print("[MIDEA] Connecting to Midea cloud...")
                self.cloud = connect_to_cloud(account=email, password=password, appname="NetHome Plus")
                print("[MIDEA] Authentication successful")
                
                appliances = self.cloud.list_appliances()
                target_name = os.getenv("MIDEA_DEVICE_NAME")
                
                ac_list = []
                for a in appliances:
                    ctype = str(a.get("type", "")).lower()
                    if ctype in ("0xac", "ac", "172", "-84"):
                        ac_list.append(a)
                
                print(f"[MIDEA] Found {len(ac_list)} compatible climate device(s).")
                if not ac_list:
                    self.status = "NO_DEVICE"
                    return
                
                selected = None
                if target_name:
                    for a in ac_list:
                        if a.get("name", "").lower() == target_name.lower():
                            selected = a
                            break
                    if not selected:
                        print(f"[MIDEA] Warning: device with name '{target_name}' not found. Selecting first available.")
                        selected = ac_list[0]
                else:
                    selected = ac_list[0]
                
                print(f"[MIDEA] Selected device: {selected.get('name')}")
                
                self.device = appliance_state(
                    appliance_id=selected["id"],
                    appliance_type=selected["type"],
                    cloud=self.cloud,
                    use_cloud=True
                )
                self.status = "OK"
                print("[MIDEA] Device state read successfully. Plugin ready.")
            except Exception as e:
                self.status = "ERROR"
                print(f"[MIDEA] Initialization failed: {e}")

_manager = MideaManager()

# Initialize only if this module is loaded as a plugin (not in CLI diagnose mode)



PLUGIN = {
    "name": "midea_cloud",
    "description": (
        "Kontrola Midea klima-uredjaja (Air Conditioner) preko Midea Cloud servisa. "
        "Koristi ovaj alat svaki put kada korisnik traži da upravlja klimom, npr.: "
        "'Ultron, uključi klimu', 'ugasi klimu', 'postavi klimu na 23 stepena', "
        "'prebaci klimu na hlađenje/grejanje', 'pojačaj ventilator na maksimum', 'koja je temperatura klime'. "
        "Ne koristi za druge uređaje. action mora biti tačno definisan string."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Jedna od akcija: 'power_on', 'power_off', 'set_temperature', 'set_mode', 'set_fan_speed', 'get_status'"
            },
            "temperature": {
                "type": "NUMBER",
                "description": "Ciljna temperatura u celzijusima (npr. 23). Potrebno samo za 'set_temperature'."
            },
            "mode": {
                "type": "STRING",
                "description": "Nacin rada: 'auto', 'cool' (hlađenje), 'dry' (isušivanje), 'heat' (grejanje), 'fan' (ventilator). Potrebno samo za 'set_mode'."
            },
            "fan_speed": {
                "type": "STRING",
                "description": "Brzina ventilatora: 'auto', 'low', 'medium', 'high', 'silent'. Potrebno samo za 'set_fan_speed'."
            }
        },
        "required": ["action"]
    }
}

def _execute_with_retry(dev, cloud, action_func):
    try:
        action_func()
    except Exception as e:
        print(f"[MIDEA] Command failed, attempting re-auth. Error: {e}")
        try:
            cloud.authenticate()
            action_func()
        except Exception as e2:
            print(f"[MIDEA] Re-auth and retry failed: {e2}")
            raise e2

def run(parameters: dict, player=None, session_memory=None) -> str:
    if _manager.status == "INITIALIZING":
        _manager._init_cloud()
    dry_run = str(os.getenv("MIDEA_DRY_RUN", "false")).lower() == "true"
    action = parameters.get("action", "")
    
    if dry_run:
        return f"[MIDEA DRY RUN] Action: {action}. Parameters: {parameters}"
        
    with _manager.lock:
        if _manager.status == "MISSING_CONFIG":
            return "Nisam uspeo da se povežem. Nedostaju MIDEA_EMAIL ili MIDEA_PASSWORD u konfiguraciji."
        if _manager.status == "INITIALIZING":
            return "Povezivanje sa Midea cloud-om je još uvek u toku. Pokušaj ponovo za koji trenutak."
        if _manager.status == "ERROR":
            return "Midea cloud servis trenutno nije dostupan ili je došlo do greške pri prijavi."
        if _manager.status == "NO_DEVICE" or not _manager.device or not _manager.cloud:
            return "Nisam pronašao nijednu klimu povezanu sa Midea nalogom."
            
        dev = _manager.device
        cloud = _manager.cloud
        
        try:
            if action == "power_on":
                def cmd():
                    dev.state.running = True
                    dev.apply(cloud)
                _execute_with_retry(dev, cloud, cmd)
                return "Klima je uključena."
                
            elif action == "power_off":
                def cmd():
                    dev.state.running = False
                    dev.apply(cloud)
                _execute_with_retry(dev, cloud, cmd)
                return "Klima je isključena."
                
            elif action == "set_temperature":
                temp = parameters.get("temperature")
                if temp is None:
                    return "Nije navedena temperatura."
                def cmd():
                    dev.state.target_temperature = float(temp)
                    dev.apply(cloud)
                _execute_with_retry(dev, cloud, cmd)
                return f"Klima je podešena na {temp} stepeni."
                
            elif action == "set_mode":
                mode_str = parameters.get("mode", "").lower()
                modes = {"auto": 1, "cool": 2, "dry": 3, "heat": 4, "fan": 5}
                if mode_str not in modes:
                    return f"Nepoznat režim rada: {mode_str}."
                
                sr_modes = {"auto": "automatski", "cool": "hlađenje", "dry": "isušivanje", "heat": "grejanje", "fan": "ventilator"}
                def cmd():
                    dev.state.mode = modes[mode_str]
                    dev.state.running = True
                    dev.apply(cloud)
                _execute_with_retry(dev, cloud, cmd)
                return f"Klima je uključena i prebačena na {sr_modes[mode_str]}."
                
            elif action == "set_fan_speed":
                speed_str = parameters.get("fan_speed", "").lower()
                speeds = {"auto": 102, "low": 40, "medium": 60, "high": 80, "silent": 20}
                if speed_str not in speeds:
                    return f"Nepoznata brzina ventilatora: {speed_str}."
                def cmd():
                    dev.state.fan_speed = speeds[speed_str]
                    dev.apply(cloud)
                _execute_with_retry(dev, cloud, cmd)
                return f"Ventilator klime je postavljen na {speed_str}."
                
            elif action == "get_status":
                def cmd():
                    dev.refresh(cloud)
                _execute_with_retry(dev, cloud, cmd)
                st = dev.state
                
                if not st.running:
                    return f"Klima je trenutno isključena. Trenutna sobna temperatura je {st.indoor_temperature} stepeni."
                
                inv_modes = {1: "auto", 2: "hlađenje", 3: "isušivanje", 4: "grejanje", 5: "ventilator"}
                cur_mode = inv_modes.get(st.mode, "nepoznat")
                
                return f"Klima je uključena, u režimu {cur_mode}, podešena na {st.target_temperature} stepeni. Sobna temperatura je {st.indoor_temperature} stepeni."
                
            else:
                return f"Nepoznata Midea komanda: {action}."
                
        except Exception as e:
            print(f"[MIDEA] Error during {action}: {e}")
            return "Došlo je do greške u komunikaciji sa Midea servisom."

def diagnose():
    print("Midea Cloud Plugin Diagnostics")
    print("==============================\n")
    email = os.getenv("MIDEA_EMAIL")
    password = os.getenv("MIDEA_PASSWORD")
    
    print(f"Midea config: {'OK' if email and password else 'MISSING (MIDEA_EMAIL / MIDEA_PASSWORD)'}")
    if not email or not password:
        return
        
    print("\n[Connecting to cloud...]")
    try:
        cloud = connect_to_cloud(account=email, password=password, appname="NetHome Plus")
        print("Cloud connection: OK")
        print("Authentication: OK")
        
        appliances = cloud.list_appliances()
        print(f"Devices found: {len(appliances)}")
        
        ac_list = [a for a in appliances if str(a.get("type", "")).lower() in ("0xac", "ac", "172", "-84")]
        print(f"Climate devices: {len(ac_list)}")
        
        if not ac_list:
            print("No climate devices found.")
            return
            
        target_name = os.getenv("MIDEA_DEVICE_NAME")
        selected = ac_list[0]
        if target_name:
            for a in ac_list:
                if a.get("name", "").lower() == target_name.lower():
                    selected = a
                    break
                    
        print(f"Selected climate: {selected.get('name')}")
        
        device = appliance_state(
            appliance_id=selected["id"],
            appliance_type=selected["type"],
            cloud=cloud,
            use_cloud=True
        )
        print("Device online: YES")
        print("State read: OK")
        
        print("\n--- Current State ---")
        st = device.state
        print(f"Power: {'ON' if st.running else 'OFF'}")
        print(f"Mode: {st.mode}")
        print(f"Target Temp: {st.target_temperature}°C")
        print(f"Indoor Temp: {st.indoor_temperature}°C")
        print(f"Outdoor Temp: {st.outdoor_temperature}°C")
        print(f"Fan Speed: {st.fan_speed}")
        
    except Exception as e:
        print(f"\nError during diagnostics: {e}")

if __name__ == "__main__":
    diagnose()
