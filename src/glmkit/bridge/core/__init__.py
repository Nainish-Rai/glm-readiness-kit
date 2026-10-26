"""Framework-neutral bridge core: pure functions over JSON bodies and parsed SSE events.

Nothing in this package imports a web framework or an HTTP client. The proxy (``glmkit.bridge.proxy``), the
Anthropic path (issue 07), the LiteLLM plugin (issue 08) and the guard (issue 09) all drive it.
"""

from glmkit.bridge.core.errors import ClassifiedError, ErrorClassifier, TableErrorClassifier
from glmkit.bridge.core.events import FixEmitter, FixEvent
from glmkit.bridge.core.middleware import BridgeMiddleware, Reject
from glmkit.bridge.core.pipeline import BridgeCore, RewriteContext
from glmkit.bridge.core.retry import ParseErrorRetry, RetryPlan, RetryPolicy
from glmkit.bridge.core.stream import StreamRewriter
from glmkit.bridge.core.upstream import UPSTREAMS, UpstreamProfile, get_upstream, upstream_for_endpoint

__all__ = [
    "UPSTREAMS",
    "BridgeCore",
    "BridgeMiddleware",
    "ClassifiedError",
    "ErrorClassifier",
    "FixEmitter",
    "FixEvent",
    "ParseErrorRetry",
    "Reject",
    "RetryPlan",
    "RetryPolicy",
    "RewriteContext",
    "StreamRewriter",
    "TableErrorClassifier",
    "UpstreamProfile",
    "get_upstream",
    "upstream_for_endpoint",
]
