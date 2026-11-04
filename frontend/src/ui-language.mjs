import {uiMessages} from './ui-messages.mjs';

let language='zh-CN';
/** Presentation state only. App synchronizes this before rendering its children. */
export function setUiLanguage(value){language=value==='en'?'en':'zh-CN';}
export function getUiLanguage(){return language;}
/** Call only at owned UI copy boundaries, never on repository/model/user content. */
export function ui(chinese,english){return uiIn(language,chinese,english);}
/** Explicit-language variant for components preserving a legacy language prop. */
export function uiIn(selectedLanguage,chinese,english){
 if(selectedLanguage!=='en')return chinese;
 if(english!==undefined)return english;
 if(Object.hasOwn(uiMessages,chinese))return uiMessages[chinese];
 if(typeof chinese==='string'&&Object.hasOwn(uiMessages,chinese.trim())){
  return chinese.match(/^\s*/)[0]+uiMessages[chinese.trim()]+chinese.match(/\s*$/)[0];
 }
 return chinese;
}

/** Lazy getters for source-owned label dictionaries created before preferences load.
 * This helper is not used for form values, identifiers, requests or raw records.
 * Arrays keep their shape; keys and non-string values remain unchanged.
 * @template T
 * @param {T} source
 * @returns {T}
 */
export function uiLabels(source){
 if(typeof source==='string')return /** @type {T} */(ui(source));
 if(!source||typeof source!=='object')return source;
 const result=Array.isArray(source)?[]:{};
 for(const key of Object.keys(source)){
  const value=source[key];
  const nested=value&&typeof value==='object'?uiLabels(value):null;
  Object.defineProperty(result,key,{enumerable:true,configurable:false,
   get:()=>typeof value==='string'?ui(value):nested??value});
 }
 return /** @type {T} */(result);
}
