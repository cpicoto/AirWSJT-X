"""Collect Piper's native runtime, phonemizer data and Python modules."""

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("piper")

