import {ui} from './ui-language.mjs';
import React from 'react';
type Data=Record<string,any>;

// Pure display: entering an overview or expanding a summary cannot call a
// provider, submit a decision, or create a replacement controller action.
export function TaskSituationSummary({value,label=ui("当前状况与下一步")}:{value:Data;label?:string}){
 return <div aria-label={label}><p><strong>{value.title}</strong></p><p>{value.reason}</p><p><strong>{ui("下一步：")}</strong>{value.next}</p></div>;
}
