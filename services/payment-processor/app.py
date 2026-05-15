import os
import time
import uuid
import logging
import json
from datetime import datetime, timezone

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, condecimal
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# ── Structured JSON logging ──────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": "payment-processor",
            "message": record.getMessage(),
        }
        if record.exc_info:
            log["exception"] = self.formatException(record.exc_info)
        return json.dumps(log)

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger(__name__)

# ── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="payment-processor", version="1.0.0", docs_url=None, redoc_url=None)

# ── Prometheus metrics ───────────────────────────────────────────────────────
REQUESTS_TOTAL = Counter(
    "processor_requests_total",
    "Total payment processing requests",
    ["status"],
)
REQUEST_DURATION = Histogram(
    "processor_request_duration_seconds",
    "Payment processing request duration",
)

# ── Models ───────────────────────────────────────────────────────────────────
class PaymentRequest(BaseModel):
    payment_id: str = Field(..., description="Unique payment identifier")
    amount: float = Field(..., gt=0, description="Payment amount, must be positive")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO 4217 currency code")
    merchant_id: str = Field(..., description="Merchant identifier")
    card_last_four: str = Field(..., min_length=4, max_length=4, description="Last 4 digits of card")

class PaymentResponse(BaseModel):
    payment_id: str
    transaction_id: str
    status: str
    processed_at: str
    amount: float
    currency: str

# ── Middleware: request logging ───────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    logger.info(
        "request handled",
        extra={},
    )
    # log as structured fields by re-logging with extra context
    logger.info(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "INFO",
        "service": "payment-processor",
        "message": "request handled",
        "method": request.method,
        "path": request.url.path,
        "status_code": response.status_code,
        "duration_seconds": round(duration, 4),
    }))
    return response

# ── Endpoints ────────────────────────────────────────────────────────────────
@app.get("/healthz")
def health():
    return {"status": "healthy", "service": "payment-processor"}

@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/process", response_model=PaymentResponse)
def process_payment(payment: PaymentRequest, request: Request):
    start = time.time()
    # Read trace_id from header that gateway sent
    trace_id = request.headers.get("X-Trace-Id", "unknown")
    logger.info(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "INFO",
        "service": "payment-processor",
        "message": "processing payment",
        "payment_id": payment.payment_id,
        "amount": payment.amount,
        "currency": payment.currency,
        "merchant_id": payment.merchant_id,
        # Never log full card data — only last four is already safe
        "card_last_four": payment.card_last_four,
        "trace_id": trace_id,
    }))

    # Simulate processing logic
    transaction_id = f"txn-{uuid.uuid4().hex[:12]}"
    processed_at = datetime.now(timezone.utc).isoformat()

    duration = time.time() - start
    REQUEST_DURATION.observe(duration)
    REQUESTS_TOTAL.labels(status="success").inc()

    logger.info(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "INFO",
        "service": "payment-processor",
        "message": "payment processed successfully",
        "payment_id": payment.payment_id,
        "transaction_id": transaction_id,
    }))

    return PaymentResponse(
        payment_id=payment.payment_id,
        transaction_id=transaction_id,
        status="approved",
        processed_at=processed_at,
        amount=payment.amount,
        currency=payment.currency,
    )
