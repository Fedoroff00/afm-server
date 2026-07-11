#!/usr/bin/env python3
import os, sys, time, json, subprocess, logging, threading, queue, re
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

import requests
import importlib
import yaml
from logging.handlers import TimedRotatingFileHandler

try:
    import inotify.adapters
    HAS_INOTIFY = True
except ImportError:
    HAS_INOTIFY = False

CONFIG_PATH = "/etc/astra-monitor/config.yaml"
STATE_FILE = "/var/lib/astra-monitor/state.json"
RETRY_FILE = "/var/lib/astra-monitor/retry_queue.json"
LOG_FILE = "/var/log/astra-monitor.log"
MONITOR_PORT = 9090

HEARTBEAT_INTERVAL = 300
FULL_SCAN_INTERVAL = 3600
BATCH_INTERVAL = 5
MAX_WORKERS = 4
MAX_RETRIES = 5
RETRY_DELAY_BASE = 30

TEXT_EXTENSIONS = {'.txt','.log','.md','.csv','.json','.xml','.yaml','.yml','.ini','.cfg','.conf','.html'}
CONVERTIBLE_EXTENSIONS = {'.odt','.docx','.rtf','.pdf'}
SKIP_DIRS = {'.cache','.mozilla','.dropbox','.recoll','.local','node_modules','__pycache__'}

def setup_logging():
    logger = logging.getLogger('afm-agent')
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    file_handler = TimedRotatingFileHandler(LOG_FILE, when='midnight', backupCount=7)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    return logger

log = setup_logging()

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f)

state_lock = threading.Lock()
known_files = {}
retry_queue_list = []

def load_state():
    global known_files, retry_queue_list
    try:
        with open(STATE_FILE, 'r') as f:
            known_files = json.load(f)
    except:
        known_files = {}
    try:
        with open(RETRY_FILE, 'r') as f:
            retry_queue_list = json.load(f)
    except:
        retry_queue_list = []

def save_state():
    with state_lock:
        with open(STATE_FILE, 'w') as f:
            json.dump(known_files, f)
        with open(RETRY_FILE, 'w') as f:
            json.dump(retry_queue_list, f)

def extract_text(filepath, ext):
    if ext in TEXT_EXTENSIONS:
        try:
            with open(filepath, 'r', errors='ignore') as f:
                return f.read()
        except:
            return ""
    elif ext in CONVERTIBLE_EXTENSIONS:
        try:
            result = subprocess.run(['pandoc', filepath, '--to', 'plain', '--wrap=none'],
                                    capture_output=True, text=True, timeout=30)
            return result.stdout if result.returncode == 0 else ""
        except:
            return ""
    return ""

def should_index(filename):
    ext = os.path.splitext(filename)[1].lower()
    return ext in TEXT_EXTENSIONS or ext in CONVERTIBLE_EXTENSIONS

def get_file_owner(filepath):
    try:
        from pwd import getpwuid
        return getpwuid(os.stat(filepath).st_uid).pw_name
    except:
        return "unknown"

class ServerUploader:
    def __init__(self, server_url, token):
        self.server_url = server_url
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({'Authorization': f'Token {token}'})

    def send_files(self, files_batch):
        payload = {'files': files_batch}
        try:
            resp = self.session.post(urljoin(self.server_url, '/api/agent/files/'), json=payload)
            if resp.status_code == 201:
                log.info(f"Sent {len(files_batch)} files.")
                return len(files_batch)
            else:
                log.error(f"Upload error: {resp.status_code} {resp.text}")
                return 0
        except Exception as e:
            log.error(f"Upload request failed: {e}")
            return 0

    def send_heartbeat(self):
        try:
            resp = self.session.post(urljoin(self.server_url, '/api/agent/heartbeat/'))
            if resp.status_code == 200:
                return resp.json()
            else:
                log.error(f"Heartbeat failed: {resp.status_code}")
                return None
        except Exception as e:
            log.error(f"Heartbeat error: {e}")
            return None

    def send_incident(self, incident_data):
        try:
            resp = self.session.post(urljoin(self.server_url, '/api/agent/incident/'), json=incident_data)
            if resp.status_code == 201:
                log.info(f"Incident reported: {incident_data.get('trigger_word')} in {incident_data.get('file_name')}")
                return True
            else:
                log.error(f"Incident report failed: {resp.status_code}")
                return False
        except Exception as e:
            log.error(f"Incident request failed: {e}")
            return False

