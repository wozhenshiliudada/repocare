import {ui} from './ui-language.mjs';

export function usageText(value) {
 const total=value?.request_budget;
 if(total==null)return ui('尚未读取原额度','Original allowance not loaded');
 const split=Number.isInteger(value.requests_reserved_active)&&Number.isInteger(value.requests_ended_unknown);
 return ui('总上限 ','Limit ')+total+ui(' · 已用 ',' · Used ')+value.requests_used+
  (split?ui(' · 进行中预留 ',' · In-flight reservations ')+value.requests_reserved_active+
   ui(' · 已结束待核对 ',' · Ended, unverified ')+value.requests_ended_unknown:
   ui(' · 预留或待核对 ',' · Reserved or unverified ')+(value.requests_reserved_unknown??'—'))+
  ui(' · 可用 ',' · Available ')+value.requests_remaining;
}
