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
	find src -type d -name '__pycache__' -prune -exec rm -rf {} \;
	poetry run bin/build_docker.py -u $(DOCKER_USERNAME) -i $(DOCKER_IMAGENAME)

.PHONY: docker-build-dev
docker-build-dev: ## Build Dockerfile.dev with name 'os_credits-dev'
	find src -type d -name '__pycache__' -prune -exec rm -rf {} \;
	docker build -f Dockerfile.dev -t os_credits-dev .

.PHONY: up-dev
up-dev: ## Build Dockerfile.dev with name 'os_credits-dev' and docker-compose up --detach
	docker-compose up --detach

.PHONY: up-dev
down: ## docker-compose down
	docker-compose down

.PHONY: docs
docs: ## Build HTML documentation
	cd docs && $(MAKE) html
