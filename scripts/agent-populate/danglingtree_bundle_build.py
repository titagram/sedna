"""Build + validate SemanticDraftBundle for DanglingTree from the writeup segments."""
import sys, json
sys.path.insert(0, '/home/titagram/sedna/src')
sys.path.insert(0, '/home/titagram/sedna/tests/knowledge')
from test_semantic_llm import _prepared_from_markdown
from sedna.knowledge.semantic.drafts import SemanticDraftBundle
from sedna.knowledge.semantic.materialize import validate_segment_accounting

md = open('/home/titagram/htb-writeups/write-ups/machines/DanglingTree/DanglingTree.md').read()
prepared = _prepared_from_markdown(md, title='Machine: DanglingTree')
print('segments:', len(prepared.segments))

bundle = {
    "artifacts": [{
        "draft_type": "case", "artifact_type": "case", "knowledge_role": "case_study",
        "local_id": "case-danglingtree", "origin": "explicit",
        "title": "ADCS ESC4 custom template to SYSTEM (DanglingTree)",
        "starting_access": "anonymous SMB disclosure on a Windows Server 2025 domain controller",
        "source_quality": "complete", "difficulty": "medium",
        "outcome": "SYSTEM shell and both flags via ADCS ESC4 admin certificate",
        "transferable_properties": [
            "anonymous SMB share can leak low-priv credentials",
            "Windows Admin Center on a non-standard port is a PowerShell RCE foothold",
            "an internal SmarterMail instance needs a chisel pivot",
            "an account that owns a certificate template has WRITE_DACL and can mint an admin cert",
            "writing nTSecurityDescriptor over LDAP requires the SD-flags control",
        ],
        "non_transferable_properties": [],
        "steps": [
            {"artifact_type": "case_step", "local_id": "step-1", "ordinal": 1,
             "state_before": {"access": "anonymous network access", "environment": ["Windows Server 2025 DC", "danglingtree.htb domain"], "privileges": []},
             "observations": ["ports 88,389,445,3389,6600 open", "port 6600 is Windows Admin Center", "anonymous IT share holds a RoE PDF with anderson.w creds"],
             "hypotheses": [{"statement": "WAC is the intended foothold", "origin": "inferred"}],
             "selected_action": {"intent": "enumerate ADCS, SMB and the WAC endpoint"},
             "evidence": [{"summary": "anonymous SMB IT share leaks a RoE PDF with anderson.w credentials", "origin": "explicit", "category": "recon"}],
             "state_after": {"access": "low-priv domain user", "environment": [], "privileges": ["anderson.w"]},
             "origin": "explicit", "citations": [{"segment_indexes": [1]}]},
            {"artifact_type": "case_step", "local_id": "step-2", "ordinal": 2,
             "state_before": {"access": "low-priv domain user", "environment": ["WAC on 6600"], "privileges": ["anderson.w"]},
             "observations": ["WAC exposes /api/WinREST/PowerShell/nodes/<node>/invokeCommand with a script param"],
             "hypotheses": [{"statement": "invokeCommand gives PowerShell RCE as anderson", "origin": "inferred"}],
             "selected_action": {"intent": "invoke a PowerShell command via the WAC endpoint"},
             "evidence": [{"summary": "obtained PowerShell code execution as anderson.w through WAC", "origin": "explicit", "category": "exploitation"}],
             "state_after": {"access": "PowerShell RCE as anderson.w", "environment": [], "privileges": ["anderson.w"]},
             "origin": "explicit", "citations": [{"segment_indexes": [2]}]},
            {"artifact_type": "case_step", "local_id": "step-3", "ordinal": 3,
             "state_before": {"access": "PowerShell RCE as anderson.w", "environment": [], "privileges": ["anderson.w"]},
             "observations": ["internal service on 127.0.0.1:17017 is SmarterMail", "SmarterMail has an unauthenticated hub endpoint and an auth bypass CVE-2026-24423"],
             "hypotheses": [{"statement": "pivot to SmarterMail yields svc_mail", "origin": "inferred"}],
             "selected_action": {"intent": "chisel-pivot to SmarterMail and exploit the auth bypass"},
             "evidence": [{"summary": "chisel R:17017:127.0.0.1:17017 reached internal SmarterMail and yielded svc_mail", "origin": "explicit", "category": "exploitation"}],
             "state_after": {"access": "svc_mail account", "environment": [], "privileges": ["svc_mail"]},
             "origin": "explicit", "citations": [{"segment_indexes": [3]}]},
            {"artifact_type": "case_step", "local_id": "step-4", "ordinal": 4,
             "state_before": {"access": "svc_mail account", "environment": [], "privileges": ["svc_mail"]},
             "observations": ["svc_mail can read DPAPI artifacts of other users"],
             "hypotheses": [{"statement": "decrypting DPAPI yields another user's credential", "origin": "inferred"}],
             "selected_action": {"intent": "extract and decrypt DPAPI artifacts to recover alex.o"},
             "evidence": [{"summary": "DPAPI blob decryption recovered alex.o credentials", "origin": "explicit", "category": "privilege-escalation"}],
             "state_after": {"access": "alex.o account", "environment": [], "privileges": ["alex.o", "support-it"]},
             "origin": "explicit", "citations": [{"segment_indexes": [4]}]},
            {"artifact_type": "case_step", "local_id": "step-5", "ordinal": 5,
             "state_before": {"access": "alex.o account", "environment": [], "privileges": ["alex.o", "support-it"]},
             "observations": ["alex.o has ForceChangePassword over jake.h"],
             "hypotheses": [{"statement": "reset jake.h password to gain its rights", "origin": "inferred"}],
             "selected_action": {"intent": "reset jake.h password via ForceChangePassword ACL"},
             "evidence": [{"summary": "reset jake.h password as alex.o, jake is in Helpdesk_Cert_Support/Template_Editors/DevOps_PKI", "origin": "explicit", "category": "privilege-escalation"}],
             "state_after": {"access": "jake.h account", "environment": [], "privileges": ["jake.h", "ManageCertificates on CA"]},
             "origin": "explicit", "citations": [{"segment_indexes": [5]}]},
            {"artifact_type": "case_step", "local_id": "step-6", "ordinal": 6,
             "state_before": {"access": "jake.h account", "environment": ["danglingtree-DC-CA ADCS"], "privileges": ["jake.h", "ManageCertificates"]},
             "observations": ["EmployeeAuthTemplate is owned by jake.h (ESC4)", "writing nTSecurityDescriptor needs the SD_FLAGS control"],
             "hypotheses": [{"statement": "as owner, jake can grant Authenticated Users FullControl and mint an admin cert", "origin": "inferred"}],
             "selected_action": {"intent": "use create_authenticated_users_sd + security_descriptor_control to complete the template"},
             "evidence": [{"summary": "wrote DACL granting Authenticated Users GENERIC_ALL, then completed the template for SAN impersonation", "origin": "explicit", "category": "exploitation"}],
             "state_after": {"access": "FullControl on EmployeeAuthTemplate", "environment": [], "privileges": ["jake.h", "certificate template owner"]},
             "origin": "explicit", "citations": [{"segment_indexes": [6]}]},
            {"artifact_type": "case_step", "local_id": "step-7", "ordinal": 7,
             "state_before": {"access": "FullControl on certificate template", "environment": [], "privileges": ["jake.h"]},
             "observations": ["request a cert impersonating Administrator", "authenticate with it to get NT hash"],
             "hypotheses": [{"statement": "admin cert leads to NT hash and SYSTEM", "origin": "inferred"}],
             "selected_action": {"intent": "request admin cert, extract NT hash, Pass-the-Hash to SYSTEM"},
             "evidence": [{"summary": "certipy req -upn administrator -sid -500 minted admin.pfx; auth yielded Administrator NT hash; psexec/smbexec gave SYSTEM", "origin": "explicit", "category": "privilege-escalation"}],
             "state_after": {"access": "nt authority\\system", "environment": [], "privileges": ["SYSTEM", "domain admin"]},
             "origin": "explicit", "citations": [{"segment_indexes": [7]}]},
        ],
        "citations": [{"segment_indexes": [1, 2, 3, 4, 5, 6, 7]}],
    }],
    "execution_examples": [{
        "local_id": "ex-dt-1",
        "parent_local_id": "step-6",
        "command_template": "certipy-ad req -u {{user}} -p {{password}} -ca danglingtree-DC-CA -target {{dc}} -template EmployeeAuthTemplate -upn administrator@{{domain}} -sid S-1-5-21-{{dom}}-500 -out admin.pfx",
        "placeholders": [
            {"name": "user", "kind": "value", "binding_policy": "host_supplied", "role": "domain user owning the template"},
            {"name": "password", "kind": "value", "binding_policy": "host_supplied", "role": "password of the domain user"},
            {"name": "dc", "kind": "target", "binding_policy": "authorized_scope", "role": "HTB domain controller"},
            {"name": "domain", "kind": "value", "binding_policy": "host_supplied", "role": "AD domain name"},
            {"name": "dom", "kind": "value", "binding_policy": "host_supplied", "role": "domain SID RID prefix for administrator"}
        ],
        "capability_hint": "request an ADCS certificate impersonating the domain Administrator (ESC1/ESC4 style)",
        "purpose": "obtain a certificate that authenticates as the domain Administrator",
        "observed_role": "attacker with an account owning the certificate template",
        "prerequisites": [{"statement": "the account owns/controls a certificate template that allows user-supplied SAN", "citations": [{"segment_indexes": [6]}]}],
        "platform_constraints": [{"dimension": "os_family", "relation": "required", "value": "windows", "citations": [{"segment_indexes": [1]}]}],
        "citations": [{"segment_indexes": [6]}]
    }],
    "ignored_segment_indexes": [0, 8],
}

# Validate
b = SemanticDraftBundle.model_validate(bundle)
print("Pydantic OK. artifacts:", len(b.artifacts), "exec:", len(b.execution_examples))
validate_segment_accounting(prepared, b)
print("Segment accounting OK")

# Export the validated bundle as JSON for the harness
import pathlib
out = pathlib.Path('/tmp/bundles/DanglingTree.json')
out.write_text(b.model_dump_json(indent=2))
print("wrote", out)
