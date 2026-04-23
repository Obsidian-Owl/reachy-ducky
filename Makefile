# Reachy Ducky — developer convenience targets.
#
# Most day-to-day tasks use ``uv run`` directly (see CLAUDE.md). This
# Makefile wraps the multi-step local sim flow so motion/lifecycle work
# doesn't need real hardware. The sim target requires the reachy-mini
# package installed with the ``mujoco`` extra — see the "Local sim dev
# flow" block in CLAUDE.md.
.PHONY: sim-daemon sim-stop

# PID file for the background reachy-mini-daemon process. ``sim-daemon``
# writes it; ``sim-stop`` reads + cleans it. Kept out of the tree root
# via a dotfile so ``git status`` doesn't flag it.
SIM_DAEMON_PIDFILE := .sim-daemon.pid

sim-daemon:
	@if [ -f "$(SIM_DAEMON_PIDFILE)" ] && kill -0 "$$(cat $(SIM_DAEMON_PIDFILE))" 2>/dev/null; then \
	  echo "reachy-mini-daemon already running (pid $$(cat $(SIM_DAEMON_PIDFILE)))"; \
	  exit 0; \
	fi
	@echo "Starting reachy-mini-daemon --sim in background..."
	@reachy-mini-daemon --robot-name reachy_mini_sim --sim --headless --localhost-only & \
	  echo $$! > "$(SIM_DAEMON_PIDFILE)"
	@sleep 2
	@echo "reachy-mini-daemon pid $$(cat $(SIM_DAEMON_PIDFILE))"

# Verify the PID still owns a reachy-mini-daemon process before signalling.
# Stale pidfile + OS-recycled PID is the classic hazard (daemon exits
# uncleanly, another process inherits the PID, ``make sim-stop`` kills the
# wrong thing). ``ps -p <pid> -o comm=`` prints the short command name;
# the ``grep -q`` check refuses to kill unless the name matches.
sim-stop:
	@if [ -f "$(SIM_DAEMON_PIDFILE)" ]; then \
	  pid=$$(cat $(SIM_DAEMON_PIDFILE)); \
	  if kill -0 "$$pid" 2>/dev/null; then \
	    comm=$$(ps -p "$$pid" -o comm= 2>/dev/null | tr -d ' '); \
	    if echo "$$comm" | grep -q reachy-mini-daemon; then \
	      echo "Stopping reachy-mini-daemon pid $$pid..."; \
	      kill "$$pid"; \
	    else \
	      echo "PID $$pid does not own reachy-mini-daemon (found '$$comm'); leaving alone"; \
	    fi; \
	  fi; \
	  rm -f "$(SIM_DAEMON_PIDFILE)"; \
	fi
	@echo "sim-daemon stopped"
