# Observability Stack

Distributed tracing with Jaeger for kinonee.

## Quick Start

```bash
make tracing        # Start backend + Jaeger
make tracing-down   # Stop everything
```

## Jaeger UI

Open http://localhost:16686 to view traces.

You'll see:
- Request waterfall (API -> DB -> Redis)
- Per-operation timing
- Error traces

## Enable Prometheus & Grafana

Uncomment the services in `docker-compose.observability.yml` to enable:
- **Prometheus** (http://localhost:9090) - Metrics
- **Grafana** (http://localhost:3001) - Dashboards

## Useful PromQL Queries

```promql
# Request latency p95
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))

# Error rate
sum(rate(http_requests_total{status_code=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))

# Cache hit rate
sum(rate(cache_hits_total[5m])) / (sum(rate(cache_hits_total[5m])) + sum(rate(cache_misses_total[5m])))
```
