"""本地模型适配：离线加载、固定版本、查询/文档规则隔离、可终止的推理进程。"""

import hashlib
import json
import logging
import math
import multiprocessing as mp
import threading
from typing import Protocol

from app.core.config import get_settings
from app.core.logging import exception_location

logger = logging.getLogger(__name__)


class EmbeddingError(ValueError):
    pass


class EmbeddingProvider(Protocol):
    def profile(self) -> dict: ...
    def chunks(self, content: str) -> list[dict]: ...
    def encode(self, texts: list[str], *, query: bool = False) -> list[list[float]]: ...
    def close(self) -> None: ...


def fingerprint(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_vectors(vectors, count, dimension):
    if len(vectors) != count or any(
        len(v) != dimension or any(not math.isfinite(x) for x in v) or sum(x * x for x in v) < 1e-12
        for v in vectors
    ):
        raise EmbeddingError("模型返回了无效向量或向量维度不匹配。")


def token_chunks(content, tokenizer, budget, overlap):
    if budget <= overlap or budget < 1:
        raise EmbeddingError("分块预算必须大于重叠长度。")
    offsets = tokenizer(content, add_special_tokens=False, return_offsets_mapping=True)[
        "offset_mapping"
    ]
    chunks = []
    offset = 0
    while offset < len(offsets):
        end_token = min(offset + budget, len(offsets))
        if end_token < len(offsets):
            # 在页内优先靠近段落结尾切分；长段落仍按 token 预算切分，保留原文偏移。
            lower = offset + max(overlap + 1, budget // 2)
            for candidate in range(end_token, lower, -1):
                if "\n" in content[offsets[candidate - 1][1] : offsets[candidate][0]]:
                    end_token = candidate
                    break
        start, end = offsets[offset][0], offsets[end_token - 1][1]
        if end > start:
            chunks.append({"content": content[start:end], "start": start, "end": end})
        if end_token == len(offsets):
            break
        offset = max(offset + 1, end_token - overlap)
    return chunks


def pool_hidden(hidden, mask, pooling):
    import torch

    if not torch.all(mask.sum(1) > 0):
        raise EmbeddingError("模型输入没有有效 token。")
    if pooling == "cls":
        return hidden[:, 0]
    if pooling == "mean":
        return (hidden * mask.unsqueeze(-1)).sum(1) / mask.sum(1, keepdim=True)
    if pooling == "last":
        # 取最后一个有效位置，兼容左/右 padding，而不是用有效长度推断位置。
        positions = torch.arange(mask.shape[1], device=mask.device).expand_as(mask)
        last = positions.masked_fill(mask == 0, -1).max(1).values
        return hidden[torch.arange(hidden.shape[0], device=hidden.device), last]
    raise EmbeddingError("不支持的池化方式。")


def worker_failure(exc, stage):
    return {
        "message": str(exc)
        if isinstance(exc, EmbeddingError)
        else "本地模型加载或推理失败，请检查模型和依赖。",
        "exception_type": type(exc).__name__,
        "stage": stage,
        "location": exception_location(exc),
    }


def _model_worker(connection, options):
    stage = "dependencies"
    try:
        from pathlib import Path

        import torch
        from transformers import AutoModel, AutoTokenizer

        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)
        stage = "manifest"
        directory = Path(options.pop("directory"))
        manifest = json.loads((directory / "manifest.json").read_text("utf-8"))
        if manifest["model"] != options["model"] or manifest["revision"] != options["revision"]:
            raise EmbeddingError("模型目录与配置版本不一致，请检查配置。")
        for name, digest in manifest["files"].items():
            path = directory / name
            if not path.is_file():
                raise EmbeddingError("模型文件完整性检查失败，请重新下载。")
            with path.open("rb") as file:
                actual = hashlib.file_digest(file, "sha256").hexdigest()
            if actual != digest:
                raise EmbeddingError("模型文件完整性检查失败，请重新下载。")
        stage = "tokenizer"
        tokenizer = AutoTokenizer.from_pretrained(
            directory, local_files_only=True, trust_remote_code=False
        )
        stage = "model"
        model = (
            AutoModel.from_pretrained(
                directory, local_files_only=True, trust_remote_code=False, weights_only=True
            )
            .to("cpu")
            .eval()
        )
        stage = "configuration"
        if model.config.hidden_size != options["dimension"] or options["max_tokens"] > getattr(
            model.config, "max_position_embeddings", options["max_tokens"]
        ):
            raise EmbeddingError("模型维度或最大输入长度与配置不匹配。")
        while True:
            operation, payload, query = connection.recv()
            try:
                if operation == "chunks":
                    stage = "chunks"
                    prefix_tokens = len(
                        tokenizer(options["document_prefix"], add_special_tokens=False)["input_ids"]
                    )
                    budget = min(
                        options["chunk_tokens"],
                        options["max_tokens"]
                        - tokenizer.num_special_tokens_to_add()
                        - prefix_tokens,
                    )
                    result = token_chunks(payload, tokenizer, budget, options["overlap"])
                else:
                    stage = "tokenize"
                    prefix = options["query_prefix"] if query else options["document_prefix"]
                    inputs = tokenizer(
                        [prefix + text for text in payload],
                        padding=True,
                        truncation=False,
                        return_tensors="pt",
                    )
                    if inputs["input_ids"].shape[1] > options["max_tokens"]:
                        raise EmbeddingError("文本超过模型输入长度，请缩短查询或调整分块。")
                    with torch.inference_mode():
                        stage = "inference"
                        hidden = model(**inputs).last_hidden_state
                        mask = inputs["attention_mask"]
                        stage = "pooling"
                        output = pool_hidden(hidden, mask, options["pooling"])
                        result = torch.nn.functional.normalize(output, dim=1).tolist()
                connection.send((True, result))
            except EmbeddingError as exc:
                connection.send((False, worker_failure(exc, stage)))
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        # 不通过跨进程异常传递文本、文件内容或依赖的完整异常信息。
        try:
            connection.send((False, worker_failure(exc, stage)))
        except (EOFError, BrokenPipeError):
            pass
    finally:
        connection.close()


class LocalTransformersProvider:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self._lock = threading.Lock()
        self._process = None
        self._connection = None

    def profile(self):
        s = self.settings
        return {
            "adapter": "transformers-last-v2"
            if s.embedding_pooling == "last"
            else "transformers-v1",
            "model": s.embedding_model,
            "revision": s.embedding_revision,
            "dimension": s.embedding_dimension,
            "pooling": s.embedding_pooling,
            "query_prefix": s.embedding_query_prefix,
            "document_prefix": s.embedding_document_prefix,
            "max_tokens": s.embedding_max_tokens,
        }

    def _stop(self):
        if self._connection:
            self._connection.close()
        if self._process:
            if self._process.is_alive():
                self._process.terminate()
            self._process.join(timeout=5)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=5)
        self._connection, self._process = None, None

    def _call(self, operation, payload, query=False):
        s = self.settings
        if not self._lock.acquire(timeout=s.embedding_timeout_seconds):
            raise EmbeddingError("模型忙，请稍后重试。")
        try:
            if not (s.embedding_dir / "manifest.json").is_file():
                raise EmbeddingError("本地模型未准备，请先运行 backend/setup_embeddings.py。")
            if self._process is None or not self._process.is_alive():
                self._stop()
                context = mp.get_context("spawn")
                self._connection, child = context.Pipe()
                options = {
                    **self.profile(),
                    "directory": str(s.embedding_dir),
                    "chunk_tokens": s.chunk_tokens,
                    "overlap": s.chunk_overlap,
                }
                self._process = context.Process(
                    target=_model_worker, args=(child, options), daemon=True
                )
                self._process.start()
                child.close()
            self._connection.send((operation, payload, query))
            if not self._connection.poll(s.embedding_timeout_seconds):
                self._stop()
                logger.warning("event=embedding_timeout model=%s", s.embedding_model)
                raise EmbeddingError("本地模型推理超时，推理进程已停止，可稍后重试。")
            success, result = self._connection.recv()
            if not success:
                self._stop()
                logger.warning(
                    "event=embedding_worker_failed model=%s stage=%s exception_type=%s location=%s",
                    s.embedding_model,
                    result["stage"],
                    result["exception_type"],
                    result["location"],
                )
                raise EmbeddingError(result["message"])
            return result
        except (EOFError, BrokenPipeError, OSError) as exc:
            self._stop()
            raise EmbeddingError("模型进程中断，请重试。") from exc
        finally:
            self._lock.release()

    def chunks(self, content):
        return self._call("chunks", content)

    def encode(self, texts, *, query=False):
        result = self._call("encode", texts, query)
        validate_vectors(result, len(texts), self.settings.embedding_dimension)
        return result

    def close(self):
        with self._lock:
            self._stop()


provider: EmbeddingProvider = LocalTransformersProvider()
