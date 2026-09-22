# Third-party dependencies

The installer uses the packages and versions in `requirements.lock`. The Windows wheels were downloaded from PyPI and verified against their SHA-256 hashes; their original metadata and license files remain inside each wheel. The exact file list and hashes are in `docs/WHEELS_WINDOWS_SHA256.json`.

The agent uses the Hyperliquid and x402-avm/Algorand SDKs, plus NumPy, pandas, requests, keyring and the HTTP components listed in the lock file. Dependency licenses continue to apply to their respective components. This package does not modify or replace their license metadata.

The presentation video source is `media/crear_video.py`. This separate editing tool requires Pillow, FFmpeg and the DejaVu Sans font; it is not needed to run the agent and is not installed on the customer computer.
