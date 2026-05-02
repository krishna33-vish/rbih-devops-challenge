# Payment Platform — RBIH DevOps Challenge

Two payment microservices deployed on a local Kubernetes cluster.

```
Client → POST /pay → payment-gateway → payment-processor
```

---

## Step 1 — Install prerequisites

**macOS**
```bash
brew install kind kubectl
```
Also make sure **Docker Desktop** is installed and running.

**Linux**
```bash
# kind
curl -Lo /usr/local/bin/kind https://kind.sigs.k8s.io/dl/v0.23.0/kind-linux-amd64
chmod +x /usr/local/bin/kind

# kubectl
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
chmod +x kubectl && sudo mv kubectl /usr/local/bin/kubectl

# Docker (if not installed)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker
```

---

## Step 2 — Clone and run

```bash
git clone <repo-url>
cd payment-platform
make setup
```

That's it. The command will:
- Build both Docker images
- Create a local Kubernetes cluster
- Deploy all services
- Wait until everything is ready

---

## Step 3 — Test

```bash
make test
```

Expected output:

```json
--- Health check ---
{
    "status": "healthy",
    "service": "payment-gateway"
}

--- Test payment ---
{
    "payment_id": "pay-a1b2c3d4e5f6",
    "transaction_id": "txn-9f8e7d6c5b4a",
    "status": "approved",
    "amount": 99.99,
    "currency": "USD"
}
```

---

## Other useful commands

```bash
make status     # show running pods
make logs       # tail live logs from both services
make teardown   # delete the cluster when done
```

---

## Manual curl commands

If you want to test manually, first run this in one terminal:

```bash
kubectl port-forward svc/payment-gateway 8080:8080 -n payments
```

Then in another terminal:

```bash
# Health check
curl http://localhost:8080/healthz

# Metrics
curl http://localhost:8080/metrics

# Send a payment
curl -X POST http://localhost:8080/pay \
  -H "Content-Type: application/json" \
  -d '{
    "amount": 49.99,
    "currency": "INR",
    "merchant_id": "merchant-001",
    "card_last_four": "4242"
  }'
```

---

## Architecture

```
Internet / Client
       │
       ▼  POST /pay
┌─────────────────────┐
│   payment-gateway   │  port-forward 8080
│   port 8080         │
└────────┬────────────┘
         │ ClusterIP (HTTP, internal only)
         │ NetworkPolicy: only gateway can reach processor
         ▼
┌─────────────────────┐
│  payment-processor  │  No external access
│  port 8080          │
└─────────────────────┘

Namespace: payments
Cluster:   kind (local)
```

---

## Repository layout

```
payment-platform/
├── services/
│   ├── payment-gateway/        # FastAPI app + Dockerfile
│   └── payment-processor/      # FastAPI app + Dockerfile
├── k8s/
│   ├── namespace.yaml
│   ├── gateway/                # Deployment, Service, NetworkPolicy
│   └── processor/              # Deployment, Service, NetworkPolicy
├── monitoring/
│   └── alerts.yaml             # Prometheus alert rules
├── kind-config.yaml            # cluster definition
├── Makefile                    # all commands
└── README.md
```

---

## API

### `POST /pay`

```json
{
  "amount": 99.99,
  "currency": "USD",
  "merchant_id": "merchant-001",
  "card_last_four": "4242"
}
```

| Field | Type | Notes |
|---|---|---|
| `amount` | float | Must be > 0 |
| `currency` | string | 3-letter ISO code (USD, INR, GBP) |
| `merchant_id` | string | Any string |
| `card_last_four` | string | Exactly 4 digits |

Response codes:

| Code | Meaning |
|---|---|
| `200` | Payment approved |
| `503` | Processor unreachable |
| `504` | Processor timed out |
| `502` | Processor returned an error |

### `GET /healthz` — both services
### `GET /metrics` — both services (Prometheus format)

---

## What was done and why

### Security

**NetworkPolicy** — The processor only accepts traffic from the gateway pod. Nothing else in the cluster can reach it directly, even if another service is compromised.

**Non-root containers** — Both services run as UID 1001 with all Linux capabilities dropped. Even if an attacker got into the container they cannot escalate privileges.

**Multi-stage Docker builds** — The build toolchain is thrown away. The runtime image contains only what is needed to run the app, keeping the attack surface small.

**No secrets in manifests** — `PROCESSOR_URL` is a non-sensitive internal URL. In production, credentials would be injected via Kubernetes Secrets backed by a secrets manager.

*Not implemented (time trade-off):* mTLS between services. NetworkPolicy provides network-layer isolation as a compensating control. mTLS via cert-manager would be the next step.

### Observability

**Structured JSON logs** — Every log line is a JSON object with `timestamp`, `level`, `service`, `trace_id`. No custom parser needed for any log aggregation platform.

**Trace ID** — The gateway generates a `trace_id` per request and passes it to the processor. Grep one ID to see the full journey across both services.

**Prometheus metrics** — Custom counters and histograms on both services:
- `gateway_requests_total{status}` — success vs error counts
- `gateway_request_duration_seconds` — latency histogram
- `gateway_processor_errors_total{error_type}` — timeout / connection / HTTP breakdown

**Alert rules** (`monitoring/alerts.yaml`) — 5 rules covering service down, high error rate, processor connection failures, p95 latency breach, and crash-looping pods. Each has a runbook URL for on-call engineers.

### Reliability

**Liveness + readiness probes** — Readiness gates traffic until the app is ready. Liveness restarts pods that become stuck.

**10-second timeout** — The gateway times out calls to the processor after 10 seconds, preventing a slow processor from cascading into a gateway outage.

**Graceful shutdown** — Pods get 30 seconds to finish in-flight requests before being killed.

### What I would add in production

| Item | Why |
|---|---|
| Multiple replicas + pod spread across nodes | Run at least 2 replicas per service with topologySpreadConstraints so a single node failure does not take the service down. Add PodDisruptionBudget to prevent both pods being evicted during node upgrades. |
| mTLS via cert-manager | Encrypt and authenticate service-to-service traffic |
| Helm chart | One chart, different values per environment (dev/staging/prod) |
| HorizontalPodAutoscaler | Auto-scale on CPU/RPS |
| Grafana dashboard | Pre-built golden signals dashboard for on-call engineers |
| GitHub Actions CI | Build, test, scan images on every PR |
| Trivy image scanning | Catch known CVEs in base images before deploy |
