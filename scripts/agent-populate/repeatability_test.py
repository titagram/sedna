"""Repeatability test: I (agent) construct SemanticDraftBundle for multiple machines
from their real writeup content, validate, and materialize."""
import sys, json
sys.path.insert(0, '/home/titagram/sedna/src')
sys.path.insert(0, '/home/titagram/sedna/tests/knowledge')
from pathlib import Path

from test_semantic_llm import _prepared_from_markdown
from sedna.knowledge.semantic.drafts import SemanticDraftBundle
from sedna.knowledge.semantic.materialize import materialize_semantic_content, validate_segment_accounting
from sedna.knowledge.schema.common import VerificationStatus
from sedna.knowledge.schema.semantic import SemanticCallMetadata

SRC = '/tmp/sedna-smoke-src/write-ups/machines'


def facts_bundle():
    return {
        "artifacts": [{
            "draft_type": "case", "artifact_type": "case", "knowledge_role": "case_study",
            "local_id": "case-facts", "origin": "explicit",
            "title": "Camaleon CMS 2.9.0 compromise (Facts)",
            "starting_access": "unauthenticated web access to facts.htb",
            "source_quality": "complete", "difficulty": "medium",
            "outcome": "root shell via sudoable facter RCE",
            "transferable_properties": ["open registration gives foothold", "S3 on non-standard port"],
            "non_transferable_properties": [],
            "steps": [
                {"artifact_type": "case_step", "local_id": "step-1", "ordinal": 1,
                 "state_before": {"access": "unauthenticated web", "environment": ["nginx 1.26.3"], "privileges": []},
                 "observations": ["ports 22 and 80 open"], "hypotheses": [{"statement": "web is the foothold", "origin": "inferred"}],
                 "selected_action": {"intent": "enumerate the web app"},
                 "evidence": [{"summary": "blog runs Camaleon CMS 2.9.0", "origin": "explicit", "category": "recon"}],
                 "state_after": {"access": "unauthenticated web", "environment": ["Camaleon CMS"], "privileges": []},
                 "origin": "explicit", "citations": [{"segment_indexes": [0]}]},
                {"artifact_type": "case_step", "local_id": "step-2", "ordinal": 2,
                 "state_before": {"access": "unauthenticated web", "environment": ["Camaleon CMS"], "privileges": []},
                 "observations": ["open /admin/register"], "hypotheses": [{"statement": "escalate CMS user to admin", "origin": "inferred"}],
                 "selected_action": {"intent": "register and escalate with CVE-2025-2304"},
                 "evidence": [{"summary": "escalated aaa to admin", "origin": "explicit", "category": "exploitation"}],
                 "state_after": {"access": "admin", "environment": [], "privileges": ["admin"]},
                 "origin": "explicit", "citations": [{"segment_indexes": [1, 2]}]},
            ],
            "citations": [{"segment_indexes": [0, 1, 2]}],
        }],
        "execution_examples": [],
        "ignored_segment_indexes": [3, 4, 5, 6],
    }


