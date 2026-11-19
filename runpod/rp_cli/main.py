"""shared command-line helpers."""

import sys

from runpod.cli import main

if __name__ == "__main__":
    main.run()
else:
    sys.modules[__name__] = main
