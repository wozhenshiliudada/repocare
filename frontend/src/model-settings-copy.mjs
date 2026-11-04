const copy={
 'zh-CN':{
  'models.historyCapacity':'保留的历史版本也计入容量；删除不会清除旧记录。',
  'models.editShort':'编辑','models.delete':'删除','models.compactDefault':'默认',
  'models.deleteHint':'从新选择中移除；旧记录、文件和预算保留。',
  'models.confirmDelete':'确认删除','models.cancelDelete':'取消','models.removed':'已从新选择中移除，旧记录保留。',
  'models.restored':'已恢复到模型选择。','models.archiveError':'未能更新模型列表，请刷新核对后重试。',
  'models.restore':'恢复','models.archivedCount':'已删除的模型（{count}）',
  'models.configure':'展开模型设置','models.manualTitle':'手动添加模型','models.manualIdHint':'填写准确的模型 ID',
  'models.existingService':'使用已保存的服务','models.newService':'新的服务地址',
  'models.description':'按服务商管理可选模型。保存后，可在新会话的模型菜单中选择。',
  'models.volcengineProvider':'火山方舟','models.originalServiceModel':'原服务中已保存的模型','models.modelCount':'{count} 个模型','models.selectedCount':'已选 {count} 个','models.deleteModel':'删除模型',
  'models.providerDeleteHint':'请在下方逐个模型点删除并确认；不会批量删除服务商的模型。',
  'models.savedKeySources':'每个已保存模型复用自己的密钥；目录和新增模型使用「{name}」的密钥，也可输入新密钥。',
  'models.pendingRemovalCount':'保存时将停用 {count} 个模型','models.pendingRemoval':'保存时将停用的模型','models.pendingRemovalHint':'按保存后，这些模型的所有已保存版本将移出新选择；旧会话、文件与预算保留。',
  'models.retainedVersionCount':'{count} 个待停用版本','models.conflictingChoices':'同一服务的同一 API 模型不能同时添加和停用，请核对选择。',
  'models.reviewDisableUnknown':'停用结果未知。请重新读取列表，核对后明确保留剩余停用意图；不会自动重放。',
  'models.keepDisabledIntent':'已核对，保留剩余停用意图',
  'models.unchanged':'已保存的模型选择保持不变，未创建新版本。当前会话继续使用原模型。',
  'models.saveChoices':'保存模型选择','models.choicesSaved':'已保存 {count} 个新增或修改的模型，停用 {disabled} 个模型。当前会话保持原模型；请在新会话的模型菜单中选择。',
  'models.versionHistory':'保留的历史版本（{count}）','models.historyHint':'相同模型在主列表显示最新未删除版本。旧版本和已绑定的会话、文件及预算保持不变。',
  'models.editTitle':'编辑模型提供商','models.immutable':'保存模型选择；仅新增或修改的模型另存版本。当前会话、审批、监控和预算保持原绑定，留空密钥复用各模型自己的已保存密钥。'
 },
 en:{
  'models.historyCapacity':'Retained historical versions also count toward capacity; deleting does not erase old records.',
  'models.editShort':'Edit','models.delete':'Delete','models.compactDefault':'Default',
  'models.deleteHint':'Remove from new choices; existing records, files, and budgets are retained.',
  'models.confirmDelete':'Confirm delete','models.cancelDelete':'Cancel','models.removed':'Removed from new choices. Existing records are retained.',
  'models.restored':'Model restored to choices.','models.archiveError':'Could not update models. Refresh to check before retrying.',
  'models.restore':'Restore','models.archivedCount':'Deleted models ({count})',
  'models.configure':'Expand model settings','models.manualTitle':'Manual model','models.manualIdHint':'Enter the exact model ID',
  'models.existingService':'Use a saved service','models.newService':'New service URL',
  'models.description':'Manage model choices by provider. Choose saved models from a new conversation’s model menu.',
  'models.volcengineProvider':'Volcengine Ark','models.originalServiceModel':'Saved model from original service','models.modelCount':'{count} models','models.selectedCount':'{count} selected','models.deleteModel':'Delete model',
  'models.providerDeleteHint':'Delete and confirm individual models below. Provider models are never deleted in a hidden batch.',
  'models.savedKeySources':'Saved models keep their own keys. The directory and new models use the key from “{name}”, or enter a new key.',
  'models.pendingRemovalCount':'{count} models to disable on Save','models.pendingRemoval':'Models to disable on Save','models.pendingRemovalHint':'Saving removes all saved versions of these models from new choices. Existing sessions, files, and budgets are retained.',
  'models.retainedVersionCount':'{count} versions pending removal','models.conflictingChoices':'The same API model at one service cannot be added and disabled together. Review the choices.',
  'models.reviewDisableUnknown':'The removal result is unknown. Reload the list, review it, and explicitly keep any remaining removal intent. It is never replayed automatically.',
  'models.keepDisabledIntent':'Checked; keep remaining removal intent',
  'models.unchanged':'Saved model choices are unchanged. No new version was created; the current session keeps its original model.',
  'models.saveChoices':'Save model choices','models.choicesSaved':'Saved {count} new or changed models; disabled {disabled} models. The current session keeps its original model; choose saved models from a new conversation’s model menu.',
  'models.versionHistory':'Retained versions ({count})','models.historyHint':'The main list shows the latest unarchived version per model. Older versions and their bound sessions, files, and budgets are retained.',
  'models.editTitle':'Edit model provider','models.immutable':'Save model choices; only new or changed models create versions. Existing sessions, approvals, monitors, and budgets keep their original binding. Leave the key blank to reuse each model’s own saved key.'
 }
};
export function modelSettingsCopy(language,key,variables={}){
 const value=(copy[language]||copy['zh-CN'])[key];
 return value===undefined?undefined:value.replace(/\{([^}]+)\}/g,(_,name)=>String(variables[name]??'{'+name+'}'));
}
