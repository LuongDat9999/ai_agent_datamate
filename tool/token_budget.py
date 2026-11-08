"""Token counting shared by prompt construction and Hugging Face generation."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Mapping, Sequence


@lru_cache(maxsize=2)
def load_tokenizer(model_id: str) -> Any:
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(model_id, local_files_only=True)


def chat_token_count(
    messages: Sequence[Mapping[str, str]],
    *,
    tokenizer: Any | None = None,
    model_id: str | None = None,
    enable_thinking: bool | None = None,
) -> int:
    """Count the exact tokens sent to ``model.generate`` when possible."""
    tokenizer = tokenizer or load_tokenizer(model_id or os.getenv("HF_MODEL", "Qwen/Qwen3-14B"))
    options = {"tokenize": False, "add_generation_prompt": True}
    if enable_thinking is not None:
        options["enable_thinking"] = enable_thinking
    text = tokenizer.apply_chat_template(list(messages), **options)
    encoded = tokenizer(text, return_tensors=None)
    input_ids = encoded["input_ids"] if isinstance(encoded, dict) else encoded.input_ids
    if hasattr(input_ids, "shape"):
        return int(input_ids.shape[-1])
    if input_ids and isinstance(input_ids[0], list):
        return len(input_ids[0])
    return len(input_ids)


def prompt_token_count(prompt: str) -> int:
    """Count a prompt exactly as the HF backend does, with a conservative fallback."""
    messages = [{"role": "user", "content": prompt}]
    try:
        return chat_token_count(messages)
    except Exception:
        # Keep the budget guard conservative when the model tokenizer is unavailable.
        return max(1, (len(prompt) + 1) // 2)


def prompt_budget() -> int:
    return int(os.getenv("MAX_PROMPT_TOKENS", "6000"))


def prompt_margin() -> int:
    return max(0, int(os.getenv("PROMPT_TOKEN_MARGIN", "128")))


__all__ = ["chat_token_count", "load_tokenizer", "prompt_budget", "prompt_margin", "prompt_token_count"]
