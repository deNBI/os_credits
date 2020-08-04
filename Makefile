HOST=127.0.0.1
PORT=8080
DOCKER_USERNAME=$(USER)
DOCKER_IMAGENAME=os_credits


.DEFAULT_GOAL:=help

.PHONY: help
help: ## Display this help message
	@echo 'Usage: make <command>'
	@cat $(MAKEFILE_LIST) | grep '^[a-zA-Z]'  | \
	    sort | \
	    awk -F ':.*?## ' 'NF==2 {printf "  %-26s%s\n", $$1, $$2}'

.PHONY: clean-pyc
clean-pyc: ## Remove python bytecode files and folders such as __pycache__
	find . -name '*.pyc' -exec rm --force {} +
	find . -name '*.pyo' -exec rm --force {} +
	find . -type d -name '__pycache__' -prune -exec rm -rf {} \;
	rm -rf .mypy_cache

.PHONY: clean-build
clean-build: ## Remove any python build artifacts
	rm --force --recursive build/
	rm --force --recursive dist/
	rm --force --recursive *.egg-info

.PHONY: docker-build
docker-build: ## Call bin/build_docker.py with $DOCKER_USERNAME[$USER] and $DOCKER_IMAGENAME[os_credits]
	find . -type d -name '__pycache__' -prune -exec rm -rf {} \;
	poetry run bin/build_docker.py -u $(DOCKER_USERNAME) -i $(DOCKER_IMAGENAME)

.PHONY: docker-build-dev
docker-build-dev: ## Build Dockerfile.dev with name 'os_credits-dev'
	find . -type d -name '__pycache__' -prune -exec rm -rf {} \;
	docker build -f Dockerfile.dev -t os_credits-dev .

.PHONY: docker-run-dev
docker-run-dev: ## Run 'os_credits-dev' inside 'docker-compose.yml' attached - os_credits-dev:80 -> localhost:8000
	poetry run docker-compose up 

.PHONY: docker-project_usage-dev
docker-project_usage-dev: ## Run 'os_credits-dev' and integrate it into the 'dev' profile of 'project_usage'
	docker stop portal_credits || true
	docker rm portal_credits || true
	docker run \
		--publish=8002:80 \
		--name portal_credits \
		--network project_usage_portal \
		--volume $(PWD)/src:/code/src:ro \
		--env-file .env \
		--env MAIL_NOT_STARTTLS=1 \
		--env MAIL_SMTP_SERVER=portal_smtp_server \
		--detach \
		os_credits-dev:latest

.PHONY: docs
docs: ## Build HTML documentation
	cd docs && $(MAKE) html

.PHONY: docs-doctest
docs-doctest: ## Run doctests inside documentation
	cd docs && $(MAKE) doctest

.PHONY: test
test: ## Start tests/docker-compose.yml, run test suite and stop docker-compose
	poetry run docker-compose -f tests/docker-compose.yml up --detach
	@echo 'Waiting until InfluxDB is ready'
	. tests/test.env && until `curl -o /dev/null -s -I -f "http://$$INFLUXDB_HOST:$$INFLUXDB_PORT/ping"`; \
		do printf '.'; \
		sleep 1; \
		done
	poetry run pytest --color=yes tests src || true
	poetry run docker-compose -f tests/docker-compose.yml down --volumes --remove-orphans

.PHONY: test-online
test-online: ## Same as `test` but does also run tests against Perun
	env TEST_ONLINE=1 $(MAKE) test

.PHONY: test-online-only
test-online-only: ## Only run tests against Perun
	poetry run env TEST_ONLINE=1 pytest --color=yes --no-cov tests/test_perun.py

.PHONY: mypy
mypy: ## Run `mypy`, a static type checker for python, see 'htmlcov/mypy/index.html'
	poetry run mypy src/os_credits --html-report=htmlcov/mypy

.PHONY: setup
setup: ## Setup development environment
	@echo 'Requires poetry from - https://poetry.eustace.io/docs/'
	poetry install
	poetry run pre-commit install -t pre-commit
	poetry run pre-commit install -t pre-push
