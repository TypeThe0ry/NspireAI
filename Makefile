.PHONY: all ui extension extension-docker clean bridge-test

all: ui

ui:
	./scripts/build-ui.sh

extension:
	$(MAKE) -C src/extension
	mkdir -p dist
	cp src/extension/nspire_ai_nav.luax.tns dist/nspire_ai_nav.luax.tns

extension-docker:
	./scripts/build-extension-docker.sh

bridge-test:
	./scripts/test-bridge.sh

clean:
	rm -rf .build dist
	$(MAKE) -C src/extension clean || true