def nibbles_bundle():
    return {
        "artifacts": [{
            "draft_type": "case", "artifact_type": "case", "knowledge_role": "case_study",
            "local_id": "case-nibbles", "origin": "explicit",
            "title": "NibbleBlog RCE and sudo monitor.sh privesc (Nibbles)",
            "starting_access": "unauthenticated web access to Apache port 80",
            "source_quality": "complete", "difficulty": "easy",
            "outcome": "root flag via crafted sudo monitor.sh",
            "transferable_properties": ["check page source for hidden dirs", "guess common admin passwords", "sudo NOPASSWD script with missing file"],
            "non_transferable_properties": [],
            "steps": [
                {"artifact_type": "case_step", "local_id": "step-1", "ordinal": 1,
                 "state_before": {"access": "unauthenticated web", "environment": ["Apache port 80"], "privileges": []},
                 "observations": ["page source reveals /nibbleblog directory", "gobuster finds admin.php"],
                 "hypotheses": [{"statement": "NibbleBlog 4.0.3 has a public RCE CVE", "origin": "inferred"}],
                 "selected_action": {"intent": "enumerate /nibbleblog and locate the admin panel"},
                 "evidence": [{"summary": "found /nibbleblog/admin.php login and users.xml with admin user", "origin": "explicit", "category": "recon"}],
                 "state_after": {"access": "unauthenticated web", "environment": ["NibbleBlog 4.0.3"], "privileges": []},
                 "origin": "explicit", "citations": [{"segment_indexes": [0]}]},
                {"artifact_type": "case_step", "local_id": "step-2", "ordinal": 2,
                 "state_before": {"access": "unauthenticated web", "environment": ["NibbleBlog 4.0.3"], "privileges": []},
                 "observations": ["username admin known, password guessed from common list"],
                 "hypotheses": [{"statement": "valid credentials enable the RCE exploit", "origin": "inferred"}],
                 "selected_action": {"intent": "guess admin password and run the NibbleBlog 4.0.3 RCE exploit"},
                 "evidence": [{"summary": "logged in as admin and got RCE as user nibbler via nibbleblog_4.0.3.py", "origin": "explicit", "category": "exploitation"}],
                 "state_after": {"access": "shell as nibbler", "environment": [], "privileges": ["nibbler"]},
                 "origin": "explicit", "citations": [{"segment_indexes": [0]}]},
                {"artifact_type": "case_step", "local_id": "step-3", "ordinal": 3,
                 "state_before": {"access": "shell as nibbler", "environment": [], "privileges": ["nibbler"]},
                 "observations": ["sudo -l allows NOPASSWD monitor.sh", "monitor.sh does not exist"],
                 "hypotheses": [{"statement": "a missing sudo script path can be created by us", "origin": "inferred"}],
                 "selected_action": {"intent": "create monitor.sh with a bash payload and execute it via sudo"},
                 "evidence": [{"summary": "created monitor.sh with /bin/bash and ran it via sudo for root", "origin": "explicit", "category": "privilege_escalation"}],
                 "state_after": {"access": "root shell", "environment": [], "privileges": ["root"]},
                 "origin": "explicit", "citations": [{"segment_indexes": [0]}]},
            ],
            "citations": [{"segment_indexes": [0]}],
        }],
        "execution_examples": [{
            "local_id": "ex-nibbles-1", "parent_local_id": "step-3",
            "command_template": "sudo /home/nibbler/personal/stuff/{{script}}",
            "placeholders": [{"name": "script", "kind": "path", "binding_policy": "host_supplied",
                              "role": "sudoable script path"}],
            "capability_hint": "execute arbitrary commands as root via a sudoable script",
            "purpose": "privilege escalation from nibbler to root",
            "observed_role": "attacker shell as nibbler",
            "prerequisites": [{"statement": "user can sudo the script without a password", "citations": [{"segment_indexes": [0]}]}],
            "platform_constraints": [{"dimension": "os_family", "relation": "required", "value": "linux", "citations": [{"segment_indexes": [0]}]}],
            "citations": [{"segment_indexes": [0]}],
        }],
        "ignored_segment_indexes": [],
    }


