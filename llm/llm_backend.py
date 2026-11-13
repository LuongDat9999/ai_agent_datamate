# src/llm_backend.py
import os, re, json, hashlib, time
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
from abc import ABC, abstractmethod
from pathlib import Path
from functools import lru_cache

from functools import lru_cache

class LLMBackend(ABC):
    @abstractmethod
    def generate(self, messages, n=1, temperature=0.0, max_tokens=800, seed=None) -> list[str]: ...

class MockBackend(LLMBackend):
    """Không cần model: trả về khối đúng định dạng để test parse → validator → checker → formatter."""
    def __init__(self):
        self.replay = self._load_replay(os.getenv("LLM_MOCK_FILE"))

    @staticmethod
    def _load_replay(path_value):
        if not path_value:
            return {}
        path = Path(path_value)
        if not path.is_file():
            raise FileNotFoundError(f"LLM_MOCK_FILE không tồn tại: {path}")
        replay = {}
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"LLM_MOCK_FILE dòng {line_number} không phải JSON hợp lệ") from error
                if not isinstance(item, dict) or not all(
                    isinstance(item.get(key), str) and item[key].strip()
                    for key in ("question", "type", "sql")
                ):
                    raise ValueError(
                        f"LLM_MOCK_FILE dòng {line_number} phải có question/type/sql là chuỗi không rỗng"
                    )
                replay[item["question"]] = (item["type"], item["sql"])
        return replay

    def generate(self, messages, n=1, **kw):
        prompt = messages[-1]["content"]
        question_match = re.search(r"\[CÂU HỎI GỐC\]\s*\n([^\n]*)", prompt)
        question = question_match.group(1).strip() if question_match else None
        replay = self.replay.get(question) if question is not None else None
        if replay:
            answer_type, sql = replay
        else:
            table = re.search(r"TABLE\s+(\w+)", prompt)
            table_name = table.group(1) if table else "ban_hang"
            answer_type, sql = "number", f"SELECT COUNT(*) FROM {table_name}"
        out = f"<thinking>mock</thinking><type>{answer_type}</type><sql>{sql}</sql>"
        return [out] * n


class VLLMBackend(LLMBackend):
    """Bản nộp cuối: chạy offline trong Docker."""
    def __init__(self, model_path, **kw):
        from vllm import LLM
        self.llm = LLM(model=model_path, enable_prefix_caching=True, **kw)
    def generate(self, messages, n=1, temperature=0.0, max_tokens=800, seed=None):
        from vllm import SamplingParams
        sp = SamplingParams(n=n, temperature=temperature, max_tokens=max_tokens, seed=seed)
        return [o.text for o in self.llm.chat([messages], sp)[0].outputs]

class CachedBackend(LLMBackend):
    """Cache ra đĩa theo hash prompt: tiết kiệm quota GPU và giúp kết quả lặp lại được."""
    def __init__(self, inner, cache_dir=".llm_cache"):
        self.inner, self.dir = inner, Path(cache_dir); self.dir.mkdir(exist_ok=True)
    def generate(self, messages, n=1, temperature=0.0, max_tokens=800, seed=None):
        key = hashlib.sha256(json.dumps([messages, n, temperature, max_tokens, seed], ensure_ascii=False).encode()).hexdigest()
        f = self.dir / f"{key}.json"
        if f.exists(): return json.loads(f.read_text())
        outs = self.inner.generate(messages, n, temperature, max_tokens, seed)
        self.last_prompt_tokens = getattr(self.inner, "last_prompt_tokens", None)
        self.last_peak_memory = getattr(self.inner, "last_peak_memory", None)
        f.write_text(json.dumps(outs, ensure_ascii=False)); return outs

def get_backend():
    kind = os.getenv("LLM_BACKEND", "hf")
    if kind == "mock":   return MockBackend()
    if kind == "hf":     return HFBackend()
    if kind == "openai": return CachedBackend(OpenAICompatBackend(os.environ["LLM_BASE_URL"], os.environ["LLM_MODEL"]))
    if kind == "vllm":   return VLLMBackend(os.environ["LLM_MODEL_PATH"])
    raise ValueError(kind)

