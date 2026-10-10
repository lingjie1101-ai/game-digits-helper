# Source handoff notes

This repository contains the V3.5 Python source and build metadata. It intentionally excludes the packaged EXE, generated `_internal` runtime, per-user `runtime/` state/logs, captures, and bundled OCR runtime binaries.

For a local Windows build, install the packages in `build-requirements.txt`, then place the official Umi-OCR Rapid 2.1.5 release under `third_party/Umi-OCR` as expected by `BUILD.ps1`. See `THIRD_PARTY.txt` for the upstream source and licenses. Do not commit per-user runtime data or game screenshots.
