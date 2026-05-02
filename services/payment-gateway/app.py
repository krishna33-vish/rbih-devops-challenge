import os
import time
import uuid
import logging
import json
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# ── Config ────────────────────────────────────────────────────────────────────
PROCESSOR_URL = os.environ.get("PROCESSOR_URL", "http://payment-processor:8080")
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "10"))

# ── Structured JSON logging ───────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": "payment-gateway",
            "message": record.getMessage(),
        }
        if record.exc_info:
            log["exception"] = self.formatException(record.exc_info)
        return json.dumps(log)

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="payment-gateway", version="1.0.0", docs_url=None, redoc_url=None)

# ── Prometheus metrics ────────────────────────────────────────────────────────
REQUESTS_TOTAL = Counter(
    "gateway_requests_total",
    "Total payment requests received by the gateway",
    ["status"],
)
REQUEST_DURATION = Histogram(
    "gateway_request_duration_seconds",
    "End-to-end payment request duration at the gateway",
)
PROCESSOR_ERRORS = Counter(
    "gateway_processor_errors_total",
    "Number of errors communicating with the payment-processor",
    ["error_type"],
)

# ── Models ────────────────────────────────────────────────────────────────────
class PaymentRequest(BaseModel):
    amount: float = Field(..., gt=0, description="Payment amount, must be positive")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO 4217 currency code")
    merchant_id: str = Field(..., description="Merchant identifier")
    card_last_four: str = Field(..., min_length=4, max_length=4, description="Last 4 digits of card")

# ── Middleware: request logging ───────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    # Generate a trace ID so we can correlate gateway and processor logs
    trace_id = request.headers.get("X-Trace-Id", uuid.uuid4().hex)
    request.state.trace_id = trace_id
    response = await call_next(request)
    duration = time.time() - start
    logger.info(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "INFO",
        "service": "payment-gateway",
        "message": "request handled",
        "method": request.method,
        "path": request.url.path,
        "status_code": response.status_code,
        "duration_seconds": round(duration, 4),
        "trace_id": trace_id,
    }))
    return response

# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/healthz")
def health():
    return {"status": "healthy", "service": "payment-gateway"}

@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/pay")
async def pay(payment: PaymentRequest, request: Request):
    start = time.time()
    payment_id = f"pay-{uuid.uuid4().hex[:12]}"
    trace_id = getattr(request.state, "trace_id", uuid.uuid4().hex)

    logger.info(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "INFO",
        "service": "payment-gateway",
        "message": "forwarding payment to processor",
        "payment_id": payment_id,
        "amount": payment.amount,
        "currency": payment.currency,
        "merchant_id": payment.merchant_id,
        "trace_id": trace_id,
        # card_last_four is safe to log; never log full card numbers
    }))

    payload = {
        "payment_id": payment_id,
        "amount": payment.amount,
        "currency": payment.currency,
        "merchant_id": payment.merchant_id,
        "card_last_four": payment.card_last_four,
    }

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{PROCESSOR_URL}/process",
                json=payload,
                headers={"X-Trace-Id": trace_id},
            )
            resp.raise_for_status()

    except httpx.TimeoutException:
        PROCESSOR_ERRORS.labels(error_type="timeout").inc()
        REQUESTS_TOTAL.labels(status="error").inc()
        logger.error(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "ERROR",
            "service": "payment-gateway",
            "message": "timeout reaching payment-processor",
            "payment_id": payment_id,
            "trace_id": trace_id,
        }))
        raise HTTPException(status_code=504, detail="Payment processor timeout")

    except httpx.HTTPStatusError as exc:
        PROCESSOR_ERRORS.labels(error_type="http_error").inc()
        REQUESTS_TOTAL.labels(status="error").inc()
        logger.error(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "ERROR",
            "service": "payment-gateway",
            "message": "processor returned error",
            "payment_id": payment_id,
            "processor_status": exc.response.status_code,
            "trace_id": trace_id,
        }))
        raise HTTPException(status_code=502, detail="Payment processor error")

    except httpx.RequestError as exc:
        PROCESSOR_ERRORS.labels(error_type="connection_error").inc()
        REQUESTS_TOTAL.labels(status="error").inc()
        logger.error(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "ERROR",
            "service": "payment-gateway",
            "message": "could not reach payment-processor",
            "payment_id": payment_id,
            "error": str(exc),
            "trace_id": trace_id,
        }))
        raise HTTPException(status_code=503, detail="Payment processor unavailable")

    duration = time.time() - start
    REQUEST_DURATION.observe(duration)
    REQUESTS_TOTAL.labels(status="success").inc()

    logger.info(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "INFO",
        "service": "payment-gateway",
        "message": "payment completed",
        "payment_id": payment_id,
        "trace_id": trace_id,
        "duration_seconds": round(duration, 4),
    }))

    return resp.json()
