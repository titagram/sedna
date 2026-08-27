import json, os, sys
sys.path.insert(0, '/home/titagram/sedna/src')
from sedna.knowledge.semantic.drafts import SemanticDraftBundle

def cite(i): return [{'segment_indexes':[i]}]
def step(i,before,after,obs,hyp,act,evidence,category,conditions,details=[]):
    return {'artifact_type':'case_step','local_id':f'fireflow-step-{i}','ordinal':i,
      'state_before':{'access':before,'environment':['Linux HTB target'],'privileges':[]},
      'state_after':{'access':after,'environment':['Linux HTB target'],'privileges':[]},
      'observations':obs,'hypotheses':[{'statement':x,'origin':'explicit'} for x in hyp],
      'selected_action':{'intent':act,'capability_ref':None},
      'evidence':[{'summary':x,'origin':'explicit','category':category} for x in evidence],
      'negative_evidence':[],'transfer_conditions':conditions,'case_specific_details':details,
      'origin':'explicit','citations':cite(i)}
steps=[
 step(1,'none / unauthenticated','unauthenticated access to flow.fireflow.htb',
  ['Nmap exposes SSH and Nginx.','TLS SAN exposes fireflow.htb and wildcard subdomains.','The Open Agent link leads to flow.fireflow.htb and a Langflow playground flow_id.'],
  ['A public Langflow flow_id is available for testing.'],'Enumerate web vhosts and identify the leaked flow_id.',
  ['A distinct Langflow vhost and flow_id were found.'],'observation',['Web enumeration reveals an application flow_id.']),
 step(2,'unauthenticated Langflow access','code execution as www-data',
  ['CVE-2026-33017 provides unauthenticated RCE through build_public_tmp with a valid flow_id.','The payload yields a www-data reverse shell.','Langflow .env exposes a reusable superuser password.'],
  ['The known flow_id is sufficient for unauthenticated Langflow RCE.'],'Exploit the public flow with a custom component payload.',
  ['The crafted build request returns a www-data shell.'],'exploitation',['Langflow is reachable and a public flow_id is known.'],['Code executes during component graph build; place attacker code in the component method.']),
 step(3,'www-data host shell','SSH as nightfall and RCE as mcp in a Kubernetes pod',
  ['The Langflow password is reused by nightfall for SSH.','nightfall home contains .mcp/config.json with custom MCP server access.','The MCP service supports JWT alg=none.','A forged admin JWT registers and invokes a malicious tool, yielding mcp pod RCE.'],
  ['Credential reuse enables the SSH pivot.','The advertised none algorithm permits admin impersonation.'],'Reuse the password over SSH, then abuse unsigned JWT to register an MCP shell tool.',
  ['SSH as nightfall succeeds.','MCP tool registration yields pod-level code execution.'],'lateral-movement',['A leaked config points to a service advertising insecure JWT algorithms.']),
 step(4,'mcp pod uid 1000','root on host filesystem',
  ['The pod service account has get on nodes/proxy.','Kubelet /pods reveals a privileged node-exporter pod mounting host /, /proc and /sys.','Direct websocket exec to kubelet :10250 uses output=1&error=1 and v4.channel.k8s.io.','The host root is mounted at /host/root.'],
  ['nodes/proxy permits direct kubelet exec without pods/exec when a privileged pod exists.'],'Use the kubelet websocket against the privileged node-exporter pod and read the host root flag.',
  ['The privileged pod provides command execution against the host filesystem.'],'privilege-escalation',['SA has nodes/proxy and a privileged host-root pod exists.'],['Use node IP :10250, not API service IP; strip the first channel byte from frames.'])]
case={'draft_type':'case','artifact_type':'case','knowledge_role':'case_study','local_id':'fireflow-case','origin':'explicit','title':'Fireflow: Langflow to host root via MCP JWT and Kubernetes nodes/proxy','starting_access':'none/unauthenticated','source_quality':'complete','difficulty':'medium','outcome':'User and root access obtained through Langflow, SSH, MCP and kubelet exec','transferable_properties':['Leaked flow IDs can expose Langflow RCE.','Credential reuse can provide SSH access.','JWT alg=none can enable admin impersonation.','nodes/proxy plus a privileged host-root pod enables kubelet exec.'],'non_transferable_properties':['Exact target credentials, flow ID, pod name and host address.'],'steps':steps,'citations':cite(1)}
ex={'local_id':'fireflow-ex-1','parent_local_id':'fireflow-step-4','command_template':'python3 kube_exec.py --node {{node}} --namespace {{namespace}} --pod {{pod}} --container {{container}} "cat /host/root/root/root.txt"','placeholders':[{'name':'node','kind':'target','binding_policy':'authorized_scope','role':'authorized node IP'},{'name':'namespace','kind':'value','binding_policy':'host_supplied','role':'pod namespace'},{'name':'pod','kind':'value','binding_policy':'host_supplied','role':'privileged pod'},{'name':'container','kind':'value','binding_policy':'host_supplied','role':'container'}],'capability_hint':'websocket exec to kubelet 10250','purpose':'Read host root through privileged pod mount','observed_role':'mcp pod user','prerequisites':[{'statement':'SA has nodes/proxy and privileged host-root pod exists.','citations':cite(6)}],'platform_constraints':[{'dimension':'execution_environment','relation':'required','value':'Kubernetes pod with service-account token','citations':cite(6)},{'dimension':'os_family','relation':'compatible','value':'linux','citations':cite(6)}],'citations':cite(6)}
bundle={'artifacts':[case],'execution_examples':[ex],'ignored_segment_indexes':[0,7]}
SemanticDraftBundle.model_validate(bundle)
os.makedirs('/home/titagram/sedna/scripts/agent-populate/bundles',exist_ok=True)
json.dump(bundle,open('/home/titagram/sedna/scripts/agent-populate/bundles/Fireflow.json','w'),indent=2)
print('VALID: YES, artifacts=1, steps=4, examples=1')