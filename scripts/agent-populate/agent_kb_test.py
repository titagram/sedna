"""Agent-authored KB population test: I (the agent) construct the SemanticDraftBundle
for Facts manually, validate it, and materialize it into a fresh KB via Sedna's
own compiler/materializer. This bypasses the flaky host-LLM extraction and tests
whether an agent can populate the KB directly."""
import sys, json, shutil
sys.path.insert(0, '/home/titagram/sedna/src')
sys.path.insert(0, '/home/titagram/sedna/tests/knowledge')
from pathlib import Path

from test_semantic_llm import _prepared_from_markdown
from sedna.knowledge.semantic.drafts import (
    SemanticDraftBundle, DraftCitation, DraftContextAssertion,
    DraftTypedContext, DraftApplicabilityContext,
)
from sedna.knowledge.schema.common import Origin
from sedna.knowledge.schema.context import ContextRelation

md = open('/tmp/sedna-testsrc/write-ups/machines/Facts/Facts.md').read()
prepared = _prepared_from_markdown(md, title="Machine: Facts")
print("n_segments:", len(prepared.segments))

# Build the bundle as a plain dict exactly matching the draft schema.
bundle_dict = {
    "artifacts": [
        {
            "draft_type": "case",
            "artifact_type": "case",
            "knowledge_role": "case_study",
            "local_id": "case-facts",
            "origin": "explicit",
            "title": "Camaleon CMS 2.9.0 compromise (Facts)",
            "starting_access": "unauthenticated web access to facts.htb",
            "source_quality": "complete",
            "difficulty": "medium",
            "outcome": "root shell obtained via sudoable facter RCE",
            "transferable_properties": ["open registration gives foothold", "S3 on non-standard port", "sudoable facter GTFOBins"],
            "non_transferable_properties": [],
            "steps": [
                {
                    "artifact_type": "case_step",
                    "local_id": "step-1",
                    "ordinal": 1,
                    "state_before": {"environment": ["nginx 1.26.3", "facts.htb"], "privileges": []},
                    "observations": ["ports 22 (SSH) and 80 (HTTP nginx) open"],
                    "hypotheses": [{"statement": "web is the initial foothold surface", "origin": "inferred"}],
                    "selected_action": {"intent": "enumerate the web application and try open registration"},
                    "evidence": [{"summary": "port 80 redirects to facts.htb, blog runs Camaleon CMS 2.9.0", "origin": "explicit", "category": "recon"}],
                    "state_after": {"environment": ["Camaleon CMS 2.9.0"], "privileges": []},
                    "origin": "explicit",
                    "citations": [{"segment_indexes": [0]}],
                },
                {
                    "artifact_type": "case_step",
                    "local_id": "step-2",
                    "ordinal": 2,
                    "state_before": {"environment": ["Camaleon CMS 2.9.0"], "privileges": []},
                    "observations": ["/admin/register enables open account creation"],
                    "hypotheses": [{"statement": "a low-priv CMS user can be escalated to admin", "origin": "inferred"}],
                    "selected_action": {"intent": "register an account and exploit CVE-2025-2304 to escalate to admin"},
                    "evidence": [{"summary": "registered aaa:aaa and escalated to admin with CVE-2025-2304", "origin": "explicit", "category": "exploitation"}],
                    "state_after": {"environment": [], "privileges": ["admin"]},
                    "origin": "explicit",
                    "citations": [{"segment_indexes": [1, 2]}],
                },
                {
                    "artifact_type": "case_step",
                    "local_id": "step-3",
                    "ordinal": 3,
                    "state_before": {"environment": [], "privileges": ["admin"]},
                    "observations": ["LFI CVE-2024-46987 reads arbitrary files", "S3/MinIO on port 54321"],
                    "hypotheses": [{"statement": "LFI and S3 leaks will yield credentials and SSH keys", "origin": "inferred"}],
                    "selected_action": {"intent": "read /etc/passwd and enumerate the S3 buckets for secrets"},
                    "evidence": [{"summary": "read user.txt via LFI and found id_ed25519 in internal S3 bucket", "origin": "explicit", "category": "exploitation"}],
                    "state_after": {"environment": [], "privileges": ["admin"]},
                    "origin": "explicit",
                    "citations": [{"segment_indexes": [3, 4]}],
                },
                {
                    "artifact_type": "case_step",
                    "local_id": "step-4",
                    "ordinal": 4,
                    "state_before": {"environment": [], "privileges": ["admin"]},
                    "observations": ["SSH private key obtained and cracked with john"],
                    "hypotheses": [{"statement": "the SSH key belongs to a user and gives a shell", "origin": "inferred"}],
                    "selected_action": {"intent": "crack the SSH key passphrase and log in as the matching user"},
                    "evidence": [{"summary": "cracked id_ed25519 and SSHed in as trivia", "origin": "explicit", "category": "lateral"}],
                    "state_after": {"environment": [], "privileges": ["trivia"]},
                    "origin": "explicit",
                    "citations": [{"segment_indexes": [5]}],
                },
                {
                    "artifact_type": "case_step",
                    "local_id": "step-5",
                    "ordinal": 5,
                    "state_before": {"environment": [], "privileges": ["trivia"]},
                    "observations": ["sudo -l shows /usr/bin/facter NOPASSWD"],
                    "hypotheses": [{"statement": "facter --custom-dir executes arbitrary Ruby as root", "origin": "inferred"}],
                    "selected_action": {"intent": "use GTFOBins facter custom-dir to get a root shell"},
                    "evidence": [{"summary": "ran sudo facter --custom-dir /tmp with a pwn.rb payload for root", "origin": "explicit", "category": "privilege_escalation"}],
                    "state_after": {"environment": [], "privileges": ["root"]},
                    "origin": "explicit",
                    "citations": [{"segment_indexes": [6]}],
                },
            ],
            "citations": [{"segment_indexes": [0, 1, 2, 3, 4, 5, 6]}],
        }
    ],
    "execution_examples": [
        {
            "local_id": "ex-1",
            "parent_local_id": "step-5",
            "command_template": "sudo /usr/bin/facter --custom-dir /tmp/{{fact_dir}}",
            "placeholders": [
                {"name": "fact_dir", "kind": "path", "binding_policy": "host_supplied", "role": "directory containing the malicious facter fact"},
            ],
            "capability_hint": "execute arbitrary Ruby as root via sudoable facter",
            "purpose": "privilege escalation from an unprivileged user to root",
            "observed_role": "attacker shell",
            "prerequisites": [{"statement": "user can sudo /usr/bin/facter without a password", "citations": [{"segment_indexes": [6]}]}],
            "platform_constraints": [{"dimension": "os_family", "relation": "required", "value": "linux", "citations": [{"segment_indexes": [6]}]}],
            "citations": [{"segment_indexes": [6]}],
        }
    ],
    "ignored_segment_indexes": [],
}

