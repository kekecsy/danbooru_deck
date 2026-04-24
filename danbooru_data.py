import os
import json
import datetime

class DanbooruData:
    def __init__(self, target_date=None):
        self.base_dir = './hot_pic'
        self.drawer_dir = './drawer'
        self.today_str = target_date if target_date else datetime.datetime.now().strftime('%Y-%m-%d')
        self.save_dir = os.path.join(self.base_dir, self.today_str)
        
        self.stats_path = os.path.join(self.base_dir, "artist_stats.json")
        self.log_path = os.path.join(self.base_dir, "log.json")
        self.status_path = os.path.join(self.base_dir, "status.json")
        
        self.txtdata_path = os.path.join(self.drawer_dir, "txtdata.txt")
        self.disk_drawer_path = os.path.join(self.drawer_dir, "disk_drawer.json")
        self.hot_drawer_path = os.path.join(self.drawer_dir, "hot_drawer.txt")
        self.need_update_path = os.path.join(self.drawer_dir, "need_update.json")
        
        self._init_directories()
        
        self.log_data = self._load_json(self.log_path, {})
        self.artist_stats = self._load_json(self.stats_path, {})
        
        # Load drawer data
        with open(self.txtdata_path, 'r', encoding='utf-8') as f:
            self.txtdata1 = f.read().split('\n')
        self.disk_drawer = self._load_json(self.disk_drawer_path, {"1": [], "2": []})
        self.txtdata2 = self.disk_drawer.get("1", []) + self.disk_drawer.get("2", [])
        self.all_drawer = set(self.txtdata1 + self.txtdata2)
        
        # Build folder_to_disk dictionary
        self.folder_to_disk = {}
        for k, v in self.disk_drawer.items():
            for folder in v:
                self.folder_to_disk[folder] = k

    def _init_directories(self):
        os.makedirs(self.save_dir, exist_ok=True)
        os.makedirs(self.drawer_dir, exist_ok=True)
        
        if not os.path.exists(self.txtdata_path):
            with open(self.txtdata_path, 'w', encoding='utf-8') as f:
                f.write('')
        if not os.path.exists(self.disk_drawer_path):
            with open(self.disk_drawer_path, 'w', encoding='utf-8') as f:
                json.dump({"1": [], "2": []}, f, ensure_ascii=False, indent=4)
        if not os.path.exists(self.hot_drawer_path):
            with open(self.hot_drawer_path, 'w', encoding='utf-8') as f:
                f.write('')

    def _load_json(self, path, default=None):
        if default is None:
            default = {}
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return default
        return default

    def _save_json(self, path, data):
        temp_path = path + ".tmp"
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(temp_path, path)

    def save_global_data(self):
        self._save_json(self.log_path, self.log_data)
        self._save_json(self.stats_path, self.artist_stats)

    def load_viewer_data(self):
        return self._load_json(os.path.join(self.save_dir, "viewer_data.json"), [])

    def save_viewer_data(self, data):
        self._save_json(os.path.join(self.save_dir, "viewer_data.json"), data)

    def load_ids_data(self):
        return self._load_json(os.path.join(self.save_dir, "ids_data.json"), [])
        
    def save_ids_data(self, data):
        self._save_json(os.path.join(self.save_dir, "ids_data.json"), data)

    def write_status(self, state, page=None):
        data = {
            "state": state,
            "page": page,
            "time": datetime.datetime.now().isoformat()
        }
        self._save_json(self.status_path, data)

    def get_folder_name(self, name):
        return (name.replace(":", "%3A").replace("/", "%2F").replace("!", "_")
                .replace("?", "_").replace("<", "_").replace(">", "_").rstrip('.'))

    def get_disk_key(self, artist_name, default="2"):
        f_name = self.get_folder_name(artist_name)
        return self.folder_to_disk.get(f_name, default)

    def load_need_update(self):
        temp_nu = self._load_json(self.need_update_path, {"1": [], "2": []})
        return {"1": set(temp_nu.get("1", [])), "2": set(temp_nu.get("2", []))}

    def save_need_update(self, nu_sets):
        final_nu = {k: sorted(list(v)) for k, v in nu_sets.items()}
        self._save_json(self.need_update_path, final_nu)
        
    def load_hot_drawer(self):
        with open(self.hot_drawer_path, 'r', encoding='utf-8') as f:
            return f.read().split('\n')
            
    def save_hot_drawer(self, output_list):
        with open(self.hot_drawer_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(output_list))
