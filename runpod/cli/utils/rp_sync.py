"""
Watches a directory for changes and syncs them to a remote directory.
"""

import os
import time
import threading

from watchdog.observers.polling import PollingObserver as Observer
from watchdog.events import FileSystemEventHandler

from .rp_runpodignore import get_ignore_list, should_ignore

class WatcherHandler(FileSystemEventHandler):
    """Watches a directory for changes and syncs them to a remote directory."""

    def __init__(self, action_function, local_path):
        self.action_function = action_function
        self.local_path = local_path

        self.ignore_list = get_ignore_list()

    def on_any_event(self, event):
        """ Called on any event. """
        if event.is_directory or should_ignore(event.src_path, self.ignore_list):
            return

        file_name = os.path.basename(event.src_path)
        print(f"Syncing {file_name}...")

        self.action_function()


def start_watcher(action_function, local_path, testing=False):
    """
    Starts the watcher.
    """
    event_handler = WatcherHandler(action_function, local_path)
    observer = Observer()
    observer.schedule(event_handler, local_path, recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(1)

            if testing:
                raise KeyboardInterrupt
    except KeyboardInterrupt:
        observer.stop()

    observer.join()

def sync_directory(ssh_client, local_path, remote_path):
    """
    Syncs a local directory to a remote directory.
    """
    def sync():
        ssh_client.rsync(local_path, remote_path, quiet=True)

    threading.Thread(target=start_watcher, args=(sync, local_path)).start()

    return sync # For testing purposes