def devarea_bundle():
    return {
        "artifacts": [{
            "draft_type": "case", "artifact_type": "case", "knowledge_role": "case_study",
            "local_id": "case-devarea", "origin": "explicit",
            "title": "Apache CXF SSRF to Hoverfly RCE and world-writable bash privesc (DevArea)",
            "starting_access": "unauthenticated access to FTP and HTTP services",
            "source_quality": "complete", "difficulty": "medium",
            "outcome": "root flag via SUID rootbash from world-writable /usr/bin/bash",
            "transferable_properties": ["download JARs from anonymous FTP", "CXF XOP Include SSRF file read", "Hoverfly middleware RCE", "world-writable system binary + systemd timer"],
            "non_transferable_properties": [],
            "steps": [
                {"artifact_type": "case_step", "local_id": "step-1", "ordinal": 1,
                 "state_before": {"access": "unauthenticated", "environment": ["FTP, HTTP, Jetty 8080, Hoverfly 8888"], "privileges": []},
                 "observations": ["ports 21/22/80/8080/8500/8888 open", "FTP anonymous allowed", "CXF SOAP and Hoverfly"],
                 "hypotheses": [{"statement": "the FTP JAR and SOAP service hide the real attack surface", "origin": "inferred"}],
                 "selected_action": {"intent": "enumerate FTP, decompile the JAR, and map the SOAP service"},
                 "evidence": [{"summary": "downloaded employee-service.jar via anonymous FTP and decompiled to find Apache CXF 3.2.14", "origin": "explicit", "category": "recon"}],
                 "state_after": {"access": "unauthenticated", "environment": ["Apache CXF 3.2.14 SOAP"], "privileges": []},
                 "origin": "explicit", "citations": [{"segment_indexes": [0, 1]}]},
                {"artifact_type": "case_step", "local_id": "step-2", "ordinal": 2,
                 "state_before": {"access": "unauthenticated", "environment": ["Apache CXF 3.2.14 SOAP"], "privileges": []},
                 "observations": ["CXF 3.2.14 vulnerable to CVE-2022-46364 XOP SSRF", "file:// URIs resolved server-side"],
                 "hypotheses": [{"statement": "XOP Include SSRF can read arbitrary files including service credentials", "origin": "inferred"}],
                 "selected_action": {"intent": "use the XOP SSRF to read hoverfly.service"},
                 "evidence": [{"summary": "read hoverfly.service via CVE-2022-46364 and extracted admin credentials and dev_ryan service user", "origin": "explicit", "category": "exploitation"}],
                 "state_after": {"access": "unauthenticated", "environment": [], "privileges": []},
                 "origin": "explicit", "citations": [{"segment_indexes": [2]}]},
                {"artifact_type": "case_step", "local_id": "step-3", "ordinal": 3,
                 "state_before": {"access": "unauthenticated", "environment": ["Hoverfly"], "privileges": []},
                 "observations": ["Hoverfly middleware RCE CVE-2025-54123", "authenticated via admin JWT"],
                 "hypotheses": [{"statement": "middleware script injection yields a shell as dev_ryan", "origin": "inferred"}],
                 "selected_action": {"intent": "inject a bash reverse shell as Hoverfly middleware and trigger via proxy"},
                 "evidence": [{"summary": "PUT middleware with a reverse shell and triggered it via the proxy to get a shell as dev_ryan", "origin": "explicit", "category": "exploitation"}],
                 "state_after": {"access": "shell as dev_ryan", "environment": [], "privileges": ["dev_ryan"]},
                 "origin": "explicit", "citations": [{"segment_indexes": [2]}]},
                {"artifact_type": "case_step", "local_id": "step-4", "ordinal": 4,
                 "state_before": {"access": "shell as dev_ryan", "environment": [], "privileges": ["dev_ryan"]},
                 "observations": ["sudo -l allows NOPASSWD syswatch.sh", "/usr/bin/bash is 777", "syswatch-monitor.timer runs as root"],
                 "hypotheses": [{"statement": "overwriting world-writable bash lets a root timer execute our payload", "origin": "inferred"}],
                 "selected_action": {"intent": "get a non-bash shell, kill bash, and overwrite /usr/bin/bash with a SUID payload"},
                 "evidence": [{"summary": "spawned a python/sh shell, killed bash, wrote a SUID payload to /usr/bin/bash, and waited for the root timer to create rootbash", "origin": "explicit", "category": "privilege_escalation"}],
                 "state_after": {"access": "root shell", "environment": [], "privileges": ["root"]},
                 "origin": "explicit", "citations": [{"segment_indexes": [3, 4]}]},
            ],
            "citations": [{"segment_indexes": [0, 1, 2, 3, 4]}],
        }],
        "execution_examples": [{
            "local_id": "ex-devarea-1", "parent_local_id": "step-2",
            "command_template": "curl -s -X POST 'http://devarea.htb:8080/employeeservice' -H 'Content-Type: multipart/related; boundary={{boundary}}' --data-binary @{{payload}}",
            "placeholders": [
                {"name": "boundary", "kind": "value", "binding_policy": "host_supplied", "role": "MIME boundary string"},
                {"name": "payload", "kind": "path", "binding_policy": "host_supplied", "role": "multipart XOP SOAP payload file"},
            ],
            "capability_hint": "SSRF arbitrary file read via CXF XOP Include",
            "purpose": "read arbitrary server files through the SOAP service",
            "observed_role": "attacker host",
            "prerequisites": [{"statement": "SOAP endpoint is reachable and CXF parses multipart XOP Include", "citations": [{"segment_indexes": [2]}]}],
            "platform_constraints": [{"dimension": "os_family", "relation": "required", "value": "linux", "citations": [{"segment_indexes": [2]}]}],
            "citations": [{"segment_indexes": [2]}],
        }],
        "ignored_segment_indexes": [5, 6, 7],
    }


BUILDERS = {"Facts": facts_bundle, "DevArea": devarea_bundle, "Nibbles": nibbles_bundle}

results = {}
for name, builder in BUILDERS.items():
    try:
        md = open(f'{SRC}/{name}/{name}.md').read()
        prepared = _prepared_from_markdown(md, title=f"Machine: {name}")
        bundle_dict = builder()
        bundle = SemanticDraftBundle.model_validate(bundle_dict)
        validate_segment_accounting(prepared, bundle)
        call_meta = SemanticCallMetadata(purpose="sedna.semantic.extract", provider="agent",
                                         model="deepseek-v4-flash", agent_id="agent-main",
                                         input_tokens=0, output_tokens=0)
        content = materialize_semantic_content(prepared, bundle, call_meta, VerificationStatus.VERIFIED)
        results[name] = {"status": "OK", "nsegments": len(prepared.segments),
                         "artifacts": len(content.artifacts),
                         "steps": sum(len(a.steps) for a in content.artifacts if hasattr(a, "steps")),
                         "examples": len(content.execution_examples)}
    except Exception as e:
        results[name] = {"status": "FAIL", "nsegments": "?",
                         "error": f"{type(e).__name__}: {str(e)[:200]}"}

print("=== REPEATABILITY RESULTS (agent-built bundles) ===")
for name, r in results.items():
    print(name, "->", r)
