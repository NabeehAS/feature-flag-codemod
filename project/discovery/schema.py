from __future__ import annotations

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)


class EvidenceItem(BaseModel):
    """
    One auditable reason supporting a remediation target.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    source: Literal[
        "business_context",
        "repository",
    ]

    quote: str = Field(
        min_length=1,
        max_length=500,
    )

    explanation: str = Field(
        min_length=1,
        max_length=1_000,
    )

    @field_validator(
        "quote",
        "explanation",
    )
    @classmethod
    def normalize_text(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "evidence text cannot be blank"
            )

        return normalized


class RemediationTarget(BaseModel):
    """
    A proposed deprecated flag and its proven final state.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    flag_name: str = Field(
        min_length=1,
        max_length=200,
    )

    final_state: StrictBool

    files_affected: list[str] = Field(
        min_length=1,
        max_length=200,
    )

    evidence: list[
        EvidenceItem
    ] = Field(
        min_length=1,
        max_length=20,
    )

    @field_validator("flag_name")
    @classmethod
    def normalize_flag_name(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "flag_name cannot be blank"
            )

        return normalized

    @field_validator(
        "files_affected"
    )
    @classmethod
    def validate_files(
        cls,
        value: list[str],
    ) -> list[str]:
        normalized = [
            path.strip()
            for path in value
        ]

        if any(
            not path
            for path in normalized
        ):
            raise ValueError(
                "files_affected cannot "
                "contain blank paths"
            )

        if (
            len(set(normalized))
            != len(normalized)
        ):
            raise ValueError(
                "files_affected cannot "
                "contain duplicates"
            )

        return normalized

    @model_validator(mode="after")
    def require_business_context_evidence(
        self,
    ) -> "RemediationTarget":
        has_business_evidence = any(
            item.source
            == "business_context"
            for item in self.evidence
        )

        if not has_business_evidence:
            raise ValueError(
                "each remediation target requires "
                "business_context evidence"
            )

        return self


class DiscoveryPlan(BaseModel):
    """
    Structured output returned by the AI discovery layer.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    targets: list[
        RemediationTarget
    ] = Field(
        max_length=100,
    )

    @model_validator(mode="after")
    def reject_duplicate_flags(
        self,
    ) -> "DiscoveryPlan":
        names = [
            target.flag_name
            for target in self.targets
        ]

        if (
            len(set(names))
            != len(names)
        ):
            raise ValueError(
                "a discovery plan cannot contain "
                "duplicate flag targets"
            )

        return self