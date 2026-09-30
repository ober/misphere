# Compatible with GNU make and FreeBSD make. Command-line variables are passed
# through the environment so filenames never become shell command text.

setup: .venv/.misphere-ready

.venv/.misphere-ready: requirements.txt scripts/setup.sh
	@sh scripts/setup.sh

convert: setup
	@.venv/bin/python scripts/run.py convert

batch: setup
	@.venv/bin/python scripts/run.py batch

inspect: setup
	@.venv/bin/python scripts/run.py inspect

test: setup
	@.venv/bin/python -m unittest discover -s tests -v

help:
	@printf '%s\n' \
	  'make                         Install/check dependencies (same as make setup)' \
	  'make convert INPUT=clip.mp4   Convert to clip-stitched.mp4' \
	  'make convert INPUT=clip.mp4 OUTPUT=panorama.mp4 WIDTH=1440 SECONDS=3' \
	  'make batch INPUT=/videos     Convert to /videos-stitched' \
	  'make inspect INPUT=clip.mp4   Inspect camera metadata' \
	  'make test                    Run synthetic tests' \
	  'Optional settings: PYTHON=python3.12 WIDTH=3456 SECONDS=3 SEAM_DEGREES=1'

.PHONY: setup convert batch inspect test help
