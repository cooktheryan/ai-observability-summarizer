"""
MLflow Tracing Integration

Centralized MLflow tracing initialization with graceful degradation.
When mlflow-tracing is not installed or not configured, all operations
become no-ops so the application runs without any tracing overhead.
"""

import logging
import functools

logger = logging.getLogger(__name__)

_tracing_initialized = False


def setup_tracing() -> bool:
    """Initialize MLflow tracing from environment configuration.

    Reads MLFLOW_TRACKING_URI, MLFLOW_EXPERIMENT_NAME, and MLFLOW_TRACING_ENABLED
    from the environment (via core.config). Idempotent - safe to call multiple times.

    Returns:
        True if tracing was successfully initialized, False otherwise.
    """
    global _tracing_initialized
    if _tracing_initialized:
        return True

    try:
        from core.config import MLFLOW_TRACKING_URI, MLFLOW_EXPERIMENT_NAME, MLFLOW_TRACING_ENABLED
    except ImportError:
        logger.debug("core.config not available, skipping MLflow tracing setup")
        return False

    if not MLFLOW_TRACING_ENABLED:
        logger.info("MLflow tracing disabled via MLFLOW_TRACING_ENABLED=false")
        return False

    if not MLFLOW_TRACKING_URI:
        logger.info("MLFLOW_TRACKING_URI not set, skipping MLflow tracing setup")
        return False

    try:
        import mlflow

        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)
        mlflow.openai.autolog()

        _tracing_initialized = True
        logger.info(
            "MLflow tracing initialized: uri=%s experiment=%s",
            MLFLOW_TRACKING_URI,
            MLFLOW_EXPERIMENT_NAME,
        )
        return True
    except ImportError:
        logger.info("mlflow package not installed, skipping tracing setup")
        return False
    except Exception as e:
        logger.warning("Failed to initialize MLflow tracing: %s", e)
        return False


def get_trace_decorator():
    """Return mlflow.trace if available, otherwise a no-op decorator.

    Usage:
        from core.mlflow_tracing import get_trace_decorator
        trace = get_trace_decorator()

        @trace(span_type="CHAIN", name="my_function")
        def my_function():
            ...
    """
    try:
        import mlflow
        return mlflow.trace
    except ImportError:
        def _noop_trace(*args, **kwargs):
            """No-op decorator that passes through functions unchanged."""
            if args and callable(args[0]):
                return args[0]
            def decorator(func):
                @functools.wraps(func)
                def wrapper(*a, **kw):
                    return func(*a, **kw)
                return wrapper
            return decorator
        return _noop_trace
