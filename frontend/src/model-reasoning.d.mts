export type ReasoningChoice={level:string;api_value:string|null};
export const CANONICAL_REASONING_LEVELS:string[];
export function reasoningChoices(profile:any):ReasoningChoice[];
export function selectedReasoning(profile:any):string;
export function reasoningCaption(row:ReasoningChoice,profile:any,language?:string):string;
