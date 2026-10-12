# Thin wrapper so `make <target>` works too. The real logic lives in dev.sh
# (make is not installed on the Windows host, dev.sh is the portable path).
.PHONY: up down reset rebuild logs ps sh psql test lint fmt types check migrate seed drift downgrade doctor

up down reset rebuild logs ps sh psql test lint fmt types check migrate seed drift downgrade doctor:
	@bash ./dev.sh $@

revision:
	@bash ./dev.sh revision "$(M)"
