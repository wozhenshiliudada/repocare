// One schema definition for live Pi tools and read-only Native receipt verification.
import {Type} from 'typebox';

export const repairToolSchemas = {
  read_source: Type.Object({path: Type.String(),
    start_line: Type.Optional(Type.Integer({minimum:1, description:'First line to read, or first line to search when query is supplied.'})),
    end_line: Type.Optional(Type.Integer({minimum:1, description:'Last line to read, or last line to search when query is supplied.'})),
    query: Type.Optional(Type.String({minLength:1,maxLength:200,description:'Literal text to find within optional line bounds.'})),
    reread: Type.Optional(Type.Boolean())}),
  propose_repair: Type.Object({diagnosis: Type.String(), risk: Type.String(),
    edits: Type.Array(Type.Object({path: Type.String(),
      replacements: Type.Array(Type.Object({oldText: Type.String(), newText: Type.String()}))}))}),
  // Only enabled by the controller's versioned repair-investigation Native scope.
  search_approved_sources: Type.Object({
    query: Type.String({minLength:1,maxLength:512,description:'Literal text in the original approved read roots.'}),
    max_hits: Type.Optional(Type.Integer({minimum:1,maximum:100}))}),
  read_approved_source: Type.Object({
    path: Type.String({minLength:1,maxLength:4096,description:'A fixed-commit file inside the approved read roots.'}),
    start_line: Type.Optional(Type.Integer({minimum:1})),
    max_lines: Type.Optional(Type.Integer({minimum:1,maximum:200}))}),
};
