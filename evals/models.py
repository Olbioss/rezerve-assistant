"""The models an eval run calls, made patient with free quotas (spec §8.5).

A free tier has two kinds of limit. A per-minute one passes: wait as long as
the provider asks, within reason, and ask again — a suite sends requests far
faster than customers would, so these limits are the harness's own doing. A
per-day one does not: the run stops, keeps what it finished, and resumes after
the reset. Every model of one run — the agent's, the simulated customer's,
the judge's — is wrapped, so the first daily limit any of them meets ends the
run. Anything else, a 503 included, passes through as it would in production,
where the fallback model takes over.
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence

from pydantic_ai import ModelHTTPError, ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from assistant.agent.agent import build_models
from assistant.config import Settings

log = logging.getLogger(__name__)

WAITS = (15.0, 30.0, 60.0)
MAX_HINTED_WAIT = 120.0
_HINT = re.compile(r"(?:retry|try again) in (?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?", re.I)


class QuotaExhausted(Exception):
    """A daily limit: nothing more from this model until it resets."""

    def __init__(self, model_name: str, hint: float | None) -> None:
        resets = f"; it resets in about {hint / 3600:.1f} h" if hint else ""
        super().__init__(f"{model_name} has used up its daily free quota{resets}.")
        self.model_name = model_name


def _body(error: ModelHTTPError) -> str:
    body = error.body
    return body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, default=str)


def is_daily(error: ModelHTTPError) -> bool:
    """Gemini names its quota "…PerDay…"; Groq says "requests per day (RPD)"."""
    body = _body(error)
    return "PerDay" in body or "per day" in body.lower()


def retry_hint(error: ModelHTTPError) -> float | None:
    """Seconds the provider asks us to wait: Groq's retry-after header, or
    Gemini's "Please retry in 37.4s" in the body."""
    if error.headers and (after := error.headers.get("retry-after")):
        try:
            return float(after)
        except ValueError:
            pass
    match = _HINT.search(_body(error))
    if match is None or not any(match.groups()):
        return None
    hours, minutes, seconds = (float(group or 0) for group in match.groups())
    return hours * 3600 + minutes * 60 + seconds


class PatientModel(WrapperModel):
    def __init__(
        self,
        wrapped: Model,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        waits: Sequence[float] = WAITS,
    ) -> None:
        super().__init__(wrapped)
        self._sleep = sleep
        self._waits = waits
        # Set when a daily limit is met; the harness stops the run on it even
        # if the turn that met it swallowed the error into a canned reply.
        self.exhausted: QuotaExhausted | None = None
        # Seconds spent waiting out per-minute limits, kept out of latency.
        self.waited = 0.0

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        for attempt in range(len(self._waits) + 1):
            try:
                return await super().request(messages, model_settings, model_request_parameters)
            except ModelHTTPError as error:
                if error.status_code != 429:
                    raise
                hint = retry_hint(error)
                if is_daily(error):
                    self.exhausted = QuotaExhausted(self.model_name, hint)
                    raise self.exhausted from error
                if attempt == len(self._waits):
                    raise
                wait = min(hint + 1, MAX_HINTED_WAIT) if hint is not None else self._waits[attempt]
                log.warning("%s is rate-limited; waiting %.0f s", self.model_name, wait)
                self.waited += wait
                await self._sleep(wait)
        raise AssertionError("unreachable")


def patient_chain(settings: Settings) -> tuple[Model, list[PatientModel]]:
    """The configured chain with every model in it made patient, and the
    wrappers themselves, so a caller can see which one ran out."""
    models = [PatientModel(model) for model in build_models(settings)]
    chain = models[0] if len(models) == 1 else FallbackModel(*models)
    return chain, models