# Ensure every case step state carries the required `access` field (with a
# plausible progression for this specific Facts case).
_ACCESS = {
    1: ("unauthenticated web", "unauthenticated web"),
    2: ("unauthenticated web", "authenticated CMS user"),
    3: ("authenticated CMS user", "admin"),
    4: ("admin", "admin"),
    5: ("admin", "trivia shell"),
}
def _inject_access(steps):
    for step in steps:
        ordinal = step.get("ordinal")
        before, after = _ACCESS.get(ordinal, ("unknown", "unknown"))
        step.setdefault("state_before", {})["access"] = before
        step.setdefault("state_after", {})["access"] = after
    return steps

bundle_dict["artifacts"][0]["steps"] = _inject_access(bundle_dict["artifacts"][0]["steps"])

# Validate against the real schema.
try:
    bundle = SemanticDraftBundle.model_validate(bundle_dict)
    print("BUNDLE_VALID: YES")
    print("  artifacts:", len(bundle.artifacts), "steps:", sum(len(a.steps) for a in bundle.artifacts if hasattr(a, "steps")), "examples:", len(bundle.execution_examples))
except Exception as e:
    print("BUNDLE_VALID: NO")
    print(str(e)[:2000])
    sys.exit(1)

# Materialize into a fresh KB.
from sedna.knowledge.semantic.materialize import materialize_semantic_content, validate_segment_accounting
from sedna.knowledge.schema.common import VerificationStatus
from sedna.knowledge.schema.semantic import SemanticCallMetadata

validate_segment_accounting(prepared, bundle)
print("SEGMENT_ACCOUNTING: OK")
call_meta = SemanticCallMetadata(
    purpose="sedna.semantic.extract", provider="agent", model="deepseek-v4-flash",
    agent_id="agent-main", input_tokens=0, output_tokens=0,
)
try:
    content = materialize_semantic_content(prepared, bundle, call_meta, VerificationStatus.VERIFIED)
    print("MATERIALIZED: YES artifacts:", len(content.artifacts), "examples:", len(content.execution_examples))
    for a in content.artifacts:
        print("   artifact:", type(a).__name__)
        if hasattr(a, "title"):
            print("     title:", getattr(a, "title", None))
        if hasattr(a, "steps"):
            print("     steps:", len(a.steps))
    for ex in content.execution_examples:
        print("   example:", ex.example_id, "|", ex.purpose[:60])
        print("     command_template:", ex.command_template)
        print("     platform_constraints:", [(c.dimension, c.relation, c.value) for c in ex.platform_constraints])
except Exception as e:
    print("MATERIALIZED: NO")
    print(str(e)[:1500])
