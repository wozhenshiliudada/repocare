'use strict';
// Standalone access survives refresh in this tab; embedded capabilities remain in memory.
window.mountMaintenanceUI = function(root, options = {}) {

// Only explicitly registered product messages are translated. Repository text,
// model output, commands, JSON, identifiers and editable values stay verbatim.
const i18n = (() => {
 let language=options.language==='en'?'en':'zh-CN';
 const dictionary={
 " · 进行中预留：": " · In-flight reservations: ",
 " · 已结束待核对：": " · Ended, unverified: ",
 " · 预留或待核对：": " · Reserved or unverified: ",
 "生成方案": "Generate plan",
 "方案审批": "Plan approval",
 "自主修复": "Autonomous repair",
 "封装并重测": "Prepare and retest",
 "批准发布": "Approve publication",
 "拒绝发布": "Reject publication",
 "恢复中断": "Recover interruption",
 "清理候选": "Clean up candidate",
 "候选已验证": "Candidate verified",
 "失败已回滚": "Failed; rolled back",
 "已拒绝": "Rejected",
 "候选已清理": "Candidate cleaned up",
 "等待审批": "Awaiting approval",
 "已封装，等待发布审批": "Prepared; awaiting publication approval",
 "已发布本地分支": "Local branch published",
 "方案已处理": "Plan handled",
 "操作失败": "Operation failed",
 "执行中": "Running",
 "操作完成": "Operation complete",
 "准备已中止": "Preparation aborted",
 "未执行": "Not run",
 "操作未完成": "Operation incomplete",
 "规划的文件范围校验未通过，尚未形成可执行方案。已计量请求保留；请核对下方授权范围，不会自动重试或修改文件。": "Plan file scope validation failed. No executable plan was created. Metered requests are retained. Check the authorized scope below; no retry or file change starts automatically.",
 "已停止": "Stopped",
 "正在准备": "Preparing",
 "正在规划": "Planning",
 "正在核验方案": "Verifying plan",
 "正在创建隔离候选": "Creating isolated candidate",
 "正在运行基线测试": "Running baseline tests",
 "正在应用批准的修改": "Applying approved changes",
 "正在验证候选": "Testing candidate",
 "根据失败反馈重新规划": "Replanning from failure feedback",
 "正在核验完成状态": "Verifying completion",
 "读取授权源码": "Read authorized source",
 "提出修复方案": "Propose repair",
 "当前任务 · ": "Current task · ",
 "本轮已观察模型调用：": "Model calls observed this round: ",
 " · 工具调用：": " · Tool calls: ",
 "已计量：": "Metered: ",
 " · 可用：": " · Available: ",
 "最近活动：": "Latest activity: ",
 "（工具返回错误）": " (tool returned an error)",
 "最近读取：": "Latest file read: ",
 "本轮模型回执：输入 ": "Model usage this round: input ",
 " / 输出 ": " / output ",
 " token，费用未核实": " tokens; cost unverified",
 "停止请求已登记。规划会结束本次模型进程；测试会在当前测试结束后停止，清理结果以终态报告为准。": "Stop request recorded. Planning ends the current model process; testing stops after the current test. The final report determines cleanup results.",
 "停止当前任务": "Stop current task",
 "任务已停止，原消费和证据保留。": "Task stopped. Original consumption and evidence retained.",
 "停止请求已登记，正在等待安全收尾。": "Stop request recorded. Waiting for safe completion.",
 "任务已结束，请查看终态记录。": "Task ended. Check the final record.",
 "源仓库已变化": "Source repository changed",
 "需要核验后清理": "Verify before cleanup",
 "核验并认领方案": "Verify and claim plan",
 "核验并认领运行": "Verify and claim run",
 "已中断，禁止自动重放": "Interrupted; automatic replay prohibited",
 "证据待核验": "Evidence needs verification",
 "核验中断证据": "Verify interruption evidence",
 "关闭中断登记": "Close interruption record",
 "可确认关闭登记": "Record closure may be confirmed",
 "需要人工核验": "Manual verification required",
 "登记已关闭，结果未知": "Record closed; result unknown",
 "关闭后出现证据变化": "Evidence changed after closure",
 "核验修复执行": "Verify repair execution",
 "中止中断修复": "Abort interrupted repair",
 "可确认中止": "Abort may be confirmed",
 "修复已中止，候选已移除": "Repair aborted; candidate removed",
 "预览记忆出处": "Preview memory sources",
 "查询结构出处": "Query structure sources",
 "登记此运行的经验": "Register experience from this run",
 "审阅历史经验": "Review historical experience",
 "检索历史经验": "Search historical experience",
 "设置新任务经验检索": "Configure experience retrieval for new tasks",
 "预览经验提炼": "Preview experience distillation",
 "提炼知识候选": "Distill knowledge candidate",
 "审阅知识候选": "Review knowledge candidate",
 "建立知识新版本": "Create knowledge version",
 "检索工程知识": "Search engineering knowledge",
 "预览知识更正": "Preview knowledge correction",
 "根据新经历生成更正候选": "Generate correction candidate from new experience",
 "保存新任务整理设置": "Save compaction settings for new tasks",
 "预览上下文整理": "Preview context compaction",
 "整理原任务上下文": "Compact original task context",
 "核验上下文整理登记": "Verify compaction record",
 "设置新任务维护记忆": "Configure maintenance memory for new tasks",
 "保存维护待办": "Save maintenance follow-up",
 "检索维护待办": "Search maintenance follow-ups",
 "查询统一记忆": "Query unified memory",
 "设置本地语义检索": "Configure local semantic search",
 "预览额度变化": "Preview budget change",
 "确认扩额": "Confirm budget increase",
 "继续失败任务": "Continue failed task",
 "停止本机服务": "Stop local service",
 "恢复已核验失败反馈": "Restore verified failure feedback",
 "核验失败记录清理": "Verify failure record cleanup",
 "已确认": "Confirmed",
 "工程提议": "Engineering proposal",
 "待定": "Pending",
 "已替代": "Superseded",
 "参考资料": "Reference",
 "历史报告声称": "Historical report claim",
 "请求失败": "Request failed",
 "此会话没有同仓库经验入口。": "Repository experience is unavailable in this session.",
 "新任务将检索已接受、证据仍有效且文件/测试策略匹配的经验。": "New tasks retrieve accepted experience with valid evidence and matching file and test policies.",
 "新任务的经验检索尚未启用。任务结束后仍保存待审阅候选。": "Experience retrieval is disabled for new tasks. Candidates are still saved for review when tasks end.",
 "设置只影响之后的新任务；旧任务保持原有选择。经验不会批准修改或重置额度。": "Settings affect future tasks only. Existing tasks keep their selections. Experience grants no change approval and never resets budgets.",
 "停用新任务经验检索": "Disable experience retrieval for new tasks",
 "启用新任务经验检索": "Enable experience retrieval for new tasks",
 "检索当前问题的历史经验": "Find historical experience for this issue",
 "待审阅": "Pending review",
 "已接受供参考": "Accepted as reference",
 "原隔离候选测试通过（不代表合并）": "Original isolated candidate tests passed (does not mean merged)",
 "原修复测试失败": "Original repair tests failed",
 "原尝试未验证": "Original attempt unverified",
 "未知审核状态": "Unknown review status",
 "原证据已核验": "Original evidence verified",
 "原证据失效": "Original evidence invalid",
 "无法核验原问题": "Original issue cannot be verified",
 "结果无法核验": "Result cannot be verified",
 "当前文件与测试策略匹配；内容仍属历史参考。": "Current files and test policy match; this remains historical reference.",
 "当前代码与历史不同；新任务可以参考，但必须读取当前文件重新验证。": "Current code differs from history. New tasks may consult this reference but must read and verify current files.",
 "当前适用条件不匹配，或原证据无法核验。": "Applicability conditions do not match, or original evidence cannot be verified.",
 "第 ": "Round ",
 " 轮：": ": ",
 "；测试退出码 ": "; test exit codes: ",
 "未运行": "Not run",
 "原选定检查：{0}/{1} 条命令通过。不代表未覆盖行为正确或已经合并。": "Original selected checks: {0}/{1} commands passed. This does not establish correctness outside their coverage or a merge.",
 "查看原改动与验证摘要": "View original changes and validation summary",
 "查看原改动与失败摘录": "View original changes and failure excerpt",
 "原任务": "Original task",
 "无法核验": "Cannot verify",
 "原运行": "Original run",
 "经验指纹": "Experience fingerprint",
 "接受此经验供参考": "Accept experience as reference",
 "撤销接受此经验": "Revoke acceptance of experience",
 "拒绝此经验": "Reject experience",
 "预览提炼知识或方法": "Preview knowledge or procedure distillation",
 "尚无经验候选。新任务安全结束后自动记录；旧运行可在其详情中明确登记。": "No experience candidates yet. They are recorded after new tasks end safely; register earlier runs explicitly in their details.",
 "经验登记未完成 ": "Experience registration incomplete: ",
 "；原运行结果保留。": "; original run result retained.",
 "最近一次经验检索": "Latest experience search",
 "没有已接受且适用的匹配经验。": "No accepted, applicable experience matches.",
 "排除 ": "Excluded ",
 "当前仅显示原记录；运行结束后核验维护进度。": "Showing original records only. Verify maintenance progress when the run ends.",
 "待办是你的工作安排。关闭待办只表示你不再跟进，不代表已修复、测试通过或合并。": "Follow-ups track your work. Closing one means you stop following it; it does not establish a fix, passing tests or a merge.",
 "之后的新任务参考相关维护待办": "Let future tasks consult related maintenance follow-ups",
 "保存新任务设置": "Save settings for new tasks",
 "继续跟进": "Keep following up",
 "暂缓": "Defer",
 "我已关闭跟进": "Closed by me",
 "低": "Low",
 "普通": "Normal",
 "优先": "High",
 "最高": "Highest",
 "待办标题": "Follow-up title",
 "需要跟进什么": "What needs follow-up",
 "跟进状态": "Follow-up status",
 "优先级": "Priority",
 "保存待办": "Save follow-up",
 "重置为新待办": "Reset to new follow-up",
 "已登记检查点 ": "Recorded checkpoints: ",
 "；保留修复尝试 ": "; repair attempts retained: ",
 "；待跟进 ": "; open follow-ups: ",
 "。检查点仍需原恢复入口核验。": ". Checkpoints still require verification through their original recovery controls.",
 "由你关闭跟进": "Follow-up closed by you",
 "方案待审批": "Plan awaiting approval",
 "候选测试通过，尚不表示已合并": "Candidate tests passed; does not establish a merge",
 "候选测试失败": "Candidate tests failed",
 "未验证": "Unverified",
 "；出处 ": "; source ",
 "原测试退出码：": "Original test exit codes: ",
 "编辑这条待办": "Edit this follow-up",
 "原记录暂不可核验：": "Original record cannot currently be verified: ",
 "文档": "Document",
 "历史案例": "Historical case",
 "维护进度": "Maintenance progress",
 "代码结构": "Code structure",
 "关系声明": "Relationship declaration",
 "知识与方法": "Knowledge and procedures",
 "已接受参考": "Accepted reference",
 "原资料核验有效": "Original source verified",
 "需按当前源码重新核验": "Reverify against current source",
 "不代表当前代码已验证": "Does not establish verification of current code",
 "存在待处理冲突": "Unresolved conflict",
 "原分类": "Original classification",
 "未声明": "Unspecified",
 "审阅": "Review",
 "该类资料无单独审阅状态": "This source type has no separate review status",
 "当前适用性": "Current applicability",
 "传入本次模型输入": "Included in this model input",
 "已传入，仍只是参考": "Included; remains reference only",
 "此处仅为查询结果": "Query result only",
 "同主题文档存在多个决策分类；此视图不替你选择有效规则。": "Documents on this topic have different decision classifications. This view does not choose a governing rule for you.",
 "匹配线索：": "Match clues: ",
 "。这是词面匹配，不代表内容已获批准。": ". Lexical matches do not establish content approval.",
 "本地语义相似度：": "Local semantic similarity: ",
 "；相似度不代表事实正确或已获批准。": "; similarity establishes neither accuracy nor approval.",
 "本次实际传入的记忆清单": "Memory actually included in this input",
 "提交巡查": "Commit inspection",
 "修复任务": "Repair task",
 "；资料 ": "; sources: ",
 " 项；整理装载清单新增模型请求 0。": "; compiling this loading receipt added 0 model requests.",
 "清单证明传入了哪些资料，不证明模型理解、采纳或产生收益。历史批准仍不授权本次修改。": "This receipt shows which sources were included. It does not establish model understanding, adoption or benefit. Historical approvals do not authorize current changes.",
 "未装载：": "Not loaded: ",
 "本地混合检索已开启。": "Local hybrid search enabled.",
 "当前使用词面检索。": "Lexical search is active.",
 "本地语义模型只帮助查找参考，不调用收费语言模型。只影响后续查询和新任务；旧任务资料与审批保持原样。": "The local semantic model only helps find references; it calls no paid language model. Changes affect future queries and new tasks. Existing task sources and approvals remain intact.",
 "启用本地混合检索": "Enable local hybrid search",
 "保存检索设置": "Save search settings",
 "已登记模型数据 ": "Registered model data: ",
 " MiB；运行时不自动下载。": " MiB; no automatic runtime download.",
 "本次启动未登记本地语义模型。": "No local semantic model registered for this service.",
 "找到 ": "Found ",
 " 项；支持各类资料并列显示。查询不重新装载旧任务。检索方式：": " items across source types. This query does not reload existing tasks. Search mode: ",
 "本地混合检索": "Local hybrid search",
 "词面检索": "Lexical search",
 "同主题还有 ": "Other sources on this topic: ",
 " 项资料可供对照": " items for comparison",
 "同主题不等于内容一致或存在矛盾。请对照原分类、审阅和出处；这里不替你选择有效规则，也不改变任务资料。": "A shared topic establishes neither agreement nor conflict. Compare original classifications, reviews and sources. This view chooses no governing rule and changes no task sources.",
 "同主题共 ": "Sources on this topic: ",
 " 项；主结果已显示 ": "; shown in main results: ",
 " 项，以下补充 ": "; additional items below: ",
 " 项。": " items.",
 "仍有 ": "Remaining: ",
 " 项未展开，请缩小查询或分批查看原资料。": " items not expanded. Narrow the query or inspect original sources in batches.",
 "另有 ": "Additionally, ",
 " 个同主题分组未展开；这不是全部资料。": " topic groups are not expanded; this is not the full source set.",
 "未提供：": "Unavailable: ",
 "此会话没有原生上下文整理入口。": "Native context compaction is unavailable in this session.",
 "原始会话保留。整理只改变后续分析上下文，不执行历史动作，也不会增加已完成规划回合。": "The original session is retained. Compaction only changes subsequent analysis context. It replays no actions and adds no completed planning rounds.",
 "新任务达到阈值时自动整理": "Compact new tasks automatically at the threshold",
 "设置只影响之后的新任务；旧任务保持原设置。摘要调用使用当前模型和原请求额度，可能产生费用。": "Settings affect future tasks only. Existing tasks keep their settings. Summary calls use the current model and original request budget and may incur costs.",
 "模型上下文容量（0表示未知）": "Model context capacity (0 means unknown)",
 "开始整理的阈值（保守输入估算）": "Compaction threshold (conservative input estimate)",
 "保留近期原文量": "Recent original text to retain",
 "摘要输出上限": "Summary output limit",
 "容量与保留设置": "Capacity and retention settings",
 "以上容量、阈值、保留量和输出上限均以 token 计量。容量是当前配置的声明，不是自动探测结果。摘要输出分配独立；修复规划的输出上限与原请求账本保持原设置。": "Capacity, threshold, retention and output limit are measured in tokens. Capacity is a configuration declaration, not an automatic measurement. Summary output is allocated separately; repair planning limits and the original request ledger keep their settings.",
 "保存为新任务设置": "Save for new tasks",
 "任务 ": "Task ",
 "运行中": "Running",
 "检查点已核验": "Checkpoint verified",
 "当前不可整理": "Compaction unavailable",
 "已完成规划回合 ": "Completed planning rounds: ",
 "；保守上下文输入估算 ": "; conservative context input estimate: ",
 "该任务创建时已允许整理；整理仍需原剩余额度。": "Compaction was enabled when this task was created. It still uses the original remaining budget.",
 "该任务没有启用整理，保持原设置。": "Compaction is disabled for this task. Original settings retained.",
 "预览整理此任务": "Preview task compaction",
 "确认原任务整理": "Confirm original task compaction",
 "；共享剩余额度 ": "; shared remaining budget: ",
 "；至少预留两次请求以覆盖原生摘要可能的分段。": "; at least two requests must remain for possible native summary segments.",
 "此预览已不适用于当前检查点或额度；请重新预览当前任务。": "This preview no longer matches the checkpoint or budget. Preview the current task again.",
 "确认消耗原额度整理上下文": "Confirm context compaction using original budget",
 "待核验整理 · ": "Compaction awaiting verification · ",
 "只补已核验的元数据，或明确保留旧检查点；不会重发摘要请求，也不清除未知消费。": "Only restore verified metadata or explicitly retain the old checkpoint. No summary is resent and unknown consumption is preserved.",
 "核验已完成摘要并补登记": "Verify completed summary and restore record",
 "保留旧检查点并放弃此整理": "Keep old checkpoint and abandon compaction",
 "已登记整理次数：": "Recorded compactions: ",
 "。不计作修复成功次数。": ". These are not counted as successful repairs.",
 "从已接受的经历提炼候选，审阅后才供新任务参考。提炼使用当前模型和原请求额度，可能产生费用。": "Distill candidates from accepted experience. New tasks may consult them after review. Distillation uses the current model and original request budget and may incur costs.",
 "接受候选不会把推测升级为已确认规则；当前源码、测试和新审批仍须独立核验。": "Accepting a candidate does not turn assumptions into confirmed rules. Current source, tests and fresh approvals still require independent verification.",
 "检索当前问题的工程知识": "Find engineering knowledge for this issue",
 "确认提炼范围与消耗": "Confirm distillation scope and consumption",
 "来源经历：": "Source experience: ",
 "。只生成待审阅候选。": ". Generates candidates for review only.",
 "确认用现有额度提炼候选": "Confirm distillation using existing budget",
 "触发条件": "Triggers",
 "前提": "Prerequisites",
 "建议步骤": "Suggested steps",
 "新验证要求": "New verification requirements",
 "停止条件": "Stop conditions",
 "确认按新经历更正旧知识": "Confirm knowledge correction from new experience",
 "旧版本：": "Previous version: ",
 "；新来源：": "; new sources: ",
 "。新候选必须单独审阅。": ". The new candidate requires separate review.",
 "确认使用现有额度生成更正候选": "Confirm correction candidate using existing budget",
 "未知": "Unknown",
 "分类：工程提议；": "Classification: engineering proposal; ",
 "已被明确新版本替代": "Explicitly superseded by a newer version",
 "原经历：": "Original experience: ",
 "当前代码已变化，必须重验": "Current code changed; revalidation required",
 "仍须当前独立验证": "Current independent validation still required",
 "选择新经历，让模型建立更正版本": "Choose new experience for a model-generated correction",
 "勾选1至3条已接受且出处有效的经历。旧资料保留；旧审阅不适用于新候选。": "Select 1–3 accepted experiences with valid sources. Old sources are retained; previous reviews do not apply to the new candidate.",
 "预览更正来源与消耗": "Preview correction sources and consumption",
 "接受此版本供参考": "Accept this version as reference",
 "撤销接受此版本": "Revoke acceptance of this version",
 "拒绝此版本": "Reject this version",
 "修订为新版本（保留旧版本）": "Revise as a new version (retain original)",
 "排查方法": "Investigation procedure",
 "工程知识": "Engineering knowledge",
 "知识类型草稿": "Knowledge type draft",
 "标题": "Title",
 "内容": "Content",
 "（每行一项）": " (one item per line)",
 "保存为待审阅新版本": "Save as a new version for review",
 "尚无知识候选。先在仓库经验中选择已接受的经历，预览后明确启动提炼。": "No knowledge candidates yet. Select accepted repository experience, preview it, then explicitly start distillation.",
 "最近一次知识检索": "Latest knowledge search",
 "没有已接受且适用的匹配知识。": "No accepted, applicable knowledge matches.",
 "可以继续此任务": "Task can continue",
 "先审阅新方案": "Review the new plan first",
 "原方案已处理": "Original plan handled",
 "先核验清理登记": "Verify cleanup record first",
 "先恢复失败反馈": "Restore failure feedback first",
 "候选已通过，等待审阅": "Candidate passed; awaiting review",
 "执行中断，先核验现场": "Execution interrupted; verify current state first",
 "消费未知，先查看证据": "Consumption unknown; inspect evidence first",
 "可用额度不足": "Insufficient available budget",
 "必要来源已失效": "Required sources invalid",
 "服务需要安全重启": "Service needs a safe restart",
 "任务正在执行": "Task is running",
 "没有完整规划检查点": "No complete planning checkpoint",
 "旧记录没有同任务会话": "Historical record has no same-task session",
 "恢复已阻断": "Recovery blocked",
 "任务检查点缺少运行登记": "Task checkpoint lacks a run record",
 "任务恢复": "Task recovery",
 "请核验当前记录": "Verify the current record",
 "所属仓库": "Repository",
 "任务 ID": "Task ID",
 "未绑定；不能作为同任务记忆恢复": "Unbound; cannot restore same-task memory",
 "已完成规划": "Completed planning",
 " 回合": " rounds",
 "（原登记，当前会话未核验）": " (original record; current session unverified)",
 "已核验已提交检查点": "Committed checkpoint verified",
 "执行中，尚未核验当前会话": "Running; current session unverified",
 "尚无完整检查点": "No complete checkpoint",
 "未绑定": "Unbound",
 "会话检查点": "Session checkpoint",
 "原请求账本": "Original request ledger",
 "总上限 ": "Total limit ",
 " · 已用 ": " · Used ",
 " · 未知保留 ": " · Reserved unknown ",
 " · 剩余 ": " · Remaining ",
 "已核验": "Verified",
 "已失效": "Invalid",
 "未启用": "Disabled",
 "必要来源": "Required sources",
 "文档/案例：": "Documents/cases: ",
 "；代码结构：": "; code structure: ",
 "尚未核验；请查看长任务上下文整理": "Unverified; see long-task context compaction",
 "未绑定原任务，无法核验": "No original task binding; cannot verify",
 "已允许（沿用原任务设置和原额度）": "Enabled (original task settings and budget)",
 "未启用（原任务设置）": "Disabled (original task settings)",
 "旧任务未配置；不会自动启用": "Not configured for this historical task; never enabled automatically",
 "上下文压缩": "Context compaction",
 "原会话含未提交尾部；继续只使用已提交检查点，不重放尾部。": "The original session has an uncommitted tail. Continuation uses only the committed checkpoint and never replays the tail.",
 "最近失败": "Latest failure",
 "运行 ": "Run ",
 " · 修复第 ": " · Repair round ",
 " 轮 · 测试退出码 ": " · Test exit codes ",
 "查看原失败摘要": "View original failure summary",
 "；已封存，反馈登记待恢复。": "; sealed, feedback record awaiting recovery.",
 "存在原失败记录，但当前不能核验其反馈。下方保留原恢复入口。": "An original failure record exists, but its feedback cannot currently be verified. Its original recovery controls remain below.",
 "暂无已核验的失败反馈；不从自由日志补造历史。": "No verified failure feedback. Free-form logs are not used to invent history.",
 "下一步：在左侧选择这份方案对应的原运行，查看结果与恢复入口。": "Next: select the original run for this plan on the left to inspect results and recovery controls.",
 "下一步：生成同任务新方案，再重新审批。继续会使用剩余额度。": "Next: generate a fresh plan for this task and approve it again. Continuation uses the remaining budget.",
 "下一步：在下方核验认领历史方案，再审批当前补丁。旧批准不会继承。": "Next: verify and claim the historical plan below, then approve the current patch. Previous approval is not inherited.",
 "下一步：核验并完成清理登记；只登记元数据，不运行模型或测试。": "Next: verify and complete the cleanup record. This records metadata only, with no model or test run.",
 "下一步：核验并恢复已封存的失败反馈；不运行模型或测试。": "Next: verify and restore sealed failure feedback. No model or tests are run.",
 "下一步：审阅已验证候选；发布仍需独立审批。先处理当前候选，才能再继续规划。": "Next: review the verified candidate. Publication still requires separate approval. Handle this candidate before continuing planning.",
 "下一步：使用下方“核验修复执行”；未知状态先保留现场。": "Next: use Verify repair execution below. Preserve the current state when it is unknown.",
 "下一步：查看操作记录中的原调用证据。未知消费不能靠刷新或重启清零。": "Next: inspect original call evidence in the operation log. Refreshing or restarting cannot clear unknown consumption.",
 "下一步：审阅“调整请求总上限”，确认后才可发起新规划。": "Next: review Adjust total request limit. Confirm it before starting new planning.",
 "下一步：恢复并核验连接时的必要来源，不提供忽略来源的按钮。": "Next: restore and verify the required sources from connection time. There is no source-bypass control.",
 "下一步：使用原启动方式和原账本安全重启服务，不换目录重置额度。": "Next: safely restart using the original launch method and ledger. Do not change directories to reset the budget.",
 "等待当前操作安全结束；刷新只查看状态。": "Wait for the current operation to end safely. Refresh only reads status.",
 "本次尚未保存完整规划回合。请核验原尝试；不猜测已完成内容。": "No complete planning round was saved. Verify the original attempt; completed work is not inferred.",
 "此历史记录没有可恢复的 Pi 任务绑定；新问题应从新任务入口开始。": "This historical record has no recoverable Pi task binding. Start new issues from the new-task entry.",
 "下一步：查看下方原始证据与核验入口。": "Next: inspect original evidence and verification controls below.",
 "查看具体阻止原因与协议说明": "View blocking reasons and protocol details",
 "当前展示不授予执行权限。": "This display grants no execution permission.",
 "在原授权范围内继续修复": "Continue repair within the original authorization",
 "继续此任务，先生成方案": "Continue task by generating a plan first",
 "核验并恢复失败反馈": "Verify and restore failure feedback",
 "核验并完成清理登记": "Verify and complete cleanup record",
 "使用原剩余额度，在原自主授权范围内继续同一任务？": "Continue the same task using its original remaining budget and autonomous authorization?",
 "恢复与刷新不等于批准；每份新方案仍需重新审批，发布也需另行批准。": "Recovery and refresh grant no approval. Each new plan needs fresh approval, and publication requires separate approval.",
 "暂无修改": "No changes",
 " 条结果": " results",
 "记忆出处 · ": "Memory sources · ",
 " 条": " entries",
 "来源是只读参考；历史批准不授权当前任务。出处回执不证明模型判断正确。": "Sources are read-only references. Historical approvals do not authorize the current task, and source receipts do not establish correct model judgment.",
 "原文 SHA256：": "Original text SHA256: ",
 "上下文回执：": "Context receipt: ",
 "集合来源：": "Collection source: ",
 " · 原条目：": " · Original entry: ",
 " · 历史运行：": " · Historical run: ",
 "原清单 SHA256：": "Original manifest SHA256: ",
 "决策替代声明 · ": "Decision supersession declarations · ",
 "仅展示原文声明，保留双方原状态；声明不授予批准，不自动选择有效规则。": "Shows original declarations and retains both original statuses. Declarations grant no approval and do not automatically choose a governing rule.",
 "关系文件 SHA256：": "Relationship file SHA256: ",
 "原文声明：Supersedes: ": "Original declaration: Supersedes: ",
 " · 第 ": " · Line ",
 " 行": "",
 "声明行 SHA256：": "Declaration line SHA256: ",
 "新条目": "New entry",
 "旧条目": "Previous entry",
 "本次未启用来源记忆。": "Source memory is not enabled for this session.",
 "来源与索引已核验": "Sources and index verified",
 "来源已失效或无法核验；已阻止新规划与旧方案审批。": "Sources are invalid or unverifiable. New planning and historical plan approvals are blocked.",
 "来源：": "Source: ",
 "\n索引：": "\nIndex: ",
 "本次 Pi 规划会读取经核验的替代声明；原分类保留，声明不授予审批。": "Pi planning reads verified supersession declarations. Original classifications are retained; declarations grant no approval.",
 "存在多种决策状态，相关问题需先审阅：": "Multiple decision statuses exist. Review related issues first: ",
 "上次预览；执行前重新检索核验。": "Previous preview; search and verify again before execution.",
 "上次预览已过期，不能用于授权。": "The previous preview is stale and cannot authorize execution.",
 "状态混合，阻止注入：": "Mixed statuses; input blocked: ",
 "中断登记 ": "Interruption record ",
 "核验方案消费记录、源快照、运行文件和 Git 登记。关闭登记后，执行结果仍记为未知；后续修复需生成新方案并重新批准。": "Verify plan consumption, source snapshots, run files and Git records. Closing the record keeps execution results unknown. Further repair requires a new plan and fresh approval.",
 "源快照一致": "Source snapshot matches",
 "原方案证据一致": "Original plan evidence matches",
 "运行目录存在": "Run directory exists",
 "运行记录": "Run record",
 "候选分支": "Candidate branch",
 "无登记": "No record",
 "发布分支": "Publication branch",
 "相关工作区登记": "Related worktree records",
 "核验详情与阻止原因": "Verification details and blocking reasons",
 "我已核验上述证据，同意仅关闭本次中断登记并保留原方案的已消费状态。": "I verified this evidence and agree only to close this interruption record, retaining the original plan as consumed.",
 "确认关闭中断登记": "Confirm interruption record closure",
 "先核验当前证据；历史预览不能直接批准关闭。": "Verify current evidence first. Historical previews cannot approve closure.",
 "登记关闭记录": "Record closure",
 "正在执行…": "Running…",
 "收尾结果：": "Completion result: ",
 "完成": "Complete",
 "修复执行核验 ": "Repair execution verification ",
 "执行尚未完成。先核验进程、候选和源仓库；只有条件满足才允许明确中止。证据未知或有人工修改时保留现场。": "Execution is incomplete. Verify the process, candidate and source repository first. Explicit abort is allowed only when conditions hold. Preserve the current state if evidence is unknown or manual changes exist.",
 "进程、工作区核验与阻止原因": "Process and worktree verification; blocking reasons",
 "我已审阅当前证据，同意中止本次修复并移除身份和内容未变的候选；保留报告和原模型额度。": "I reviewed current evidence and agree to abort this repair and remove the candidate only if its identity and contents are unchanged. Reports and the original model budget are retained.",
 "确认中止修复": "Confirm repair abort",
 "已记录执行事实": "Recorded execution facts",
 "结构目录身份 SHA256：": "Structure directory identity SHA256: ",
 "\n清单文件 SHA256：": "\nManifest file SHA256: ",
 "\n图 SHA256：": "\nGraph SHA256: ",
 "固定模型查询 SHA256：": "Fixed model query SHA256: ",
 "本次未启用代码结构来源。": "Code structure sources are not enabled for this session.",
 "代码结构来源已核验": "Code structure sources verified",
 "代码结构来源已失效；已阻止新规划、认领与审批。": "Code structure sources are invalid. New planning, claims and approvals are blocked.",
 "本次 Pi 读取明确查询的已核验结构元数据；原索引基线可能不同于候选，结构不授予审批。": "Pi reads explicitly queried, verified structure metadata. The original index baseline may differ from the candidate; structure grants no approval.",
 "只展示所选源码的结构关系，不代表完整依赖或运行安全。本次不向模型传递结构图。": "Shows structure relationships for selected source files only. This establishes neither complete dependencies nor runtime safety. No structure graph is passed to the model.",
 " 节点 / ": " nodes / ",
 " 关系": " relationships",
 " 行\n源码 SHA256：": "\nSource SHA256: ",
 "上次结构查询；操作前重新核验。": "Previous structure query; verify again before an action.",
 "上次结构查询已过期，不能用于授权。": "Previous structure query is stale and cannot authorize execution.",
 "符号与关系出处": "Symbol and relationship sources",
 "\n源码 SHA256：": "\nSource SHA256: ",
 "限定范围内无字面匹配；不能推定仓库不存在该符号。": "No literal match in the selected scope. This does not establish that the symbol is absent from the repository.",
 "查询回执 SHA256：": "Query receipt SHA256: ",
 "执行器：": "Executor: ",
 "\n授权文件：": "\nAuthorized files: ",
 "\n已计量模型请求：": "\nMetered model requests: ",
 " · 剩余额度：": " · Remaining budget: ",
 "\n仓库：": "\nRepository: ",
 "服务版本：": "Service version: ",
 "\n启动时间：": "\nStarted: ",
 "\n单次输出上限：": "\nOutput limit per call: ",
 " token · 每次规划：": " tokens · Planning time per call: ",
 "分钟": " min",
 "\n代码已更新，请使用原账本重启服务。": "\nCode changed. Restart the service using the original ledger.",
 "旧服务尚未提供版本信息，请重启升级。": "This older service provides no version information. Restart to upgrade.",
 "；新增 ": "; added ",
 " 次；保留已计量 ": " requests; retained metered ",
 " 次、预留或待核对 ": " and reserved or unverified ",
 " 次；变更后可用 ": " requests; available after change ",
 " 次。": " requests.",
 "确认增加总上限": "Confirm total limit increase",
 "方案 ": "Plan ",
 " · 历史只读": " · Historical, read-only",
 "未完成登记 ": "Incomplete record ",
 "规划调用中断或失败 · 消耗未知": "Planning call interrupted or failed · Consumption unknown",
 "已保留额度：": "Reserved budget: ",
 "。不会自动重试或因重启恢复额度。": ". No automatic retry; restart does not restore the budget.",
 "已观察到的调用开始：": "Observed call starts: ",
 "未取得": "Unavailable",
 "；这些记录不代表服务端实际消费或计费。": "; these records do not establish provider-side consumption or charges.",
 "运行事件：": "Run events: ",
 "已取得结束事实": "Completion facts recorded",
 "只取得部分事实，或尚未取得": "Facts partial or unavailable",
 "规划失败 · 调用次数已确认": "Planning failed · Call count confirmed",
 "已记录模型调用：": "Recorded model calls: ",
 "。任务仍失败，未生成可批准的方案；不会自动重试。": ". The task failed and produced no approvable plan. No automatic retry.",
 "调用次数是本机适配器计数，实际费用以服务商账单为准。": "Call counts come from the local adapter. Actual charges depend on the provider bill.",
 "每一步，都留下证据": "Evidence at every step",
 "输入问题并生成方案，在此查看修改范围、差异与风险。": "Describe an issue and generate a plan. Review scope, changes and risks here.",
 "待审批方案": "Plan awaiting approval",
 "问题": "Issue",
 "诊断": "Diagnosis",
 "风险": "Risks",
 "基线": "Baseline",
 "授权测试": "Authorized tests",
 "原始规划模型请求": "Original planning model requests",
 "未计量": "Unmetered",
 "本次历史经验": "Historical experience for this task",
 "没有匹配经验": "No matching experience",
 "历史经验只供参考；当前文件、测试与新的审批决定本次修改。": "Historical experience is reference only. Current files, tests and fresh approval govern these changes.",
 "经验上下文回执：": "Experience context receipt: ",
 "本次工程知识": "Engineering knowledge for this task",
 "没有匹配知识": "No matching knowledge",
 "引用的是经审阅的工程提议，仍需对当前代码验证。": "These are reviewed engineering proposals. They still require validation against current code.",
 "知识上下文回执：": "Knowledge context receipt: ",
 "原始 Pi 规划事件与用量": "Original Pi planning events and usage",
 "原规划读取了经核验的结构元数据。": "Original planning read verified structure metadata.",
 "方案绑定了代码结构来源；图数据未传给模型。": "The plan binds code structure sources. Graph data was not passed to the model.",
 "结构上下文回执 SHA256：": "Structure context receipt SHA256: ",
 "此方案的代码结构来源已失效。核验原来源前不能认领或批准。": "This plan’s code structure sources are invalid. Verify original sources before claiming or approving it.",
 "此方案的记忆来源已失效。恢复并核验原来源前不能认领或批准。": "This plan’s memory sources are invalid. Restore and verify original sources before claiming or approving it.",
 "批准仅允许执行这份方案；测试通过后，发布仍须另行审批。": "Approval allows only this plan to execute. After tests pass, publication still requires separate approval.",
 "这份补丁与已验证失败的补丁相同，修改审批已阻断。请查看失败记录并生成不同方案。": "This patch matches a verified failed patch. Change approval is blocked. Review the failure record and generate a different plan.",
 "拒绝重复方案": "Reject duplicate plan",
 "这是历史方案。先核验当前仓库、策略和证据并认领，再重新审批。认领不会修改代码。": "This is a historical plan. Verify the current repository, policy and evidence, claim it, then approve it again. Claiming does not change code.",
 "批准修改并测试": "Approve changes and tests",
 "拒绝方案": "Reject plan",
 "查看关联运行登记": "View linked run record",
 "登记此运行为待审阅经验": "Register this run as experience for review",
 "执行状态": "Execution status",
 "权限模式": "Permission mode",
 "自主": "Autonomous",
 "人工审批": "Manual approval",
 "源保持不变": "Source unchanged",
 "尚未发布": "Not published",
 "继续自运行 ": "Continued from run ",
 "，沿用原请求账本。": ", using the original request ledger.",
 "清理操作的反馈转换尚未登记。核验只更新清理审计，不调用模型或测试，不修改 Git 分支或文件，不授予旧批准。": "The cleanup feedback transition is not recorded. Verification updates cleanup audit only. It runs no model or tests, changes no Git branches or files, and grants no previous approval.",
 "失败运行已结束，但反馈检查点未保存。第": "The failed run ended without a saved feedback checkpoint. Round ",
 "轮测试退出码：": " test exit codes: ",
 "。恢复只登记已核验反馈，不调用模型、不运行测试，也不继承旧批准。": ". Recovery records verified feedback only. It runs no model or tests and inherits no previous approval.",
 "续修已阻断：": "Continuation blocked: ",
 "续修将携带第": "Continuation includes actual failure feedback from round ",
 "轮的实际失败反馈，测试退出码：": "; test exit codes: ",
 "。新方案需要重新审批。": ". The new plan requires fresh approval.",
 "此前候选已安全结束。继续会创建新的隔离尝试；原报告和已用额度保留。": "The previous candidate ended safely. Continuation creates a new isolated attempt; original reports and consumed budget are retained.",
 "继续该问题，先生成方案": "Continue issue by generating a plan first",
 "继续同一问题，使用剩余额度创建新的隔离候选？原授权范围和报告保留。": "Continue the same issue and create a new isolated candidate using remaining budget? Original authorization and reports are retained.",
 "基线测试": "Baseline tests",
 "候选测试": "Candidate tests",
 "原始规划请求：": "Original planning requests: ",
 "；执行已保存方案新增模型请求：0。": "; additional model requests to execute saved plan: 0.",
 "这是历史运行。认领只核验固定仓库、策略与当前证据，后续发布仍需独立批准。": "This is a historical run. Claiming only verifies the fixed repository, policy and current evidence. Publication still requires separate approval.",
 "封装候选并重新测试": "Prepare candidate and retest",
 "独立发布审批": "Separate publication approval",
 "准备阶段": "Preparation stage",
 "未记录": "Not recorded",
 "准备批次": "Preparation batch",
 "测试进程": "Test process",
 "PID {0} · 创建时间 {1}": "PID {0} · Created {1}",
 "无已记录运行进程；不代表未知启动窗口已安全": "No recorded running process; unknown launch windows are not established safe",
 "封装提交": "Prepared commit",
 "未完成": "Incomplete",
 "目标分支": "Target branch",
 "证据核验": "Evidence verification",
 "指纹一致": "Fingerprint matches",
 "未完成或证据已变化": "Incomplete or evidence changed",
 "尚无可批准指纹": "No approvable fingerprint yet",
 "封装后完整测试": "Complete tests after preparation",
 "我已审阅封装提交与测试，同意创建上述本地发布分支。": "I reviewed the prepared commit and tests and agree to create the local publication branch above.",
 "批准发布分支": "Approve publication branch",
 "核验并中止中断准备": "Verify and abort interrupted preparation",
 "归档旧准备并重新验证": "Archive previous preparation and revalidate",
 "清理本次候选工作区；保留运行报告与已发布分支。": "Remove this candidate worktree. Retain run reports and published branches.",
 "审批与执行证据": "Approval and execution evidence",
 "描述这个仓库中的问题或失败测试": "Describe an issue or failing test in this repository",
 "允许在启动时授权的文件与测试范围内自主修复？发布仍须另行批准。": "Allow autonomous repair within the files and tests authorized at launch? Publication still requires separate approval.",
 "已连接。请生成方案，开始本地审批流程。": "Connected. Generate a plan to begin the local approval process.",
 "Pi · 仓库维护工作台": "Pi · Repository maintenance workbench",
 "仓库维护工作台": "Repository maintenance workbench",
 "选择仓库": "Choose repository",
 "本机审批会话": "Local approval session",
 "新任务": "New task",
 "让修改有据可查。": "Make changes traceable.",
 "先审方案，再验证候选。发布分支需要你另行批准。": "Review the plan, then validate the candidate. Publishing a branch requires your separate approval.",
 "需要修复的问题": "Issue to repair",
 "本次只检查下方授权文件，尚不支持完整全库扫描。建议描述具体报错、失败测试或预期行为；范围以连接时确认的配置为准。": "Only the authorized files below are checked; full repository scanning is not supported here. Describe errors, failing tests or expected behavior. Scope follows the configuration confirmed at connection time.",
 "生成待审批方案": "Generate plan for approval",
 "在授权范围内自主修复": "Repair autonomously within scope",
 "人工审批每次执行一轮；自主模式仅按启动时授权的范围运行。": "Manual approval executes one round at a time. Autonomous mode stays within the scope authorized at launch.",
 "调整请求总上限": "Adjust total request limit",
 "增加总上限，保留已经计量和消费未知的额度。不会自动运行任务。": "Increase the total limit while retaining metered and unknown consumption. No task runs automatically.",
 "新的总上限": "New total limit",
 "来源记忆": "Source memory",
 "预览此问题的记忆出处": "Preview memory sources for this issue",
 "本次会话": "This session",
 "代码结构出处": "Code structure sources",
 "查找符号或相对路径": "Find a symbol or relative path",
 "仓库经验记忆": "Repository experience memory",
 "工程知识与方法": "Engineering knowledge and procedures",
 "长任务上下文整理": "Long-task context compaction",
 "维护进度与待办": "Maintenance progress and follow-ups",
 "统一记忆查询": "Unified memory query",
 "使用上方问题查找各类出处。查询不调用模型，不改变已选资料或审批。": "Use the issue above to find sources across memory types. Queries call no model and change no selected sources or approvals.",
 "查询相关记忆": "Find related memory",
 "刷新状态": "Refresh status",
 "审阅与操作": "Review and actions",
 "正在连接本地服务…": "Connecting to the local service…",
 "操作记录": "Operation log",
 "此工作台创建隔离候选和本地发布分支。合并与 GitHub 推送仍需单独处理。": "This workbench creates isolated candidates and local publication branches. Merging and GitHub pushes require separate handling."
};
 const bindings=new Set();
 const message=read=>({read,toString(){return String(read());},[Symbol.toPrimitive](){return String(read());}});
 const read=value=>value&&typeof value.read==='function'?read(value.read()):value;
 const t=key=>Object.assign(message(()=>language==='en'?(dictionary[key]??key):key),{source:key});
 const live=read=>message(read);
 function bind(target,value,attribute){
  const update=()=>{const result=String(read(value)??'');if(attribute)target.setAttribute(attribute,result);else target.textContent=result;};
  update();if(value&&typeof value.read==='function')bindings.add({target,update});
 }
 function setText(target,value){
  target.textContent=String(read(value)??'');
  // Register the text node, never its parent: labels can later contain inputs.
  bind(target.firstChild?.nodeType===3?target.firstChild:target,value);
 }
 function attribute(target,name,value){bind(target,value,name);}
 function prune(){for(const entry of bindings)if(entry.target.isConnected===false)bindings.delete(entry);}
 for(const target of root.querySelectorAll?.('[data-i18n]')||[]){
  const source=target.getAttribute('data-i18n');
  const text=[...(target.childNodes||[])].find(node=>node.nodeType===3&&node.textContent.trim()===source.trim());
  if(text){const prefix=text.textContent.match(/^\s*/)[0],suffix=text.textContent.match(/\s*$/)[0];bind(text,live(()=>prefix+read(t(source))+suffix));}
 }
 for(const name of ['aria-label','placeholder','title'])for(const target of root.querySelectorAll?.('[data-i18n-'+name+']')||[])attribute(target,name,t(target.getAttribute('data-i18n-'+name)));
 function textNode(value){const target=document.createTextNode('');bind(target,value);return target;}
 return {t,live,setText,attribute,prune,read,textNode,setLanguage(value){language=value==='en'?'en':'zh-CN';prune();for(const entry of bindings)entry.update();},getLanguage(){return language;},dispose(){bindings.clear();}};
})();
const {t,live,setText}=i18n;
const uiError=message=>Object.assign(new Error(String(i18n.read(message))),{displayMessage:message});
const format=(message,values)=>live(()=>String(i18n.read(message)).replace(/\{(\d+)\}/g,(_,index)=>String(i18n.read(values[index]))));
const access = options.embedded ? null : window.RepoCareTabSession.open('maintenance');
function requestHoldText(budget){
 if(Number.isInteger(budget.requests_reserved_active)&&Number.isInteger(budget.requests_ended_unknown))
  return t(" · 进行中预留：")+budget.requests_reserved_active+t(" · 已结束待核对：")+budget.requests_ended_unknown;
 return t(" · 预留或待核对：")+(budget.requests_reserved_unknown??'—');
}
const token = options.token ?? (access?.token || '');
let state = null, selection = null, pending = false;
let stopping = false, polling = false;
const $ = id => root.querySelector('[data-maintenance-id="'+id+'"], [id="'+id+'"]');
// These fields are local drafts; actions still require their original explicit buttons.
for(const id of ['issue','structure-query'])$(id)?.setAttribute?.('data-form-assist-draft','true');
const labels = {plan:t("生成方案"),decision:t("方案审批"),auto:t("自主修复"),prepare:t("封装并重测"),publish:t("批准发布"),reject_publish:t("拒绝发布"),recover:t("恢复中断"),cleanup:t("清理候选"),candidate_ready:t("候选已验证"),rolled_back:t("失败已回滚"),rejected:t("已拒绝"),discarded:t("候选已清理"),awaiting_approval:t("等待审批"),prepared:t("已封装，等待发布审批"),published:t("已发布本地分支"),consumed:t("方案已处理"),failed:t("操作失败"),running:t("执行中"),finished:t("操作完成"),aborted:t("准备已中止")};
const label = value => labels[value] || String(value || t("未执行"));
const planningError = value => String(value||t("操作未完成")).includes('Pi tool path outside task scope')?
 t("规划的文件范围校验未通过，尚未形成可执行方案。已计量请求保留；请核对下方授权范围，不会自动重试或修改文件。"):String(value||t("操作未完成"));
Object.assign(labels,{stopped:t("已停止"),starting:t("正在准备"),planning:t("正在规划"),reviewing_plan:t("正在核验方案"),creating_workspace:t("正在创建隔离候选"),baseline_testing:t("正在运行基线测试"),editing:t("正在应用批准的修改"),candidate_testing:t("正在验证候选"),replanning:t("根据失败反馈重新规划"),verifying_completion:t("正在核验完成状态"),read_source:t("读取授权源码"),propose_repair:t("提出修复方案")});
function activity(){
 const root=$('activity');root.replaceChildren();
 const job=state.jobs.find(item=>item.id===state.active);
 if(!job)return;
 const el=card(live(()=>t("当前任务 · ")+label(job.action))),progress=job.progress||{};
 el.append(node('p',live(()=>label(progress.phase||'starting')),'status'));
 if(progress.requests_started!==undefined)el.append(node('p',live(()=>t("本轮已观察模型调用：")+progress.requests_started+t(" · 工具调用：")+(progress.tool_calls||0))));
 el.append(node('p',live(()=>t("已计量：")+state.requests_used+requestHoldText(state)+t(" · 可用：")+state.requests_remaining),'small'));
 const latest=(progress.events||[]).filter(event=>event.tool).at(-1);
 if(latest)el.append(node('p',live(()=>t("最近活动：")+label(latest.tool)+(latest.is_error?t("（工具返回错误）"):''))));
 const read=(progress.events||[]).filter(event=>event.path).at(-1);
 if(read)el.append(node('p',live(()=>t("最近读取：")+read.path)));
 const usage=(progress.usage||[]).reduce((sum,item)=>({input:sum.input+(item.input||0),output:sum.output+(item.output||0)}),{input:0,output:0});
 if(progress.usage?.length)el.append(node('p',live(()=>t("本轮模型回执：输入 ")+usage.input+t(" / 输出 ")+usage.output+t(" token，费用未核实")),'small'));
 if(job.stop_requested_at)el.append(node('p',live(()=>t("停止请求已登记。规划会结束本次模型进程；测试会在当前测试结束后停止，清理结果以终态报告为准。"))));
 else if(state.task_control?.can_stop){
  const stop=node('button',live(()=>t("停止当前任务")),'danger');stop.id='stop-task';stop.disabled=stopping;
  stop.onclick=async()=>{
   if(stopping)return;stopping=true;stop.disabled=true;
   try{
    const target=state.task_control.job_id;
    await api('actions/stop_job',{request_id:crypto.randomUUID().replaceAll('-',''),job_id:target,revision:state.task_control.revision});await refresh();
    const outcome=state.jobs.find(item=>item.id===target);
    notice(live(()=>outcome?.status==='stopped'?t("任务已停止，原消费和证据保留。"):outcome?.status==='running'?t("停止请求已登记，正在等待安全收尾。"):t("任务已结束，请查看终态记录。")));
   }
   catch(error){notice(live(()=>error.displayMessage||error.message),true);}
   finally{stopping=false;}
  };el.append(stop);
 }
 root.append(el);
}
labels.integrated=t("已发布本地分支");labels.source_changed=t("源仓库已变化");labels.cleanup_required=t("需要核验后清理");
labels.claim_plan=t("核验并认领方案");labels.claim_run=t("核验并认领运行");labels.interrupted=t("已中断，禁止自动重放");labels.evidence_invalid=t("证据待核验");
labels.inspect_interruption=t("核验中断证据");labels.close_interruption=t("关闭中断登记");labels.closure_available=t("可确认关闭登记");labels.manual_inspection_required=t("需要人工核验");labels.registration_closed=t("登记已关闭，结果未知");labels.resolution_conflict=t("关闭后出现证据变化");
labels.inspect_repair=t("核验修复执行");labels.recover_repair=t("中止中断修复");labels.abort_available=t("可确认中止");labels.repair_aborted=t("修复已中止，候选已移除");
labels.memory_preview=t("预览记忆出处");
labels.structure_preview=t("查询结构出处");
Object.assign(labels,{experience_capture:t("登记此运行的经验"),experience_review:t("审阅历史经验"),experience_search:t("检索历史经验"),experience_settings:t("设置新任务经验检索")});
Object.assign(labels,{knowledge_preview:t("预览经验提炼"),knowledge_propose:t("提炼知识候选"),knowledge_review:t("审阅知识候选"),knowledge_revise:t("建立知识新版本"),knowledge_search:t("检索工程知识")});
Object.assign(labels,{knowledge_refresh_preview:t("预览知识更正"),knowledge_refresh_propose:t("根据新经历生成更正候选")});
Object.assign(labels,{context_settings:t("保存新任务整理设置"),compaction_preview:t("预览上下文整理"),compact_task:t("整理原任务上下文")});
labels.resolve_compaction=t("核验上下文整理登记");
Object.assign(labels,{maintenance_settings:t("设置新任务维护记忆"),maintenance_note:t("保存维护待办"),maintenance_search:t("检索维护待办")});
labels.memory_query=t("查询统一记忆");
labels.retrieval_settings=t("设置本地语义检索");
labels.budget_preview=t("预览额度变化");labels.extend_budget=t("确认扩额");labels.resume_run=t("继续失败任务");labels.stop_service=t("停止本机服务");
labels.recover_feedback=t("恢复已核验失败反馈");
labels.reconcile_feedback_cleanup=t("核验失败记录清理");
const memoryLabels={confirmed:t("已确认"),proposed:t("工程提议"),pending:t("待定"),superseded:t("已替代"),reference:t("参考资料"),historical:t("历史报告声称")};
function node(tag, text, cls){const el=document.createElement(tag);if(text!==undefined)setText(el,text);if(cls)el.className=cls;return el;}
function notice(text,error=false){setText($('notice'),live(()=>text));$('notice').classList.toggle('error',error);}
async function api(path,body){
 const response=await fetch((options.apiPrefix || '/api/')+path,{method:body?'POST':'GET',headers:{Authorization:'Bearer '+token,...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
 const result=await response.json();if(response.status===401)access?.expire();if(!response.ok)throw uiError(live(()=>result.error || t("请求失败")));return result;
}
function button(text,fn,cls='',id){const el=node('button',live(()=>text),cls);if(id)el.id=id;el.disabled=pending||Boolean(state?.active);el.onclick=fn;return el;}
function card(title){const el=node('div',undefined,'card');el.append(node('h2',live(()=>title)));return el;}
function renderExperiences(){
 const target=$('experience-entries'),box=$('experience-controls');if(!target||!box)return;
 target.replaceChildren();box.replaceChildren();const memory=state.experience_memory;
 if(!memory?.settings_revision){box.append(node('p',live(()=>t("此会话没有同仓库经验入口。"))));return;}
 box.append(node('p',live(()=>memory.enabled?t("新任务将检索已接受、证据仍有效且文件/测试策略匹配的经验。"):t("新任务的经验检索尚未启用。任务结束后仍保存待审阅候选。"))));
 box.append(node('p',live(()=>t("设置只影响之后的新任务；旧任务保持原有选择。经验不会批准修改或重置额度。")),'small'));
 const toggle=button(live(()=>memory.enabled?t("停用新任务经验检索"):t("启用新任务经验检索")),()=>act('experience_settings',{enabled:!memory.enabled,revision:memory.settings_revision}));
 toggle.disabled=toggle.disabled||state.runtime.code_changed;box.append(toggle);
 box.append(button(live(()=>t("检索当前问题的历史经验")),()=>act('experience_search',{issue:$('issue').value}),'quiet'));
 const reviews={pending:t("待审阅"),accepted:t("已接受供参考"),rejected:t("已拒绝")};
 const outcomes={candidate_test_passed:t("原隔离候选测试通过（不代表合并）"),test_failed:t("原修复测试失败"),not_verified:t("原尝试未验证")};
 for(const entry of [...memory.entries].reverse()){
  const item=node('details');item.append(node('summary',live(()=>entry.id.slice(0,8)+' · '+(reviews[entry.review]||t("未知审核状态"))+' · '+(entry.freshness==='verified'?t("原证据已核验"):t("原证据失效")))));
  item.append(node('p',live(()=>entry.issue||t("无法核验原问题"))),node('p',live(()=>outcomes[entry.outcome]||t("结果无法核验"))));
  item.append(node('p',live(()=>entry.applicability==='matching_files_and_policy'?t("当前文件与测试策略匹配；内容仍属历史参考。"):entry.applicability==='revalidate_changed_files'?t("当前代码与历史不同；新任务可以参考，但必须读取当前文件重新验证。"):t("当前适用条件不匹配，或原证据无法核验。")),'small'));
  for(const round of entry.rounds||[])item.append(node('p',live(()=>t("第 ")+round.number+t(" 轮：")+round.diagnosis+t("；测试退出码 ")+(round.test_exits.join('、')||t("未运行")))));
  for(const round of entry.rounds||[])if(round.change_excerpt){
   const detail=node('details'),validation=round.validation_summary;
   const result=validation?format(t("原选定检查：{0}/{1} 条命令通过。不代表未覆盖行为正确或已经合并。"),[validation.passed,validation.total]):round.failure_excerpt;
   detail.append(node('summary',live(()=>validation?t("查看原改动与验证摘要"):t("查看原改动与失败摘录"))),node('pre',live(()=>round.change_excerpt)),node('pre',live(()=>result)));item.append(detail);
  }
  if(entry.reason)item.append(node('p',live(()=>entry.reason),'small'));
  const facts=node('dl');field(facts,live(()=>t("原任务")),live(()=>entry.task_id||t("无法核验")));field(facts,live(()=>t("原运行")),live(()=>entry.id));field(facts,live(()=>t("经验指纹")),live(()=>entry.payload_sha256||t("无法核验")));item.append(facts);
  if(entry.review!=='accepted'){
   const accept=button(live(()=>t("接受此经验供参考")),()=>act('experience_review',{run_id:entry.id,revision:entry.revision,review_status:'accepted'}));
   accept.disabled=accept.disabled||entry.freshness!=='verified'||state.runtime.code_changed;item.append(accept);
  }
  if(entry.review!=='rejected'){
   const reject=button(live(()=>entry.review==='accepted'?t("撤销接受此经验"):t("拒绝此经验")),()=>act('experience_review',{run_id:entry.id,revision:entry.revision,review_status:'rejected'}),'quiet');
   reject.disabled=reject.disabled||state.runtime.code_changed;item.append(reject);
  }
   if(entry.review==='accepted'&&entry.freshness==='verified'){
    const reflect=button(live(()=>t("预览提炼知识或方法")),()=>act('knowledge_preview',{experience_ids:[entry.id]}),'quiet');
    reflect.disabled=reflect.disabled||state.runtime.code_changed;item.append(reflect);
   }
  target.append(item);
 }
 if(!memory.entries.length)target.append(node('p',live(()=>t("尚无经验候选。新任务安全结束后自动记录；旧运行可在其详情中明确登记。"))));
 for(const [id,error] of memory.capture_errors||[])target.append(node('p',live(()=>t("经验登记未完成 ")+id.slice(0,8)+'：'+error.reason+t("；原运行结果保留。")),'small'));
 const search=state.jobs.findLast(job=>job.action==='experience_search'&&job.status==='finished')?.result?.experience_search;
 if(search){
  target.append(node('h3',live(()=>t("最近一次经验检索"))));
  for(const hit of search.hits)target.append(node('p',live(()=>hit.id.slice(0,8)+' · '+hit.episode.issue)));
  if(!search.hits.length)target.append(node('p',live(()=>t("没有已接受且适用的匹配经验。"))));
  for(const entry of search.excluded)target.append(node('p',live(()=>t("排除 ")+entry.id.slice(0,8)+'：'+entry.reason),'small'));
 }
}
function renderMaintenance(){
 const box=$('maintenance-controls'),items=$('maintenance-items');if(!box||!items)return;
 const value=state.maintenance_memory;items.replaceChildren();
 const disabled=Boolean(pending||state.active||state.runtime.code_changed||value?.status!=='verified');
 for(const control of box.querySelectorAll('button,input,textarea,select'))control.disabled=disabled;
 if(value?.status!=='verified'){items.append(node('p',live(()=>value?.reason||t("当前仅显示原记录；运行结束后核验维护进度。")),'small'));return;}
 if(!box.firstChild){
  box.append(node('p',live(()=>t("待办是你的工作安排。关闭待办只表示你不再跟进，不代表已修复、测试通过或合并。")),'small'));
  const enabled=node('input');enabled.type='checkbox';enabled.checked=value.enabled;
  const toggle=node('label',live(()=>t("之后的新任务参考相关维护待办")));toggle.prepend(enabled);box.append(toggle);
  box.append(button(live(()=>t("保存新任务设置")),()=>act('maintenance_settings',{enabled:enabled.checked,revision:state.maintenance_memory.expected_revision}),'quiet'));
  const form=node('div');form.dataset.maintenanceForm='true';form.dataset.revision=value.expected_revision;
  const title=node('input');title.maxLength=200;title.dataset.field='title';title.setAttribute('data-form-assist-draft','true');
  const detail=node('textarea');detail.maxLength=1200;detail.rows=3;detail.dataset.field='detail';detail.setAttribute('data-form-assist-draft','true');
  const status=node('select');status.dataset.field='status';status.setAttribute('data-form-assist-draft','true');for(const [key,text] of [['open',t("继续跟进")],['deferred',t("暂缓")],['closed_by_user',t("我已关闭跟进")]]){const opt=node('option',live(()=>text));opt.value=key;status.append(opt);}
  const priority=node('select');priority.dataset.field='priority';priority.setAttribute('data-form-assist-draft','true');for(let n=0;n<=3;n++){const opt=node('option',live(()=>[t("低"),t("普通"),t("优先"),t("最高")][n]));opt.value=String(n);priority.append(opt);}priority.value='1';
  for(const [text,input] of [[t("待办标题"),title],[t("需要跟进什么"),detail],[t("跟进状态"),status],[t("优先级"),priority]]){const label=node('label',live(()=>text));label.append(input);form.append(label);}
  form.append(button(live(()=>t("保存待办")),()=>act('maintenance_note',{note_id:form.dataset.noteId||null,revision:form.dataset.revision,
   title:title.value,detail:detail.value,status:status.value,priority:Number(priority.value)})));
  form.append(button(live(()=>t("重置为新待办")),()=>{delete form.dataset.noteId;form.dataset.revision=state.maintenance_memory.expected_revision;title.value='';detail.value='';status.value='open';priority.value='1';},'quiet'));
  box.append(form);
 }
 const form=box.querySelector('[data-maintenance-form]');
 items.append(node('p',live(()=>t("已登记检查点 ")+value.cursors.length+t("；保留修复尝试 ")+value.fixes.length+t("；待跟进 ")+value.issues.length+t("。检查点仍需原恢复入口核验。")),'small'));
 for(const row of value.observations){const b=row.body;
  if(!['note','run','plan'].includes(b.kind))continue;
  const entry=node('details');entry.append(node('summary',live(()=>b.title)),node('p',live(()=>b.detail),'small'));
  const stage={open:t("继续跟进"),deferred:t("暂缓"),closed_by_user:t("由你关闭跟进"),awaiting_approval:t("方案待审批"),consumed:t("方案已处理"),candidate_test_passed:t("候选测试通过，尚不表示已合并"),test_failed:t("候选测试失败"),not_verified:t("未验证")};
  entry.append(node('p',live(()=>(stage[b.stage]||b.stage)+t("；出处 ")+row.source_ref.kind+':'+row.source_ref.id.slice(0,8)+' / '+row.source_ref.sha256.slice(0,12)),'small'));
  if(b.test_exits.length)entry.append(node('p',live(()=>t("原测试退出码：")+b.test_exits.join('、')),'small'));
  if(b.kind==='note')entry.append(button(live(()=>t("编辑这条待办")),()=>{form.dataset.noteId=row.source_ref.id;form.dataset.revision=value.expected_revision;
   for(const key of ['title','detail','status','priority'])form.querySelector('[data-field="'+key+'"]').value=String(key==='status'?b.stage:b[key]);},'quiet'));
  items.append(entry);
 }
 for(const row of value.excluded||[])items.append(node('p',live(()=>t("原记录暂不可核验：")+row.kind+' '+row.id.slice(0,8)+' · '+row.reason),'small'));
}
function memoryItems(target,items){
 const names={SourceDocumentAdapter:t("文档"),RunCaseAdapter:t("历史案例"),MaintenanceStateAdapter:t("维护进度"),StructureFactAdapter:t("代码结构"),DecisionClaimAdapter:t("关系声明"),KnowledgeEntryAdapter:t("知识与方法")};
 const reviews={accepted:t("已接受参考"),awaiting_review:t("待审阅"),rejected:t("已拒绝")};
 const fresh={valid:t("原资料核验有效"),stale:t("需按当前源码重新核验"),unverified:t("不代表当前代码已验证"),conflict:t("存在待处理冲突")};
 for(const item of items||[]){const box=node('details');box.append(node('summary',live(()=>(names[item.adapter]||item.adapter)+' · '+item.topic)));
  const fields=node('dl');field(fields,live(()=>t("原分类")),live(()=>memoryLabels[item.classification]||item.classification||t("未声明")));
  field(fields,live(()=>t("审阅")),live(()=>reviews[item.review_state]||t("该类资料无单独审阅状态")));field(fields,live(()=>t("当前适用性")),live(()=>fresh[item.freshness]||item.freshness));
  field(fields,live(()=>t("传入本次模型输入")),live(()=>item.in_context?t("已传入，仍只是参考"):t("此处仅为查询结果")));box.append(fields);
  if(item.topic_group?.mixed_decisions)box.append(node('p',live(()=>t("同主题文档存在多个决策分类；此视图不替你选择有效规则。"))));
  box.append(node('pre',live(()=>JSON.stringify(item.summary,null,2))));
  if(item.matched_terms?.length)box.append(node('p',live(()=>t("匹配线索：")+item.matched_terms.join('、')+t("。这是词面匹配，不代表内容已获批准。")),'small'));
  if(item.semantic_cosine!==null&&item.semantic_cosine!==undefined)box.append(node('p',live(()=>t("本地语义相似度：")+Number(item.semantic_cosine).toFixed(3)+t("；相似度不代表事实正确或已获批准。")),'small'));
  for(const ref of item.source_refs||[])box.append(node('p',live(()=>ref.kind+' · '+ref.id+' · '+ref.sha256.slice(0,12)+(ref.path?' · '+ref.path+(ref.start_line?':'+ref.start_line:''):'')),'small'));
  target.append(box);
 }
}
function loadedMemory(target,evidence){
 const load=evidence?.memory_load;if(!load||!Array.isArray(load.items))return;
 const box=node('details');box.append(node('summary',live(()=>t("本次实际传入的记忆清单"))),
  node('p',live(()=>(load.task_type==='commit_inspection'?t("提交巡查"):t("修复任务"))+t("；资料 ")+load.items.length+t(" 项；整理装载清单新增模型请求 0。")),'small'));
 box.append(node('p',live(()=>t("清单证明传入了哪些资料，不证明模型理解、采纳或产生收益。历史批准仍不授权本次修改。")),'small'));
 memoryItems(box,load.items);for(const row of load.excluded||[])box.append(node('p',live(()=>t("未装载：")+row.adapter+' · '+row.reason),'small'));target.append(box);
}
function renderCatalog(){
 const box=$('catalog-result'),query=$('catalog-query');if(!box||!query)return;
 query.disabled=Boolean(pending||state.active||!state.memory_catalog?.adapters||state.runtime.code_changed);
 query.onclick=()=>act('memory_query',{query:$('issue').value,limit:20});box.replaceChildren();
 const retrieval=state.memory_retrieval;
 if(retrieval){
  box.append(node('p',live(()=>retrieval.enabled?t("本地混合检索已开启。"):t("当前使用词面检索。")),'small'));
  box.append(node('p',live(()=>t("本地语义模型只帮助查找参考，不调用收费语言模型。只影响后续查询和新任务；旧任务资料与审批保持原样。")),'small'));
  if(retrieval.available||retrieval.enabled){const enabled=node('input');enabled.type='checkbox';enabled.checked=Boolean(retrieval.enabled);
   enabled.disabled=!retrieval.available&&!retrieval.enabled;
   const label=node('label',live(()=>t("启用本地混合检索")));label.prepend(enabled);box.append(label);
   const save=button(live(()=>t("保存检索设置")),()=>act('retrieval_settings',{enabled:enabled.checked,revision:state.memory_retrieval.revision}),'quiet');
   save.disabled=Boolean(pending||state.active||state.runtime.code_changed||!retrieval.revision);box.append(save);
   box.append(node('p',live(()=>t("已登记模型数据 ")+(retrieval.data_bytes/1024/1024).toFixed(1)+t(" MiB；运行时不自动下载。")),'small'));
  }else box.append(node('p',live(()=>retrieval.reason||t("本次启动未登记本地语义模型。")),'small'));
 }
 const value=state.jobs.findLast(j=>j.action==='memory_query'&&j.status==='finished')?.result?.memory_query;
 if(value){box.append(node('p',live(()=>t("找到 ")+value.returned_items+t(" 项；支持各类资料并列显示。查询不重新装载旧任务。检索方式：")+(value.retrieval?.startsWith('local_')?t("本地混合检索"):t("词面检索"))),'small'));memoryItems(box,value.items);
  const peers=value.topic_peers;
  if(peers?.available_groups){
   const compare=node('details');compare.append(node('summary',live(()=>t("同主题还有 ")+peers.available_peers+t(" 项资料可供对照"))),
    node('p',live(()=>t("同主题不等于内容一致或存在矛盾。请对照原分类、审阅和出处；这里不替你选择有效规则，也不改变任务资料。")),'small'));
   for(const group of peers.groups||[]){const section=node('details');section.append(node('summary',live(()=>group.topic)),
     node('p',live(()=>t("同主题共 ")+group.total_items+t(" 项；主结果已显示 ")+group.in_results+t(" 项，以下补充 ")+group.returned_peers+t(" 项。")),'small'));
    memoryItems(section,group.peers);if(group.omitted_peers)section.append(node('p',live(()=>t("仍有 ")+group.omitted_peers+t(" 项未展开，请缩小查询或分批查看原资料。")),'small'));
    compare.append(section);
   }
   if(peers.omitted_groups)compare.append(node('p',live(()=>t("另有 ")+peers.omitted_groups+t(" 个同主题分组未展开；这不是全部资料。")),'small'));
   box.append(compare);
  }
  for(const row of value.excluded||[])box.append(node('p',live(()=>t("未提供：")+row.adapter+' · '+row.reason),'small'));}
}
function renderContext(){
 const box=$('context-controls'),target=$('context-tasks');if(!box||!target)return;
 box.replaceChildren();target.replaceChildren();const value=state.task_compaction;
 if(!value?.settings){box.append(node('p',live(()=>value?.reason||t("此会话没有原生上下文整理入口。"))));return;}
 box.append(node('p',live(()=>t("原始会话保留。整理只改变后续分析上下文，不执行历史动作，也不会增加已完成规划回合。")),'small'));
 const config=value.settings,enabled=node('input');enabled.type='checkbox';enabled.checked=config.enabled;
 const toggle=node('label',live(()=>t("新任务达到阈值时自动整理")));toggle.prepend(enabled);box.append(toggle);
 box.append(node('p',live(()=>t("设置只影响之后的新任务；旧任务保持原设置。摘要调用使用当前模型和原请求额度，可能产生费用。")),'small'));
 const fields={context_window_tokens:t("模型上下文容量（0表示未知）"),trigger_tokens:t("开始整理的阈值（保守输入估算）"),keep_recent_tokens:t("保留近期原文量"),summary_max_output_tokens:t("摘要输出上限")};
 const inputs={};const advanced=node('details');advanced.append(node('summary',live(()=>t("容量与保留设置"))));
 for(const [key,title] of Object.entries(fields)){const input=node('input');input.type='number';input.min='0';input.step='1';input.value=String(config[key]);input.setAttribute('data-form-assist-draft','true');inputs[key]=input;const label=node('label',live(()=>title));label.append(input);advanced.append(label);}
 advanced.append(node('p',live(()=>t("以上容量、阈值、保留量和输出上限均以 token 计量。容量是当前配置的声明，不是自动探测结果。摘要输出分配独立；修复规划的输出上限与原请求账本保持原设置。")),'small'));box.append(advanced);
 const save=button(live(()=>t("保存为新任务设置")),()=>{const config={schema_version:1,enabled:enabled.checked};for(const [key,input] of Object.entries(inputs))config[key]=Number(input.value);return act('context_settings',{config,revision:value.settings_revision});});
 save.disabled=save.disabled||state.runtime.code_changed;box.append(save);
 for(const task of value.tasks||[]){const item=node('details');item.append(node('summary',live(()=>t("任务 ")+task.task_id.slice(0,8)+' · '+(task.status==='busy'?t("运行中"):task.status==='verified'?t("检查点已核验"):t("当前不可整理")))));
  if(task.reason)item.append(node('p',live(()=>task.reason),'small'));
  if(task.status==='verified'){
   item.append(node('p',live(()=>t("已完成规划回合 ")+task.completed_planning_rounds+t("；保守上下文输入估算 ")+task.context_bytes_estimate+'。'),'small'));
   item.append(node('p',live(()=>task.config?.enabled?t("该任务创建时已允许整理；整理仍需原剩余额度。"):t("该任务没有启用整理，保持原设置。")),'small'));
   const preview=button(live(()=>t("预览整理此任务")),()=>act('compaction_preview',{task_id:task.task_id}),'quiet');preview.disabled=preview.disabled||!task.can_compact||state.runtime.code_changed;item.append(preview);
  }target.append(item);
 }
 const preview=state.jobs.findLast(j=>j.action==='compaction_preview'&&j.status==='finished')?.result?.compaction_preview;
 if(preview){const item=node('details');item.open=true;item.append(node('summary',live(()=>t("确认原任务整理"))),node('p',live(()=>t("任务 ")+preview.task_id.slice(0,8)+t("；共享剩余额度 ")+preview.budget.requests_remaining+t("；至少预留两次请求以覆盖原生摘要可能的分段。"))));
  const current=(value.tasks||[]).find(task=>task.task_id===preview.task_id);
  const fresh=current?.status==='verified'&&current.can_compact&&current.revision===preview.revision;
  if(!fresh)item.append(node('p',live(()=>t("此预览已不适用于当前检查点或额度；请重新预览当前任务。")),'small'));
  const start=button(live(()=>t("确认消耗原额度整理上下文")),()=>act('compact_task',{task_id:preview.task_id,revision:preview.revision}));start.disabled=start.disabled||!preview.can_compact||!fresh||state.runtime.code_changed;item.append(start);target.append(item);}
 for(const pendingIntent of value.pending||[]){const item=node('details');item.append(node('summary',live(()=>t("待核验整理 · ")+pendingIntent.task_id.slice(0,8))),
  node('p',live(()=>t("只补已核验的元数据，或明确保留旧检查点；不会重发摘要请求，也不清除未知消费。")),'small'));
  const reconcile=button(live(()=>t("核验已完成摘要并补登记")),()=>act('resolve_compaction',{intent_id:pendingIntent.id,revision:pendingIntent.revision,choice:'reconcile'}));
  reconcile.disabled=reconcile.disabled||!pendingIntent.can_reconcile||state.runtime.code_changed;item.append(reconcile);
  const abandon=button(live(()=>t("保留旧检查点并放弃此整理")),()=>act('resolve_compaction',{intent_id:pendingIntent.id,revision:pendingIntent.revision,choice:'abandon'}),'quiet');
  abandon.disabled=abandon.disabled||state.runtime.code_changed;item.append(abandon);target.append(item);}
 target.append(node('p',live(()=>t("已登记整理次数：")+(value.compactions?.length||0)+t("。不计作修复成功次数。")),'small'));
}

function renderKnowledge(){
 const box=$('knowledge-controls'),target=$('knowledge-entries');if(!box||!target)return;
 box.replaceChildren();target.replaceChildren();const memory=state.knowledge_memory;
 box.append(node('p',live(()=>t("从已接受的经历提炼候选，审阅后才供新任务参考。提炼使用当前模型和原请求额度，可能产生费用。")),'small'));
 box.append(node('p',live(()=>t("接受候选不会把推测升级为已确认规则；当前源码、测试和新审批仍须独立核验。")),'small'));
 box.append(button(live(()=>t("检索当前问题的工程知识")),()=>act('knowledge_search',{issue:$('issue').value}),'quiet'));
 const preview=state.jobs.findLast(j=>j.action==='knowledge_preview'&&j.status==='finished')?.result?.knowledge_preview;
 if(preview){const area=node('details');area.open=true;area.append(node('summary',live(()=>t("确认提炼范围与消耗"))),
  node('p',live(()=>t("来源经历：")+preview.experience_ids.map(i=>i.slice(0,8)).join('、')+t("；共享剩余额度 ")+preview.remaining_requests+t("。只生成待审阅候选。"))));
  const start=button(live(()=>t("确认用现有额度提炼候选")),()=>act('knowledge_propose',{experience_ids:preview.experience_ids,revision:preview.revision}));
  start.disabled=start.disabled||state.runtime.code_changed;area.append(start);box.append(area);}
 const names={triggers:t("触发条件"),preconditions:t("前提"),steps:t("建议步骤"),verification:t("新验证要求"),stop_conditions:t("停止条件")};
 const correction=state.jobs.findLast(j=>j.action==='knowledge_refresh_preview'&&j.status==='finished')?.result?.knowledge_refresh_preview;
 if(correction){const area=node('details');area.open=true;area.append(node('summary',live(()=>t("确认按新经历更正旧知识"))),
  node('p',live(()=>t("旧版本：")+correction.knowledge_id.slice(0,8)+t("；新来源：")+correction.experience_ids.map(i=>i.slice(0,8)).join('、')+t("；共享剩余额度 ")+correction.remaining_requests+t("。新候选必须单独审阅。"))));
  const start=button(live(()=>t("确认使用现有额度生成更正候选")),()=>act('knowledge_refresh_propose',{knowledge_id:correction.knowledge_id,experience_ids:correction.experience_ids,revision:correction.revision}));
  start.disabled=start.disabled||state.runtime.code_changed;area.append(start);box.append(area);}
 for(const entry of [...(memory?.entries||[])].reverse()){
  const item=node('details');item.append(node('summary',live(()=>(entry.note?.title||entry.id.slice(0,8))+' · '+({pending:t("待审阅"),accepted:t("已接受供参考"),rejected:t("已拒绝")}[entry.review]||t("未知")))));
  item.append(node('p',live(()=>t("分类：工程提议；")+(entry.lifecycle==='superseded'?t("已被明确新版本替代"):entry.freshness==='verified'?t("原证据已核验"):t("原证据失效")))));
  if(entry.note){item.append(node('p',live(()=>entry.note.summary)));
   for(const [key,label] of Object.entries(names)){item.append(node('h3',live(()=>label)));const list=node('ul');for(const text of entry.note[key])list.append(node('li',live(()=>text)));item.append(list);}
   item.append(node('p',live(()=>t("原经历：")+entry.refs.map(r=>r.id.slice(0,8)).join('、')+'；'+(entry.applicability==='requires_revalidation'?t("当前代码已变化，必须重验"):t("仍须当前独立验证"))),'small'));
  }
  if(entry.reason)item.append(node('p',live(()=>entry.reason),'error'));
  if(entry.can_refresh&&!state.runtime.code_changed){const area=node('details');area.append(node('summary',live(()=>t("选择新经历，让模型建立更正版本"))),
   node('p',live(()=>t("勾选1至3条已接受且出处有效的经历。旧资料保留；旧审阅不适用于新候选。")),'small'));const choices=[];
   for(const episode of state.experience_memory?.entries||[]){if(episode.review!=='accepted'||episode.freshness!=='verified'||episode.applicability==='changed')continue;
    const input=node('input');input.type='checkbox';input.value=episode.id;choices.push(input);const label=node('label');label.append(input,i18n.textNode(live(()=>episode.issue+' · '+episode.id.slice(0,8))));area.append(label);}
   area.append(button(live(()=>t("预览更正来源与消耗")),()=>act('knowledge_refresh_preview',{knowledge_id:entry.id,experience_ids:choices.filter(i=>i.checked).map(i=>i.value)}),'quiet'));item.append(area);}
  const canUse=entry.freshness==='verified'&&entry.lifecycle==='active'&&!state.runtime.code_changed;
  if(entry.review!=='accepted'){const accept=button(live(()=>t("接受此版本供参考")),()=>act('knowledge_review',{knowledge_id:entry.id,revision:entry.revision,review_status:'accepted'}));accept.disabled=accept.disabled||!canUse;item.append(accept);}
  if(entry.review!=='rejected')item.append(button(live(()=>entry.review==='accepted'?t("撤销接受此版本"):t("拒绝此版本")),()=>act('knowledge_review',{knowledge_id:entry.id,revision:entry.revision,review_status:'rejected'}),'quiet'));
  if(entry.note&&canUse){const editor=node('details');editor.append(node('summary',live(()=>t("修订为新版本（保留旧版本）"))));
   const inputs={};const type=node('select');for(const [value,label] of [['procedure',t("排查方法")],['knowledge',t("工程知识")]]){const opt=node('option',live(()=>label));opt.value=value;type.append(opt);}type.value=entry.note.kind;i18n.attribute(type,"aria-label",live(()=>t("知识类型草稿")));type.setAttribute('data-form-assist-draft','true');editor.append(type);
   for(const [key,label] of Object.entries({title:t("标题"),summary:t("内容"),...names})){const input=node('textarea');input.value=Array.isArray(entry.note[key])?entry.note[key].join('\n'):entry.note[key];input.rows=key==='title'?2:4;inputs[key]=input;input.setAttribute('data-form-assist-draft','true');const labelEl=node('label',live(()=>label+(names[key]?t("（每行一项）"):'')));labelEl.append(input);editor.append(labelEl);}
   editor.append(button(live(()=>t("保存为待审阅新版本")),()=>{const candidate={kind:type.value};for(const [key,input] of Object.entries(inputs))candidate[key]=names[key]?input.value.split('\n').map(v=>v.trim()).filter(Boolean):input.value.trim();return act('knowledge_revise',{knowledge_id:entry.id,revision:entry.revision,candidate});},'quiet'));item.append(editor);}
  target.append(item);
 }
 if(!memory?.entries?.length)target.append(node('p',live(()=>t("尚无知识候选。先在仓库经验中选择已接受的经历，预览后明确启动提炼。"))));
 const result=state.jobs.findLast(j=>j.action==='knowledge_search'&&j.status==='finished')?.result?.knowledge_search;
 if(result){target.append(node('h3',live(()=>t("最近一次知识检索"))));for(const hit of result.hits)target.append(node('p',live(()=>hit.knowledge.note.title)));if(!result.hits.length)target.append(node('p',live(()=>t("没有已接受且适用的匹配知识。"))));for(const item of result.excluded)target.append(node('p',live(()=>t("排除 ")+item.id.slice(0,8)+'：'+item.reason),'small'));}
}
function recoverySummary(){
 const target=$('task-recovery');if(!target)return;target.replaceChildren();
 const value=(state.recovery_cards||[]).find(c=>c.kind===selection?.kind&&c.id===selection?.id);if(!value)return;
 const titles={available:t("可以继续此任务"),review_plan:t("先审阅新方案"),plan_completed:t("原方案已处理"),cleanup_pending:t("先核验清理登记"),feedback_pending:t("先恢复失败反馈"),candidate_pending:t("候选已通过，等待审阅"),repair_pending:t("执行中断，先核验现场"),unknown:t("消费未知，先查看证据"),budget_exhausted:t("可用额度不足"),source_invalid:t("必要来源已失效"),runtime_changed:t("服务需要安全重启"),busy:t("任务正在执行"),missing:t("没有完整规划检查点"),not_bound:t("旧记录没有同任务会话"),blocked:t("恢复已阻断"),registration_pending:t("任务检查点缺少运行登记")};
 const el=card(live(()=>t("任务恢复")));el.classList.add('recovery-card');el.append(node('p',live(()=>titles[value.status]||t("请核验当前记录")),'status'));
 const facts=node('dl');field(facts,live(()=>t("所属仓库")),live(()=>state.repository.split(/[\\/]/).at(-1)));field(facts,live(()=>t("任务 ID")),live(()=>value.task.id||t("未绑定；不能作为同任务记忆恢复")));
 field(facts,live(()=>t("已完成规划")),live(()=>value.task.completed_rounds===null?t("无法核验"):value.task.completed_rounds+t(" 回合")+(value.task.status==='verified'?'':t("（原登记，当前会话未核验）"))));
 const checkpoints={verified:t("已核验已提交检查点"),busy:t("执行中，尚未核验当前会话"),missing:t("尚无完整检查点"),not_bound:t("未绑定"),blocked:t("无法核验")};
 field(facts,live(()=>t("会话检查点")),live(()=>checkpoints[value.task.status]||t("无法核验")));
 const b=value.budget;field(facts,live(()=>t("原请求账本")),live(()=>t("总上限 ")+b.request_budget+t(" · 已用 ")+b.requests_used+t(" · 未知保留 ")+b.requests_reserved_unknown+t(" · 剩余 ")+b.requests_remaining));
 const source=s=>s==='verified'?t("已核验"):s==='invalid'?t("已失效"):t("未启用");field(facts,live(()=>t("必要来源")),live(()=>t("文档/案例：")+source(value.sources.memory)+t("；代码结构：")+source(value.sources.structure)));
 const contextTask=(state.task_compaction?.tasks||[]).find(task=>task.task_id===value.task.id);
 let compression=t("尚未核验；请查看长任务上下文整理");
 if(!value.task.id)compression=t("未绑定原任务，无法核验");
 else if(value.task.status==='verified'&&contextTask?.status==='verified'){
  compression=contextTask.config?(contextTask.config.enabled?t("已允许（沿用原任务设置和原额度）"):t("未启用（原任务设置）")):t("旧任务未配置；不会自动启用");
 }
 field(facts,live(()=>t("上下文压缩")),live(()=>compression));el.append(facts);
 if(value.task.tail_uncommitted)el.append(node('p',live(()=>t("原会话含未提交尾部；继续只使用已提交检查点，不重放尾部。")),'small'));
 const failure=value.latest_failure;
 if(failure?.status==='verified'){
  el.append(node('h3',live(()=>t("最近失败"))),node('p',live(()=>t("运行 ")+failure.run_id.slice(0,8)+t(" · 修复第 ")+failure.failed_round+t(" 轮 · 测试退出码 ")+failure.test_exits.join('、'))));
  const details=node('details');details.append(node('summary',live(()=>t("查看原失败摘要"))),node('p',live(()=>failure.diagnosis)),node('pre',live(()=>failure.excerpt)));el.append(details);
 }else if(failure?.status==='sealed'){el.append(node('h3',live(()=>t("最近失败"))),node('p',live(()=>t("运行 ")+failure.run_id.slice(0,8)+t(" · 修复第 ")+failure.failed_round+t(" 轮 · 测试退出码 ")+failure.test_exits.join('、')+t("；已封存，反馈登记待恢复。"))));}
 else if(failure){el.append(node('p',live(()=>t("存在原失败记录，但当前不能核验其反馈。下方保留原恢复入口。")),'small'));}
 else el.append(node('p',live(()=>t("暂无已核验的失败反馈；不从自由日志补造历史。")),'small'));
 const help={plan_completed:t("下一步：在左侧选择这份方案对应的原运行，查看结果与恢复入口。"),available:t("下一步：生成同任务新方案，再重新审批。继续会使用剩余额度。"),review_plan:t("下一步：在下方核验认领历史方案，再审批当前补丁。旧批准不会继承。"),cleanup_pending:t("下一步：核验并完成清理登记；只登记元数据，不运行模型或测试。"),feedback_pending:t("下一步：核验并恢复已封存的失败反馈；不运行模型或测试。"),candidate_pending:t("下一步：审阅已验证候选；发布仍需独立审批。先处理当前候选，才能再继续规划。"),repair_pending:t("下一步：使用下方“核验修复执行”；未知状态先保留现场。"),unknown:t("下一步：查看操作记录中的原调用证据。未知消费不能靠刷新或重启清零。"),budget_exhausted:t("下一步：审阅“调整请求总上限”，确认后才可发起新规划。"),source_invalid:t("下一步：恢复并核验连接时的必要来源，不提供忽略来源的按钮。"),runtime_changed:t("下一步：使用原启动方式和原账本安全重启服务，不换目录重置额度。"),busy:t("等待当前操作安全结束；刷新只查看状态。"),missing:t("本次尚未保存完整规划回合。请核验原尝试；不猜测已完成内容。"),not_bound:t("此历史记录没有可恢复的 Pi 任务绑定；新问题应从新任务入口开始。"),blocked:t("下一步：查看下方原始证据与核验入口。")};
 el.append(node('p',live(()=>help[value.status]||help.blocked),'recovery-next'));
 const diagnostic=node('details');diagnostic.append(node('summary',live(()=>t("查看具体阻止原因与协议说明"))),node('p',live(()=>value.reason||t("当前展示不授予执行权限。"))));if(value.task.reason)diagnostic.append(node('p',live(()=>value.task.reason)));if(failure?.reason)diagnostic.append(node('p',live(()=>failure.reason)));el.append(diagnostic);
 for(const operation of value.actions){
  const payload={run_id:value.id,revision:operation.revision};let text,id;
  if(operation.action==='resume_run'){payload.mode=operation.mode;text=operation.mode==='auto'?t("在原授权范围内继续修复"):t("继续此任务，先生成方案");id=operation.mode==='auto'?'resume-auto':'resume-plan';}
  else if(operation.action==='recover_feedback'){text=t("核验并恢复失败反馈");id='recover-feedback';}
  else if(operation.action==='reconcile_feedback_cleanup'){text=t("核验并完成清理登记");id='reconcile-feedback-cleanup';}
  else continue;
  el.append(button(live(()=>text),()=>{if(operation.mode!=='auto'||confirm(t("使用原剩余额度，在原自主授权范围内继续同一任务？")))act(operation.action,payload);},'primary',id));
 }
 el.append(node('p',live(()=>t("恢复与刷新不等于批准；每份新方案仍需重新审批，发布也需另行批准。")),'small'));target.append(el);
}
function field(container,title,value){container.append(node('dt',live(()=>title)),node('dd',live(()=>value)));}
function diff(container,text){
 const el=node('div',undefined,'diff');container.append(el);
 if(!text){el.append(node('p',live(()=>t("暂无修改"))));return;}
 if(text.length>300000||!window.Diff2Html){el.append(node('pre',live(()=>text)));return;}
 try{
  const template=document.createElement('template');template.innerHTML=Diff2Html.html(text,{drawFileList:false,outputFormat:'side-by-side',matching:'none',diffMaxChanges:3000,diffMaxLineLength:10000});
  const tags=new Set(['DIV','SPAN','TABLE','TBODY','THEAD','TR','TD','TH','PRE','CODE','INS','DEL','A']);
  for(const item of [...template.content.querySelectorAll('*')]){
   if(!tags.has(item.tagName)){item.replaceWith(i18n.textNode(live(()=>item.textContent)));continue;}
   for(const attr of [...item.attributes])if(!['class','id','title','colspan','rowspan'].includes(attr.name))item.removeAttribute(attr.name);
  }
  el.append(template.content);
 }catch{el.append(node('pre',live(()=>text)));}
}
function tests(container,title,values){
 const section=node('details');section.append(node('summary',live(()=>title+' · '+values.length+t(" 条结果"))));
 for(const item of values){section.append(node('pre',live(()=>JSON.stringify(item,null,2))));}
 container.append(section);
}
function memoryCitations(container,evidence){
 const values=evidence?.memory_citations||evidence?.citations||[];
 const el=node('details',undefined,'memory-citations');
 el.append(node('summary',live(()=>t("记忆出处 · ")+values.length+t(" 条"))));
 el.append(node('p',live(()=>t("来源是只读参考；历史批准不授权当前任务。出处回执不证明模型判断正确。")),'small'));
 for(const item of values){
  el.append(node('p',live(()=>item.id+' · '+(memoryLabels[item.classification]||item.classification)+' · '+item.path+':'+item.start_line+'–'+item.end_line)));
  memoryOrigin(el,item);
  el.append(node('div',live(()=>t("原文 SHA256：")+item.source_sha256),'fingerprint'));
 }
 const receipt=evidence?.memory_context_sha256||evidence?.context_sha256;
 if(receipt)el.append(node('div',live(()=>t("上下文回执：")+receipt),'fingerprint'));
 container.append(el);
}
function memoryOrigin(container,item){
 if(!item.origin)return;
 const origin=item.origin;
 container.append(node('p',live(()=>t("集合来源：")+origin.member_id+t(" · 原条目：")+origin.entry_id+(origin.run_id?t(" · 历史运行：")+origin.run_id:'')),'small'));
 container.append(node('div',live(()=>t("原清单 SHA256：")+origin.catalog_sha256),'fingerprint'));
}
function memoryRelations(container,value){
 if(!value?.enabled)return;
 const el=node('details',undefined,'decision-relations');
 el.append(node('summary',live(()=>t("决策替代声明 · ")+value.relations.length+t(" 条"))));
 el.append(node('p',live(()=>t("仅展示原文声明，保留双方原状态；声明不授予批准，不自动选择有效规则。")),'small'));
 el.append(node('div',live(()=>t("关系文件 SHA256：")+value.manifest_sha256),'fingerprint'));
 for(const relation of value.relations){
  el.append(node('p',live(()=>relation.id+' · '+relation.newer.id+' → '+relation.older.id)));
  el.append(node('p',live(()=>t("原文声明：Supersedes: ")+relation.newer.id+' -> '+relation.older.id+t(" · 第 ")+relation.statement_line+t(" 行")),'small'));
  el.append(node('div',live(()=>t("声明行 SHA256：")+relation.statement_sha256),'fingerprint'));
  for(const [role,item] of [[t("新条目"),relation.newer],[t("旧条目"),relation.older]]){
   el.append(node('p',live(()=>role+' '+item.id+' · '+(memoryLabels[item.classification]||item.classification)+' · '+item.path+':'+item.start_line+'–'+item.end_line),'small'));
   memoryOrigin(el,item);el.append(node('div',live(()=>t("原文 SHA256：")+item.source_sha256),'fingerprint'));
  }
 }
 container.append(el);
}
function renderMemory(){
 const memory=state.source_memory||{enabled:false};
 const el=$('memory-configuration');el.replaceChildren();
 $('memory-preview').hidden=!memory.enabled;
 $('memory-preview').disabled=pending||Boolean(state.active)||memory.status!=='verified';
 if(!memory.enabled){el.append(node('p',live(()=>t("本次未启用来源记忆。"))));return;}
 el.append(node('p',live(()=>memory.status==='verified'?t("来源与索引已核验"):t("来源已失效或无法核验；已阻止新规划与旧方案审批。")),memory.status==='verified'?'small':'error'));
 el.append(node('div',live(()=>t("来源：")+memory.binding.source_root+t("\n索引：")+memory.binding.index),'configuration'));
 if(memory.error)el.append(node('p',live(()=>memory.error),'error'));
 if(memory.binding.decision_relations?.model_context)el.append(node('p',live(()=>t("本次 Pi 规划会读取经核验的替代声明；原分类保留，声明不授予审批。")),'small'));
 if(memory.mixed_topics?.length)el.append(node('p',live(()=>t("存在多种决策状态，相关问题需先审阅：")+memory.mixed_topics.join('、')),'error'));
 for(const item of memory.sources||[]){
  el.append(node('p',live(()=>item.id+' · '+(memoryLabels[item.classification]||item.classification)+' · '+item.path+':'+item.start_line+'–'+item.end_line),'small'));
  memoryOrigin(el,item);
 }
 memoryRelations(el,memory.decision_relations);
 const preview=$('memory-preview-result');preview.replaceChildren();
 const job=[...state.jobs].reverse().find(item=>item.action==='memory_preview'&&item.result?.memory_preview);
 if(job){const value=job.result.memory_preview;
  preview.append(node('p',live(()=>memory.status==='verified'?t("上次预览；执行前重新检索核验。"):t("上次预览已过期，不能用于授权。")),'small'));
  if(value.mixed_topics.length)preview.append(node('p',live(()=>t("状态混合，阻止注入：")+value.mixed_topics.join('、')),'error'));
  memoryCitations(preview,value);
  memoryRelations(preview,value.decision_relations);
 }
}
function interruption(run){
 const el=card(live(()=>t("中断登记 ")+run.run_id.slice(0,8)));
 el.append(node('p',live(()=>t("核验方案消费记录、源快照、运行文件和 Git 登记。关闭登记后，执行结果仍记为未知；后续修复需生成新方案并重新批准。"))));
 el.append(button(live(()=>t("核验中断证据")),()=>act('inspect_interruption',{run_id:run.run_id}),'primary','inspect-interruption'));
 const preview=run.reconciliation;
 if(preview){
  el.append(node('p',live(()=>label(preview.status)),'status'));
  const checks=preview.checks,info=node('dl');
  field(info,live(()=>t("源快照一致")),live(()=>String(checks.source_matches)));field(info,live(()=>t("原方案证据一致")),live(()=>String(checks.plan_matches??t("未知"))));
  field(info,live(()=>t("运行目录存在")),live(()=>String(checks.directory_exists)));field(info,live(()=>t("运行记录")),live(()=>checks.run_evidence));
  field(info,live(()=>t("候选分支")),live(()=>checks.candidate_ref||t("无登记")));field(info,live(()=>t("发布分支")),live(()=>checks.publication_ref||t("无登记")));
  field(info,live(()=>t("相关工作区登记")),live(()=>String(checks.worktree_registrations?.length??t("未知"))));el.append(info);
  const details=node('details');details.append(node('summary',live(()=>t("核验详情与阻止原因"))),node('pre',live(()=>JSON.stringify(preview,null,2))));el.append(details);
  if(preview.can_close_registration&&!run.resolution){
   const confirmation=node('label',undefined,'confirm'),check=node('input');check.type='checkbox';check.id='confirm-close-interruption';
   confirmation.append(check,node('span',live(()=>t("我已核验上述证据，同意仅关闭本次中断登记并保留原方案的已消费状态。"))));el.append(confirmation);
   const close=button(live(()=>t("确认关闭中断登记")),()=>{if(check.checked)act('close_interruption',{run_id:run.run_id,revision:preview.revision});},'danger','close-interruption');
   close.disabled=true;check.onchange=()=>{close.disabled=!check.checked||pending||Boolean(state.active);};el.append(close);
  }
 }else el.append(node('p',live(()=>t("先核验当前证据；历史预览不能直接批准关闭。"))));
 if(run.resolution){const audit=node('details');audit.append(node('summary',live(()=>t("登记关闭记录"))),node('pre',live(()=>JSON.stringify(run.resolution,null,2))));el.append(audit);}
 $('content').append(el);
}
async function act(action,payload){
 if(pending||state?.active)return;
 pending=true;render();notice(live(()=>label(action)+t("正在执行…")));
 try{
  const id=crypto.randomUUID().replaceAll('-','');
  const job=await api('actions/'+action,{...payload,request_id:id});
  await refresh();
  // Poll only our local operation state. Never retry a mutation or request model status.
  while(state.jobs.find(j=>j.id===job.id)?.status==='running'){
   await new Promise(resolve=>setTimeout(resolve,800));await refresh();
  }
  const done=state.jobs.find(j=>j.id===job.id);
  if(done?.status==='failed')throw uiError(live(()=>planningError(done.error)));
  if(done?.result?.plan_id)selection={kind:'plan',id:done.result.plan_id};
  if(done?.result?.run_id)selection={kind:'run',id:done.result.run_id};
  const workflowStatus=done?.result?.workflow_status;
  if(done?.status==='stopped'){
   const needsReview=['cleanup_required','source_changed','interrupted'].includes(workflowStatus);
   notice(live(()=>t("任务已停止，原消费和证据保留。")+(workflowStatus?t("收尾结果：")+label(workflowStatus)+'。':'')),needsReview);return;
  }
  notice(live(()=>label(action)+t("完成")+(workflowStatus?'：'+label(workflowStatus):'')),['failed','rolled_back','cleanup_required','source_changed','interrupted','evidence_invalid'].includes(workflowStatus));
  if(action==='maintenance_note'&&done?.result?.note_id){const form=$('maintenance-controls')?.querySelector('[data-maintenance-form]');
   if(form){form.dataset.noteId=done.result.note_id;form.dataset.revision=state.maintenance_memory.expected_revision;}}
  return done;
 }catch(error){notice(live(()=>error.displayMessage||error.message),true);return null;}
 finally{pending=false;render();options.onState?.(state);}
}
function repairInterruption(run){
 const el=card(live(()=>t("修复执行核验 ")+run.run_id.slice(0,8)));
 el.append(node('p',live(()=>t("执行尚未完成。先核验进程、候选和源仓库；只有条件满足才允许明确中止。证据未知或有人工修改时保留现场。"))));
 el.append(button(live(()=>t("核验修复执行")),()=>act('inspect_repair',{run_id:run.run_id}),'primary','inspect-repair'));
 const preview=run.repair_inspection;
 if(preview){
  el.append(node('p',live(()=>label(preview.status)),'status'));
  const details=node('details');details.append(node('summary',live(()=>t("进程、工作区核验与阻止原因"))),node('pre',live(()=>JSON.stringify(preview,null,2))));el.append(details);
  if(preview.can_abort){
   const confirmation=node('label',undefined,'confirm'),check=node('input');check.type='checkbox';check.id='confirm-abort-repair';
   confirmation.append(check,node('span',live(()=>t("我已审阅当前证据，同意中止本次修复并移除身份和内容未变的候选；保留报告和原模型额度。"))));el.append(confirmation);
   const abort=button(live(()=>t("确认中止修复")),()=>{if(check.checked)act('recover_repair',{run_id:run.run_id,revision:preview.session_revision});},'danger','abort-repair');
   abort.disabled=true;check.onchange=()=>{abort.disabled=!check.checked||pending||Boolean(state.active);};el.append(abort);
  }
 }
 const facts=node('details');facts.append(node('summary',live(()=>t("已记录执行事实"))),node('pre',live(()=>JSON.stringify(run.repair_execution||{},null,2))));el.append(facts);$('content').append(el);
}
function structureReceipt(container,binding){
 container.append(node('div',live(()=>t("结构目录身份 SHA256：")+binding.path_sha256+t("\n清单文件 SHA256：")+binding.manifest_file_sha256+t("\n图 SHA256：")+binding.graph_sha256),'fingerprint'));
 if(binding.model_context)container.append(node('div',live(()=>t("固定模型查询 SHA256：")+binding.model_context.query_sha256),'fingerprint'));
}
function renderStructure(){
 const value=state.structure_index||{enabled:false},el=$('structure-configuration');el.replaceChildren();
 $('structure-preview').hidden=!value.enabled;
 $('structure-preview').disabled=pending||Boolean(state.active)||value.status!=='verified';
 const preview=$('structure-preview-result');preview.replaceChildren();
 if(!value.enabled){el.append(node('p',live(()=>t("本次未启用代码结构来源。"))));return;}
 el.append(node('p',live(()=>value.status==='verified'?t("代码结构来源已核验"):t("代码结构来源已失效；已阻止新规划、认领与审批。")),value.status==='verified'?'small':'error'));
 el.append(node('p',live(()=>value.model_context?t("本次 Pi 读取明确查询的已核验结构元数据；原索引基线可能不同于候选，结构不授予审批。"):t("只展示所选源码的结构关系，不代表完整依赖或运行安全。本次不向模型传递结构图。")),'small'));
 structureReceipt(el,value.binding);
 if(value.error)el.append(node('p',live(()=>value.error),'error'));
 if(value.status==='verified'){
  el.append(node('p',live(()=>value.engine.name+' '+value.engine.version+' · '+value.nodes+t(" 节点 / ")+value.edges+t(" 关系")),'small'));
  for(const file of value.files)el.append(node('p',live(()=>file.path+' · '+file.lines+t(" 行\n源码 SHA256：")+file.source_sha256),'fingerprint'));
 }
 const job=[...state.jobs].reverse().find(j=>j.action==='structure_preview'&&j.result?.structure_preview);
 if(!job)return;
 const result=job.result.structure_preview;
 preview.append(node('p',live(()=>value.status==='verified'?t("上次结构查询；操作前重新核验。"):t("上次结构查询已过期，不能用于授权。")),'small'));
 const symbols=new Map([...result.hits,...result.related_nodes].map(n=>[n.id,n]));
 const details=node('details');details.className='structure-citations';details.append(node('summary',live(()=>t("符号与关系出处"))));
 for(const symbol of symbols.values())details.append(node('p',live(()=>symbol.name+' · '+symbol.kind+' · '+symbol.path+':'+symbol.start_line+'–'+symbol.end_line+t("\n源码 SHA256：")+symbol.source_sha256),'fingerprint'));
 for(const edge of result.edges){const a=symbols.get(edge.source),b=symbols.get(edge.target);
  details.append(node('p',live(()=>a.name+' → '+b.name+' · '+edge.kind+'\n'+a.path+':'+a.start_line+' → '+b.path+':'+b.start_line),'small'));
 }
 if(!result.hits.length)details.append(node('p',live(()=>t("限定范围内无字面匹配；不能推定仓库不存在该符号。")),'small'));
 details.append(node('div',live(()=>t("查询回执 SHA256：")+result.context_sha256),'fingerprint'));preview.append(details);
}
function render(){
 if(!state)return;
 activity();
 $('plan').disabled=pending||Boolean(state.active)||state.source_memory?.status==='invalid'||state.structure_index?.status==='invalid';$('auto').hidden=!state.allow_auto;$('auto').disabled=$('plan').disabled;
 setText($('configuration'),live(()=>t("执行器：")+state.provider+t("\n授权文件：")+state.policy.allowed_paths.join('、')+t("\n已计量模型请求：")+state.requests_used+'/'+state.request_budget+requestHoldText(state)+t(" · 剩余额度：")+state.requests_remaining+t("\n仓库：")+state.repository));
 const runtime=state.runtime;
 setText($('service-version'),live(()=>runtime?t("服务版本：")+runtime.build_sha256.slice(0,12)+t("\n启动时间：")+runtime.started_at+t("\n单次输出上限：")+runtime.max_output_tokens+t(" token · 每次规划：")+runtime.planning_timeout_seconds/60+t("分钟")+(runtime.code_changed?t("\n代码已更新，请使用原账本重启服务。"):''):t("旧服务尚未提供版本信息，请重启升级。")));
 if(!runtime||runtime.code_changed||(state.request_budget>0&&state.requests_remaining<=0)){$('plan').disabled=true;$('auto').disabled=true;}
 $('budget-panel').hidden=!state.request_budget;
 $('budget-preview').disabled=pending||Boolean(state.active)||!runtime||runtime.code_changed;
 const budgetBox=$('budget-preview-result');budgetBox.replaceChildren();
 const preview=[...state.jobs].reverse().find(job=>job.action==='budget_preview'&&job.result?.budget_preview)?.result.budget_preview;
 if(preview){
  budgetBox.append(node('p',live(()=>t("总上限 ")+preview.old_total+' → '+preview.new_total+t("；新增 ")+preview.added_requests+t(" 次；保留已计量 ")+preview.requests_used+t(" 次、预留或待核对 ")+preview.requests_reserved_unknown+t(" 次；变更后可用 ")+preview.new_remaining+t(" 次。"))));
  const extend=button(live(()=>t("确认增加总上限")),()=>act('extend_budget',{new_total:preview.new_total,revision:preview.revision}),'','extend-budget');
  extend.disabled=extend.disabled||!runtime||runtime.code_changed||preview.old_total!==state.request_budget||preview.requests_used!==state.requests_used||preview.requests_reserved_unknown!==state.requests_reserved_unknown;
  budgetBox.append(extend);
 }
 renderMemory();
 renderExperiences();
  renderKnowledge();
  renderContext();
  renderMaintenance();
  renderCatalog();
 renderStructure();
 $('items').replaceChildren();
 for(const [kind,items] of [['plan',state.plans],['run',state.runs]])for(const item of [...items].reverse()){
  const id=item.id||item.run_id;
  $('items').append(button(live(()=>(kind==='plan'?t("方案 "):t("运行 "))+id.slice(0,8)+' · '+label(item.status)+(item.claimed?'':t(" · 历史只读"))),()=>{selection={kind,id};render();},'quiet'));
 }
 for(const entry of state.recovery_cards||[])if(entry.kind==='task')$('items').append(button(live(()=>t("未完成登记 ")+entry.id.slice(0,8)),()=>{selection={kind:'task',id:entry.id};render();},'quiet'));
 $('jobs').replaceChildren();
 for(const job of [...state.jobs].reverse())$('jobs').append(node('div',live(()=>label(job.action)+' · '+label(job.status)+(job.error?'\n'+planningError(job.error):job.result?.workflow_status?' · '+label(job.result.workflow_status):'')),'job'));
 for(const attempt of [...(state.planning_attempts||[])].reverse())if(attempt.status==='unknown'){
  const evidence=attempt.evidence||{};const box=node('details');box.className='planning-failure';
  box.append(node('summary',live(()=>t("规划调用中断或失败 · 消耗未知"))));
  box.append(node('p',live(()=>t("已保留额度：")+attempt.reserved+t("。不会自动重试或因重启恢复额度。"))));
  box.append(node('p',live(()=>t("已观察到的调用开始：")+(evidence.requests_started??t("未取得"))+t("；这些记录不代表服务端实际消费或计费。"))));
  box.append(node('p',live(()=>t("运行事件：")+(evidence.events_complete?t("已取得结束事实"):t("只取得部分事实，或尚未取得")))));
  box.append(node('pre',live(()=>JSON.stringify(evidence,null,2))));$('jobs').append(box);
 }
 for(const attempt of [...(state.planning_attempts||[])].reverse())if(attempt.status==='finished'&&attempt.outcome==='failed'){
  const evidence=attempt.evidence||{};const box=node('details');box.className='planning-failure';
  box.append(node('summary',live(()=>t("规划失败 · 调用次数已确认"))));
  box.append(node('p',live(()=>t("已记录模型调用：")+evidence.requests+t("。任务仍失败，未生成可批准的方案；不会自动重试。"))));
  box.append(node('p',live(()=>t("调用次数是本机适配器计数，实际费用以服务商账单为准。"))));
  box.append(node('pre',live(()=>JSON.stringify(evidence,null,2))));$('jobs').append(box);
 }
 recoverySummary();
 $('content').replaceChildren();
 if(!selection){const el=card(live(()=>t("每一步，都留下证据")));el.classList.add('empty');el.append(node('p',live(()=>t("输入问题并生成方案，在此查看修改范围、差异与风险。"))));$('content').append(el);return;}
 if(selection.kind==='plan'){
  const item=state.plans.find(p=>p.id===selection.id);if(!item)return;
  const el=card(live(()=>t("待审批方案")));const info=node('dl');
  field(info,live(()=>t("问题")),live(()=>item.plan.issue));field(info,live(()=>t("诊断")),live(()=>item.plan.diagnosis));field(info,live(()=>t("风险")),live(()=>item.plan.risk));field(info,live(()=>t("基线")),live(()=>item.plan.base_sha));field(info,live(()=>t("授权测试")),live(()=>JSON.stringify(item.plan.tests)));el.append(info,node('div',live(()=>item.fingerprint),'fingerprint'));
  field(info,live(()=>t("原始规划模型请求")),live(()=>item.planning_evidence?.requests??t("未计量")));
  if(item.planning_evidence?.experience_context_sha256){
   field(info,live(()=>t("本次历史经验")),live(()=>(item.planning_evidence.experience_citations||[]).map(e=>e.id.slice(0,8)).join('、')||t("没有匹配经验")));
   el.append(node('p',live(()=>t("历史经验只供参考；当前文件、测试与新的审批决定本次修改。")),'small'));
   el.append(node('div',live(()=>t("经验上下文回执：")+item.planning_evidence.experience_context_sha256),'fingerprint'));
  }
  if(item.planning_evidence?.knowledge_context_sha256){
   field(info,live(()=>t("本次工程知识")),live(()=>(item.planning_evidence.knowledge_citations||[]).map(r=>r.id.slice(0,8)).join('、')||t("没有匹配知识")));
   el.append(node('p',live(()=>t("引用的是经审阅的工程提议，仍需对当前代码验证。")),'small'));
   el.append(node('div',live(()=>t("知识上下文回执：")+item.planning_evidence.knowledge_context_sha256),'fingerprint'));
  }
  const evidence=node('details');evidence.append(node('summary',live(()=>t("原始 Pi 规划事件与用量"))),node('pre',live(()=>JSON.stringify(item.planning_evidence||{},null,2))));el.append(evidence);
  loadedMemory(el,item.planning_evidence);
  if(item.memory_binding)memoryCitations(el,item.planning_evidence);
  memoryRelations(el,item.decision_relations);
  if(item.structure_binding){
   el.append(node('p',live(()=>item.structure_binding.model_context?t("原规划读取了经核验的结构元数据。"):t("方案绑定了代码结构来源；图数据未传给模型。")),'small'));structureReceipt(el,item.structure_binding);
   if(item.planning_evidence?.structure_context_sha256)el.append(node('div',live(()=>t("结构上下文回执 SHA256：")+item.planning_evidence.structure_context_sha256),'fingerprint'));
   if(state.structure_index?.status==='invalid')el.append(node('p',live(()=>t("此方案的代码结构来源已失效。核验原来源前不能认领或批准。")),'error'));
  }
  if(item.memory_binding&&state.source_memory?.status==='invalid')el.append(node('p',live(()=>t("此方案的记忆来源已失效。恢复并核验原来源前不能认领或批准。")),'error'));
  diff(el,item.diff);el.append(node('p',live(()=>t("批准仅允许执行这份方案；测试通过后，发布仍须另行审批。"))));
  if(item.repeated_failed_patch){
   el.append(node('p',live(()=>t("这份补丁与已验证失败的补丁相同，修改审批已阻断。请查看失败记录并生成不同方案。")),'error'));
   if(item.claimed)el.append(button(live(()=>t("拒绝重复方案")),()=>act('decision',{plan_id:item.id,fingerprint:item.fingerprint,decision:'reject'}),'danger'));
  }else if(item.status==='awaiting_approval'&&!item.claimed){
   const claim=button(live(()=>t("核验并认领方案")),()=>act('claim_plan',{plan_id:item.id,revision:item.revision}),'primary','claim-plan');
   const recovery=(state.recovery_cards||[]).find(c=>c.kind==='plan'&&c.id===item.id);claim.disabled=claim.disabled||Boolean(recovery&& !['review_plan','not_bound'].includes(recovery.status));
   el.append(node('p',live(()=>t("这是历史方案。先核验当前仓库、策略和证据并认领，再重新审批。认领不会修改代码。"))),claim);
  }else if(item.status==='awaiting_approval'){
   const args={plan_id:item.id,fingerprint:item.fingerprint};
   const approve=button(live(()=>t("批准修改并测试")),()=>act('decision',{...args,decision:'approve'}),'primary','approve-plan');
   const recovery=(state.recovery_cards||[]).find(c=>c.kind==='plan'&&c.id===item.id);approve.disabled=approve.disabled||Boolean(recovery&& !['review_plan','not_bound'].includes(recovery.status));
   el.append(approve,button(live(()=>t("拒绝方案")),()=>act('decision',{...args,decision:'reject'}),'danger','reject-plan'));
  }else{
   el.append(node('span',live(()=>label(item.status)),'status'));
   if(item.run_id)el.append(button(live(()=>t("查看关联运行登记")),()=>{selection={kind:'run',id:item.run_id};render();},'quiet','linked-run'));
  }
  $('content').append(el);return;
 }
 const run=state.runs.find(r=>r.run_id===selection.id);if(!run)return;
 if(run.status==='evidence_invalid'||run.resolution){interruption(run);return;}
 if(run.repair_finished===false){repairInterruption(run);return;}
 const el=card(live(()=>t("运行 ")+run.run_id.slice(0,8)));const info=node('dl');
 if(state.experience_memory?.settings_revision){el.append(button(live(()=>t("登记此运行为待审阅经验")),()=>act('experience_capture',{run_id:run.run_id}),'quiet'));}
 field(info,live(()=>t("执行状态")),live(()=>label(run.status)));field(info,live(()=>t("权限模式")),live(()=>run.mode==='auto'?t("自主"):t("人工审批")));field(info,live(()=>t("源保持不变")),live(()=>String(run.source_unchanged)));field(info,live(()=>t("候选分支")),live(()=>run.branch));field(info,live(()=>t("发布分支")),live(()=>run.integration_branch||t("尚未发布")));el.append(info);
 if(run.continued_from_run)el.append(node('p',live(()=>t("继续自运行 ")+run.continued_from_run.slice(0,8)+t("，沿用原请求账本。"))));
 if(!state.recovery_cards&&run.feedback_cleanup){
  el.append(node('p',live(()=>t("清理操作的反馈转换尚未登记。核验只更新清理审计，不调用模型或测试，不修改 Git 分支或文件，不授予旧批准。"))));
  const reconcile=button(live(()=>t("核验并完成清理登记")),()=>act('reconcile_feedback_cleanup',{run_id:run.run_id,revision:run.feedback_cleanup.revision}),'','reconcile-feedback-cleanup');
  reconcile.disabled=reconcile.disabled||!state.runtime||state.runtime.code_changed;el.append(reconcile);
 }
 if(!state.recovery_cards&&run.failure_recovery&&!run.failure_recovery.blocked){
  el.append(node('p',live(()=>t("失败运行已结束，但反馈检查点未保存。第")+run.failure_recovery.failed_round+t("轮测试退出码：")+run.failure_recovery.test_exits.join('、')+t("。恢复只登记已核验反馈，不调用模型、不运行测试，也不继承旧批准。"))));
  const restore=button(live(()=>t("核验并恢复失败反馈")),()=>act('recover_feedback',{run_id:run.run_id,revision:run.failure_recovery.revision}),'','recover-feedback');
  restore.disabled=restore.disabled||!state.runtime||state.runtime.code_changed;el.append(restore);
 }else if(!state.recovery_cards&&run.continuation&&!run.continuation.available){el.append(node('p',live(()=>t("续修已阻断：")+run.continuation.reason)));}
 if(!state.recovery_cards&&run.continuation?.available){
  if(run.continuation.failure_feedback)el.append(node('p',live(()=>t("续修将携带第")+run.continuation.failure_feedback.failed_round+t("轮的实际失败反馈，测试退出码：")+run.continuation.failure_feedback.test_exits.join('、')+t("。新方案需要重新审批。"))));
  el.append(node('p',live(()=>t("此前候选已安全结束。继续会创建新的隔离尝试；原报告和已用额度保留。"))));
  const resumePlan=button(live(()=>t("继续该问题，先生成方案")),()=>act('resume_run',{run_id:run.run_id,revision:run.continuation.revision,mode:'approval'}),'','resume-plan');
  resumePlan.disabled=resumePlan.disabled||!state.runtime||state.runtime.code_changed||(state.request_budget>0&&state.requests_remaining<=0);el.append(resumePlan);
  if(run.continuation.auto_allowed){const resumeAuto=button(live(()=>t("在原授权范围内继续修复")),()=>{if(confirm(t("继续同一问题，使用剩余额度创建新的隔离候选？原授权范围和报告保留。")))act('resume_run',{run_id:run.run_id,revision:run.continuation.revision,mode:'auto'});},'','resume-auto');resumeAuto.disabled=resumePlan.disabled;el.append(resumeAuto);}
 }
 diff(el,run.diff||run.rounds?.at(-1)?.diff||'');
 tests(el,live(()=>t("基线测试")),run.baseline_tests||[]);tests(el,live(()=>t("候选测试")),run.candidate_tests||[]);
 if(run.planning_origin?.plan_sha256)el.append(node('p',live(()=>t("原始规划请求：")+(run.planning_origin.provider_evidence?.requests??t("未计量"))+t("；执行已保存方案新增模型请求：0。"))));
 const memoryOrigin=run.planning_origin?.provider_evidence;
 loadedMemory(el,memoryOrigin);for(const round of run.rounds||[])loadedMemory(el,round.provider_evidence);
 if(memoryOrigin?.memory_context_sha256)memoryCitations(el,memoryOrigin);
 for(const round of run.rounds||[])if(round.provider_evidence?.memory_context_sha256)memoryCitations(el,round.provider_evidence);
 const args={run_id:run.run_id,revision:run.revision};const review=run.review;
 if(!run.claimed){
  el.append(node('p',live(()=>t("这是历史运行。认领只核验固定仓库、策略与当前证据，后续发布仍需独立批准。"))));
  if(run.status!=='evidence_invalid')el.append(button(live(()=>t("核验并认领运行")),()=>act('claim_run',args),'primary','claim-run'));
  $('content').append(el);return;
 }
 if(run.status==='candidate_ready'&&!review)el.append(button(live(()=>t("封装候选并重新测试")),()=>act('prepare',{...args,retry_aborted:false}),'primary','prepare'));
 if(review){
  const reviewCard=card(live(()=>t("独立发布审批")));reviewCard.append(node('span',live(()=>label(review.status)),'status'));
  const details=node('dl');field(details,live(()=>t("准备阶段")),live(()=>review.operation_stage||t("未记录")));field(details,live(()=>t("准备批次")),live(()=>review.attempt_id||t("未记录")));field(details,live(()=>t("测试进程")),live(()=>review.test_inflight?.pid?format(t("PID {0} · 创建时间 {1}"),[review.test_inflight.pid,review.test_inflight.created_at]):t("无已记录运行进程；不代表未知启动窗口已安全")));field(details,live(()=>t("封装提交")),live(()=>review.core?.commit||t("未完成")));field(details,live(()=>t("目标分支")),live(()=>review.core?.target_branch||t("未完成")));field(details,live(()=>t("证据核验")),live(()=>review.hash_matches===true&&review.diff_hash_matches===true?t("指纹一致"):t("未完成或证据已变化")));reviewCard.append(details,node('div',live(()=>review.review_sha256||t("尚无可批准指纹")),'fingerprint'));tests(reviewCard,live(()=>t("封装后完整测试")),review.tests||[]);diff(reviewCard,review.diff||'');
  if(['prepared','awaiting_approval','rejected'].includes(review.status)&&review.hash_matches===true&&review.diff_hash_matches===true){
   const confirmation=node('label',undefined,'confirm');const check=node('input');check.type='checkbox';check.id='confirm-publish';confirmation.append(check,node('span',live(()=>t("我已审阅封装提交与测试，同意创建上述本地发布分支。"))));reviewCard.append(confirmation);
   const approve=button(live(()=>t("批准发布分支")),()=>{if(check.checked)act('publish',{...args,fingerprint:review.review_sha256});},'primary','publish');approve.disabled=true;check.onchange=()=>{approve.disabled=!check.checked||pending||Boolean(state.active);};reviewCard.append(approve,button(live(()=>t("拒绝发布")),()=>act('reject_publish',{...args,fingerprint:review.review_sha256}),'danger','reject-publish'));
  }
  if(['preparing','recovering','cleanup_required','failed'].includes(review.status))reviewCard.append(button(live(()=>t("核验并中止中断准备")),()=>act('recover',args),'','recover'));
  if(review.status==='aborted')reviewCard.append(button(live(()=>t("归档旧准备并重新验证")),()=>act('prepare',{...args,retry_aborted:true}),'','retry-prepare'));
  el.append(reviewCard);
 }
 if(!['discarded','evidence_invalid','repair_aborted'].includes(run.status)&&!['preparing','recovering','cleanup_required','failed'].includes(run.review?.status)){
  const confirm=node('label',undefined,'confirm');const check=node('input');check.type='checkbox';check.id='confirm-cleanup';confirm.append(check,node('span',live(()=>t("清理本次候选工作区；保留运行报告与已发布分支。"))));el.append(confirm);
  const cleanup=button(live(()=>t("清理候选")),()=>{if(check.checked)act('cleanup',args);},'danger','cleanup');cleanup.disabled=true;check.onchange=()=>{cleanup.disabled=!check.checked||pending||Boolean(state.active);};el.append(cleanup);
 }
 const audit=node('details');audit.append(node('summary',live(()=>t("审批与执行证据"))),node('pre',live(()=>JSON.stringify({authorization:run.authorization,events:run.events,error:run.error},null,2))));el.append(audit);$('content').append(el);
}
async function refresh(){state=await api('state');if(state.focus_plan_id&&!selection&&state.plans.some(p=>p.id===state.focus_plan_id))selection={kind:'plan',id:state.focus_plan_id};if(!selection){const plan=state.plans.findLast(p=>p.status==='awaiting_approval'),run=state.runs.at(-1),unlinked=(state.recovery_cards||[]).findLast(c=>c.kind==='task');if(plan)selection={kind:'plan',id:plan.id};else if(run)selection={kind:'run',id:run.run_id};else if(unlinked)selection={kind:'task',id:unlinked.id};}if(state.repository_picker_url){const link=$('repository-picker');const url=new URL(state.repository_picker_url);if(/^http:\/\/127\.0\.0\.1:\d+$/.test(url.origin)&&/^#session=[a-f0-9]{64}$/.test(url.hash)){if(options.embedded){link.href='#';link.onclick=event=>{event.preventDefault();options.onChooseRepository?.();};}else link.href=url.href;link.hidden=false;}if(!state.plans.length&&!state.runs.length&&$('issue').value.startsWith('修复 add 函数')){$('issue').value='';i18n.attribute($('issue'),"placeholder",live(()=>t("描述这个仓库中的问题或失败测试")));}}render();options.onState?.(state);}
$('plan').onclick=()=>act('plan',{issue:$('issue').value});
$('auto').onclick=()=>{if(confirm(t("允许在启动时授权的文件与测试范围内自主修复？发布仍须另行批准。")))act('auto',{issue:$('issue').value});};
$('refresh').onclick=()=>refresh().catch(error=>notice(live(()=>error.displayMessage||error.message),true));
$('budget-preview').onclick=()=>act('budget_preview',{new_total:Number($('budget-total').value)});
$('memory-preview').onclick=()=>act('memory_preview',{issue:$('issue').value});
$('structure-preview').onclick=()=>act('structure_preview',{query:$('structure-query').value});
const ready = refresh().then(()=>notice(live(()=>t("已连接。请生成方案，开始本地审批流程。")))).catch(error=>notice(live(()=>error.displayMessage||error.message),true));
const timer = setInterval(async()=>{if(pending||polling||!state?.active)return;polling=true;try{await refresh();}catch(error){notice(live(()=>error.displayMessage||error.message),true);}finally{polling=false;}},800);

return {ready, refresh,setLanguage:i18n.setLanguage,
 select(value){selection=value;render();},
 async createTask(issue){return act('plan',{issue});},
 getState(){return state;},
 dispose(){clearInterval(timer);i18n.dispose();}};
};
if (!document.getElementById('maintenance-views')) window.mountMaintenanceUI(document);
