"""Build + validate SemanticDraftBundle for Cohort from the writeup segments."""
import sys, json, pathlib
sys.path.insert(0, '/home/titagram/sedna/src')
sys.path.insert(0, '/home/titagram/sedna/tests/knowledge')
from test_semantic_llm import _prepared_from_markdown
from sedna.knowledge.semantic.drafts import SemanticDraftBundle
from sedna.knowledge.semantic.materialize import validate_segment_accounting

md = open('/home/titagram/htb-writeups/write-ups/machines/Cohort/Cohort.md').read()
prepared = _prepared_from_markdown(md, title='Machine: Cohort')
print('segments:', len(prepared.segments))

bundle = {
    "artifacts": [{
        "draft_type": "case", "artifact_type": "case", "knowledge_role": "case_study",
        "local_id": "case-cohort", "origin": "explicit",
        "title": "SSRF -> hidden vhost -> marimo pre-auth RCE -> PackageKit TOCTOU (Cohort)",
        "starting_access": "unauthenticated web access to a Cohort Analytics portal",
        "source_quality": "complete", "difficulty": "easy",
        "outcome": "root shell via SSRF to marimo pre-auth RCE and a PackageKit SUID-bash race",
        "transferable_properties": [
            "an SSRF in a report-source URL validator can disclose hidden nginx virtual hosts",
            "alternate loopback representations (127.1, 0.0.0.0, 2130706433, 0x7f000001) bypass string filters",
            "marimo <=0.20.4 exposes an unauthenticated WebSocket /terminal/ws RCE (CVE-2026-39987)",
            "PackageKit TOCTOU race (CVE-2026-41651) lets an unprivileged user install a SUID bash",
        ],
        "non_transferable_properties": [],
        "steps": [
            {"artifact_type": "case_step", "local_id": "step-1", "ordinal": 1,
             "state_before": {"access": "unauthenticated network access", "environment": ["Linux", "cohort.htb"], "privileges": []},
             "observations": ["22/tcp ssh, 80/tcp http, 443/tcp https", "TLS cert has SAN *.cohort.htb hinting at hidden vhosts"],
             "hypotheses": [{"statement": "hidden virtual hosts exist behind the portal", "origin": "inferred"}],
             "selected_action": {"intent": "enumerate the web portal and TLS certs for hidden vhosts"},
             "evidence": [{"summary": "nmap found 22/80/443; TLS cert SAN *.cohort.htb suggests more vhosts", "origin": "explicit", "category": "recon"}],
             "state_after": {"access": "web access", "environment": [], "privileges": []},
             "origin": "explicit", "citations": [{"segment_indexes": [3]}]},
            {"artifact_type": "case_step", "local_id": "step-2", "ordinal": 2,
             "state_before": {"access": "web access", "environment": ["cohort.htb SPA", "portal.html"], "privileges": []},
             "observations": ["portal.html exposes a 'register and validate a report source URL' form"],
             "hypotheses": [{"statement": "the URL validator is server-side and injectable (SSRF)", "origin": "inferred"}],
             "selected_action": {"intent": "test the /api/validate URL validator for SSRF"},
             "evidence": [{"summary": "portal.html has a source-URL validator form", "origin": "explicit", "category": "enumeration"}],
             "state_after": {"access": "web access", "environment": [], "privileges": []},
             "origin": "explicit", "citations": [{"segment_indexes": [4]}]},
            {"artifact_type": "case_step", "local_id": "step-3", "ordinal": 3,
             "state_before": {"access": "web access", "environment": ["/api/validate"], "privileges": []},
             "observations": ["127.0.0.1 and localhost are blocked, but alternate forms bypass the filter", "SSRF echoes a preview of the fetched URL"],
             "hypotheses": [{"statement": "alternate loopback representations bypass the SSRF filter", "origin": "explicit"}],
             "selected_action": {"intent": "use alternate loopback forms to probe internal services"},
             "evidence": [{"summary": "127.1 / 0.0.0.0 / 2130706433 / 0x7f000001 bypassed the string filter in /api/validate", "origin": "explicit", "category": "exploitation"}],
             "state_after": {"access": "SSRF into loopback", "environment": [], "privileges": []},
             "origin": "explicit", "citations": [{"segment_indexes": [5]}]},
            {"artifact_type": "case_step", "local_id": "step-4", "ordinal": 4,
             "state_before": {"access": "SSRF into loopback", "environment": [], "privileges": []},
             "observations": ["internal services on 5000 (cohort-insights API) and 8888 (marimo)", "/status endpoint discloses nginx upstream routing and a hidden vhost nb-1be3782a8afd3ad5.cohort.htb"],
             "hypotheses": [{"statement": "the hidden vhost proxies to marimo on 127.0.0.1:8888", "origin": "explicit"}],
             "selected_action": {"intent": "use the hidden vhost to reach the internal marimo notebook"},
             "evidence": [{"summary": "/status disclosed the hidden vhost -> 127.0.0.1:8888 (marimo notebook)", "origin": "explicit", "category": "enumeration"}],
             "state_after": {"access": "knowledge of hidden vhost to marimo", "environment": [], "privileges": []},
             "origin": "explicit", "citations": [{"segment_indexes": [6]}]},
            {"artifact_type": "case_step", "local_id": "step-5", "ordinal": 5,
             "state_before": {"access": "access to hidden vhost", "environment": ["marimo 0.20.4 on 127.0.0.1:8888"], "privileges": []},
             "observations": ["marimo /terminal/ws spawns an OS pseudoterminal", "validate_auth() is missing on this route -> unauthenticated shell"],
             "hypotheses": [{"statement": "the missing auth check on /terminal/ws gives an unauthenticated RCE", "origin": "explicit"}],
             "selected_action": {"intent": "open the WebSocket with SNI=hidden vhost to get an interactive shell"},
             "evidence": [{"summary": "raw WebSocket handshake to /terminal/ws yielded uid=1000(marimo) shell", "origin": "explicit", "category": "exploitation"}],
             "state_after": {"access": "shell as marimo", "environment": [], "privileges": ["marimo"]},
             "origin": "explicit", "citations": [{"segment_indexes": [7]}]},
            {"artifact_type": "case_step", "local_id": "step-6", "ordinal": 6,
             "state_before": {"access": "shell as marimo", "environment": [], "privileges": ["marimo"]},
             "observations": ["user flag readable at /home/marimo/user.txt"],
             "hypotheses": [{"statement": "read the user flag", "origin": "explicit"}],
             "selected_action": {"intent": "read /home/marimo/user.txt"},
             "evidence": [{"summary": "obtained user flag at /home/marimo/user.txt", "origin": "explicit", "category": "post-exploitation"}],
             "state_after": {"access": "user flag captured", "environment": [], "privileges": ["marimo"]},
             "origin": "explicit", "citations": [{"segment_indexes": [8]}]},
            {"artifact_type": "case_step", "local_id": "step-7", "ordinal": 7,
             "state_before": {"access": "shell as marimo", "environment": ["PackageKit 1.2.8-2ubuntu1.2"], "privileges": ["marimo"]},
             "observations": ["PackageKit is vulnerable to a TOCTOU race (CVE-2026-41651)", "three bugs chain: unconditional flag overwrite, silent state-transition rejection, late flag read", "SIMULATE flag bypasses polkit; the race lets an unprivileged user install an arbitrary .deb as root"],
             "hypotheses": [{"statement": "PackageKit TOCTOU can install a SUID bash", "origin": "explicit"}],
             "selected_action": {"intent": "run the CVE-2026-41651 PoC to install a SUID bash"},
             "evidence": [{"summary": "ran PoC which set SUID on /bin/bash, euid=0(root)", "origin": "explicit", "category": "privilege-escalation"}],
             "state_after": {"access": "euid 0 via SUID bash", "environment": [], "privileges": ["root (SUID)"]},
             "origin": "explicit", "citations": [{"segment_indexes": [9]}]},
            {"artifact_type": "case_step", "local_id": "step-8", "ordinal": 8,
             "state_before": {"access": "euid 0 via SUID bash", "environment": [], "privileges": ["root"]},
             "observations": ["the -p flag preserves the effective UID from the setuid bit"],
             "hypotheses": [{"statement": "run the SUID bash with -p to read the root flag", "origin": "explicit"}],
             "selected_action": {"intent": "run /tmp/.suid_bash -p to cat the root flag"},
             "evidence": [{"summary": "root flag read via /tmp/.suid_bash -p -c", "origin": "explicit", "category": "post-exploitation"}],
             "state_after": {"access": "root flag captured", "environment": [], "privileges": ["root"]},
             "origin": "explicit", "citations": [{"segment_indexes": [10]}]},
        ],
        "citations": [{"segment_indexes": [3, 4, 5, 6, 7, 8, 9, 10]}],
    }],
    "execution_examples": [{
        "local_id": "ex-cohort-1",
        "parent_local_id": "step-5",
        "command_template": "curl -sk -X POST https://{{vhost}}/api/validate -H 'Content-Type: application/json' -d '{\"url\":\"http://{{loopback}}/\",\"format\":\"csv\"}'",
        "placeholders": [
            {"name": "vhost", "kind": "target", "binding_policy": "authorized_scope", "role": "HTB target hostname"},
            {"name": "loopback", "kind": "value", "binding_policy": "host_supplied", "role": "alternate loopback representation bypassing the SSRF filter, e.g. 127.1"}
        ],
        "capability_hint": "exercise an SSRF in a website URL validator to reach internal services",
        "purpose": "verify the SSRF and enumerate internal services via alternate loopback representations",
        "observed_role": "attacker with web access to a vulnerable URL validator",
        "prerequisites": [{"statement": "the site has a server-side URL validator vulnerable to SSRF", "citations": [{"segment_indexes": [5]}]}],
        "platform_constraints": [{"dimension": "os_family", "relation": "required", "value": "linux", "citations": [{"segment_indexes": [1]}]}],
        "citations": [{"segment_indexes": [5]}]
    }],
    "ignored_segment_indexes": [0, 1, 2, 11],
}

b = SemanticDraftBundle.model_validate(bundle)
print("Pydantic OK. artifacts:", len(b.artifacts), "exec:", len(b.execution_examples))
validate_segment_accounting(prepared, b)
print("Segment accounting OK")

out = pathlib.Path('/tmp/bundles/Cohort.json')
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(b.model_dump_json(indent=2))
print("wrote", out)
