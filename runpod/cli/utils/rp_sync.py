"""
Watches a directory for changes and syncs them to a remote directory.
"""

import threading
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver as Observer

from runpod.cli import STOP_EVENT

from .rp_runpodignore import get_ignore_list, should_ignore


class WatcherHandler(FileSystemEventHandler):
    """Watches a directory for changes and syncs them to a remote directory."""

    def __init__(self, action_function, local_path):
        self.action_function = action_function
        self.local_path = local_path

        self.ignore_list = get_ignore_list()
        self.debouncer = None

    def on_any_event(self, event):
        """Called on any event."""
        if event.is_directory or should_ignore(event.src_path, self.ignore_list):
            return

        if self.debouncer is not None:
            self.debouncer.cancel()  # Cancel any existing timer

        # Start a new timer that will call the action function after 1 second
        self.debouncer = threading.Timer(0.5, self.action_function)
        self.debouncer.start()


def start_watcher(action_function, local_path):
    """
    Starts the watcher.
    """
    event_handler = WatcherHandler(action_function, local_path)
    observer = Observer()
    observer.schedule(event_handler, local_path, recursive=True)
    observer.start()

    try:
        while not STOP_EVENT.is_set():
            time.sleep(0.5)
    finally:
        observer.stop()
        observer.join()


def sync_directory(ssh_client, local_path, remote_path):
    """
    Syncs a local directory to a remote directory.
    """

    def sync():
        print("Syncing files...")
        ssh_client.rsync(local_path, remote_path, quiet=True)

    threading.Thread(target=start_watcher, daemon=True, args=(sync, local_path)).start()

    return sync  # For testing purposes
