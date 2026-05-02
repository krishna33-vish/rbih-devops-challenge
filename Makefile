CLUSTER_NAME  := payment-platform
NAMESPACE     := payments
GATEWAY_IMG   := rbih-hiring/devops-challenge/payment-gateway:1.0.0
PROCESSOR_IMG := rbih-hiring/devops-challenge/payment-processor:1.0.0

.PHONY: setup test logs status teardown

setup:
	@echo "\n>>> [1/5] Building images..."
	docker build -t $(PROCESSOR_IMG) services/payment-processor/
	docker build -t $(GATEWAY_IMG)   services/payment-gateway/

	@echo "\n>>> [2/5] Creating kind cluster..."
	@if kind get clusters 2>/dev/null | grep -q "^$(CLUSTER_NAME)$$"; then \
		echo "Cluster '$(CLUSTER_NAME)' already exists, skipping."; \
	else \
		kind create cluster --name $(CLUSTER_NAME) --config kind-config.yaml; \
	fi

	@echo "\n>>> [3/5] Loading images into cluster..."
	kind load docker-image $(PROCESSOR_IMG) --name $(CLUSTER_NAME)
	kind load docker-image $(GATEWAY_IMG)   --name $(CLUSTER_NAME)

	@echo "\n>>> [4/5] Deploying services..."
	kubectl apply -f k8s/namespace.yaml
	kubectl apply -f k8s/processor/
	kubectl apply -f k8s/gateway/

	@echo "\n>>> [5/5] Waiting for pods to be ready..."
	kubectl rollout status deployment/payment-processor -n $(NAMESPACE) --timeout=120s
	kubectl rollout status deployment/payment-gateway   -n $(NAMESPACE) --timeout=120s

	@echo "\n✅ Done! Run: make test"

test:
	@echo "\n>>> Clearing port 8080 if already in use..."
	@lsof -ti:8080 | xargs kill -9 2>/dev/null || true
	@sleep 1
	@echo "\n>>> Running health check and test payment..."
	@kubectl port-forward svc/payment-gateway 8080:8080 -n $(NAMESPACE) & \
	PF_PID=$$!; \
	sleep 4; \
	echo "\n--- Health check ---"; \
	curl -sf http://localhost:8080/healthz | python3 -m json.tool; \
	echo "\n--- Test payment ---"; \
	curl -sf -X POST http://localhost:8080/pay \
		-H "Content-Type: application/json" \
		-d '{"amount":934.99,"currency":"INR","merchant_id":"merchant-011","card_last_four":"4242"}' \
		| python3 -m json.tool; \
	kill $$PF_PID 2>/dev/null || true

logs:
	kubectl logs -l app=payment-gateway   -n $(NAMESPACE) --follow --prefix &
	kubectl logs -l app=payment-processor -n $(NAMESPACE) --follow --prefix

status:
	kubectl get pods -n $(NAMESPACE)

teardown:
	kind delete cluster --name $(CLUSTER_NAME)
	@echo "✅ Cluster deleted."
