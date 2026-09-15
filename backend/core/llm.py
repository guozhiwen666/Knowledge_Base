"""模型客户端：向量化与流式生成（对应 5.5 的 Prompt 组装与流式回答生成）。

服务层对模型的依赖一律是"注入一个函数"，不绑死厂商：

* 向量化 —— ``Callable[[Sequence[str]], Sequence[Sequence[float]]]``
* 流式生成 —— ``Callable[[str], Iterator[str]]``

本模块提供基于 OpenAI 兼容协议的实现（`.env` 里配的是 Dashscope 兼容模式网关，
``OPENAI_BASE_URL`` 指向 ``.../compatible-mode/v1``），换厂商只改 `.env`。

**token 用量怎么取**：流式响应默认不带 usage，需要在请求里打开
``stream_options={"include_usage": True}``。用完流后从
:attr:`OpenAiChatStream.last_usage` 读，供 11.1 的 ``prompt_tokens`` /
``completion_tokens`` / ``total_tokens`` 落库。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from core.config import Settings

__all__ = [
    "OpenAiEmbedding",
    "OpenAiChatStream",
    "build_embedding",
    "build_llm_stream",
]


class OpenAiEmbedding:
    """向量化实现：走 OpenAI 兼容协议的 ``/embeddings``。

    Dashscope 的 ``text-embedding-v4`` 单次请求有条数上限，因此按
    ``TEXT_EMBEDDING_BATCH_SIZE`` 分批发送，最后按原顺序拼回一个列表。
    """

    def __init__(self, settings: Settings) -> None:
        from openai import OpenAI

        self._client = OpenAI(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
        )
        self._model = settings.embedding_model
        self._batch_size = max(int(settings.embedding_batch_size), 1)
        self.dimension = settings.embedding_dim

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        """把一批文本转成向量。

        :param texts: 待向量化文本。
        :return: 与入参一一对应的向量列表（顺序保证一致）。
        :raise RuntimeError: 密钥或基址没配。
        """
        # 第 1 步：空入参直接返回，避免无意义的网络往返
        items = list(texts)
        if not items:
            return []
        if not self._model:
            raise RuntimeError("未配置向量化模型（TEXT_EMBEDDING_MODEL）")

        # 第 2 步：分批请求
        vectors: list[list[float]] = []
        for start in range(0, len(items), self._batch_size):
            batch = items[start : start + self._batch_size]
            response = self._client.embeddings.create(model=self._model, input=batch)
            vectors.extend([list(item.embedding) for item in response.data])
        return vectors


class OpenAiChatStream:
    """流式生成实现：走 OpenAI 兼容协议的 ``/chat/completions``。

    :attr:`last_usage` 保存最近一次流式调用的 token 用量，形如
    ``{"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}``；
    网关不回 usage 时为空字典，调用方按 0 处理即可（11.1 允许 Token 为 0，
    例如 FAQ 缓存命中时本来就不调模型）。
    """

    def __init__(self, settings: Settings) -> None:
        from openai import OpenAI

        self._client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
        )
        self._model = settings.llm_model
        self._temperature = settings.llm_temperature
        self.last_usage: dict = {}

    def __call__(self, prompt: str) -> Iterator[str]:
        """按流式返回增量文本。"""
        # 第 1 步：每次调用先清空上一次的用量，避免串数据
        self.last_usage = {}
        if not self._model:
            raise RuntimeError("未配置对话模型（LLM_DEFAULT_MODEL）")

        # 第 2 步：发起流式请求，要求网关回传 usage
        stream = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self._temperature,
            stream=True,
            stream_options={"include_usage": True},
        )

        # 第 3 步：逐块产出增量文本；末尾的 usage 块没有 choices，需单独处理
        for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                self.last_usage = {
                    "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                    "completion_tokens": int(
                        getattr(usage, "completion_tokens", 0) or 0
                    ),
                    "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
                }
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            text = getattr(delta, "content", None) if delta is not None else None
            if text:
                yield text


def build_embedding(settings: Settings):
    """构造向量化函数。"""
    return OpenAiEmbedding(settings)


def build_llm_stream(settings: Settings):
    """构造流式生成函数。"""
    return OpenAiChatStream(settings)
