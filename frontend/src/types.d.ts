export {};
declare global {
 interface Window {
  RepoCareTabSession:{open:(kind:string)=>{token:string;expire:()=>void;selectedView:()=>string|null;saveView:(id:string|null)=>boolean}};
  mountMaintenanceUI:(root:HTMLElement,options:Record<string,unknown>)=>Controller;
  mountWorkbenchOnboarding:(root:HTMLElement,options:Record<string,unknown>)=>OnboardingController;
 }
 interface Controller {ready:Promise<unknown>;refresh:()=>Promise<void>;select:(value:any)=>void;createTask:(issue:string)=>Promise<any>;getState:()=>any;dispose:()=>void;setLanguage?:(language:string)=>void;}
 interface OnboardingController {inspectRepository:(path:string)=>Promise<void>;dispose:()=>void;showPicker:()=>void;connectRepository:(url:string)=>Promise<void>;setLanguage?:(language:string)=>void;}
}
