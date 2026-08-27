"""Build and validate the completed Nimbus SemanticDraftBundle."""
import sys, pathlib
sys.path.insert(0, '/home/titagram/sedna/src')
sys.path.insert(0, '/home/titagram/sedna/tests/knowledge')
from test_semantic_llm import _prepared_from_markdown
from sedna.knowledge.semantic.drafts import SemanticDraftBundle
from sedna.knowledge.semantic.materialize import validate_segment_accounting

md=open('/home/titagram/htb-writeups/write-ups/machines/Nimbus/Nimbus.md').read()
prepared=_prepared_from_markdown(md,title='Machine: Nimbus')
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
 step('step-1',1,'unauthenticated network access','web scheduler access',['Linux','nginx','nimbus.htb'],[],[],
      ['22/tcp SSH and 80/tcp nginx','IP redirects to nimbus.htb','vhost enumeration identifies aws.nimbus.htb'],
      'name-based hosting exposes additional cloud-facing application routes',
      'enumerate vhosts and scheduler endpoints','nimbus.htb and aws.nimbus.htb identified',1),
 step('step-2',2,'web scheduler access','temporary AWS role credentials',['/jobs/preview SSRF','IMDS'],[],['nimbus-web-role'],
      ['/jobs/preview fetches remote YAML','octal IPv4 0251.0376.0251.0376 reaches 169.254.169.254','IMDS returns nimbus-web-role credentials'],
      'alternative IPv4 notation bypasses the internal-address filter',
      'fetch IMDS role credentials through the SSRF','temporary nimbus-web-role credentials obtained',2),
 step('step-3',3,'temporary AWS role credentials','proven SQS message delivery',['LocalStack','nimbus-jobs'],['nimbus-web-role'],['sqs:SendMessage'],
      ['worker source reveals queue URL and yaml.load plus python3 -c execution','manual SigV4 helper returned misleading empty HTTP 204','boto3 returned a real MessageId'],
      'protocol-correct boto3 delivery reaches the queue consumed by the worker',
      'send a block-scalar Python job with boto3 and require MessageId','SQS MessageId confirmed',3),
 step('step-4',4,'proven SQS message delivery','interactive worker shell and user proof',['worker container','/app'],['sqs:SendMessage'],['uid 1000 worker'],
      ['worker consumed the block-scalar Python job','reverse shell connected from the target','/home/worker/user.txt was readable'],
      'the documented script field yields reliable RCE in the worker container',
      'enqueue a bounded Python reverse shell and inspect the worker context','shell as worker and user proof obtained with flag redacted',4),
 step('step-5',5,'interactive worker shell','privileged CodeBuild execution',['floci:4566','CodeBuild'],['worker'],['CodeBuild project creation and start'],
      ['LocalStack health exposes CodeBuild','project uses privilegedMode true','Floci build requires BASH_FUNC_id%% environment entry'],
      'a privileged CodeBuild project provides a stronger container boundary than the worker shell',
      'create and start a privileged CodeBuild project','corrected build entered privileged execution',5),
 step('step-6',6,'privileged CodeBuild execution','host-root proof',['privileged build container','shared host kernel'],['container root'],['host root'],
      ['overlay upperdir reveals a host-visible path','/proc/sys/kernel/modprobe is writable in the privileged build','invalid executable format triggers the kernel usermode-helper','proof copied to S3 loot bucket'],
      'controlling modprobe through a shared kernel executes the overlay helper as host root',
      'write a host-visible helper, replace modprobe path, trigger it, and retrieve redacted proof','CodeBuild pwn:2 succeeded and root proof was obtained',6),
 step('step-7',7,'completed compromise','corrected reusable workflow',['SQS','listeners','single worker','CodeBuild'],[],[],
      ['generic 204 was not proof of delivery','a successful shell existed on an older listener but was missed','parse-time os.system can block the single consumer outside subprocess timeout','root path was CodeBuild privilegedMode rather than direct worker escape'],
      'verifying the previous attack-chain edge and all observability channels prevents payload-iteration loops',
      'require MessageId, inspect every listener, use bounded script jobs, and follow the service-misconfiguration chain','anti-loop controls documented from verified evidence',7),
]