class Agent:
    def __init__(self, config):
        self.server_url = config['server_url']
        self.token = config['token']
        self.uploader = ServerUploader(self.server_url, self.token)
        load_state()
        self.event_queue = queue.Queue()
        self.triggers = []
        self.config = {}
        self.executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self.running = True

        if HAS_INOTIFY:
            self._start_inotify_watchers()
        else:
            log.warning("inotify not available, only periodic scan.")
        threading.Thread(target=self._batch_sender, daemon=True).start()
        threading.Thread(target=self._retry_worker, daemon=True).start()
        self._load_plugins()
        if MONITOR_PORT > 0:
            threading.Thread(target=self._start_monitor, daemon=True).start()

    def _start_inotify_watchers(self):
        dirs = self.config.get('scan_directories', '/home').split(',')
        for d in dirs:
            d = d.strip()
            if not os.path.isdir(d):
                continue
            i = inotify.adapters.InotifyTrees([d])
            threading.Thread(target=self._inotify_loop, args=(i,), daemon=True).start()
            log.info(f"Inotify watching {d}")

    def _inotify_loop(self, inotify_instance):
        for event in inotify_instance.event_gen(yield_nones=False):
            if not self.running:
                break
            (_, type_names, path, filename) = event
            full_path = os.path.join(path, filename)
            if any(t in type_names for t in ('IN_CLOSE_WRITE', 'IN_CREATE', 'IN_MOVED_TO')):
                if os.path.isfile(full_path) and should_index(filename) and not self._is_excluded(full_path):
                    self.event_queue.put(('new', full_path))
            elif any(t in type_names for t in ('IN_DELETE', 'IN_MOVED_FROM')):
                self.event_queue.put(('delete', full_path))

    def _is_excluded(self, path):
        for pattern in self.config.get('exclude_patterns', []):
            if re.search(pattern, path):
                return True
        return False

    def _should_skip_file(self, full_path, stat):
        max_size = int(self.config.get('max_file_size_mb', 50)) * 1024 * 1024
        return stat.st_size > max_size

    def process_new_file(self, path):
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            return
        if self._should_skip_file(path, stat):
            return
        ext = os.path.splitext(path)[1].lower()
        content = extract_text(path, ext)
        if content is None:
            content = ""
        owner = get_file_owner(path)
        file_data = {
            'file_path': path,
            'file_name': os.path.basename(path),
            'file_size': stat.st_size,
            'file_mtime': datetime.fromtimestamp(stat.st_mtime).isoformat(),
            'content_text': content[:10000],
            'file_owner': owner
        }
        success = self.uploader.send_files([file_data])
        if success:
            with state_lock:
                known_files[path] = (stat.st_mtime, stat.st_size)
            save_state()
            self._check_triggers_and_report(path, content, owner)
        else:
            self._enqueue_retry(path, file_data)

    def _check_triggers_and_report(self, path, content, owner):
        for word in self.triggers:
            if word.lower() in content.lower():
                incident = {
                    'file_path': path,
                    'file_name': os.path.basename(path),
                    'file_owner': owner,
                    'context': content[:200],
                    'trigger_word': word
                }
                self.uploader.send_incident(incident)
                for plugin in self.plugins:
                    try:
                        plugin.on_incident(incident)
                    except:
                        pass
                break

    def _enqueue_retry(self, path, file_data):
        with state_lock:
            retry_queue_list.append({
                'path': path,
                'attempt': 0,
                'next_retry': time.time() + RETRY_DELAY_BASE,
                'payload': file_data
            })
            save_state()

    def _retry_worker(self):
        while self.running:
            try:
                now = time.time()
                to_retry = []
                with state_lock:
                    for item in retry_queue_list[:]:
                        if item['next_retry'] <= now:
                            to_retry.append(item)
                for item in to_retry:
                    log.info(f"Retrying {item['path']} (attempt {item['attempt']+1})")
                    success = self.uploader.send_files([item['payload']])
                    if success:
                        with state_lock:
                            retry_queue_list.remove(item)
                            if os.path.exists(item['path']):
                                stat = os.stat(item['path'])
                                known_files[item['path']] = (stat.st_mtime, stat.st_size)
                            save_state()
                    else:
                        item['attempt'] += 1
                        if item['attempt'] >= MAX_RETRIES:
                            log.error(f"Max retries reached for {item['path']}, giving up.")
                            with state_lock:
                                retry_queue_list.remove(item)
                                save_state()
                        else:
                            item['next_retry'] = now + RETRY_DELAY_BASE * (2 ** item['attempt'])
                            with state_lock:
                                save_state()
            except Exception as e:
                log.error(f"Retry worker error: {e}")
            time.sleep(10)

    def _batch_sender(self):
        while self.running:
            files = set()
            deletes = set()
            try:
                while True:
                    typ, path = self.event_queue.get_nowait()
                    if typ == 'new':
                        files.add(path)
                    elif typ == 'delete':
                        deletes.add(path)
            except queue.Empty:
                pass
            for path in deletes:
                with state_lock:
                    known_files.pop(path, None)
                    save_state()
            futures = []
            for path in files:
                futures.append(self.executor.submit(self.process_new_file, path))
            for f in futures:
                try:
                    f.result(timeout=30)
                except:
                    pass
            time.sleep(BATCH_INTERVAL)

    def full_scan(self):
        dirs = self.config.get('scan_directories', '/home').split(',')
        for d in dirs:
            d = d.strip()
            if not os.path.isdir(d):
                continue
            for root, dirs, files in os.walk(d):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
                for f in files:
                    if f.startswith('.') or not should_index(f):
                        continue
                    full_path = os.path.join(root, f)
                    if self._is_excluded(full_path):
                        continue
                    try:
                        stat = os.stat(full_path)
                    except:
                        continue
                    if self._should_skip_file(full_path, stat):
                        continue
                    cached = known_files.get(full_path)
                    if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
                        continue
                    self.event_queue.put(('new', full_path))


    def _load_plugins(self):
        self.plugins = []
        plugins_dir = "/opt/astra-monitor/plugins"
        if not os.path.isdir(plugins_dir):
            return
        sys.path.insert(0, plugins_dir)
        for filename in os.listdir(plugins_dir):
            if filename.endswith('.py') and not filename.startswith('_'):
                modname = filename[:-3]
                try:
                    mod = importlib.import_module(modname)
                    self.plugins.append(mod)
                    log.info(f"Plugin loaded: {modname}")
                except Exception as e:
                    log.error(f"Failed to load plugin {modname}: {e}")

    def heartbeat_loop(self):
        last_full_scan = 0
        while self.running:
            resp = self.uploader.send_heartbeat()
            if resp:
                self.triggers = resp.get('triggers', [])
                self.config = resp.get('config', {})
                command = resp.get('command')
                if command == 'full_index':
                    log.info("Received full scan command.")
                    self.full_scan()
                elif command == 'restart':
                    log.info("Restart command received. Restarting...")
                    os.execv(sys.executable, [sys.executable] + sys.argv)
                elif command == 'update':
                    log.info("Update command received.")
                    url = resp.get('download_url')
                    if url:
                        try:
                            r = requests.get(url)
                            with open('/tmp/astra-monitor-agent_latest.deb', 'wb') as f:
                                f.write(r.content)
                            subprocess.run(['sudo', 'dpkg', '-i', '/tmp/astra-monitor-agent_latest.deb'])
                            log.info("Agent updated. Restarting...")
                            os.execv(sys.executable, [sys.executable] + sys.argv)
                        except Exception as e:
                            log.error(f"Update failed: {e}")
            now = time.time()
            if now - last_full_scan > FULL_SCAN_INTERVAL:
                log.info("Starting periodic full scan...")
                self.full_scan()
                last_full_scan = now
            time.sleep(HEARTBEAT_INTERVAL)

    def _start_monitor(self):
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import json as json_lib
        class MonitorHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/metrics':
                    self.send_response(200)
                    self.send_header('Content-type', 'text/plain')
                    self.end_headers()
                    with state_lock:
                        count = len(known_files)
                    self.wfile.write(f"astra_agent_files_scanned {count}\n".encode())
                elif self.path == '/status':
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    status = {
                        'known_files': len(known_files),
                        'retry_queue': len(retry_queue_list),
                        'triggers': self.server.agent.triggers,
                        'config': self.server.agent.config
                    }
                    self.wfile.write(json_lib.dumps(status).encode())
                elif self.path.startswith('/file?'):
                    import urllib.parse
                    params = urllib.parse.parse_qs(self.path.split('?', 1)[1])
                    path = params.get('path', [None])[0]
                    if path and os.path.isfile(path):
                        self.send_response(200)
                        self.send_header('Content-type', 'application/octet-stream')
                        self.end_headers()
                        with open(path, 'rb') as f:
                            self.wfile.write(f.read())
                    else:
                        self.send_response(404)
                        self.end_headers()
                else:
                    self.send_response(404)
                    self.end_headers()
            def do_POST(self):
                if self.path == '/scan':
                    self.server.agent.full_scan()
                    self.send_response(200)
                    self.send_header('Content-type', 'text/plain')
                    self.end_headers()
                    self.wfile.write(b"Scan started")
                else:
                    self.send_response(404)
                    self.end_headers()
        server = HTTPServer(('0.0.0.0', MONITOR_PORT), MonitorHandler)
        server.agent = self
        log.info(f"REST API listening on port {MONITOR_PORT}")
        server.serve_forever()

    def stop(self):
        self.running = False
        self.executor.shutdown(wait=True)

def main():
    config = load_config()
    agent = Agent(config)
    try:
        agent.heartbeat_loop()
    except KeyboardInterrupt:
        agent.stop()
    except Exception as e:
        log.exception(f"Fatal error: {e}")

if __name__ == '__main__':
    main()
