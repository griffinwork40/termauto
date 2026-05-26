"""mlx-lm wrapper: load a model once, generate completions on demand."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from . import DEFAULT_MODEL

log = logging.getLogger(__name__)


# Known special / stop tokens that occasionally leak through mlx-lm's decode.
# Stripped post-hoc so the parser never sees them.
_STOP_TOKENS = (
    "<|im_end|>",       # Qwen chat
    "<|endoftext|>",    # GPT family
    "<|eot_id|>",       # Llama 3
    "<|end|>",          # Phi
    "<end_of_turn>",    # Gemma
    "</s>",             # Mistral, older
)


def _strip_stop_tokens(text: str) -> str:
    for tok in _STOP_TOKENS:
        idx = text.find(tok)
        if idx != -1:
            text = text[:idx]
    return text.rstrip()


class InferenceEngine:
    """Thread-safe MLX inference engine.

    Holds one model warm in memory. `generate()` is serialized via a lock —
    mlx-lm is not safe to call concurrently from multiple threads with the
    same model object.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        self._model = None
        self._tokenizer = None
        self._lock = threading.Lock()
        self._loaded_at: Optional[float] = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Load the model. Idempotent; safe to call multiple times."""
        if self._model is not None:
            return

        # Lazy import — mlx_lm has heavy native deps we don't want at module
        # import time (e.g. when running `termauto status`).
        from mlx_lm import load  # type: ignore

        log.info("loading model: %s", self.model_name)
        t0 = time.monotonic()
        with self._lock:
            if self._model is not None:
                return
            self._model, self._tokenizer = load(self.model_name)
            self._loaded_at = time.monotonic()
        log.info("model loaded in %.2fs", self._loaded_at - t0)

    def generate(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 256,
        temperature: float = 0.4,
        top_p: float = 0.9,
    ) -> str:
        """Generate a completion. Blocks until done. Thread-safe."""
        if self._model is None:
            self.load()

        from mlx_lm import generate  # type: ignore
        from mlx_lm.sample_utils import make_sampler  # type: ignore

        # Apply the chat template — Qwen2.5-Coder-Instruct expects this.
        prompt = self._tokenizer.apply_chat_template(  # type: ignore[union-attr]
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )

        sampler = make_sampler(temp=temperature, top_p=top_p)

        with self._lock:
            t0 = time.monotonic()
            output = generate(
                self._model,
                self._tokenizer,
                prompt=prompt,
                max_tokens=max_tokens,
                sampler=sampler,
                verbose=False,
            )
            elapsed = time.monotonic() - t0

        log.info("generated %d chars in %.2fs", len(output), elapsed)
        return _strip_stop_tokens(output)

    def warmup(self) -> None:
        """Run one tiny generation so the first real request is hot."""
        if self._model is None:
            self.load()
        try:
            self.generate(
                [{"role": "user", "content": "hi"}],
                max_tokens=4,
                temperature=0.0,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("warmup failed: %s", e)
