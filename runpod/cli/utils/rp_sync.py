"""
Watches a directory for changes and syncs them to a remote directory.
"""

import os
import time
import threading

from watchdog.observers.polling import PollingObserver as Observer
from watchdog.events import FileSystemEventHandler


IGNORE_EXTENSIONS = ['.tmp', '.log', '.pyc', '.swp']
IGNORE_FILES = ['ignoreme.txt']

class WatcherHandler(FileSystemEventHandler):
    """Watches a directory for changes and syncs them to a remote directory."""

    def __init__(self, ssh_client, local_path, remote_path):
        self.ssh_client = ssh_client
        self.local_path = local_path
        self.remote_path = remote_path

    def should_ignore(self, file_path):
        """
        Returns True if the file should be ignored, False otherwise.
        """
        if any(file_path.endswith(ext) for ext in IGNORE_EXTENSIONS):
            return True

        if os.path.basename(file_path) in IGNORE_FILES:
            return True

        return False

    def on_modified(self, event):
        """
        Called when a file is modified.
        """
        if event.is_directory or self.should_ignore(event.src_path):
            return
        print(f'File {event.src_path} has been modified.')
        self.ssh_client.rsync(os.path.join(self.local_path, ''), self.remote_path)

    def on_moved(self, event):
        """
        Called when a file is moved or renamed.
        """
        if event.is_directory or self.should_ignore(event.dest_path):
            return
        print(f'File {event.dest_path} has been moved or renamed.')
        self.ssh_client.rsync(os.path.join(self.local_path, ''), self.remote_path)


    def on_created(self, event):
        """
        Called when a file is created.
        """
        if event.is_directory or self.should_ignore(event.src_path):
            return
        print(f'File {event.src_path} has been created.')

    def on_deleted(self, event):
        """
        Called when a file is deleted.
        """
        if event.is_directory or self.should_ignore(event.src_path):
            return
        print(f'File {event.src_path} has been deleted.')

def start_watcher(ssh_client, local_path, remote_path):
    """
    Starts the watcher.
    """
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
    """
    Syncs a local directory to a remote directory.
    """
    threading.Thread(target=start_watcher, args=(ssh_client, local_path, remote_path)).start()
