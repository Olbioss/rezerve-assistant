"""The simulated customer: a Groq model playing a person with a goal, who
gives their details only when asked and says when they are done (spec §8.1).
"""

from datetime import date

from pydantic_ai import Agent
from pydantic_ai.models import Model

from assistant.agent.format import MONTHS, WEEKDAYS
from evals.cache import DiskCache
from evals.graders import Line
from evals.scenario import Scenario

DONE = "[[BITTI]]"

INSTRUCTIONS = """\
Bir işletmenin randevu asistanıyla yazışan bir müşteriyi canlandırıyorsun.

Bugün: {today}.
Kişiliğin: {persona}
Amacın: {goal}
Yalnızca sorulursa vereceğin bilgiler:
{facts}

Kurallar:
- Yalnızca müşterinin bir sonraki mesajını yaz: tırnak, açıklama ya da rol etiketi yok.
- Günü amacındaki sözcüklerle söyle ("haftaya perşembe" ise "haftaya perşembe"); tarihi \
kendin hesaplayıp yazma.
- Gerçek bir müşteri gibi kısa yaz; bilgileri asistan sormadan sıralama.
- Asistan bir işlem için onay sorarsa: işlem amacına uyuyorsa onayla, uymuyorsa düzeltmesini iste.
- Amacına ulaştığın an (örneğin randevun alındığında) ya da ulaşılamayacağı belli olduğunda, \
teşekkür ya da veda yazmadan yalnızca {done} yaz.
- Asistan seni işletmeye yönlendirirse (bir telefon numarası verir ya da seni aktardığını \
söylerse) asistanla işin bitmiştir: yalnızca {done} yaz.
"""


class Simulator:
    def __init__(self, model: Model, cache: DiskCache) -> None:
        self._agent = Agent(model, output_type=str)
        self._model_name = model.model_name
        self._cache = cache

    async def next_message(
        self, scenario: Scenario, lines: list[Line], today: date, trial: int = 1
    ) -> str | None:
        """What the customer writes next, or None once they are done. The
        customer knows the date, as a person would, so it can tell a wrong day
        when it is offered; and on another day the cache asks again, since
        "haftaya perşembe" is another Thursday. Each trial of a run meets a
        customer of its own: a later trial adds its number to the cache key."""
        instructions = INSTRUCTIONS.format(
            today=f"{today.day} {MONTHS[today.month - 1]} {today.year} {WEEKDAYS[today.weekday()]}",
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

        key = [self._model_name, instructions, prompt] + ([f"trial {trial}"] if trial > 1 else [])
        text: str = await self._cache.get_or_compute("simulator", key, ask)
        if DONE in text or not text.strip():
            return None
        return text.strip()
