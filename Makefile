IMAGE_NAME := psyb0t/telethon-plus
TAG        := latest
TEST_TAG   := $(TAG)-test
DEV_IMAGE  := psyb0t/telethon-plus-dev:latest

PYPROJECT := pyproject.toml
BUMP_HOST := bash scripts/bump_exclude_newer.sh $(PYPROJECT)
PY_SOURCES := app login.py tests
# Unit test coverage of app/ below this fails make test-unit, and with it CI.
# Measured 95% on 2026-11-07; raise it as coverage grows, never lower it to
# make a red build pass.
COVERAGE_FLOOR := 93

-include .env
export

UID := $(shell id -u)
GID := $(shell id -g)
DOCKER_SOCK := /var/run/docker.sock
DOCKER_GID := $(shell stat -c '%g' $(DOCKER_SOCK) 2>/dev/null || echo 0)

# Everything dev-side runs inside the dev image; the locked environment is
# baked into /opt/venv there, so the bind mount stays free of a .venv.
DEV_RUN := docker run --rm \
	-u $(UID):$(GID) \
	-e HOME=/tmp \
	-v $(PWD):/work \
	-w /work \
	$(DEV_IMAGE)

DEV_RUN_TTY := docker run --rm -it \
	-u $(UID):$(GID) \
	-e HOME=/tmp \
	-v $(PWD):/work \
	-w /work \
	$(DEV_IMAGE)

# The integration suite starts the production image through the host's
# docker socket and calls it on a published port, so it runs on the host
# network with the repo at its host path.
DEV_RUN_DIND := docker run --rm \
	-u $(UID):$(GID) \
	--group-add $(DOCKER_GID) \
	--network host \
	-e HOME=/tmp \
	-e PYTHONPATH=$(PWD) \
	-e TELETHON_TESTS_SKIP_BUILD=1 \
	-v $(DOCKER_SOCK):$(DOCKER_SOCK) \
	-v $(PWD):$(PWD) \
	-w $(PWD)/tests \
	$(DEV_IMAGE)

.PHONY: all help dev-image shell build build-test run login \
        test test-unit lint format sec audit docs \
        pkg-lock pkg-upgrade pkg-add pkg-remove pkg-update clean

all: build ## Build image

help: ## Display this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

dev-image: ## Build or refresh the sandboxed dev image
	docker build -f Dockerfile.dev -t $(DEV_IMAGE) .

shell: dev-image ## Open a shell in the dev image
	$(DEV_RUN_TTY) bash

build: ## Build the Docker image
	docker build -t $(IMAGE_NAME):$(TAG) .

build-test: ## Build the image the integration tests run
	docker build -t $(IMAGE_NAME):$(TEST_TAG) .

run: build ## Run container locally on :8080 (needs TELETHON_API_ID/HASH/SESSION env)
	@mkdir -p $${PWD}/.cache
	docker run --rm -it \
		-p 8080:8080 \
		-e TELETHON_API_ID \
		-e TELETHON_API_HASH \
		-e TELETHON_SESSION \
		-e TELETHON_HTTP_LISTEN_ADDRESS=0.0.0.0:8080 \
		-v $${PWD}/.cache:/cache \
		$(IMAGE_NAME):$(TAG)

login: build ## Interactive login — writes TELETHON_SESSION to .env automatically
	@SESSION_FILE=.session.tmp && \
	touch "$$SESSION_FILE" && \
	docker run --rm -it \
		-e TELETHON_API_ID \
		-e TELETHON_API_HASH \
		-e TELETHON_SESSION_OUTPUT_FILE=/session \
		-v "$$(pwd)/$$SESSION_FILE:/session" \
		$(IMAGE_NAME):$(TAG) login; \
	SESSION=$$(cat "$$SESSION_FILE" 2>/dev/null); \
	rm -f "$$SESSION_FILE"; \
	if [ -z "$$SESSION" ]; then echo "Login failed or was aborted."; exit 1; fi; \
	if [ -f .env ] && grep -q '^TELETHON_SESSION=' .env; then \
		sed -i "s|^TELETHON_SESSION=.*|TELETHON_SESSION=$$SESSION|" .env; \
		echo "TELETHON_SESSION updated in .env"; \
	else \
		echo "TELETHON_SESSION=$$SESSION" >> .env; \
		echo "TELETHON_SESSION added to .env"; \
	fi

