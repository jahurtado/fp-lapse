# fp-lapse — common workflow shortcuts. Run `make` to list them.
#
# All targets that hit the Pi use the `pi3` SSH alias (configured in
# ~/.ssh/config). Override with `PI=other-host make deploy` etc.

.DEFAULT_GOAL := help
.PHONY: help test run emulator deploy sync restart ship logs status state \
        frame e2e shell clean

PI       ?= pi3
PI_DIR   ?= ~/fp-lapse
PORT     ?= 9999
PY       := uv run python

# ----------------------------------------------------------------------
# Help
# ----------------------------------------------------------------------

help:
	@printf "\nfp-lapse — common targets\n\n"
	@printf "  Dev (on this Mac)\n"
	@printf "    make test       — full unittest suite\n"
	@printf "    make run        — launch the app with Tk mocks + control server\n"
	@printf "    make emulator   — alias of \`make run\`\n"
	@printf "\n  Deploy (Mac → Pi)\n"
	@printf "    make deploy     — rsync --delete the repo to the Pi\n"
	@printf "    make sync       — uv sync on Pi (after version / deps bump)\n"
	@printf "    make restart    — restart fp-lapse.service on the Pi\n"
	@printf "    make ship       — deploy + restart + status (typical flow)\n"
	@printf "\n  Inspect the running Pi service\n"
	@printf "    make status     — systemctl status\n"
	@printf "    make logs       — journalctl -f (Ctrl+C to stop)\n"
	@printf "    make state      — GET /state from the control server\n"
	@printf "    make frame      — save the rendered frame and \`open\` it on Mac\n"
	@printf "\n  Tests\n"
	@printf "    make e2e        — run scripts/e2e_smoke.py against the Pi\n"
	@printf "\n  Misc\n"
	@printf "    make shell      — ssh into the Pi\n"
	@printf "    make clean      — remove __pycache__ and *.pyc locally\n"
	@printf "\n  Override host / dir / port with:\n"
	@printf "    PI=pi3 PI_DIR=~/fp-lapse PORT=9999 make <target>\n\n"

# ----------------------------------------------------------------------
# Dev (Mac)
# ----------------------------------------------------------------------

test:
	$(PY) -m unittest discover -s tests

run:
	FP_LAPSE_MOCK=1 FP_LAPSE_CONTROL=1 FP_LAPSE_CONTROL_PORT=$(PORT) \
		$(PY) -m fp_lapse

emulator: run

# ----------------------------------------------------------------------
# Deploy (Mac → Pi)
# ----------------------------------------------------------------------

# `--delete` is load-bearing: without it, files removed from the repo
# linger on the Pi. The four excludes are explained in CONTRIBUTING.md.
deploy:
	rsync -av --delete \
		--exclude '.git' --exclude '.venv' \
		--exclude '__pycache__' --exclude 'runtime' \
		./ $(PI):$(PI_DIR)/

# Re-install the package on the Pi so installed metadata (notably the
# version read by `importlib.metadata`) matches `pyproject.toml`.
# Needed after a version bump or a deps change. Source-only edits are
# already picked up via the editable install.
sync:
	ssh $(PI) 'cd $(PI_DIR) && $$HOME/.local/bin/uv sync --extra pi --python /usr/bin/python3'

restart:
	ssh $(PI) 'sudo systemctl restart fp-lapse.service'
	@sleep 3
	@ssh $(PI) 'systemctl is-active fp-lapse.service'

ship: deploy restart
	@$(MAKE) -s state

# ----------------------------------------------------------------------
# Inspect (Pi)
# ----------------------------------------------------------------------

status:
	@ssh $(PI) 'systemctl status fp-lapse.service --no-pager'

logs:
	ssh $(PI) 'sudo journalctl -u fp-lapse.service -f'

state:
	@ssh $(PI) 'curl -s http://127.0.0.1:$(PORT)/state' | python3 -m json.tool

frame:
	@ssh $(PI) 'curl -s http://127.0.0.1:$(PORT)/frame.png' > /tmp/fp-lapse-frame.png
	@open /tmp/fp-lapse-frame.png

# ----------------------------------------------------------------------
# E2E (Pi)
# ----------------------------------------------------------------------

# Sensible defaults for a 25 s run with the first config. For
# anything else, ssh in and call the script directly:
#   ssh pi3 'sudo python3 ~/fp-lapse/scripts/e2e_smoke.py \
#       --config "Fast 3x 1/50" --seconds 15 --min-shots 6 --journal'
E2E_ARGS ?= --seconds 25 --min-shots 4 --journal

e2e:
	@ssh $(PI) "sudo python3 $(PI_DIR)/scripts/e2e_smoke.py $(E2E_ARGS)"

# ----------------------------------------------------------------------
# Misc
# ----------------------------------------------------------------------

shell:
	ssh $(PI)

clean:
	find . -name '__pycache__' -type d -exec rm -rf {} +
	find . -name '*.pyc' -delete
