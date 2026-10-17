"""The eval harness (spec §8): customers, scripted or simulated, graded on the
bookings they leave in Rezerve's database."""

import os

# Pydantic AI's startup banner would land in the middle of a run's output.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
