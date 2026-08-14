"""keiser-garmin-sync: sync Keiser M Series indoor rides to Garmin Connect.

Cloud-to-cloud, no Bluetooth hardware and no third-party bridge service. Usable
as a one-shot CLI (``keiser-garmin-sync sync``) or a long-running service
(``keiser-garmin-sync serve`` / container).
"""
from __future__ import annotations

__all__ = ["__version__"]
__version__ = "1.1.0"
