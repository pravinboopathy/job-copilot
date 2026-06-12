"""Internal building blocks for the tailoring pipeline.

- ``llm`` — LiteLLM wrapper with retries, JSON extraction, provider quirks.
- ``prompts.templates`` — keyword-extraction + truthfulness-rule prompts.
- ``prompts.refinement`` — AI-phrase blacklist and replacements.

Underscore-prefixed because nothing in here is part of the user-facing
CLI surface; everything is consumed by ``src/cli.py``, ``src/pipeline.py``,
``src/resume_tailor.py``, and ``src/adapters.py``.
"""