test: build-test dev-image ## Run the integration tests against a real Telegram account (needs .env, see .env.example)
	$(DEV_RUN_DIND) pytest -c pytest.ini

test-unit: dev-image ## Run the unit tests with coverage (no Docker, no Telegram)
	$(DEV_RUN) pytest tests/unit/ -c tests/unit/pytest.ini --cov=app --cov-report=term-missing:skip-covered --cov-fail-under=$(COVERAGE_FLOOR)

lint: dev-image ## Lint Python sources (flake8 + mypy)
	$(DEV_RUN) flake8 $(PY_SOURCES)
	$(DEV_RUN) mypy app login.py

format: dev-image ## Format Python sources
	$(DEV_RUN) isort $(PY_SOURCES)
	$(DEV_RUN) black $(PY_SOURCES)

sec: dev-image ## Security scan (semgrep + bandit + pip-audit) -> sec.sarif for the Security tab; reports, never fails
	$(DEV_RUN) bash scripts/sec.sh

audit: dev-image ## Check the locked runtime dependencies for advisories (fails on a finding)
	$(DEV_RUN) sh -ec 'uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt >/dev/null; pip-audit --no-deps --disable-pip -r /tmp/requirements.txt'

# The GitHub Pages build of docs/, with the image GitHub itself uses, into a
# gitignored directory. Pages serves it under /docker-telethon-plus/.
DOCS_BUILD_IMAGE := ghcr.io/actions/jekyll-build-pages:v1.0.13@sha256:6791ebfd912185ed59bfb5fb102664fa872496b79f87ff8b9cfba292a7345041
DOCS_OUT := .testing/docs-site

docs: ## Build the documentation site locally into .testing/docs-site
	docker run --rm -u $(UID):$(GID) -e HOME=/tmp -e GITHUB_WORKSPACE=/work \
		-e GITHUB_REPOSITORY=psyb0t/docker-telethon-plus -e INPUT_SOURCE=docs \
		-e INPUT_DESTINATION=$(DOCS_OUT) -e INPUT_VERBOSE=false \
		-v $(PWD):/work $(DOCS_BUILD_IMAGE)

# Every dependency change bumps [tool.uv] exclude-newer to seven days ago
# first, so the supply-chain age gate is anchored to the moment of the change.
pkg-lock: dev-image ## Refresh uv.lock (honors exclude-newer)
	$(DEV_RUN) uv lock

pkg-upgrade: dev-image ## Bump exclude-newer and refresh the lock with the newest allowed pins
	$(BUMP_HOST)
	$(DEV_RUN) uv lock --upgrade

pkg-add: dev-image ## Add a package (usage: make pkg-add PKG=name==ver [GROUP=dev])
	@test -n "$(PKG)" || (echo "usage: make pkg-add PKG=name==ver [GROUP=dev]" >&2; exit 1)
	$(BUMP_HOST)
	$(DEV_RUN) uv add --no-sync $(if $(GROUP),--group $(GROUP),) $(PKG)

pkg-remove: dev-image ## Remove a package (usage: make pkg-remove PKG=name [GROUP=dev])
	@test -n "$(PKG)" || (echo "usage: make pkg-remove PKG=name [GROUP=dev]" >&2; exit 1)
	$(BUMP_HOST)
	$(DEV_RUN) uv remove --no-sync $(if $(GROUP),--group $(GROUP),) $(PKG)

pkg-update: dev-image ## Update one package (usage: make pkg-update PKG=name V=version [GROUP=dev])
	@test -n "$(PKG)" && test -n "$(V)" || (echo "usage: make pkg-update PKG=name V=version [GROUP=dev]" >&2; exit 1)
	$(BUMP_HOST)
	$(DEV_RUN) uv add --no-sync $(if $(GROUP),--group $(GROUP),) $(PKG)==$(V)

clean: ## Remove built images
	docker rmi $(IMAGE_NAME):$(TAG) || true
	docker rmi $(IMAGE_NAME):$(TEST_TAG) || true
	docker rmi $(DEV_IMAGE) || true
