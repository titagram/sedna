"""Tests for backend-neutral, validated retrieval contracts."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from sedna.knowledge.retrieval import (
    AuthorizationScope,
    AuthorizationState,
    CurrentSituation,
    EpistemicLane,
    IndexAudit,
    IndexCandidate,
    IndexedArtifactState,
    IndexedSourceState,
    IndexStateSnapshot,
    KnowledgeGap,
    KnowledgeGapCode,
    RejectedCandidate,
    RetrievalHit,
    RetrievalIndex,
    RetrievalQuery,
    RetrievalResult,
    ScoreComponents,
    SituationFacet,
    TargetKind,
    ValidatedTarget,
)
from sedna.knowledge.schema import (
    ApplicabilityContext,
    ArtifactType,
    CaseAction,
    CaseState,
    CaseStep,
    DecisionRule,
    EpistemicAssessment,
    ExtractionMetadata,
    Generalizability,
    KnowledgeCase,
    KnowledgeRole,
    ObservedOutcome,
    Origin,
    ReferenceArtifact,
    SourceLocation,
    SourceQuality,
    SourceRef,
    VerificationStatus,
)


def source_ref() -> SourceRef:
    return SourceRef(
        source_id="source-http",
        path="raw_src/reference.md",
        location=SourceLocation(start_line=1),
    )


def reference() -> ReferenceArtifact:
    return ReferenceArtifact(
        artifact_type=ArtifactType.METHODOLOGY,
        knowledge_role=KnowledgeRole.REFERENCE,
        artifact_id="reference-http",
        subject="HTTP discovery",
        statement="Inspect an HTTP service before choosing a method.",
        origin=Origin.EXPLICIT,
        applicability=ApplicabilityContext(),
        assessment=EpistemicAssessment(
            source_reliability=0.9,
            extraction_confidence=0.8,
            generalizability=Generalizability.MEDIUM,
            context_specificity=0.4,
            verification_status=VerificationStatus.VERIFIED,
            observed_outcome=ObservedOutcome.INFORMATIONAL,
            independence_group="source-http",
        ),
        source_refs=(source_ref(),),
        extraction=ExtractionMetadata(
            schema_version="2",
            parser_id="markdown",
            parser_version="1",
            extractor_id="semantic",
            extractor_version="1",
        ),
    )


def authorized_scope(target: ValidatedTarget) -> AuthorizationScope:
    return AuthorizationScope(
        state=AuthorizationState.AUTHORIZED,
        exact_targets=(target,),
    )


def case_step(*, negative: bool = False) -> CaseStep:
    role = KnowledgeRole.NEGATIVE_CASE if negative else KnowledgeRole.CASE_STUDY
    outcome = ObservedOutcome.FAILURE if negative else ObservedOutcome.SUCCESS
    return CaseStep(
        artifact_type=ArtifactType.CASE_STEP,
        knowledge_role=role,
        step_id="case-step-negative" if negative else "case-step-positive",
        ordinal=1,
        state_before=CaseState(access="none"),
        observations=("HTTP service exposed",),
        hypotheses=(),
        selected_action=CaseAction(intent="inspect_http"),
        evidence=(),
        state_after=CaseState(access="none"),
        origin=Origin.EXPLICIT,
        applicability=ApplicabilityContext(),
        assessment=EpistemicAssessment(
            source_reliability=0.9,
            extraction_confidence=0.8,
            generalizability=Generalizability.MEDIUM,
            context_specificity=0.4,
            verification_status=VerificationStatus.VERIFIED,
            observed_outcome=outcome,
            independence_group="source-http",
        ),
        source_refs=(source_ref(),),
        extraction=ExtractionMetadata(
            schema_version="2",
            parser_id="markdown",
            parser_version="1",
            extractor_id="semantic",
            extractor_version="1",
        ),
    )


def decision_rule() -> DecisionRule:
    return DecisionRule(
        artifact_type=ArtifactType.DECISION_RULE,
        knowledge_role=KnowledgeRole.REFERENCE,
        rule_id="rule-http",
        trigger_observations=("HTTP service exposed",),
        rationale="Use observed services to guide the next decision.",
        action_intent="inspect_http",
        origin=Origin.EXPLICIT,
        applicability=ApplicabilityContext(),
        assessment=EpistemicAssessment(
            source_reliability=0.9,
            extraction_confidence=0.8,
            generalizability=Generalizability.MEDIUM,
            context_specificity=0.4,
            verification_status=VerificationStatus.VERIFIED,
            observed_outcome=ObservedOutcome.INFORMATIONAL,
            independence_group="source-http",
        ),
        source_refs=(source_ref(),),
        extraction=ExtractionMetadata(
            schema_version="2",
            parser_id="markdown",
            parser_version="1",
            extractor_id="semantic",
            extractor_version="1",
        ),
    )


@pytest.mark.parametrize(
    ("value", "kind", "normalized"),
    (
        ("10.10.10.10", TargetKind.IPV4, "10.10.10.10"),
        ("2001:db8::1", TargetKind.IPV6, "2001:db8::1"),
        ("WEB.Example.Test", TargetKind.HOSTNAME, "web.example.test"),
        (
            "https://WEB.Example.Test:8443/docs",
            TargetKind.URL,
            "https://web.example.test:8443/docs",
        ),
    ),
)
def test_validated_target_classifies_network_identifiers(
    value: str,
    kind: TargetKind,
    normalized: str,
):
    target = ValidatedTarget.parse(value)

    assert target.is_valid
    assert target.kind is kind
    assert target.normalized == normalized
    assert target.error is None


def test_invalid_ipv4_is_typed_and_is_not_reclassified_as_hostname():
    target = ValidatedTarget.parse("300.456.456.123")

    assert not target.is_valid
    assert target.kind is TargetKind.INVALID
    assert target.normalized is None
    assert target.error == "invalid_ipv4"


def test_generic_targets_require_an_explicit_kind_instead_of_ambiguous_inference():
    implicit = ValidatedTarget.parse("lab:target")
    explicit = ValidatedTarget(value="lab:target", kind=TargetKind.GENERIC)

    assert implicit.kind is TargetKind.INVALID
    assert explicit.is_valid
    assert explicit.normalized == "lab:target"


@pytest.mark.parametrize(
    "value",
    (
        "300.456.456.123",
        "http://300.456.456.123/",
        "https://[2001:db8:::1]/",
        "2001:db8:::1",
        "https://300.456.456.123:8443/",
    ),
)
def test_structured_invalid_targets_cannot_be_reclassified_as_generic(value: str):
    parsed = ValidatedTarget.parse(value)
    explicit_generic = ValidatedTarget(value=value, kind=TargetKind.GENERIC)

    assert parsed.kind is TargetKind.INVALID
    assert explicit_generic.kind is TargetKind.INVALID
    assert not explicit_generic.is_valid


@pytest.mark.parametrize("value", ("http-server", "http2.example", "https-worker"))
def test_hostnames_starting_with_http_or_https_are_not_misclassified_as_urls(value: str):
    target = ValidatedTarget.parse(value)

    assert target.kind is TargetKind.HOSTNAME


@pytest.mark.parametrize(
    "value",
    ("http/host", "HTTP/host", "https/host", "HtTp/host", "HTTPS/HOST"),
)
def test_single_slash_http_targets_are_invalid_even_when_declared_generic(value: str):
    parsed = ValidatedTarget.parse(value)
    explicit_generic = ValidatedTarget(value=value, kind=TargetKind.GENERIC)

    assert parsed.kind is TargetKind.INVALID
    assert explicit_generic.kind is TargetKind.INVALID
    assert not explicit_generic.is_valid


@pytest.mark.parametrize(
    "value",
    (
        "2001:db8::zzzz",
        "abcd::gg",
        "2001:db8:zzzz",
        "1:2:3:4:5:6:7:gg",
        "http:/host",
        "https//host",
        "http:host",
        "HtTp:/host",
        "HTTPS//host",
    ),
)
def test_invalid_colon_bearing_structured_identifiers_cannot_be_generic(value: str):
    target = ValidatedTarget(value=value, kind=TargetKind.GENERIC)

    assert target.kind is TargetKind.INVALID


@pytest.mark.parametrize(
    "value",
    (
        "lab:alpha",
        "lab:alpha:beta",
        "org:project:item",
        "urn:example:asset",
        "svc:api:web",
        "lab:2001:asset",
    ),
)
def test_named_generic_namespaces_remain_available_when_not_address_shaped(value: str):
    target = ValidatedTarget(value=value, kind=TargetKind.GENERIC)

    assert target.is_valid
    assert target.kind is TargetKind.GENERIC
    assert target.normalized == value


@pytest.mark.parametrize(
    "value",
    (
        "2001:db8:zzzz",
        "1:2:3:4:5:6:7:gg",
        "abcd::gg",
        "node:2001:zz",
        "tag:beef:item",
        "gggg:g1:zzzz",
    ),
)
def test_ipv6_shaped_generic_bypasses_remain_invalid(value: str):
    target = ValidatedTarget(value=value, kind=TargetKind.GENERIC)

    assert target.kind is TargetKind.INVALID
    assert not target.is_valid


@pytest.mark.parametrize(
    "value",
    (
        "2001:db8:zzzzz",
        "2001:db8:garbage",
        "1:2:3:4:5:6:7:zzzzz",
        "2001:garbage:item",
        "2001:Db8:GaRbAgE",
        "org:project:item:layer:scope:unit:asset:detail",
    ),
)
def test_leading_hex_or_eight_component_ipv6_shapes_cannot_be_generic(value: str):
    parsed = ValidatedTarget.parse(value)
    explicit_generic = ValidatedTarget(value=value, kind=TargetKind.GENERIC)

    assert parsed.kind is TargetKind.INVALID
    assert explicit_generic.kind is TargetKind.INVALID
    assert not explicit_generic.is_valid


@pytest.mark.parametrize(
    ("value", "normalized"),
    (
        (
            "org:project:item:layer:scope:unit:asset",
            "org:project:item:layer:scope:unit:asset",
        ),
        ("Project Notes:Alpha Beta:Release Item", "project notes:alpha beta:release item"),
    ),
)
def test_non_address_seven_component_and_whitespace_generic_prose_remain_available(
    value: str,
    normalized: str,
):
    target = ValidatedTarget(value=value, kind=TargetKind.GENERIC)

    assert target.is_valid
    assert target.kind is TargetKind.GENERIC
    assert target.normalized == normalized


@pytest.mark.parametrize("value", ("http-server", "http2.example", "https-worker"))
def test_malformed_scheme_detection_does_not_reject_valid_http_prefixed_hostnames(value: str):
    assert ValidatedTarget.parse(value).kind is TargetKind.HOSTNAME


def test_live_situation_facets_have_no_invented_source_provenance():
    facet = SituationFacet(namespace="Platform", key="OS_Family", value="Linux", confidence=0.8)

    assert facet.model_dump() == {
        "namespace": "platform",
        "key": "os_family",
        "value": "linux",
        "confidence": 0.8,
    }
    with pytest.raises(ValidationError, match="source_refs"):
        SituationFacet(
            namespace="platform",
            key="os_family",
            value="linux",
            confidence=0.8,
            source_refs=(source_ref(),),
        )


def test_situation_normalizes_bounds_sorts_and_deduplicates_live_facts():
    situation = CurrentSituation(
        target=ValidatedTarget.parse("10.10.10.10"),
        terms=(" HTTP ", "http", "Service Discovery"),
        facts=(
            SituationFacet(namespace="network", key="position", value="internal", confidence=0.7),
            SituationFacet(namespace="platform", key="os_family", value="Linux", confidence=0.9),
            SituationFacet(namespace="platform", key="os_family", value="linux", confidence=0.9),
        ),
        access=(" None ", "none"),
        services=("HTTP:80", "http:80"),
        hypotheses=(" HTTP virtual host ", "http virtual host"),
        tried_outcomes=(
            ("inspect_http", "informational"),
            ("inspect_http", "informational"),
        ),
        unresolved_questions=(" OS family ", "os family"),
        authorization=AuthorizationScope(
            state=AuthorizationState.AUTHORIZED,
            cidrs=("10.10.10.0/24",),
        ),
    )

    assert situation.terms == ("http", "service discovery")
    assert tuple(facet.namespace for facet in situation.facts) == ("network", "platform")
    assert situation.access == ("none",)
    assert situation.services == ("http:80",)
    assert situation.hypotheses == ("http virtual host",)
    assert situation.tried_outcomes == (("inspect_http", "informational"),)
    assert situation.unresolved_questions == ("os family",)
    assert situation.authorization.state is AuthorizationState.AUTHORIZED
    with pytest.raises(ValidationError, match="unique"):
        CurrentSituation(
            target=ValidatedTarget.parse("10.10.10.10"),
            facts=(
                SituationFacet(namespace="platform", key="os", value="linux", confidence=0.8),
                SituationFacet(namespace="platform", key="os", value="linux", confidence=0.7),
            ),
        )


def test_situation_uses_searchable_validators_for_live_text():
    with pytest.raises(ValidationError, match="final flag"):
        CurrentSituation(
            target=ValidatedTarget.parse("10.10.10.10"),
            terms=("HTB{must_not_be_searchable}",),
        )


def test_retrieval_query_is_bounded_and_carries_only_validated_situation():
    situation = CurrentSituation(target=ValidatedTarget.parse("10.10.10.10"))
    query = RetrievalQuery(
        situation=situation,
        terms=("http", "HTTP"),
        synonyms=("web", "web"),
        max_candidates=12,
        lane_limit=3,
    )

    assert query.terms == ("http",)
    assert query.synonyms == ("web",)
    assert query.max_candidates == 12
    with pytest.raises(ValidationError):
        RetrievalQuery(situation=situation, max_candidates=101)
    with pytest.raises(ValidationError):
        RetrievalQuery(
            situation=situation,
            terms=tuple(f"{index:02d}" + "x" * 510 for index in range(32)),
        )
    with pytest.raises(ValidationError):
        CurrentSituation(target=situation.target, terms=("x" * 1_000_000,))


@pytest.mark.parametrize("value", (math.inf, -math.inf, math.nan, -0.01, 1.01))
def test_score_components_reject_non_finite_and_out_of_range_values(value: float):
    with pytest.raises(ValidationError):
        ScoreComponents(lexical_relevance=value)


def test_retrieval_hit_requires_canonical_identity_and_exact_provenance():
    artifact = reference()
    hit = RetrievalHit(
        artifact_id="reference-http",
        artifact=artifact,
        lane=EpistemicLane.REFERENCE,
        provenance=artifact.source_refs,
        score=ScoreComponents(lexical_relevance=0.8, total=0.8),
        qualification_reasons=("matches normalized HTTP query",),
    )

    assert hit.artifact == artifact
    with pytest.raises(ValidationError, match="artifact_id"):
        RetrievalHit(
            artifact_id="wrong-id",
            artifact=artifact,
            lane=EpistemicLane.REFERENCE,
            provenance=artifact.source_refs,
            score=ScoreComponents(total=0.8),
            qualification_reasons=("matches",),
        )
    with pytest.raises(ValidationError, match="provenance"):
        RetrievalHit(
            artifact_id="reference-http",
            artifact=artifact,
            lane=EpistemicLane.REFERENCE,
            provenance=(),
            score=ScoreComponents(total=0.8),
            qualification_reasons=("matches",),
        )


def test_index_candidates_expose_deeply_validated_fts_evidence_without_ranking_it():
    artifact = reference()
    candidate = IndexCandidate(
        artifact_id=artifact.artifact_id,
        artifact=artifact,
        lexical_relevance=0.7,
        matched_terms=("HTTP",),
        matched_fields=("statement",),
        matched_evidence=("HTTP discovery",),
    )

    assert candidate.matched_terms == ("http",)
    assert candidate.lexical_relevance == 0.7
    with pytest.raises(ValidationError, match="final flag"):
        IndexCandidate(
            artifact_id=artifact.artifact_id,
            artifact=artifact.model_copy(update={"statement": "HTB{copied_flag}"}),
            lexical_relevance=0.7,
        )


def test_hit_deeply_revalidates_constructed_canonical_artifacts_before_returning_them():
    artifact = reference().model_copy(update={"statement": "HTB{copied_flag}"})

    with pytest.raises(ValidationError, match="final flag"):
        RetrievalHit(
            artifact_id="reference-http",
            artifact=artifact,
            lane=EpistemicLane.REFERENCE,
            provenance=(source_ref(),),
            score=ScoreComponents(total=0.8),
            qualification_reasons=("matches",),
        )


def test_artifact_type_and_role_determine_the_only_qualifying_lane():
    positive_step = case_step()
    negative_step = case_step(negative=True)
    rule = decision_rule()
    negative_reference = reference().model_copy(update={"artifact_type": ArtifactType.ANTI_PATTERN})
    exception_reference = reference().model_copy(update={"artifact_type": ArtifactType.EXCEPTION})

    for artifact, expected_lane, identifier in (
        (reference(), EpistemicLane.REFERENCE, "reference-http"),
        (negative_reference, EpistemicLane.NEGATIVE_EVIDENCE, "reference-http"),
        (exception_reference, EpistemicLane.NEGATIVE_EVIDENCE, "reference-http"),
        (positive_step, EpistemicLane.CASE_STEP, "case-step-positive"),
        (negative_step, EpistemicLane.NEGATIVE_EVIDENCE, "case-step-negative"),
        (rule, EpistemicLane.GUIDANCE, "rule-http"),
    ):
        hit = RetrievalHit(
            artifact_id=identifier,
            artifact=artifact,
            lane=expected_lane,
            provenance=artifact.source_refs,
            score=ScoreComponents(total=0.8),
            qualification_reasons=("matches",),
        )
        assert hit.lane is expected_lane

    parent_case = KnowledgeCase(
        artifact_type=ArtifactType.CASE,
        knowledge_role=KnowledgeRole.CASE_STUDY,
        case_id="case-http",
        title="HTTP case",
        starting_access="none",
        steps=(positive_step,),
        outcome="HTTP inspected.",
        source_quality=SourceQuality.COMPLETE,
        origin=Origin.EXPLICIT,
        applicability=ApplicabilityContext(),
        assessment=positive_step.assessment,
        source_refs=(source_ref(),),
        extraction=positive_step.extraction,
    )
    with pytest.raises(ValidationError):
        RetrievalHit(
            artifact_id="case-http",
            artifact=parent_case,
            lane=EpistemicLane.CASE_STEP,
            provenance=parent_case.source_refs,
            score=ScoreComponents(total=0.8),
            qualification_reasons=("matches",),
        )


def test_result_requires_descending_score_order_and_excludes_rejected_hit_overlap():
    artifact = reference()
    high = RetrievalHit(
        artifact_id="reference-http",
        artifact=artifact,
        lane=EpistemicLane.REFERENCE,
        provenance=artifact.source_refs,
        score=ScoreComponents(total=0.9),
        qualification_reasons=("matches",),
    )
    low_artifact = reference().model_copy(update={"artifact_id": "reference-low"})
    low = RetrievalHit(
        artifact_id="reference-low",
        artifact=low_artifact,
        lane=EpistemicLane.REFERENCE,
        provenance=low_artifact.source_refs,
        score=ScoreComponents(total=0.2),
        qualification_reasons=("matches",),
    )
    target = ValidatedTarget.parse("10.10.10.10")
    authorization = authorized_scope(target)
    with pytest.raises(ValidationError, match="ordered"):
        RetrievalResult(target=target, authorization=authorization, references=(low, high))

    from sedna.knowledge.retrieval import RejectedCandidate

    rejected = RejectedCandidate(
        artifact_id="reference-http",
        artifact=artifact,
        lane=EpistemicLane.REFERENCE,
        provenance=artifact.source_refs,
        rejection_reasons=("not applicable",),
    )
    with pytest.raises(ValidationError, match="rejected"):
        RetrievalResult(
            target=target,
            authorization=authorization,
            references=(high,),
            rejected_candidates=(rejected,),
        )


def test_authorization_scope_is_typed_and_checks_target_relationships():
    ip_target = ValidatedTarget.parse("10.10.10.10")
    url_target = ValidatedTarget.parse("https://web.example.test:8443/docs")
    generic_target = ValidatedTarget(value="lab:alpha", kind=TargetKind.GENERIC)

    assert AuthorizationScope(
        state=AuthorizationState.AUTHORIZED,
        cidrs=("10.10.10.0/24",),
    ).authorizes(ip_target)
    assert AuthorizationScope(
        state=AuthorizationState.AUTHORIZED,
        url_origins=("https://web.example.test:8443",),
    ).authorizes(url_target)
    assert AuthorizationScope(
        state=AuthorizationState.AUTHORIZED,
        generic_ids=("lab:alpha",),
    ).authorizes(generic_target)
    with pytest.raises(ValidationError, match="authorized scope"):
        CurrentSituation(
            target=ip_target,
            authorization=AuthorizationScope(
                state=AuthorizationState.AUTHORIZED,
                cidrs=("10.10.11.0/24",),
            ),
        )


@pytest.mark.parametrize("host", ("10.10.10.10", "300.456.456.123"))
def test_authorization_hostname_scope_never_accepts_dotted_numeric_ip_like_values(host: str):
    with pytest.raises(ValidationError, match="hostnames"):
        AuthorizationScope(state=AuthorizationState.AUTHORIZED, hostnames=(host,))


def test_url_targets_with_ip_hosts_use_ip_authorization_not_hostname_scope():
    ipv4_url = ValidatedTarget.parse("https://10.10.10.10:8443/docs")
    ipv6_url = ValidatedTarget.parse("https://[2001:db8::1]:8443/docs")

    assert AuthorizationScope(
        state=AuthorizationState.AUTHORIZED,
        cidrs=("10.10.10.0/24",),
    ).authorizes(ipv4_url)
    assert AuthorizationScope(
        state=AuthorizationState.AUTHORIZED,
        exact_targets=(ValidatedTarget.parse("2001:db8::1"),),
    ).authorizes(ipv6_url)
    assert not AuthorizationScope(
        state=AuthorizationState.AUTHORIZED,
        hostnames=("web.example.test",),
    ).authorizes(ipv4_url)


def test_scope_and_index_candidates_enforce_explicit_cumulative_text_budgets():
    with pytest.raises(ValidationError, match="authorization scope text"):
        AuthorizationScope(
            state=AuthorizationState.AUTHORIZED,
            generic_ids=tuple(f"scope-{index:02d}-" + "x" * 2039 for index in range(64)),
        )
    artifact = reference()
    with pytest.raises(ValidationError, match="candidate match text"):
        IndexCandidate(
            artifact_id=artifact.artifact_id,
            artifact=artifact,
            lexical_relevance=0.4,
            matched_evidence=tuple(f"evidence-{index:02d}-" + "x" * 2036 for index in range(32)),
        )


def test_unauthorized_or_unknown_scope_returns_prebackend_authorization_gap():
    target = ValidatedTarget.parse("10.10.10.10")
    for state in (AuthorizationState.UNAUTHORIZED, AuthorizationState.UNKNOWN):
        scope = AuthorizationScope(state=state)
        result = RetrievalResult(
            target=target,
            authorization=scope,
            knowledge_gap=KnowledgeGap(
                code=KnowledgeGapCode.UNAUTHORIZED_SCOPE,
                summary="Authorization has not been established.",
            ),
        )
        assert result.authorization.state is state


def test_knowledge_gap_keeps_rejected_candidates_but_never_qualifying_hits():
    from sedna.knowledge.retrieval import RejectedCandidate

    artifact = reference()
    target = ValidatedTarget.parse("10.10.10.10")
    rejection = RejectedCandidate(
        artifact_id=artifact.artifact_id,
        artifact=artifact,
        lane=EpistemicLane.REFERENCE,
        provenance=artifact.source_refs,
        rejection_reasons=("confirmed incompatible platform",),
    )
    result = RetrievalResult(
        target=target,
        authorization=authorized_scope(target),
        rejected_candidates=(rejection,),
        knowledge_gap=KnowledgeGap(
            code=KnowledgeGapCode.NO_APPLICABLE_KNOWLEDGE,
            summary="No applicable knowledge.",
        ),
    )

    assert result.rejected_candidates == (rejection,)


def test_retrieval_result_separates_lanes_and_requires_consistent_gap_shape():
    artifact = reference()
    hit = RetrievalHit(
        artifact_id="reference-http",
        artifact=artifact,
        lane=EpistemicLane.REFERENCE,
        provenance=artifact.source_refs,
        score=ScoreComponents(total=0.8),
        qualification_reasons=("matches",),
    )
    target = ValidatedTarget.parse("10.10.10.10")
    authorization = authorized_scope(target)
    result = RetrievalResult(target=target, authorization=authorization, references=(hit,))

    assert result.references == (hit,)
    with pytest.raises(ValidationError, match="case_step"):
        RetrievalResult(target=target, authorization=authorization, case_steps=(hit,))
    with pytest.raises(ValidationError, match="knowledge gap"):
        RetrievalResult(
            target=target,
            authorization=authorization,
            references=(hit,),
            knowledge_gap=KnowledgeGap(
                code=KnowledgeGapCode.NO_APPLICABLE_KNOWLEDGE,
                summary="No applicable knowledge.",
            ),
        )


def test_invalid_target_result_is_a_gap_without_any_backend_candidates():
    target = ValidatedTarget.parse("300.456.456.123")
    result = RetrievalResult(
        target=target,
        knowledge_gap=KnowledgeGap(
            code=KnowledgeGapCode.INVALID_TARGET,
            summary="The supplied target is not syntactically valid.",
        ),
    )

    assert result.is_invalid_target
    with pytest.raises(ValidationError, match="matching knowledge gap"):
        RetrievalResult(target=target, references=())


def test_knowledge_gap_codes_are_closed_and_index_protocol_is_runtime_checkable():
    assert {member.value for member in KnowledgeGapCode} == {
        "invalid_target",
        "no_applicable_knowledge",
        "missing_required_context",
        "retrieval_unavailable",
        "unauthorized_scope",
    }
    assert isinstance(IndexAudit(), IndexAudit)
    assert isinstance(_IndexDouble(), RetrievalIndex)
    with pytest.raises(ValidationError):
        KnowledgeGap(code="invented", summary="Nope")


def test_retrieval_unavailable_is_a_truthful_closed_gap_without_learning_advice():
    target = ValidatedTarget.parse("10.10.10.10")
    gap = KnowledgeGap(
        code=KnowledgeGapCode.RETRIEVAL_UNAVAILABLE,
        summary="Knowledge retrieval is temporarily unavailable.",
        missing_context=("retrieval index availability",),
    )

    result = RetrievalResult(
        target=target,
        authorization=authorized_scope(target),
        knowledge_gap=gap,
    )

    assert result.knowledge_gap == gap
    assert gap.research_eligible is False
    assert gap.suggested_document_ingestion == ()

    with pytest.raises(ValidationError, match="retrieval_unavailable"):
        KnowledgeGap(
            code=KnowledgeGapCode.RETRIEVAL_UNAVAILABLE,
            summary="Knowledge retrieval is temporarily unavailable.",
            research_eligible=True,
        )
    with pytest.raises(ValidationError, match="retrieval_unavailable"):
        KnowledgeGap(
            code=KnowledgeGapCode.RETRIEVAL_UNAVAILABLE,
            summary="Knowledge retrieval is temporarily unavailable.",
            suggested_document_ingestion=("ingest more documents",),
        )

    artifact = reference()
    rejection = RejectedCandidate(
        artifact_id=artifact.artifact_id,
        artifact=artifact,
        lane=EpistemicLane.REFERENCE,
        provenance=artifact.source_refs,
        rejection_reasons=("partial backend output",),
    )
    with pytest.raises(ValidationError, match="retrieval_unavailable"):
        RetrievalResult(
            target=target,
            authorization=authorized_scope(target),
            rejected_candidates=(rejection,),
            knowledge_gap=gap,
        )


def test_index_audit_derives_rebuild_requirement_for_every_integrity_failure():
    audit = IndexAudit(
        artifact_count=4,
        fts_count=3,
        orphan_count=1,
        duplicate_id_count=1,
        corruption_count=1,
    )

    assert audit.rebuild_required
    assert audit.issues == (
        "canonical_corruption",
        "duplicate_artifact_ids",
        "fts_count_mismatch",
        "orphan_rows",
    )


def test_indexed_source_state_is_bounded_canonical_and_hash_bound() -> None:
    artifacts = (
        IndexedArtifactState(
            artifact_id="artifact-a",
            projection_digest="c" * 64,
            asserted_projection_digest="c" * 64,
        ),
        IndexedArtifactState(
            artifact_id="artifact-b",
            projection_digest="d" * 64,
            asserted_projection_digest="d" * 64,
        ),
    )
    state = IndexedSourceState.from_artifacts(
        source_id="source-a",
        source_sha256="a" * 64,
        projection_version="sqlite-projection-v1",
        artifacts=artifacts,
    )

    assert state.source_id == "source-a"
    with pytest.raises(ValidationError):
        IndexedSourceState.from_artifacts(
            source_id="source-a",
            source_sha256="not-a-hash",
            projection_version="sqlite-projection-v1",
            artifacts=artifacts,
        )
    with pytest.raises(ValidationError):
        IndexedSourceState.model_validate(state.model_dump() | {"projection_digest": "B" * 64})


def test_index_state_snapshot_is_generation_bound_sorted_and_cumulatively_bounded() -> None:
    state = IndexedSourceState.from_artifacts(
        source_id="source-a",
        source_sha256="a" * 64,
        projection_version="projection-v1",
        artifacts=(
            IndexedArtifactState(
                artifact_id="artifact-a",
                projection_digest="b" * 64,
                asserted_projection_digest="b" * 64,
            ),
        ),
    )
    snapshot = IndexStateSnapshot(
        generation=7,
        audit=IndexAudit(artifact_count=1, source_count=1, fts_count=1),
        source_states=(state,),
    )

    assert snapshot.generation == 7
    assert snapshot.source_states == (state,)
    with pytest.raises(ValidationError):
        IndexStateSnapshot(
            generation=7,
            audit=IndexAudit(artifact_count=2, source_count=1, fts_count=2),
            source_states=(state,),
        )


class _IndexDouble:
    def upsert_bundle(self, bundle: object) -> None:
        return None

    def delete_source(self, source_id: str) -> None:
        return None

    def rebuild(self, bundles: object, *, precommit_guard: object | None = None) -> IndexAudit:
        del bundles, precommit_guard
        return IndexAudit()

    def snapshot_state(self) -> IndexStateSnapshot:
        return IndexStateSnapshot(generation=0, audit=IndexAudit())

    def get_artifact(self, artifact_id: str) -> object | None:
        return None

    def list_source_states(
        self,
        *,
        after_source_id: str | None,
        limit: int,
    ) -> tuple[IndexedSourceState, ...]:
        return ()

    def search_candidates(
        self,
        query: RetrievalQuery,
        *,
        lane: EpistemicLane,
        limit: int,
    ) -> tuple[object, ...]:
        return ()

    def audit(self) -> IndexAudit:
        return IndexAudit()

    def close(self) -> None:
        return None

    def __enter__(self) -> _IndexDouble:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        return None
