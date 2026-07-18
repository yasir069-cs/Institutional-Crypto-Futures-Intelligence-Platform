"""LLM provider interface and concrete implementations.

Provider interface
------------------
Every provider implements :class:`LLMProvider` with a single async method
``complete(messages) -> ProviderResponse``. The provider layer routes
requests to the first healthy provider in the configured order, with
automatic fallback, retry, and caching.

Providers included
------------------
- ``NIMProvider``        — NVIDIA NIM (OpenAI-compatible API)
- ``OpenRouterProvider`` — OpenRouter (OpenAI-compatible API)
- ``MockProvider``       — deterministic local provider for testing

The trading engine NEVER calls providers directly — it always goes through
:class:`LLMProviderLayer`, which handles:
- Provider selection / failover
- Health monitoring
- Caching (response hash keyed on input digest)
- Rate limiting (max requests per cycle)
- Retry with exponential backoff
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import aiohttp

from app.config import settings
from app.core.errors import AIProviderError, AIProviderUnavailableError, AIResponseParseError
from app.core.logging import get_logger

log = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Response model
# --------------------------------------------------------------------------- #
@dataclass
class ProviderResponse:
    provider: str
    model: str
    decision: str  # BUY / SELL / WATCHLIST / HOLD / REJECT
    confidence: float  # 0..1
    probability: float  # 0..1
    trade_quality: str  # e.g. "A", "B", "C"
    risk_level: str  # LOW / MEDIUM / HIGH
    reasoning: str
    raw: str = ""
    latency_ms: int = 0
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "decision": self.decision,
            "confidence": round(self.confidence, 3),
            "probability": round(self.probability, 3),
            "trade_quality": self.trade_quality,
            "risk_level": self.risk_level,
            "reasoning": self.reasoning,
            "latency_ms": self.latency_ms,
            "cached": self.cached,
        }


# --------------------------------------------------------------------------- #
# Provider interface
# --------------------------------------------------------------------------- #
class LLMProvider(ABC):
    """Abstract LLM provider."""

    name: str = "abstract"

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout
        self._session: aiohttp.ClientSession | None = None
        self._healthy: bool = True
        self._last_error_time: float = 0.0
        self._error_count: int = 0
        self._success_count: int = 0

    @property
    def healthy(self) -> bool:
        return self._healthy

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "healthy": self._healthy,
            "success_count": self._success_count,
            "error_count": self._error_count,
            "last_error_ago_sec": (time.time() - self._last_error_time) if self._last_error_time else None,
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout)
            )
        return self._session

    async def aclose(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    def _mark_success(self) -> None:
        self._success_count += 1
        self._healthy = True

    def _mark_failure(self, exc: Exception) -> None:
        self._error_count += 1
        self._last_error_time = time.time()
        # Mark unhealthy after 3 consecutive failures
        if self._error_count - self._success_count >= 3:
            self._healthy = False
        log.warning(
            "ai_provider_error",
            provider=self.name,
            error=str(exc),
            error_count=self._error_count,
            healthy=self._healthy,
        )

    @abstractmethod
    async def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> ProviderResponse:
        """Send a chat completion request and return a parsed response."""
        ...


# --------------------------------------------------------------------------- #
# OpenAI-compatible provider (used by NIM and OpenRouter)
# --------------------------------------------------------------------------- #
class OpenAICompatibleProvider(LLMProvider):
    """Base class for OpenAI-compatible /chat/completions endpoints."""

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

    async def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> ProviderResponse:
        if not self._api_key:
            raise AIProviderError(
                f"{self.name} API key not configured",
                code="ai.no_api_key",
                context={"provider": self.name},
            )

        payload = {
            "model": kwargs.get("model", self._model),
            "messages": messages,
            "temperature": kwargs.get("temperature", settings.ai_temperature),
            "max_tokens": kwargs.get("max_tokens", settings.ai_max_tokens),
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/chat/completions"

        start = time.time()
        try:
            session = await self._get_session()
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    exc = AIProviderError(
                        f"{self.name} HTTP {resp.status}: {body[:300]}",
                        code=f"ai.{self.name}.http_{resp.status}",
                        context={"status": resp.status, "body": body[:500]},
                    )
                    self._mark_failure(exc)
                    raise exc
                data = await resp.json()
        except asyncio.TimeoutError as exc:
            self._mark_failure(exc)
            raise AIProviderError(f"{self.name} timeout", code=f"ai.{self.name}.timeout") from exc
        except aiohttp.ClientError as exc:
            self._mark_failure(exc)
            raise AIProviderError(
                f"{self.name} network error: {exc}",
                code=f"ai.{self.name}.network",
            ) from exc

        latency_ms = int((time.time() - start) * 1000)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            self._mark_failure(exc)
            raise AIResponseParseError(raw=str(data)[:500]) from exc

        parsed = self._parse_response(content)
        parsed.provider = self.name
        parsed.model = payload["model"]
        parsed.latency_ms = latency_ms
        parsed.raw = content
        self._mark_success()
        return parsed

    def _parse_response(self, content: str) -> ProviderResponse:
        """Parse the LLM's content into a structured ProviderResponse.

        Expects JSON-formatted output. Falls back to keyword extraction.
        """
        # Try JSON first
        try:
            # Find first { and last }
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1 and end > start:
                data = json.loads(content[start : end + 1])
                return ProviderResponse(
                    provider=self.name,
                    model=self._model,
                    decision=str(data.get("decision", "HOLD")).upper(),
                    confidence=float(data.get("confidence", 0.5)),
                    probability=float(data.get("probability", 0.5)),
                    trade_quality=str(data.get("trade_quality", "C")),
                    risk_level=str(data.get("risk_level", "MEDIUM")).upper(),
                    reasoning=str(data.get("reasoning", content[:500])),
                )
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: keyword extraction
        content_lower = content.lower()
        if "buy" in content_lower:
            decision = "BUY"
        elif "sell" in content_lower:
            decision = "SELL"
        elif "reject" in content_lower:
            decision = "REJECT"
        elif "watchlist" in content_lower:
            decision = "WATCHLIST"
        else:
            decision = "HOLD"

        return ProviderResponse(
            provider=self.name,
            model=self._model,
            decision=decision,
            confidence=0.5,
            probability=0.5,
            trade_quality="C",
            risk_level="MEDIUM",
            reasoning=content[:500],
        )


# --------------------------------------------------------------------------- #
# Concrete providers
# --------------------------------------------------------------------------- #
class NIMProvider(OpenAICompatibleProvider):
    def __init__(self) -> None:
        super().__init__(
            name="nim",
            base_url=settings.nim_base_url,
            api_key=settings.nim_api_key.get_secret_value(),
            model=settings.nim_model,
            timeout=settings.nim_timeout,
        )


class OpenRouterProvider(OpenAICompatibleProvider):
    def __init__(self) -> None:
        super().__init__(
            name="openrouter",
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key.get_secret_value(),
            model=settings.openrouter_model,
            timeout=settings.openrouter_timeout,
        )


class GroqProvider(OpenAICompatibleProvider):
    """Groq — free, fast, OpenAI-compatible.

    Free tier limits (as of 2024):
    - llama-3.1-70b-versatile: 30 req/min, 14k req/day
    - llama-3.1-8b-instant: 60 req/min, 30k req/day
    - mixtral-8x7b-32768: 30 req/min

    Get a free API key at https://console.groq.com
    """

    def __init__(self) -> None:
        super().__init__(
            name="groq",
            base_url=settings.groq_base_url,
            api_key=settings.groq_api_key.get_secret_value(),
            model=settings.groq_model,
            timeout=settings.groq_timeout,
        )


class MockProvider(LLMProvider):
    """Deterministic local provider — used for tests and offline mode.

    Produces a HOLD decision with reasoning explaining the mock was used.
    Useful for verifying the full pipeline without external API calls.
    """

    name = "mock"

    async def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> ProviderResponse:
        # Inspect the user message to make a simple rule-based decision
        user_msg = ""
        for m in messages:
            if m.get("role") == "user":
                user_msg = m.get("content", "")
                break

        # Extract direction and confluence from the message
        decision = "HOLD"
        confidence = 0.5
        if "BUY" in user_msg and "confluence" in user_msg.lower():
            # Look for high confluence
            try:
                import re

                m = re.search(r"confluence[^\d]*(\d+)", user_msg.lower())
                if m and int(m.group(1)) >= 80:
                    decision = "BUY"
                    confidence = 0.75
                elif m and int(m.group(1)) >= 75:
                    decision = "WATCHLIST"
                    confidence = 0.6
            except (ValueError, AttributeError):
                pass
        elif "SELL" in user_msg and "confluence" in user_msg.lower():
            try:
                import re

                m = re.search(r"confluence[^\d]*(\d+)", user_msg.lower())
                if m and int(m.group(1)) >= 80:
                    decision = "SELL"
                    confidence = 0.75
                elif m and int(m.group(1)) >= 75:
                    decision = "WATCHLIST"
                    confidence = 0.6
            except (ValueError, AttributeError):
                pass

        return ProviderResponse(
            provider="mock",
            model="mock-v1",
            decision=decision,
            confidence=confidence,
            probability=confidence,
            trade_quality="B" if decision in ("BUY", "SELL") else "C",
            risk_level="MEDIUM",
            reasoning=(
                f"[MOCK PROVIDER] Decision based on confluence heuristic. "
                f"Real providers (NIM/OpenRouter) would do deeper analysis. "
                f"Decision: {decision}, confidence: {confidence:.2f}."
            ),
            latency_ms=1,
            cached=False,
        )


# --------------------------------------------------------------------------- #
# Provider layer (the single entry point for AI calls)
# --------------------------------------------------------------------------- #
@dataclass
class CacheEntry:
    response: ProviderResponse
    timestamp: float
    input_digest: str
    market_snapshot: dict[str, Any]


class LLMProviderLayer:
    """Single entry point for AI calls — handles selection, cache, retry."""

    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}
        self._order: list[str] = settings.ai_provider_list
        self._cache: dict[str, CacheEntry] = {}
        self._cache_ttl = settings.ai_cache_ttl_sec
        self._lock = asyncio.Lock()
        self._cycle_request_count: int = 0
        self._cycle_reset_at: float = time.time()
        self._max_per_cycle = settings.ai_max_requests_per_cycle
        self._concurrency = settings.ai_concurrency
        self._semaphore: asyncio.Semaphore | None = None

        # Register providers
        if "groq" in self._order:
            self._providers["groq"] = GroqProvider()
        if "nim" in self._order:
            self._providers["nim"] = NIMProvider()
        if "openrouter" in self._order:
            self._providers["openrouter"] = OpenRouterProvider()
        if "mock" in self._order:
            self._providers["mock"] = MockProvider()

    def _get_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._concurrency)
        return self._semaphore

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    async def validate(
        self,
        setup_id: str,
        market_context: dict[str, Any],
        messages: list[dict[str, str]],
    ) -> ProviderResponse:
        """Validate a premium setup through the AI provider layer.

        Steps:
        1. Check cache — if valid, return cached response
        2. Check rate limit for this cycle
        3. Select healthy provider in priority order
        4. Send request with retry + exponential backoff
        5. On failure, try next provider
        6. Cache the response
        """
        # 1. Cache check
        digest = self._input_digest(messages, market_context)
        cached = self._check_cache(digest, market_context)
        if cached is not None:
            log.info("ai_cache_hit", setup_id=setup_id, provider=cached.provider)
            cached.cached = True
            return cached

        # 2. Rate limit per cycle
        await self._enforce_cycle_limit()

        # 3. Acquire concurrency slot
        async with self._get_semaphore():
            # 4. Try each healthy provider
            last_exc: Exception | None = None
            for name in self._order:
                provider = self._providers.get(name)
                if provider is None or not provider.healthy:
                    continue
                try:
                    response = await self._call_with_retry(provider, messages)
                    self._store_cache(digest, response, market_context)
                    self._cycle_request_count += 1
                    log.info(
                        "ai_validation_success",
                        setup_id=setup_id,
                        provider=name,
                        decision=response.decision,
                        confidence=response.confidence,
                        latency_ms=response.latency_ms,
                    )
                    return response
                except AIProviderError as exc:
                    last_exc = exc
                    log.warning(
                        "ai_provider_failed",
                        setup_id=setup_id,
                        provider=name,
                        error=str(exc),
                    )
                    continue

            # All providers failed
            log.error("ai_all_providers_failed", setup_id=setup_id, last_error=str(last_exc))
            raise AIProviderUnavailableError(
                f"All AI providers failed for {setup_id}: {last_exc}"
            )

    async def _call_with_retry(
        self, provider: LLMProvider, messages: list[dict[str, str]]
    ) -> ProviderResponse:
        """Call provider with exponential backoff (max 2 retries)."""
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                return await provider.complete(messages)
            except AIProviderError as exc:
                last_exc = exc
                if attempt < 2:
                    delay = 0.5 * (2**attempt)  # 0.5s, 1s
                    log.debug("ai_retry", provider=provider.name, attempt=attempt + 1, delay=delay)
                    await asyncio.sleep(delay)
        raise last_exc or AIProviderError("Unknown AI failure")

    # ------------------------------------------------------------------ #
    # Cache
    # ------------------------------------------------------------------ #
    def _input_digest(self, messages: list[dict[str, str]], snapshot: dict[str, Any]) -> str:
        """Hash the input + relevant market snapshot fields for cache key."""
        # Only hash fields that affect the decision (exclude volatile timestamps)
        cache_snapshot = {
            "symbol": snapshot.get("symbol"),
            "direction": snapshot.get("direction"),
            "confluence_score": snapshot.get("confluence_score"),
            "trend_overall": snapshot.get("trend", {}).get("overall_bias"),
            "market_structure_bias": snapshot.get("market_structure", {}).get("bias"),
            "smart_money_flow": round(snapshot.get("smart_money", {}).get("net_flow", 0), 2),
        }
        raw = json.dumps({"messages": messages, "snapshot": cache_snapshot}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _check_cache(self, digest: str, current_snapshot: dict[str, Any]) -> ProviderResponse | None:
        """Return cached response if still valid given current market state."""
        entry = self._cache.get(digest)
        if entry is None:
            return None
        # TTL check
        if time.time() - entry.timestamp > self._cache_ttl:
            return None
        # Cache invalidation rules from spec:
        # - Price movement < 0.5%
        # - Confluence change < 5%
        cached_price = entry.market_snapshot.get("price", 0)
        current_price = current_snapshot.get("price", 0)
        if cached_price > 0 and current_price > 0:
            price_change = abs(current_price - cached_price) / cached_price
            if price_change > 0.005:  # 0.5%
                return None

        cached_confluence = entry.market_snapshot.get("confluence_score", 0)
        current_confluence = current_snapshot.get("confluence_score", 0)
        if abs(current_confluence - cached_confluence) >= 5:
            return None

        return entry.response

    def _store_cache(self, digest: str, response: ProviderResponse, snapshot: dict[str, Any]) -> None:
        self._cache[digest] = CacheEntry(
            response=response,
            timestamp=time.time(),
            input_digest=digest,
            market_snapshot=dict(snapshot),
        )
        # Bound cache size
        if len(self._cache) > 1000:
            # Evict oldest
            oldest = min(self._cache.items(), key=lambda kv: kv[1].timestamp)
            self._cache.pop(oldest[0], None)

    # ------------------------------------------------------------------ #
    # Rate limiting
    # ------------------------------------------------------------------ #
    async def _enforce_cycle_limit(self) -> None:
        """Enforce max AI requests per scan cycle (resets every scan interval)."""
        now = time.time()
        if now - self._cycle_reset_at > settings.scan_interval_sec:
            self._cycle_request_count = 0
            self._cycle_reset_at = now
        if self._cycle_request_count >= self._max_per_cycle:
            raise AIProviderError(
                f"AI request limit reached for this cycle ({self._max_per_cycle})",
                code="ai.cycle_limit",
            )

    def reset_cycle(self) -> None:
        """Reset per-cycle counters — called at start of each scan."""
        self._cycle_request_count = 0
        self._cycle_reset_at = time.time()

    # ------------------------------------------------------------------ #
    # Health & monitoring
    # ------------------------------------------------------------------ #
    def provider_stats(self) -> list[dict[str, Any]]:
        return [p.stats for p in self._providers.values()]

    def healthy_providers(self) -> list[str]:
        return [name for name in self._order if name in self._providers and self._providers[name].healthy]

    async def aclose(self) -> None:
        for p in self._providers.values():
            await p.aclose()
        self._providers.clear()


__all__ = [
    "LLMProvider",
    "OpenAICompatibleProvider",
    "NIMProvider",
    "OpenRouterProvider",
    "GroqProvider",
    "MockProvider",
    "LLMProviderLayer",
    "ProviderResponse",
    "CacheEntry",
]
