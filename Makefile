.PHONY: all program-docker program-docker-ngc program-docker-ngc-auto-transport clean bridge-test program-test

all: program-docker

program-docker:
	./scripts/build-program-docker.sh

program-docker-ngc:
	NSPIRE_UI_NGC=TRUE ./scripts/build-program-docker.sh

program-docker-ngc-auto-transport:
	@echo "auto-transport was removed: build program-docker-ngc and arm once with Menu after bridge READY" >&2
	@exit 65

bridge-test:
	./scripts/test-bridge.sh

program-test:
	@task_test_dir=$$(mktemp -d); trap 'rm -f "$$task_test_dir/nav-os-call" "$$task_test_dir/nav-clock" "$$task_test_dir/nav-fragment"; rmdir "$$task_test_dir"' EXIT; \
	$(CC) -Wall -Wextra -Werror src/program/test_nav_os_call.c -o "$$task_test_dir/nav-os-call" && "$$task_test_dir/nav-os-call" && \
	$(CC) -Wall -Wextra -Werror src/program/test_nav_clock.c -o "$$task_test_dir/nav-clock" && "$$task_test_dir/nav-clock" && \
		$(CC) -Wall -Wextra -Werror src/program/test_nav_fragment.c -o "$$task_test_dir/nav-fragment" && "$$task_test_dir/nav-fragment" && \
		python3 scripts/check-ngc-startup-order.py && \
		python3 scripts/check-ngc-safe-loop.py && \
		python3 scripts/check-ngc-irq-window.py && \
		python3 scripts/check-ngc-cpu-irq.py && \
		python3 scripts/check-navnet-bridge-gate.py && \
		if test -f src/program/nspire_ai.elf && test -f dist/nspire_ai.tns; then python3 scripts/check-ngc-relocations.py src/program/nspire_ai.elf dist/nspire_ai.tns; fi && \
		./scripts/test-ngc-lcdinit-candidate.sh && \
		./scripts/test-ngc-stage-upload-gate.sh && \
		./scripts/test-device-artifact-audit.sh

clean:
	rm -rf .build dist
	$(MAKE) -C src/program clean
