import os
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import threading

class WatcherHandler(FileSystemEventHandler):
    def __init__(self, ssh_client, local_path, remote_path):
        self.ssh_client = ssh_client
        self.local_path = local_path
        self.remote_path = remote_path

    def on_modified(self, event):
        if event.is_directory:
            return
        print(f'File {event.src_path} has been modified.')
        self.ssh_client.rsync(os.path.join(self.local_path, ''), self.remote_path)

    def on_created(self, event):
        if event.is_directory:
            return
        print(f'File {event.src_path} has been created.')

    def on_deleted(self, event):
        if event.is_directory:
            return
        print(f'File {event.src_path} has been deleted.')

def start_watcher(ssh_client, local_path, remote_path):
    event_handler = WatcherHandler(ssh_client, local_path, remote_path)
    observer = Observer()
    observer.schedule(event_handler, local_path, recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()

def sync_directory(ssh_client, local_path, remote_path):
    t = threading.Thread(target=start_watcher, args=(ssh_client, local_path, remote_path,))
    t.start()
