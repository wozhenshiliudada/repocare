import {validateCiSelection} from './ci-model.mjs';

// Uses the workbench's authenticated request; upstream failures arrive as evidence.
export function createCiReadEvidence(request){
 return selection=>{
  validateCiSelection(selection);
  const query=new URLSearchParams();
  for(const key of ['url','run_id','attempt','job_id','expected_repository_id','expected_head_sha']){
   if(selection[key]!=null)query.set(key,String(selection[key]));
  }
  return request('/api/ci/evidence?'+query);
 };
}
