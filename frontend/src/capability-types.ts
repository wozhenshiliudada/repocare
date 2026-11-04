export interface RepositoryContext {
 workspace:Record<string,unknown>|null;
 remoteUrl:string|null;
 canMaintain:boolean;
}
export interface PreviewField {
 key:string;label:string;type:'text'|'textarea'|'number'|'select';required:boolean;
 options?:string[];min?:number;max?:number;format?:'https'|'argv';
}
export interface WorkflowDefinition {
 id:string;page:string;title:string;connection:'cli'|'planned';fields:PreviewField[];
}
export interface DraftPreview {
 workflow:string;title:string;status:'local_preview_only';executed:false;
 connection:'cli'|'planned';inputs:Record<string,string>;blocking_dependency:string;
}
export interface CapabilityDefinition {
 id:string;title:string;page:string;
 availability:'connected'|'mixed'|'preview'|'internal'|'engineering'|'limitation';
 workflow:string;relatedWorkflows?:string[];tab:string;description:string;baseline:string;
 nativeLinks?:Array<{page:string;tab:string;label:string}>;
}