bundle={
 'artifacts':[{
   'draft_type':'case','artifact_type':'case','knowledge_role':'case_study',
   'local_id':'case-nimbus','origin':'explicit','title':'Nimbus: SSRF to IMDS, SQS worker RCE, and CodeBuild modprobe host escape',
   'starting_access':'unauthenticated HTTP access to an internal job scheduler',
   'source_quality':'complete','difficulty':'hard',
   'outcome':'user and host-root proofs obtained; proof values redacted',
   'transferable_properties':[
     'octal IPv4 notation can bypass SSRF filters and expose IMDS role credentials',
     'SQS delivery must be validated with protocol-specific artifacts such as MessageId rather than a generic 2xx response',
     'unsafe YAML job workers that execute a script field provide queue-to-container RCE',
     'privileged CodeBuild containers can expose shared-kernel usermode-helper paths for host escape',
     'all live listeners and the previous attack-chain edge must be checked before payload variation'
   ],
   'non_transferable_properties':['account IDs, container IDs, proof values, and transient credentials are target-specific'],
   'steps':steps,'citations':[{'segment_indexes':[1,2,3,4,5,6,7]}]
 }],
 'execution_examples':[
   {
    'local_id':'ex-sqs','parent_local_id':'step-3',
    'command_template':"python3 {{sender_script}} --endpoint http://{{queue_host}} --queue {{queue_url}} --body {{message_body}}",
    'placeholders':[
      {'name':'sender_script','kind':'value','binding_policy':'host_supplied','role':'protocol-correct boto3 sender'},
      {'name':'queue_host','kind':'target','binding_policy':'authorized_scope','role':'authorized SQS endpoint'},
      {'name':'queue_url','kind':'target','binding_policy':'authorized_scope','role':'authorized queue URL'},
      {'name':'message_body','kind':'value','binding_policy':'host_supplied','role':'bounded YAML job body'}],
    'capability_hint':'deliver a verified SQS job to an unsafe worker','purpose':'obtain worker-container RCE only after MessageId confirmation',
    'observed_role':'operator with temporary authorized role credentials',
    'prerequisites':[{'statement':'the role can call sqs:SendMessage and the worker source identifies the script schema','citations':[{'segment_indexes':[3]}]}],
    'platform_constraints':[{'dimension':'execution_environment','relation':'required','value':'SQS-compatible API','citations':[{'segment_indexes':[3]}]}],
    'citations':[{'segment_indexes':[3,4]}]
   },
   {
    'local_id':'ex-codebuild','parent_local_id':'step-6',
    'command_template':"python3 {{build_controller}} --endpoint http://{{localstack_host}} --project {{project_name}}",
    'placeholders':[
      {'name':'build_controller','kind':'value','binding_policy':'host_supplied','role':'reviewed CodeBuild controller'},
      {'name':'localstack_host','kind':'target','binding_policy':'authorized_scope','role':'authorized internal LocalStack endpoint'},
      {'name':'project_name','kind':'value','binding_policy':'host_supplied','role':'controlled project name'}],
    'capability_hint':'start a privileged build that exercises the shared kernel usermode-helper','purpose':'demonstrate host-root impact of privilegedMode',
    'observed_role':'worker shell with LocalStack API access',
    'prerequisites':[{'statement':'CodeBuild is running and project creation/start are permitted','citations':[{'segment_indexes':[5]}]}],
    'platform_constraints':[{'dimension':'os_family','relation':'required','value':'linux','citations':[{'segment_indexes':[6]}]}],
    'citations':[{'segment_indexes':[5,6]}]
   }
 ],
 'ignored_segment_indexes':[0]
}

b=SemanticDraftBundle.model_validate(bundle)
print('Pydantic OK. artifacts:',len(b.artifacts),'exec:',len(b.execution_examples))
validate_segment_accounting(prepared,b)
print('Segment accounting OK')
out=pathlib.Path('/tmp/bundles/Nimbus.json'); out.parent.mkdir(parents=True,exist_ok=True)
out.write_text(b.model_dump_json(indent=2)); print('wrote',out)
