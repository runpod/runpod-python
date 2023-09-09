import os
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import threading

IGNORE_EXTENSIONS = ['.tmp', '.log', '.pyc', '.swp']
IGNORE_FILES = ['ignoreme.txt']

class WatcherHandler(FileSystemEventHandler):
    def __init__(self, ssh_client, local_path, remote_path):
        self.ssh_client = ssh_client
        self.local_path = local_path
        self.remote_path = remote_path

    def should_ignore(self, file_path):
        # Check for ignored extensions
        if any(file_path.endswith(ext) for ext in IGNORE_EXTENSIONS):
            return True

        # Check for ignored filenames
        if os.path.basename(file_path) in IGNORE_FILES:
            return True

        return False

    def on_modified(self, event):
        if event.is_directory or self.should_ignore(event.src_path):
            return
        print(f'File {event.src_path} has been modified.')
        self.ssh_client.rsync(os.path.join(self.local_path, ''), self.remote_path)

    def on_created(self, event):
        if event.is_directory or self.should_ignore(event.src_path):
            return
        print(f'File {event.src_path} has been created.')

    def on_deleted(self, event):
        if event.is_directory or self.should_ignore(event.src_path):
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