class HFBackend(LLMBackend):
    """Dùng để dev trên Kaggle/Colab (transformers + bitsandbytes). KHÔNG dùng cho bản nộp (bản nộp dùng vLLM)."""
    def __init__(self):
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
        self.model_id = os.getenv("HF_MODEL", "Qwen/Qwen3-14B")
        quant_enabled = os.getenv("LLM_4BIT", "1") == "1"
        quant = "nf4" if quant_enabled else os.getenv("HF_QUANT", "nf4").lower()
        self.thinking = os.getenv("HF_THINKING", "0") == "1"
        self.max_prompt_tokens = int(os.getenv("PROMPT_MAX_TOKENS", "4500"))
        self.name = f"hf:{self.model_id}:{quant if quant_enabled else 'fp16'}:think={int(self.thinking)}"

        if quant_enabled and quant == "int8":
            qcfg = BitsAndBytesConfig(load_in_8bit=True, llm_int8_threshold=6.0)
        elif quant_enabled and quant == "nf4":
            qcfg = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                      bnb_4bit_compute_dtype=torch.float16,
                                      bnb_4bit_use_double_quant=True)
        elif quant_enabled:
            raise ValueError(f"HF_QUANT không hợp lệ: {quant}")
        else:
            qcfg = None

        n_gpu = torch.cuda.device_count()
        # chừa chỗ cho KV cache/activation: mỗi GPU tối đa 11GiB cho trọng số
        max_memory = {i: "9GiB" for i in range(n_gpu)} or None
        self.tok = AutoTokenizer.from_pretrained(self.model_id)
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_id, quantization_config=qcfg, torch_dtype=torch.float16,
            device_map="auto", max_memory=max_memory, low_cpu_mem_usage=True,
            attn_implementation="sdpa")
        self.model.eval()
        memory = ", ".join(
            f"gpu{i}:allocated={torch.cuda.memory_allocated(i) / 2**30:.2f}GiB,"
            f"reserved={torch.cuda.memory_reserved(i) / 2**30:.2f}GiB"
            for i in range(n_gpu)
        )
        print(
            f"[HFBackend] {self.name} đã nạp trên {n_gpu} GPU "
            f"dtype=float16 quantization={'nf4' if quant_enabled and quant == 'nf4' else ('int8' if quant_enabled else 'none')} "
            f"memory=[{memory}]",
            flush=True,
        )

    def generate(self, messages, n=1, temperature=0.0, max_tokens=800, seed=None):
        import torch
        t0 = time.time()
        text = self.tok.apply_chat_template(messages, tokenize=False,
                                            add_generation_prompt=True,
                                            enable_thinking=self.thinking)
        inputs = self.tok(text, return_tensors="pt")
        n_in = int(inputs["input_ids"].shape[-1])
        self.last_prompt_tokens = n_in
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        print(f"[HFBackend] prompt={n_in} tok", flush=True)
        if n_in > self.max_prompt_tokens:
            raise ValueError(f"prompt có {n_in} token, vượt MAX_PROMPT_TOKENS={self.max_prompt_tokens}")
        inputs = inputs.to(self.model.device)
        greedy = temperature <= 0
        kw = dict(max_new_tokens=max_tokens, pad_token_id=self.tok.eos_token_id)
        if greedy:
            kw.update(do_sample=False, temperature=None, top_p=None, top_k=None)
        else:
            if seed is not None:
                torch.manual_seed(seed)
            kw.update(do_sample=True, temperature=temperature, top_p=0.95, top_k=20,
                      num_return_sequences=n)
        try:
            with torch.inference_mode():
                out = self.model.generate(**inputs, **kw)
        except torch.cuda.OutOfMemoryError as error:
            torch.cuda.empty_cache()
            self.last_peak_memory = (
                torch.cuda.max_memory_allocated() / 2**30 if torch.cuda.is_available() else 0.0
            )
            raise
        texts = [self.tok.decode(g[n_in:], skip_special_tokens=True) for g in out]
        texts = [self._strip_think(t) for t in texts]
        torch.cuda.empty_cache()
        self.last_peak_memory = (
            torch.cuda.max_memory_allocated() / 2**30 if torch.cuda.is_available() else 0.0
        )
        print(f"[HFBackend] prompt={n_in} tok, n={n}, {time.time()-t0:.1f}s", flush=True)
        return texts * n if greedy else texts   # greedy thì các mẫu giống nhau, khỏi sinh lại

    @staticmethod
    def _strip_think(t):
        if "<think>" in t and "</think>" not in t:   # bị cắt giữa chừng khi đang "suy nghĩ"
            return ""
        return re.sub(r"<think>.*?</think>\s*", "", t, flags=re.S).strip()


class CachedBackend(LLMBackend):
    def __init__(self, inner, cache_dir=".llm_cache"):
        self.inner, self.dir = inner, Path(cache_dir); self.dir.mkdir(exist_ok=True)
        self.name = getattr(inner, "name", type(inner).__name__)
    def generate(self, messages, n=1, temperature=0.0, max_tokens=800, seed=None):
        key = hashlib.sha256(json.dumps([self.name, messages, n, temperature, max_tokens, seed],
                                        ensure_ascii=False).encode()).hexdigest()
        f = self.dir / f"{key}.json"
        if f.exists(): return json.loads(f.read_text())
        outs = self.inner.generate(messages, n, temperature, max_tokens, seed)
        self.last_prompt_tokens = getattr(self.inner, "last_prompt_tokens", None)
        self.last_peak_memory = getattr(self.inner, "last_peak_memory", None)
        f.write_text(json.dumps(outs, ensure_ascii=False)); return outs


@lru_cache(maxsize=1)                      # chỉ nạp model đúng 1 lần
def get_backend():
    kind = os.getenv("LLM_BACKEND", "hf")
    if kind == "mock": return MockBackend()
    if kind == "hf":   return CachedBackend(HFBackend())
    if kind == "vllm": return VLLMBackend(os.environ["LLM_MODEL_PATH"])
    raise ValueError(f"LLM_BACKEND không hợp lệ: {kind}")