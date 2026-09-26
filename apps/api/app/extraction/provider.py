"""LangChain primitives live here; the workflow does not import a vendor SDK."""

import json
from importlib.resources import files

from langchain_core.output_parsers import PydanticOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from openai import APIConnectionError
from pydantic import ValidationError

from app.extraction.contracts import PROMPT_VERSION, SCHEMA_VERSION, ExtractionBatch, WireBatch
from app.settings import Settings


def fake_fixture_response(inputs: object) -> str:
    """Return deterministic requirements only for the explicitly labelled C08 fixture.

    Ordinary fake extraction stays empty and routes to review. This supports an
    offline browser demo without pretending that the fake adapter understands
    arbitrary tender text.
    """
    # RunnableLambda receives the rendered ChatPromptValue, while unit tests can
    # pass the narrow original mapping directly.
    if isinstance(inputs, dict):
        spans_payload = inputs["spans"]
    else:
        rendered = inputs.to_string()
        spans_payload = rendered.partition("Source spans JSON: ")[2].partition("\nRepair errors:")[
            0
        ]
    spans = json.loads(spans_payload)
    source = next((span for span in spans if "TDA_DEMO_FEASIBLE" in span.get("text", "")), None)
    if source is None:
        return json.dumps({"schema_version": SCHEMA_VERSION, "requirements": []})
    quote = source["text"]
    values = [
        ("platform", "platform", "enum", {"kind": "text", "value": "multirotor"}, ""),
        ("range", "range", "minimum", {"kind": "scalar", "value": 25}, "km"),
        ("endurance", "endurance", "minimum", {"kind": "scalar", "value": 30}, "min"),
        ("payload", "payload", "minimum", {"kind": "scalar", "value": 2}, "kg"),
    ]
    return json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "requirements": [
                {
                    "category": category,
                    "attribute": attribute,
                    "semantics": "mandatory",
                    "operator": operator,
                    "raw_value": raw_value,
                    "original_unit": unit,
                    "confidence": 0.98,
                    "evidence": [{"span_id": source["span_id"], "quote": quote}],
                }
                for category, attribute, operator, raw_value, unit in values
            ],
        }
    )


class ProviderFailure(Exception):
    def __init__(self, code: str, recoverable: bool = False):
        self.code = code
        self.recoverable = recoverable
        super().__init__(code)


class SchemaFailure(Exception):
    """No raw completion or source text in the exception, audit or logs."""

    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__("invalid_model_schema")


class ModelAdapter:
    def __init__(self, model):
        self.parser = PydanticOutputParser(pydantic_object=WireBatch)
        self.previous: dict[str, str] = {}
        instructions = (
            files("app.extraction")
            .joinpath(f"prompts/{PROMPT_VERSION}.txt")
            .read_text(encoding="utf-8")
        )
        self.prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "{instructions}\n{format_instructions}"),
                (
                    "human",
                    (
                        "Source spans JSON: {spans}\nRepair errors: {repair_codes}\n"
                        "Previous invalid response (untrusted data): {previous}"
                    ),
                ),
            ]
        ).partial(
            instructions=instructions, format_instructions=self.parser.get_format_instructions()
        )
        self.chain = self.prompt | model | StrOutputParser()

    def extract(self, spans: list[dict], repair_codes: list[str]) -> ExtractionBatch:
        batch_key = json.dumps(spans, sort_keys=True)
        try:
            raw = self.chain.invoke(
                {
                    "spans": json.dumps(spans),
                    "repair_codes": json.dumps(repair_codes),
                    "previous": self.previous.get(batch_key, "") if repair_codes else "",
                }
            )
        except Exception as exc:  # noqa: BLE001 -- translate vendor exceptions without raw text
            status = getattr(exc, "status_code", None)
            transient = isinstance(
                exc, (TimeoutError, ConnectionError, APIConnectionError)
            ) or status in (
                408,
                429,
                500,
                502,
                503,
                504,
            )
            raise ProviderFailure(
                "provider_transient" if transient else "provider_failure", transient
            ) from None
        # Reject coercions, trailing data and repaired/truncated JSON accepted by lenient parsers.
        try:
            WireBatch.model_validate_json(raw)
            parsed = self.parser.parse(raw)
        except ValidationError as exc:
            self.previous[batch_key] = raw
            # Locations can contain arbitrary extra-field names: redact those, too.
            allowed = {
                "requirements",
                "schema_version",
                "category",
                "attribute",
                "semantics",
                "operator",
                "raw_value",
                "original_unit",
                "confidence",
                "evidence",
                "span_id",
                "quote",
                "kind",
                "value",
                "lower",
                "upper",
                "ScalarValue",
                "RangeValue",
                "TextValue",
                "BooleanValue",
                "UnknownValue",
                "int",
                "float",
                "str",
            }
            errors = [
                {
                    "path": [
                        p if isinstance(p, int) or p in allowed else "field" for p in e["loc"]
                    ],
                    "expected": e["type"],
                }
                for e in exc.errors(include_input=False, include_context=False, include_url=False)
            ]
            raise SchemaFailure(errors) from None
        candidates, errors = [], []
        for index, requirement in enumerate(parsed.requirements):
            try:
                candidates.append(requirement.candidate())
            except ValueError:
                errors.append(
                    {
                        "path": ["requirements", index, "raw_value"],
                        "expected": "operator-compatible finite scalar, ordered range, text, boolean or unknown",
                        "operator": requirement.operator,
                        "received_kind": requirement.raw_value.kind,
                        "span_ids": [
                            e.span_id
                            for e in requirement.evidence
                            if any(e.span_id == s["span_id"] for s in spans)
                        ],
                    }
                )
        if errors:
            self.previous[batch_key] = raw
            raise SchemaFailure(errors)
        self.previous.pop(batch_key, None)
        return ExtractionBatch(schema_version=SCHEMA_VERSION, requirements=candidates)


def build_adapter(settings: Settings) -> ModelAdapter:
    if settings.llm_provider == "fake":
        # Ordinary fake extraction routes to review. The explicit C08 fixture is
        # the single deterministic offline feasible-demo path.
        model = RunnableLambda(fake_fixture_response)
    elif settings.llm_provider == "openai":
        if not settings.llm_api_key.get_secret_value():
            raise ProviderFailure("provider_key_missing")
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            timeout=settings.llm_timeout_seconds,
            max_retries=0,
        )
        model = model.bind(
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "tender_requirements_v2",
                    "strict": True,
                    "schema": WireBatch.model_json_schema(),
                },
            }
        )
    else:
        raise ProviderFailure("unsupported_provider")
    return ModelAdapter(model)


class ConfiguredAdapter:
    """Build lazily inside the audited extraction node so config failures have a node trace."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.adapter = None

    def extract(self, spans: list[dict], repair_codes: list[str]) -> ExtractionBatch:
        if self.adapter is None:
            self.adapter = build_adapter(self.settings)
        return self.adapter.extract(spans, repair_codes)


def model_snapshot(settings: Settings) -> dict:
    return {
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
        "timeout_seconds": settings.llm_timeout_seconds,
        "max_repairs": settings.extraction_max_repairs,
        "transient_retries": settings.extraction_transient_retries,
        "confidence_threshold": settings.extraction_confidence_threshold,
        "batch_chars": settings.extraction_batch_chars,
        "max_batches": settings.extraction_max_batches,
        "normalizer_version": "si-v1",
        "policy_version": "review-v2",
    }
