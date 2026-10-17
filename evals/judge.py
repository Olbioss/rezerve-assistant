"""The judge: a Groq model scoring the assistant's Turkish against a written
rubric, on scenarios tagged `judge` only (spec §8.3). It grades how the
assistant wrote, never whether it did the right thing — the database decides
that.
"""

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from evals.cache import DiskCache
from evals.graders import Line

RUBRIC = """\
Bir işletmenin randevu asistanının bir müşteriyle Türkçe yazışmasını puanlıyorsun.
Yalnızca asistanın mesajlarını puanla; müşterinin yazdıklarını değil.
Her ölçüt için 1–5 arası tam sayı ver:

fluency (akıcılık)
  5: doğal, hatasız Türkçe; bir Türk resepsiyonistin yazacağı gibi.
  3: anlaşılır, ama yer yer çeviri kokan, yapay ya da dil bilgisi hatalı.
  1: bozuk ya da yer yer anlaşılmaz.
politeness (nezaket)
  5: sıcak ve saygılı; müşteriye "siz" diye hitap eder.
  3: kuru ya da mesafeli; ya da yer yer laubali.
  1: kaba, küçümseyen ya da sabırsız.
clarity (açıklık)
  5: kısa; her mesajda en fazla bir soru; müşteri ne olacağını ve ne yapması gerektiğini bilir.
  3: gereğinden uzun, birden çok soru ya da yer yer belirsiz.
  1: kafa karıştırıcı ya da çelişkili.

note alanına puanların gerekçesini bir iki cümleyle yaz.
"""


class Score(BaseModel):
    fluency: int = Field(ge=1, le=5)
    politeness: int = Field(ge=1, le=5)
    clarity: int = Field(ge=1, le=5)
    note: str


class Judge:
    def __init__(self, model: Model, cache: DiskCache) -> None:
        self._agent = Agent(model, output_type=Score, instructions=RUBRIC)
        self._model_name = model.model_name
        self._cache = cache

    async def score(self, lines: list[Line]) -> Score:
        transcript = "\n".join(
            f"{'Müşteri' if line.speaker == 'customer' else 'Asistan'}: {line.text}"
            for line in lines
        )

        async def ask() -> dict[str, object]:
            result = await self._agent.run(transcript)
            return result.output.model_dump()

        value = await self._cache.get_or_compute(
            "judge", [self._model_name, RUBRIC, transcript], ask
        )
        return Score.model_validate(value)
