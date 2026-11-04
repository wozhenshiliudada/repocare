# 基于 Pi 的维护 Agent 核心

用户明确指定以 Pi 为源 Agent。采用真实 createAgentSession、ModelRuntime、工具执行循环与内存会话；当前为SDK扩展，未修改上游内核源码。
版本锁定Pi1.0.4；来源见THIRD_PARTY。

2026-10-18补充：本次规划内连续3次重复读取，或有实际源码且到达提案余量边界时，通过SDK原命名tool_choice请求首次提案；格式错误保留严格校验，随后可补读纠正，原请求/输出/时间上限不变。新经历v3优先保留真实异常，旧v1/v2记录不自动迁移。仅完成合成响应工程验证，真实模型效果须另验，详[PLANNING_QUALITY_REPAIRS](../../docs/digital-media-contest/PLANNING_QUALITY_REPAIRS.md)。

2026-10-15当前补充：同journal GUI已使用固定Native任务会话、失败反馈、经历/知识固定引用，以及受原预算控制的原生compaction。自动整理默认关闭，只有新任务创建时显式固定配置才可启用；原文件只追加，摘要不增加规划回合或继承批准。详见TASK_SESSION_PROTOCOL与TASK_COMPACTION。下方较早的“每轮新内存会话/尚无压缩”等说明属于原CLI/早期阶段，不能覆盖当前GUI路径；跨journal/旧无封存迁移与最终新UI验收仍待。

## 改造

1. 只启用read_source/propose_repair，关闭默认read/write/edit/bash/MCP、扩展/技能/项目指令发现。
2. 只读Python提供的不可变授权快照，区分editable和readonly；有界行范围/字面搜索；仅收精确唯一匹配的 oldText/newText 局部替换，不再接受模型完整 content。Node 内存重建与 Python 独立回执核验后，原方案绑定文件 SHA256；协议见 docs/digital-media-contest/PI_LOCAL_EDIT_PROTOCOL.md。
3. 方案交给Python审批/Git层，模型不直接修改仓库、执行测试或合并。
4. 用户指定整任务默认/最大100次请求，可显式配置1–100，跨轮共享；模型知道总额与余量，工具上限为会话请求余量四倍。单次输出128000 token，规划及HTTP超时30分钟，Python外围1815秒留清理余量。共同配置src/repo_maintainer/pi_runtime_limits.json；旧持久scope与耗尽账本不自动扩额，显式旧值仍可恢复。运行中服务不会热更新；显式持久扩额尚未实现。
5. 关闭Pi自动重试与HTTP隐式重试；请求预算在传输前检查。工具错误 isError=true、余量内可纠正；length 截断标 output_truncated，禁止接受方案/追加传输，不以工具调用数替代请求数。
6. 首轮测试失败时，PiProvider.revise接收真实输出/diff与当前快照，附同任务最近3轮方案、12个不同读位置和有界失败断言索引，再提新方案。摘要先绑定任务/仓库/策略/预期候选哈希，Node/Python独立核回执；每轮仍新内存会话，完整会话延续/压缩/跨任务恢复未实现。read_source重复读返回引用与提示，可reread=true，仍按原整任务额度；统计进入安全证据。新Python声明revision_protocol=1，旧CLI保留证据白名单，运行中旧进程不冒充热更新。见PI_REVISION_FOCUS。
7. 报告保存版本/源码SHA、激活工具、Agent事件、各轮请求与模型usage。费用仍为占位，不能将cost=0当免费。
8. 项目适配协议v1保存失败终态及有限安全事件，外围超时回收已取得过程快照。失败requests=null，requests_started只是适配器开始数，原DurableProvider保留未知额度，不自动重试/重启清零；响应/工具内容不进失败证据。详见docs/digital-media-contest/PI_FAILURE_PROTOCOL.md；父强杀前实时持久事件仍未实现。
9. 可选显式来源记忆通过原session.prompt只读传递，Node/Python核对上下文回执，原两个工具/额度不变。GUI额外将来源根/索引路径/原索引SHA绑定原持久scope，Pi每轮在配置读取前核验pin；重启、旧方案认领/新审批和失效边界见docs/digital-media-contest/GUI_SOURCE_MEMORY.md。不自动发现会话或加载旧批准。

## 安装与回放

```bash
npm --prefix runtime/pi ci --ignore-scripts --no-audit --no-fund
npm --prefix runtime/pi test
node runtime/pi/replay.mjs --directory .demo/pi-new
```

Pi测试覆盖真实SDK、越界/只读保护、模型错误、严格请求预算、Git/审批/回退、多轮反馈及MiMo传输兼容；最新计数见STATUS。
回放6项，使用127.0.0.1合成SSE响应；不是实网模型能力证明。

## 真实模型

默认仅使用本进程显式PI_MAINTENANCE_API_KEY/BASE_URL/MODEL，必须HTTPS；兼容OpenAI chat completions，不绑定供应商。
经用户明确授权，可显式传--credential-source openclaw-readonly；通过本机Windows全局OpenClaw的只读auth接口加载唯一DeepSeek API key profile。
不改登录/配置，密钥只在内存，不写报告、不继承给测试进程。此路径依赖本机安装，不是可移植默认凭据方式。

2026-10-08实测：DeepSeek Flash，三类自建样例各2请求、首轮红→绿，累计6次授权已用满。
validate-deepseek命令仅运行自建config/syntax/boundary，整批最多6请求，失败后不启动新样例。
实网模型多轮效果、原生其他Provider、持续会话仍待验证；新调用需要新的任务授权。

2026-10-08新增MiMo实测：显式 `--provider-config .env.mimo.local`；MiMo官方标准按量接口、mimo-v2.6-flash，自建加法样例2请求修复通过。
仅将本地三字段配置传至模型子进程；关闭thinking，当前按用户D-048发送128000输出上限（max_completion_tokens），旧4096为历史试验；无自动凭据发现或新付费复测。
Token Plan只经过payload映射测试，未实网验证；完整推理字段回传未提供。用法与官方来源见MVP_USAGE。

第三阶段的integration-replay.mjs覆盖真实Pi候选与独立发布审批：模型只提方案，Python将成功候选封装/重测，另行批准后发布全新integration分支。
未批准不创建分支，source checkout不变，cleanup只移除maintenance候选；这是本机合成模型联调，不额外读取密钥或调用实网模型。
稳定分支merge/push/PR仍不属于默认执行路径。


## 显式只读决策声明上下文（2026-10-11）

serve可在原memory-root/index/relations配置上另加memory-relations-context，固定到原持久scope；默认关闭保持上下文1/2。新上下文3保留原命中/分类，额外传直接声明及双方出处，Python核验真实源、Node独立严格核验，原结果/evidence协议1和两工具保持。全上下文回执、推理期间变动/逐轮回滚/原unknown额度及重启边界见[PI_DECISION_CONTEXT](../../docs/digital-media-contest/PI_DECISION_CONTEXT.md)。真实Pi验证使用自建loopback，不能证明付费模型记忆收益。

## 显式只读结构上下文（2026-10-11）

serve须同时明确structure-index与固定structure-context-query，默认不传结构。独立结构schema1、双侧严格核验、原索引与实际候选文件指纹、完整回执/有限出处报告及逐轮失效/原额度门见[PI_STRUCTURE_CONTEXT](../../docs/digital-media-contest/PI_STRUCTURE_CONTEXT.md)。初始提示仍不包含授权文件全文，源码只经原read_source读取；原两工具/source_memory1/2/3/分类混合门不变，无新依赖或实际模型收益结论。
