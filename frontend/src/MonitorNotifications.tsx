import {ui} from './ui-language.mjs';
import {AsyncButton} from './AsyncButton';
import React,{useState} from 'react';
export function MonitorNotifications(){
 const [permission,setPermission]=useState(()=>typeof Notification==='undefined'?'unsupported':Notification.permission),[message,setMessage]=useState('');
 async function enable(){try{setPermission(await Notification.requestPermission());setMessage(ui("通知权限已核对；后续原监控通知仍由当前工作台适配器发送。"));}catch{setMessage(ui("浏览器未能完成通知许可；页面事件与待办仍可查看。"));}}
 return <section className="native-card"><h3>{ui("本页面维护通知")}</h3><p>{ui("浏览器权限：")}{{unsupported:ui("不支持"),default:ui("尚未选择"),granted:ui("已允许"),denied:ui("已拒绝")}[permission]}{ui("。通知标识与原因以原监控事件为准，服务停机后不继续通知。")}</p><AsyncButton disabled={permission!=='default'} onClick={enable}>{ui("允许本页面维护通知")}</AsyncButton>{permission==='denied'&&<p className="muted">{ui("如需改变选择，请在浏览器站点设置核对权限；这里不会反复请求。")}</p>}{message&&<p role="status">{message}</p>}</section>;
}
