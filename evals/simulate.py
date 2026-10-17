"""The simulated customer: a Groq model playing a person with a goal, who
gives their details only when asked and says when they are done (spec §8.1).
"""

from pydantic_ai import Agent
from pydantic_ai.models import Model

from evals.cache import DiskCache
from evals.graders import Line
from evals.scenario import Scenario

DONE = "[[BITTI]]"

INSTRUCTIONS = """\
Bir işletmenin randevu asistanıyla yazışan bir müşteriyi canlandırıyorsun.

Kişiliğin: {persona}
Amacın: {goal}
Yalnızca sorulursa vereceğin bilgiler:
{facts}

Kurallar:
- Yalnızca müşterinin bir sonraki mesajını yaz: tırnak, açıklama ya da rol etiketi yok.
- Gerçek bir müşteri gibi kısa yaz; bilgileri asistan sormadan sıralama.
- Asistan bir işlem için onay sorarsa: işlem amacına uyuyorsa onayla, uymuyorsa düzeltmesini iste.
- Amacına ulaştığın an (örneğin randevun alındığında) ya da ulaşılamayacağı belli olduğunda, \
teşekkür ya da veda yazmadan yalnızca {done} yaz.
"""


class Simulator:
    def __init__(self, model: Model, cache: DiskCache) -> None:
        self._agent = Agent(model, output_type=str)
        self._model_name = model.model_name
        self._cache = cache

    async def next_message(self, scenario: Scenario, lines: list[Line]) -> str | None:
        """What the customer writes next, or None once they are done."""
        instructions = INSTRUCTIONS.format(
            persona=scenario.persona,
            goal=scenario.goal,
            facts="\n".join(f"- {key}: {value}" for key, value in scenario.facts.items()) or "-",
            done=DONE,
        )
        conversation = "\n".join(
            f"{'Sen' if line.speaker == 'customer' else 'Asistan'}: {line.text}" for line in lines
        )
        prompt = (
            f"Yazışma şu ana kadar:\n{conversation}\n\nSıradaki mesajını yaz."
            if lines
            else "Yazışmayı sen başlat: ilk mesajını yaz."
        )

        async def ask() -> str:
            result = await self._agent.run(prompt, instructions=instructions)
            return result.output

        text: str = await self._cache.get_or_compute(
            "simulator", [self._model_name, instructions, prompt], ask
        )
        if DONE in text or not text.strip():
            return None
        return text.strip()
