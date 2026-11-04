// Session-local scheduling only. Never changes the original allowance or tool authority.
export class PlanningProgress {
  constructor(budget) {
    this.budget=budget;
    this.reserve=Math.min(3,Math.max(0,budget-2));
    this.hasSource=false;this.repeated=0;this.proposals=0;
  }
  read(value) {
    this.hasSource ||= typeof value.content==='string' && value.content.length>0;
    this.repeated=value.repeat_read?this.repeated+1:0;
  }
  observe(event) {
    if(event.type==='message_end' && event.message?.role==='assistant')
      this.proposals += (event.message.content??[]).filter(c=>c.type==='toolCall'&&c.name==='propose_repair').length;
  }
  state(started) {
    const remaining=this.budget-started;
    const checkpoint=this.reserve>0 && this.hasSource && !this.proposals
      && (remaining<=this.reserve || this.repeated>=3);
    const phase=this.proposals?'correction':checkpoint?'proposal_checkpoint':
      this.repeated>=3||remaining<=Math.ceil(this.budget/2)?'converge':'diagnose';
    return {phase,requests_remaining:remaining,proposal_requests_reserved:this.reserve,
      proposal_attempts:this.proposals,repeated_read_streak:this.repeated};
  }
  provider(context,started) {
    const state=this.state(started);
    // Dynamic progress already travels in append-only tool receipts. A later system
    // message is collapsed BEFORE Pi's static sections and churns the prompt prefix.
    return {context,
      ...(state.phase==='proposal_checkpoint'?{toolChoice:{type:'function',function:{name:'propose_repair'}}}:{})};
  }
}
