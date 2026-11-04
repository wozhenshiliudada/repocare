import {ui,uiLabels} from './ui-language.mjs';
export const deliveryNames=uiLabels({candidate_review:'候选待审阅',needs_review:'需核查原结果',evidence_unavailable:'证据缺失或绑定不符',in_progress:'原任务仍在执行',delivery_recorded:'已有交付记录，未重新核验'});
export const needsDeliveryAttention=row=>['candidate_review','needs_review','evidence_unavailable'].includes(row.status);
// Persist identifiers only. Budget changes and temporary workspace capabilities
// do not constitute new delivery events. First entry never alerts old history.
export function deliveryNotices(snapshot,stored){
 const current=(snapshot?.items||[]).map(row=>row.notice_id).filter(id=>typeof id==='string');
 const valid=stored&&stored.feed_id===snapshot.feed_id&&Array.isArray(stored.seen)&&stored.seen.every(id=>typeof id==='string');
 const seen=new Set(valid?stored.seen:[]);
 const alerts=valid?(snapshot.items||[]).filter(row=>needsDeliveryAttention(row)&&!seen.has(row.notice_id)):[];
 return {alerts,state:{feed_id:snapshot.feed_id,seen:[...new Set([...seen,...current])].slice(-400)}};
}
export function notifyDelivery(alerts,Notify){
 if(!Notify||Notify.permission!=='granted')return 0;
 let sent=0;
 for(const row of alerts){try{new Notify(ui("RepoCare · 交付待办"),{body:deliveryNames[row.status]||ui("请审阅原维护结果"),tag:row.notice_id});sent++;}catch{}}
 return sent;
}
