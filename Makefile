IMAGE_NAME := psyb0t/telethon-plus
TAG        := latest
TEST_TAG   := $(TAG)-test

-include .env
export

.PHONY: all build build-test run test test-unit lint format login clean help

all: build ## Build image

build: ## Build the Docker image
	docker build -t $(IMAGE_NAME):$(TAG) .

build-test: ## Build the test Docker image
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

test: build-test ## Run integration tests in Docker (needs .env — see .env.example)
	docker build -t $(IMAGE_NAME):testrunner tests/
	docker run --rm \
		--network host \
		-v /var/run/docker.sock:/var/run/docker.sock \
		-v $$(pwd):$$(pwd) \
		-w $$(pwd)/tests \
		-e TELETHON_TESTS_SKIP_BUILD=1 \
		$(IMAGE_NAME):testrunner

test-unit: build ## Run unit tests inside the production image (no Telegram needed)
	docker run --rm \
		-u root \
		-v $$(pwd):$$(pwd) \
		-w $$(pwd) \
		-e PYTHONPATH=$$(pwd) \
		--entrypoint /bin/sh \
		$(IMAGE_NAME):$(TAG) \
		-c 'pip install --quiet pytest==9.0.3 pytest-asyncio==1.3.0 && \
		    python -m pytest tests/unit/ -c tests/unit/pytest.ini'

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

lint: ## Lint Python sources
	python -m flake8 app/ login.py
	python -m pyright app/ login.py

format: ## Format Python sources
	python -m isort app/ login.py
	python -m black app/ login.py

clean: ## Remove built images
	docker rmi $(IMAGE_NAME):$(TAG) || true
	docker rmi $(IMAGE_NAME):$(TEST_TAG) || true
	docker rmi $(IMAGE_NAME):testrunner || true

help: ## Display this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
