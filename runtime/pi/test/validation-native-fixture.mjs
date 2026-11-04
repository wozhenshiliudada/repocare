// Synthetic Native data through the real pinned SessionManager. No provider or model calls.
import fs from 'node:fs';
import {planningTaskSession} from '../task-session.mjs';
import {validateToolArguments} from '@earendil-works/pi-ai/utils/validation';
import {validationSchemas,validateValidationProposal,VALIDATION_PURPOSE} from '../validation-tool-schemas.mjs';
import {experienceHash} from '../experience-context.mjs';

try {
  let raw='';for await(const chunk of process.stdin)raw+=chunk;
  const input=JSON.parse(raw),context=input.task.context;
  const schema=context.validation_schema??'python-behavior-v1',validationToolSchemas=validationSchemas(schema);
  const manager=await planningTaskSession(input.task,context.provider,context.model,context.paths,VALIDATION_PURPOSE);
  if(input.task.mode==='create'){
    manager.appendModelChange(context.provider,context.model);manager.appendThinkingLevelChange('off');
    const timestamp=1;
    manager.appendMessage({role:'user',content:'Describe behavior cases only.',timestamp});
    const assistant=(id,args)=>manager.appendMessage({role:'assistant',provider:context.provider,model:context.model,
      timestamp,stopReason:'toolUse',content:[{type:'toolCall',id,name:'propose_validation',arguments:args}]});
    if(input.rejected){
      assistant('rejected',input.rejected);
      let text;try{validateToolArguments({name:'propose_validation',parameters:validationToolSchemas.propose_validation},
        {name:'propose_validation',arguments:input.rejected});}catch(error){text=error.message;}
      if(!text)throw new Error('Fixture rejection is schema-valid');
      manager.appendMessage({role:'toolResult',toolName:'propose_validation',toolCallId:'rejected',
        isError:true,content:[{type:'text',text}],details:{},timestamp});
    }
    if(input.semantic_rejected){
      assistant('data-rejected',input.semantic_rejected);
      manager.appendMessage({role:'toolResult',toolName:'propose_validation',toolCallId:'data-rejected',
        isError:true,content:[{type:'text',text:'Invalid validation proposal'}],details:{},timestamp});
    }
    validateValidationProposal(input.proposal,context.paths,schema);assistant('proposal',input.proposal);
    const receipt={accepted:true,purpose:VALIDATION_PURPOSE,proposal_sha256:experienceHash(input.proposal),runtime_authorized:false};
    manager.appendMessage({role:'toolResult',toolName:'propose_validation',toolCallId:'proposal',
      isError:false,content:[{type:'text',text:JSON.stringify(receipt)}],details:receipt,timestamp});
    manager.appendMessage({role:'assistant',provider:context.provider,model:context.model,
      timestamp,stopReason:'stop',content:[{type:'text',text:'Submitted for review; no tests executed.'}]});
  }
  process.stdout.write(JSON.stringify({session_file:manager.getSessionFile(),session_id:manager.getSessionId(),
    leaf_id:manager.getLeafId(),entries:manager.getEntries(),raw:fs.readFileSync(manager.getSessionFile(),'utf8')}));
} catch {process.stderr.write('Validation Native fixture failed');process.exitCode=1;}
