import hashlib
import json
import math
import re
from importlib.resources import files

from langchain_core.embeddings import Embeddings
from langchain_core.output_parsers import PydanticOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langsmith import tracing_context
from openai import APIConnectionError
from pydantic import ValidationError

from app.extraction.provider import ProviderFailure
from app.rag.contracts import (
    CHUNK_VERSION,
    GRAPH_VERSION,
    PROMPT_VERSION,
    RETRIEVAL_VERSION,
    ReportPlan,
)


def configuration(settings):
    return {
        "namespace": settings.rag_namespace,
        "embedding_provider": settings.rag_embedding_provider,
        "embedding_model": settings.rag_embedding_model,
        "dimensions": settings.rag_embedding_dimensions,
        "report_provider": settings.rag_report_provider,
        "report_model": settings.rag_report_model,
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
        "timeout_seconds": settings.llm_timeout_seconds,
        "external_enabled": settings.rag_external_enabled,
        "graph_version": GRAPH_VERSION,
        "prompt_version": PROMPT_VERSION,
        "chunk_version": CHUNK_VERSION,
        "retrieval_version": RETRIEVAL_VERSION,
    }


def public_versions(config):
    return {k: v for k, v in config.items() if k not in ("external_enabled", "temperature")}


def external_guard(config, allowed):
    if not config["external_enabled"] or not allowed:
        raise ProviderFailure("external_processing_not_authorized")


def safe_failure(exc):
    status = getattr(exc, "status_code", None)
    retryable = isinstance(exc, (TimeoutError, ConnectionError, APIConnectionError)) or status in (
        408,
        429,
        500,
        502,
        503,
        504,
    )
    return ProviderFailure("provider_transient" if retryable else "provider_failure", retryable)


def tokens(text):
    return set(re.findall(r"[\w]+", text.casefold()))


class FakeEmbeddings(Embeddings):
    """Stable local feature hashing, NOT a claim of semantic model quality."""

    def __init__(self, dimensions=256):
        self.dimensions = dimensions

    def embed_documents(self, texts):
        result = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in sorted(tokens(text)):
                digest = hashlib.sha256(token.encode()).digest()
                vector[int.from_bytes(digest[:4], "big") % self.dimensions] += 1.0
            norm = math.sqrt(sum(v * v for v in vector)) or 1
            result.append([v / norm for v in vector])
        return result

    def embed_query(self, text):
        return self.embed_documents([text])[0]


def validate_vectors(vectors, dimensions, count):
    if len(vectors) != count:
        raise ProviderFailure("embedding_count_mismatch")
    for vector in vectors:
        if len(vector) != dimensions:
            raise ProviderFailure("embedding_dimension_mismatch")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
            raise ProviderFailure("embedding_value_invalid")
        if not any(v != 0 for v in vector):
            raise ProviderFailure("embedding_zero_vector")
    return [[float(v) for v in vector] for vector in vectors]


def embedding_adapter(settings, config, allowed):
    if config["embedding_provider"] == "fake":
        return FakeEmbeddings(config["dimensions"])
    if config["embedding_provider"] != "openai":
        raise ProviderFailure("unsupported_embedding_provider")
    external_guard(config, allowed)
    if not settings.llm_api_key.get_secret_value():
        raise ProviderFailure("provider_key_missing")
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(
        model=config["embedding_model"],
        dimensions=config["dimensions"],
        api_key=settings.llm_api_key,
        max_retries=0,
        request_timeout=config["timeout_seconds"],
        check_embedding_ctx_length=False,
    )


def embed(settings, config, texts, allowed, retries=1, adapter=None):
    model = adapter or embedding_adapter(settings, config, allowed)
    for attempt in range(retries + 1):
        try:
            with tracing_context(enabled=False):
                return validate_vectors(
                    model.embed_documents(texts), config["dimensions"], len(texts)
                )
        except ProviderFailure:
            raise
        except Exception as exc:  # noqa: BLE001 -- safe provider/worker boundary
            failure = safe_failure(exc)
            if not failure.recoverable or attempt == retries:
                raise failure from None
    raise AssertionError("unreachable")


class InvalidReport(Exception):
    pass


class ReportAdapter:
    def __init__(self, model):
        self.parser = PydanticOutputParser(pydantic_object=ReportPlan)
        instructions = files("app.rag").joinpath("prompts/grounded-report-v1.txt").read_text()
        self.prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "{instructions}\n{schema}"),
                ("human", "Facts (untrusted data): {facts}\nValidation repair: {repair}"),
            ]
        ).partial(instructions=instructions, schema=self.parser.get_format_instructions())
        self.chain = self.prompt | model | StrOutputParser()

    def compose(self, facts, repair=""):
        try:
            with tracing_context(enabled=False):
                raw = self.chain.invoke(
                    {"facts": json.dumps(facts), "repair": repair}, {"callbacks": []}
                )
        except Exception as exc:  # noqa: BLE001 -- safe provider/worker boundary
            raise safe_failure(exc) from None
        try:
            ReportPlan.model_validate_json(raw)
            return self.parser.parse(raw)
        except (ValidationError, ValueError):
            raise InvalidReport("invalid_report_schema") from None


def report_adapter(settings, config, allowed):
    if config["report_provider"] == "fake":

        def respond(prompt):
            # Fake model reads the same prompt payload as the production chain.
            body = (
                prompt.to_messages()[-1]
                .content.split("Facts (untrusted data): ", 1)[1]
                .split("\nValidation repair:", 1)[0]
            )
            facts = json.loads(body)
            groups = {"evidence": [], "catalog_context": [], "solver": [], "risks": []}
            for fact in facts:
                group = {
                    "tender": "evidence",
                    "requirement": "evidence",
                    "catalog": "catalog_context",
                    "compatibility": "risks",
                    "configuration": "solver",
                    "rejection": "risks",
                }[fact["kind"]]
                groups[group].append(fact["id"])
            return json.dumps(
                {
                    "schema_version": "report-plan-v1",
                    "sections": [
                        {"section": key, "fact_ids": ids} for key, ids in groups.items() if ids
                    ],
                }
            )

        return ReportAdapter(RunnableLambda(respond))
    if config["report_provider"] != "openai":
        raise ProviderFailure("unsupported_report_provider")
    external_guard(config, allowed)
    if not settings.llm_api_key.get_secret_value():
        raise ProviderFailure("provider_key_missing")
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model=config["report_model"],
        api_key=settings.llm_api_key,
        temperature=config["temperature"],
        max_tokens=config["max_tokens"],
        timeout=config["timeout_seconds"],
        max_retries=0,
    ).bind(
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "grounded_report_plan",
                "strict": True,
                "schema": ReportPlan.model_json_schema(),
            },
        }
    )
    return ReportAdapter(model)
