# ============================================================
# Build targets
# ============================================================

.PHONY: build
build: build-local
	@echo "Build complete"

.PHONY: build-local
build-local:
	@echo "Building SAM template (local)..."
	sam build -t template.local.yaml
	@echo "Installing dependencies in Lambda build directories..."
	@for lambda_dir in .aws-sam/build/*/; do \
		cp -r shared "$${lambda_dir}" 2>/dev/null || true; \
		if [ -f shared/requirements.txt ]; then \
			pip install -r shared/requirements.txt -t "$${lambda_dir}" --quiet; \
		fi; \
		lambda_name=$$(basename "$${lambda_dir}"); \
		src_dir=$$(find lambdas -maxdepth 1 -type d | while read d; do \
			if echo "$$lambda_name" | grep -qi "$$(basename $$d)"; then echo $$d; fi; \
		done | head -1); \
		if [ -n "$$src_dir" ] && [ -f "$${src_dir}/requirements.txt" ]; then \
			pip install -r "$${src_dir}/requirements.txt" -t "$${lambda_dir}" --quiet; \
		fi; \
	done

.PHONY: build-prod
build-prod:
	@echo "Building SAM template (prod)..."
	sam build
	@for lambda_dir in .aws-sam/build/*/; do \
		cp -r shared "$${lambda_dir}" 2>/dev/null || true; \
		if [ -f shared/requirements.txt ]; then \
			mkdir -p "$${lambda_dir}lib/python3.13/site-packages"; \
			pip install -r shared/requirements.txt -t "$${lambda_dir}lib/python3.13/site-packages/" --quiet; \
		fi; \
	done

# ============================================================
# Local dev targets
# ============================================================

.PHONY: db-up
db-up:
	@echo "Starting Docker MySQL database..."
	docker compose up -d
	@echo "Waiting for MySQL to be ready..."
	@sleep 5
	@max_attempts=30; \
	attempt=0; \
	while [ $$attempt -lt $$max_attempts ]; do \
		if docker exec chonkychonk-db mysqladmin ping -h localhost -u root -proot_password &> /dev/null; then \
			echo "✓ MySQL is ready"; \
			break; \
		fi; \
		attempt=$$(($$attempt + 1)); \
		echo "Waiting for MySQL... ($$attempt/$$max_attempts)"; \
		sleep 1; \
	done; \
	if [ $$attempt -eq $$max_attempts ]; then \
		echo "✗ MySQL failed to start"; \
		exit 1; \
	fi
	@echo ""
	@echo "Database connection info:"
	@echo "  Host: localhost"
	@echo "  Port: 33306"
	@echo "  Database: chonkychonk"
	@echo "  User: chonky_user"
	@echo "  Password: chonky_password"
	@echo "  Root Password: root_password"
	@echo ""

.PHONY: db-down
db-down:
	@echo "Stopping Docker MySQL database..."
	docker compose down

.PHONY: local
local: db-up build-local
	@echo "Starting SAM local API..."
	@echo "API will be available at: http://localhost:3000"
	@echo ""
	@sam local start-api -t .aws-sam/build/template.yaml --env-vars .env.local.json --docker-network "$(shell basename $(CURDIR))_default"

# ============================================================
# Cleanup
# ============================================================

.PHONY: clean
clean:
	@echo "Cleaning build artifacts..."
	rm -rf .aws-sam

.PHONY: clean-all
clean-all: clean db-down
	@echo "Cleaned all artifacts and stopped containers"

# ============================================================
# Help
# ============================================================

.PHONY: help
help:
	@echo "Available targets:"
	@echo ""
	@echo "Local Development:"
	@echo "  make local       - Start full local dev (Docker + SAM)"
	@echo "  make db-up       - Start Docker MySQL only"
	@echo "  make db-down     - Stop Docker MySQL"
	@echo "  make build-local - Build SAM template for local"
	@echo ""
	@echo "Production:"
	@echo "  make build-prod  - Build SAM template for AWS"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean       - Remove build artifacts"
	@echo "  make clean-all   - Clean + stop containers"
