# Kagenti Deployment Guide

Deploy the AI Observability Summarizer (MCP Server + Metrics UI) on OpenShift via the [Kagenti](https://github.com/kagenti/kagenti) platform with MLflow tracing for agent observability.

## Prerequisites

| Component | Requirement |
|-----------|-------------|
| OpenShift | 4.16+ with `oc` CLI authenticated |
| Kagenti | Installed in `kagenti-system` namespace ([installation guide](https://github.com/kagenti/kagenti)) |
| MLflow | Running instance accessible in-cluster (e.g. `mlflow` namespace) |
| LLM | Llama Stack-compatible inference service (e.g. `lsd-llama32-3b-service` in `serving` namespace) |
| Prometheus | Thanos/Prometheus endpoint for metrics collection |
| Container images | Built and pushed to a registry accessible from the cluster |

## Architecture

```
+---------------------+       +---------------------+       +-------------------+
|   Metrics UI        |------>|   MCP Server        |------>| Prometheus/Thanos |
|   (Streamlit)       |       |   (FastMCP)         |------>| Tempo             |
|   Port: 8501        |       |   Port: 8085        |------>| Korrel8r          |
+---------------------+       +---------------------+       +-------------------+
         |                             |
         v                             v
+---------------------+       +---------------------+
|   LLM Service       |       |   MLflow             |
|   (Llama Stack)     |       |   (Tracing)          |
+---------------------+       +---------------------+
```

Both components are deployed as Kagenti `Agent` custom resources, which manage the underlying Deployments and Services.

## Step 1: Build and Push Container Images

From the repository root:

```bash
# Build for amd64 (required for most OpenShift clusters)
make build-ui REGISTRY=quay.io ORG=<your-org> VERSION=1.0.0
make build-mcp-server REGISTRY=quay.io ORG=<your-org> VERSION=1.0.0

# Push to registry
make push-ui REGISTRY=quay.io ORG=<your-org> VERSION=1.0.0
make push-mcp-server REGISTRY=quay.io ORG=<your-org> VERSION=1.0.0
```

> **Note**: The Makefile defaults to `PLATFORM=linux/amd64`. If building on Apple Silicon, this is handled automatically via `podman buildx build --platform`.

## Step 2: Discover Cluster Services

Before deploying, identify the in-cluster URLs for your services:

```bash
# Find MLflow
oc get svc -n mlflow
# Example: mlflow-service.mlflow.svc.cluster.local:5000

# Find LLM inference service
oc get svc -n serving | grep llama
# Example: lsd-llama32-3b-service.serving.svc.cluster.local:8321

# Verify Thanos/Prometheus
oc get svc -n openshift-monitoring | grep thanos
# Example: thanos-querier.openshift-monitoring.svc.cluster.local:9091

# Verify Tempo
oc get svc -n observability-hub | grep tempo
# Example: tempo-tempostack-gateway.observability-hub.svc.cluster.local:8080
```

## Step 3: Create Namespace and RBAC

Apply the namespace, ServiceAccount, token secret, CA ConfigMap, and ClusterRoleBindings:

```bash
oc apply -f deploy/kagenti/namespace-rbac.yaml
```

This creates:
- **Namespace** `ai-observability`
- **ServiceAccount** `mcp-analyzer` with a token Secret for Thanos authentication
- **ConfigMap** `mcp-server-trusted-ca-bundle` with OpenShift service CA injection
- **ClusterRoleBindings** for `view`, `cluster-monitoring-view`, `openshift-cluster-monitoring-view`, `grafana-prometheus-reader`, and `korrel8r-view`

Verify the token was generated:

```bash
oc get secret mcp-analyzer -n ai-observability -o jsonpath='{.data.token}' | base64 -d | head -c 50
```

## Step 4: Customize Agent CRs

Edit the Agent CR files to match your environment. The key values to update:

### `deploy/kagenti/mcp-server-agent.yaml`

| Environment Variable | Description | Example |
|---------------------|-------------|---------|
| `LLAMA_STACK_URL` | LLM inference endpoint | `http://lsd-llama32-3b-service.serving.svc.cluster.local:8321/v1/openai/v1` |
| `LLM_URL` | Same as LLAMA_STACK_URL for this deployment | (same as above) |
| `MLFLOW_TRACKING_URI` | MLflow server URL | `http://mlflow-service.mlflow.svc.cluster.local:5000` |
| `PROMETHEUS_URL` | Thanos/Prometheus endpoint | `https://thanos-querier.openshift-monitoring.svc.cluster.local:9091` |
| `TEMPO_URL` | Tempo gateway endpoint | `https://tempo-tempostack-gateway.observability-hub.svc.cluster.local:8080` |
| `KORREL8R_URL` | Korrel8r service URL | `https://korrel8r-summarizer.openshift-cluster-observability-operator.svc.cluster.local:9443` |
| `NAMESPACE` | Deployment namespace | `ai-observability` |

### `deploy/kagenti/ui-agent.yaml`

| Environment Variable | Description | Example |
|---------------------|-------------|---------|
| `LLAMA_STACK_URL` | LLM inference endpoint | `http://lsd-llama32-3b-service.serving.svc.cluster.local:8321/v1/openai/v1` |
| `MCP_SERVER_URL` | MCP Server service URL | `http://aiobs-mcp-server.ai-observability.svc.cluster.local:8085` |
| `MLFLOW_TRACKING_URI` | MLflow server URL | `http://mlflow-service.mlflow.svc.cluster.local:5000` |

Also update `image` fields in both files if using a different registry/tag.

## Step 5: Deploy Agent CRs

```bash
# Deploy MCP Server first (UI depends on it)
oc apply -f deploy/kagenti/mcp-server-agent.yaml

# Wait for MCP Server to be ready
oc wait --for=condition=Ready agent/aiobs-mcp-server -n ai-observability --timeout=120s

# Deploy UI
oc apply -f deploy/kagenti/ui-agent.yaml

# Wait for UI to be ready
oc wait --for=condition=Ready agent/aiobs-metrics-ui -n ai-observability --timeout=120s
```

## Step 6: Create Routes

```bash
oc apply -f deploy/kagenti/routes.yaml
```

Get the route URLs:

```bash
oc get routes -n ai-observability
```

## Verification

### Check pod status

```bash
oc get pods -n ai-observability
# Both pods should be Running
```

### Check MCP Server health

```bash
MCP_ROUTE=$(oc get route aiobs-mcp-server-route -n ai-observability -o jsonpath='{.spec.host}')
curl -k "https://${MCP_ROUTE}/health"
# Expected: {"status": "healthy", ...}
```

### Check MLflow tracing

```bash
oc logs -n ai-observability -l app=aiobs-mcp-server-app --tail=50 | grep -i mlflow
# Expected: "MLflow tracing initialized: uri=http://mlflow-service.mlflow.svc.cluster.local:5000 experiment=ai-observability"
```

### Access the UI

```bash
UI_ROUTE=$(oc get route aiobs-metrics-ui-route -n ai-observability -o jsonpath='{.spec.host}')
echo "Open: https://${UI_ROUTE}"
```

### Test chat with tracing

1. Open the UI in a browser
2. Navigate to the Chat tab
3. Ask: "What is the CPU usage?"
4. Open the MLflow UI and navigate to the `ai-observability` experiment
5. Verify traces appear with spans: `deterministic_chat` (CHAIN), `route_tool_call` (TOOL), `get_tool_result` (TOOL)

## Uninstall

```bash
# Remove routes
oc delete -f deploy/kagenti/routes.yaml

# Remove Agent CRs (this also removes the Deployments and Services managed by Kagenti)
oc delete -f deploy/kagenti/ui-agent.yaml
oc delete -f deploy/kagenti/mcp-server-agent.yaml

# Remove RBAC and namespace
oc delete -f deploy/kagenti/namespace-rbac.yaml
```

## Comparison: Kagenti vs Helm Deployment

| Aspect | Helm (`deploy/helm/`) | Kagenti (`deploy/kagenti/`) |
|--------|----------------------|----------------------------|
| Deployment method | `helm upgrade --install` via Makefile | `oc apply` of Agent CRs |
| Service management | Helm-managed | Kagenti-managed |
| LLM/RAG stack | Deployed as part of `make install` | Uses existing external LLM service |
| RBAC | Created by Helm templates | Applied separately via `namespace-rbac.yaml` |
| Observability stack | Installed via Makefile targets | Assumes pre-existing Prometheus, Tempo, MLflow |
| MLflow tracing | Configured via Helm values | Configured via Agent CR env vars |
| Best for | Full standalone deployment | Integration with existing Kagenti-managed platform |

## Troubleshooting

### Pod not starting

Check events and logs:

```bash
oc describe pod -n ai-observability -l app=aiobs-mcp-server-app
oc logs -n ai-observability -l app=aiobs-mcp-server-app
```

### Thanos token issues

Verify the secret exists and contains a token:

```bash
oc get secret mcp-analyzer -n ai-observability
oc get secret mcp-analyzer -n ai-observability -o jsonpath='{.data.token}' | base64 -d | wc -c
```

### MLflow connection refused

Verify MLflow is accessible from the namespace:

```bash
oc run -n ai-observability curl-test --rm -it --image=curlimages/curl -- \
  curl -s http://mlflow-service.mlflow.svc.cluster.local:5000/health
```

### MCP Server not reachable from UI

Verify the service exists and is reachable:

```bash
oc get svc -n ai-observability | grep mcp-server
oc run -n ai-observability curl-test --rm -it --image=curlimages/curl -- \
  curl -s http://aiobs-mcp-server.ai-observability.svc.cluster.local:8085/health
```
