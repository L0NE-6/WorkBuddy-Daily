# 更新日志

## v3.35（2026-10-30）

- 推送摘要增加本轮操作前 / 操作后的积分与差值（issue #44）：账号报告显示 `积分: 前 → 后（本轮 +N）`，不再只有操作前余额。
- `queryCredits` 增加可选 `meta` 输出，保留原有文本返回值；任务结束、最终领奖后再查询一次并计算差值。
- README 补充私有仓库 / Import 后 `schedule` 不触发的排查清单（issue #43）：默认分支、Actions 启用、Fork 限制、60 天无活动、UTC cron。
- 新增推送摘要积分差值回归，测试总数 64 项。

## v3.34（2026-10-30）

- 修复盲盒“能量不足”的时序问题：`t_blindbox` 原来在所有任务领奖之前执行，今天任务刚发的 +5 能量还没到账，盲盒只能看到旧余额。
- 任务执行完后、徽章/盲盒/旅行/抽奖之前先调用一次 `claim_ready_tasks`，把已达标奖励结算掉；末尾保留原领奖调用兜底。
- 真实账号验证：6 个账号的 4 类专家任务全部 `claimed 1/1`；盲盒按当前 `affordable` 开完，能量均降到 10 以下。

## v3.33（2026-10-30）

- 修复「UI 任务中心能看到、脚本却提示不在当前任务列表」：专家任务查询原来只读桌面 `/v2/activity/growth/tasks`；现在 `_expert_task_row` / `_expert_prog` 在桌面未命中时回落小程序口径（`X-Client-Platform: miniprogram`）。
- `_dynamic_expert_task_codes` 同时合并桌面与 MP 的 `expert_use_*`，不再只依赖桌面列表。
- `_accept_expert_task` 先按桌面口径 accept，失败再走 MP `_mp_accept_res`；MP-only 账号不再卡在 `task not found`。
- `claim_ready_tasks` 区分「桌面+MP 都有」与「只在 MP 出现」：前者仍走桌面 claim，后者改用 MP claim，避免专家任务完成后无法领奖。
- 新增 2 项回归（MP 回落、MP-only 领奖），测试总数 63 项。

## v3.32（2026-10-30）

- 修复动态解锁的专家任务一直停在 `not_accepted`：`_run_local_expert_task` 原来只读 `prog()`，不补 accept；任务在 `t_accept_all` 之后才出现时，真实对话和 `expert_actual_use` 都不会计入进度，日志只剩 `not_accepted None/None`。
- 进入专家任务时若状态为 `""` / `not_accepted`，先走 `_accept_with_verify`；登记失败直接跳过，不再白跑对话。
- 真实对话没有返回 `conversationId` 或服务端消息 ID 时输出明确诊断，不再静默 fallback。
- `_web_conversation_prune` 改为按账号级活跃上限清理；原实现只清同 `expert-<slug>` 前缀，跨专家累计到 10 条时永远清不掉。
- 新增 2 项回归（专家任务先 accept、跨前缀清理），测试总数 61 项。

## v3.31（2026-10-30）

- 修正 v3.30 的 5.9.3 构建指纹：`commit` 改为官方安装包 `5.9.3.42398134-d4692421` 的 `d46924219e79332f49c37ccebf27292f87001b19`，`releaseDate` 改为 `1793093056315`。
- v3.30 使用的 `c4125338… / 1789632000000` 来自第三方实现，时间早于 5.8.3，不能代表 5.9.3；继续沿用会让版本号与安装清单自相矛盾。
- UA、`DESKTOP_VERSION_CN`、事件指纹 `ideVersion` / `extVersion` 保持 5.9.3；59 项测试全部通过。

## v3.30（2026-10-30）

- 对齐 WorkBuddy 官方客户端 5.9.3（国内版）：桌面 UA 更新为 `WorkBuddy/5.9.3 WorkBuddy/5.9.3 CLI/2.165.0`，`DESKTOP_VERSION_CN` / 事件指纹 `ideVersion` 与 `extVersion` 同步 5.9.3。
- 桌面事件指纹的 `releaseDate` / `commit` 曾更新为第三方实现的取值；v3.31 已按官方安装包元数据修正。
- 仅调整国内版桌面指纹；国际版 5.9.3 的聊天头族仍按 pilot 处理，待真实账号抓包验证后再动。
- 59 项测试全部通过。

## v3.29（2026-10-30）

- 修复 Issue #42（1）：`ACCOUNTS = load_accounts()` 在 `uid_of()` 定义前执行；v3.27 给 `refresh_one` 增加 `X-User-Id` 后，首次自动续期会触发 `NameError: name 'uid_of' is not defined`，请求根本发不出去。现将 `uid_of` 定义提前到 `refresh_one` 之前，续期恢复发送并携带 `X-User-Id`。
- 修复 Issue #42（2）：`auto_refresh` 合并 `WORKBUDDY_REFRESH_TOKEN` 时不再用会轮换的 RT 去重，改为按 JWT `sub`（无 `sub` 时用显式 user/手机号）稳定身份合并；已存在账号不会被环境变量里的旧 RT 覆盖，无稳定身份时保留 store 现有账号。
- v3.28 未改这条路径：v27→v28 的脚本差异仅为 `_token_sanity` 接受 `copilot.tencent.com` 签发域及版本号；`uid_of` 调用由 v3.27 的续期头改动引入。
- 新增模块导入续期、重复运行不覆盖轮换 RT、JWT subject 合并、无稳定身份保留账号 4 项回归，测试总数 59 项。

## v3.28（2026-10-30）

- 修复 Issue #40：凭据体检接受 `https://copilot.tencent.com/auth/realms/copilot` 国内签发域，登录工具产出的 CN Token 不再被误报为未知来源；v3.27 的续期头修复继续生效。
- 修复 Issue #41：GitHub Actions 工作流会自动检测国内 / 国际版 Secrets，分别运行 `workbuddy_daily.py` 与 `workbuddy_intl_daily.py`；只配置其中一种时不会空跑另一种。
- 私有仓库持久化运行状态时同步保存 `wb_intl_tokens.json`，避免国际版轮换后的 refreshToken 在干净 runner 上丢失。
- 补充 `copilot.tencent.com` 签发域与相似域名回归测试。

## v3.27（2026-10-29）

- 修复 CN Token 续期请求头：`refresh_one` 现在携带当前 AT 的 `Authorization: Bearer ...`、`X-Request-ID`（32 hex 毫秒前缀）、`X-User-Id`（AT 的 `sub`）与 `X-Domain: www.workbuddy.cn`，保留 `X-Refresh-Token` / `X-Auth-Refresh-Source` 与空 `{}` body；无 AT/uid 时不伪造身份头。
- 修复 `credits_report.py` 个人资源包查询：`get-user-resource` 改为 `PageNumber=1` / `PageSize=100` / `ProductCode=p_tcaca` / `Status=[0,3]` + 到期时间窗；解析 `CycleCapacityRemain[Precise]`，`CapacityType=4` 按月度资源处理，并兼容旧 `CapacityRemain` / 无 `CapacityType` 回包。
- `credits_report.py` 支持 store 条目显式带 `enterpriseId` 时只读查询 `/v2/billing/meter/get-enterprise-user-usage`（404 回退网页无 `/v2` 路径），解析 `limit_num`/`limitNum` 与 `used_num`/`usedNum`，`-1` 表示不限量；报告注明企业账号不支持每日签到，不发起签到请求。
- 账号结构说明：`workbuddy_daily.py` 归一后的账号仍只有 `note` / `access_token` / `refresh_token`，不携带 `enterpriseId`；因此主脚本不做 JWT / `token_source` 猜测式企业识别，`credits_report.py` 仅在 store 显式字段存在时启用企业分支。
- 新增续期头/无 AT 形态、个人查询体、月包与旧回包、企业解析/404 回退回归，测试总数 55 项。

## v3.26（2026-10-28）

- 修复 Issue #39：`Model_chat_GLM5.2` / `create_canvas` / `Buddy_App_QQ` 等任务不再下发时，`prog()` 返回 `(None, None, None)`，而旧判断 `st not in ("completed", "claimed")` 对 `None` 恒为真 → 继续对已下线任务发真实对话，实测每轮白扣 0.12–0.16 积分。现统一按「未下发 → 跳过」处理并在日志标注。
- 覆盖范围：`t_chat_n`、`t_canvas_automation`（`create_canvas` / `automation_1` / `playbook_prompt`）、`t_buddy_apps`，以及桌面任务 `RichMeow_Chat` / `skill_1` 的 need 判定（未下发不再触发桌面对话）。
- 采用「守卫而不归档」：与 `workstation_expert` 同策略，任务若重新下发会自动恢复；归档说明见 `archive/README.md`。
- 新增 4 项「任务未下发不发请求」回归，测试总数 46 项。

## v3.25（2026-10-27）

- 修复 Issue #37：`black_cat` 未出现在当前任务列表时直接提示“任务未下发”并跳过，不再硬跑 3 次夜聊制造假失败。
- 夜猫子仍只在 23:00–08:00 CST 窗口执行；已下发且已完成/已领取时保持原跳过逻辑。
- 新增“任务缺失不发对话”回归，测试总数 38 项。

## v3.24（2026-10-27）

- 成长 profile 遇到 HTTP 401 时先用账号 RT 续期一次并重试；只有刷新失败或仍返回 401 才判登录失效。
- 小程序任务 accept 后 `target<=0` 不再按 1 猜测进度，改为有界回读；仍为空则留到下一轮，避免假完成和无效领奖。
- 合并社区 PR #36（感谢 @coderDJing）：凭据体检同时识别 `www.codebuddy.cn` 与 `www.workbuddy.cn` 的国内 copilot realm，不再把合法国内凭据误报为国际版。
- 发版对齐：v23 的 Release 资产虽已对应 `v3.23` 代码，但 tag 仍指向 `v3.22` 提交；本版把 tag、Release、`main` 和脚本版本重新绑定。
- 状态目录、国际版 403 分类、PC 灵感任务等沿用 v3.23；新增 401 续期、target 延迟下发与签发域回归，测试总数 37 项。

## v3.23（2026-10-26）

- 新增 `WORKBUDDY_STATE_DIR`（社区贡献 #35，感谢 @AvigatorC）：可把 `wb_refresh_tokens.json`、`WORKBUDDY_ACCESS_TOKEN.txt`、补偿防重和活跃去重状态统一放到持久目录；不设置时保持脚本同级目录的原行为。
- 国际版保活对话的 `HTTP 403` 明确归类为「模型无权限，非账号风控」；真正的账号受限仍以 `11140 / request illegal` 为准，由预检直接识别。
- 新增状态目录与 403 分类回归，测试总数增至 34 项。

## v3.22（2026-10-24）

- 按 10-24 线上任务实测修正 `Sequential_Tasks_7`：该任务已标记为 `PC 限定`，不再属于小程序任务集合。
- 任务 7 改为默认任务列表读取、PC accept、桌面灵感事件、默认列表回读和 Web 领奖；上报 200 但进度未点亮时明确失败，不调用领奖。
- 移除任务 7 的 mp 指纹和 mp 45 秒节奏依赖，避免 PC 限定任务被小程序骨架劫持。
- 新增 PC 流程成功与“回读仍为 0 不领奖”两条回归，测试总数增至 30 项。

## v3.21（2026-10-24）

- 国际版在刷新 RT 和任何写操作前增加只读账号状态预检；`11140 / request illegal` 直接归类为账号受限并结束该账号本轮。
- 国内版与国际版统一使用同一类受限文案，不再把风控状态误报成登录失效或普通活动关闭。
- 为国际版预检补充独立离线测试；国内版 25 项回归与全部脚本编译继续通过。

## v3.20（2026-10-24）

- 专家市场改为按职业名分页搜索，并用任务 `jump_url` 或静态映射中的 `expert_id` 精确落位，同名首条不再误选。
- 修正四个专家任务的昵称、职业名和版本：`卖得好 / 中国电商运营专家 / 1.0.1`、`斗音音 / 抖音策略师 / 1.0.2`、`商家宣传设计师 / 商家宣传页与海报设计师 / 1.6.0`、`播旺旺 / 直播电商教练 / 1.0.2`。
- `expert_actual_use` 分别上报昵称 `name` 与职业名 `expertTitle`，保持 `mode:LOCAL`、`type` 为空、`cost=0`。
- `expert_use_*` 扩展为族处理：任务表中未来出现的新 code 可通过 `jump_url` 或市场中唯一同名职业自动解析，不再只认静态四条。
- 任务发现合并桌面 `/v2`、小程序 `/v2`、无 `/v2` 的等级族三套口径；五个等级任务只做发现分类，`wechat_linked` 标记为客户端绑定，不重复上报无效事件。
- WorkBuddy `11140 / request illegal` 单独归类为账号受限，不再误报 Token 过期，也不在原账号刷新或重试。
- 离线回归增至 25 项，覆盖专家精确匹配、动态族、三源任务发现和账号受限分类。

## v3.19（2026-10-23）

- 新增四个 2026-11-20 到期的专家体验任务：中国电商运营专家、抖音策略师、商家宣传页与海报设计师、直播电商教练。
- 四个任务复用 `WorkBuddy_Valorant` 的真实对话、专家召唤、`has_expert` 和 `actual_use(mode:LOCAL)` 链，并纳入任务名称、默认使用类白名单、未覆盖检测与桌面领奖路由。
- 专家 ID 优先从任务 `jump_url` 的 `deeplink=workbuddy://experts?expertId=ex_xxx` 严格解析；仅接受 `workbuddy://experts` 深链和合法 `ex_` ID，非法、无关或多值 URL 回退内置映射。
- 新增 3 项离线回归，覆盖专家映射与深链解析、LOCAL 专家事件链、桌面领奖路由；测试总数 22 项。

## v3.18（2026-10-23）

- 所有任务动作前先做一次达标补领预扫，避免任务已达标但 `accept_status` 尚未变为 `completed` 时被动作处理器跳过；全部动作结束后再清扫一次，收取本轮新达标奖励。
- 动态任务发现同时扫描桌面与小程序口径，按 `task_code` 去重；单侧请求失败不会阻断另一侧、未覆盖任务检测或后续补领。
- 批量接受读取失败只跳过本轮登记，不再中断整个账号任务。
- accept 回读只接受 `accepted / completed / claimed`；查询失败不再因陈旧或空状态被误判成功。
- 达标补领逐任务隔离异常，一项网络错误不会阻断后续任务的 claim。
- 保留远端 `claimed` 与 `already_claimed` 幂等语义，不额外维护会与服务器漂移的本地领取缓存。
- 新增 4 类离线回归，测试总数 19 项。

## v3.17（2026-10-23）

- 修复网页真实对话只建会话、不收尾的问题：成功轮次调用 `POST /console/webchat/conversations/{id}/stop`，失败轮次再调用 `/delete`，释放上游活跃会话名额。
- 检测 HTTP 429 / code `10105` 的活跃会话上限；触发时先停止同任务前缀的最旧活跃会话，最多保留 8 条，再重建一次。
- 清理动作全程 best-effort，收尾失败不会把已经完成的对话改判为失败。
- 每日活跃真实对话兜底、专家/技能/夜猫子/旅行等所有 `webchat2` 调用都会自动收尾，避免跑满 10 条后永久报 10105。

## v3.16（2026-10-22）

- 按 10-22 双版本真机抓包，清理任务上报体中的 `wb2api-*`、`wbmp-*`、`wb-daily-*` 等自证前缀。
- 会话 ID 改为官方形态：`conversationId` 使用 UUID v4，`requestId` 使用 32 hex 毫秒前缀 ID；小程序/桌面合成事件同步修正。
- 桌面指纹对齐国内版 5.8.3：更新 UA、ideVersion/extVersion、commit、releaseDate、osVersion、CPU 型号/核数和内存，并补 `qimei36`。
- 桌面 `/v2/report` 的 `X-Domain` 改为裸主机名 `copilot.tencent.com`，`X-Request-ID` 改为官方 32 hex 形态。
- 小程序公共 base 版本更新到 2.4.0；专家/灵感事件仍按官方发射点保留 2.2.8。
- 每日活跃改为以热力图 `score > 0` 为成功判据；HTTP 200 不再直接写当天完成标记。未计分时默认补一次真实对话再回读，仍失败则下轮重试。
- 签到业务码 `10001` 改为先回读 `today_checked_in`，只有状态确认已签才报成功。

## v3.15（2026-10-21）

- 领奖改为按 `progress.current >= progress.target` 扫描，修复任务已达标但 `accept_status` 尚未变为 `completed` 时永远不领的问题。
- 桌面与小程序两种任务口径分别扫描、按 `task_code` 去重，并使用各自的领奖请求头。
- 将 `WorkBuddy_Valorant` 纳入已知任务名称和未覆盖任务检测。
- 签到改为先查活动状态，只在活动期且今日未签时领取；网络失败、空回执和业务错误不再写成签到成功。
- 猫猫旅行领取改用状态回执里的 `record_id`，并以实际 `reward_credit` 确认到账。
- 补签改用服务端日历、活动上线日和当月边界选日期，排除已补记录；默认每轮只补最近一张，补签后刷新连签再兑换。
- 补偿和新手礼包增加先查后领、真实 `credit` 判定及本地期次防重，不再把可领额度冒充本次到账。
- 企业形态仍以账号 `enterpriseId` 为准；后续真机 A/B 已证伪 `token_source=enterprise_switch` 代表企业上下文，因此不按该 JWT 声明跳过行为任务。
- 新增 Server酱微信推送（`SERVERCHAN_SENDKEY`，含 `sctp` 方糖专属通道），由社区 PR #32 贡献。
- 新增 6 项离线回归测试，覆盖进度补领、签到判定、旅行 `record_id`、补签候选、补偿本地防重和 Server酱地址解析。

## v3.14（2026-10-20）

- 国内版每日签到后补发一条 `chat_request_send` 活跃事件，点亮成长中心活跃连登与热力图。
- 修复签到连签增长、但成长中心连登长期停在 0，进而导致补签卡误判的风险。
- 活跃上报失败只作为附加项提示，不影响签到成功结果。
- 新增 `WorkBuddy_Valorant`：无畏契约拼豆专家真实对话、召唤链与使用事件自动完成。

## v3.13（2026-10-19）

- 连登管家闭环对齐：按「补签 → 档位兑换 → 抽奖」执行，兑换刚获得的抽奖次数会在同一轮直接抽完，不再等到下一次运行。
- 猫猫旅行闭环对齐：到达后领奖成功会继续检查状态，满足条件时当轮立即派出下一趟；旅行中、当日次数用尽等状态仍按原逻辑跳过。

## v3.12（2026-10-17）

- 对齐上游 v3.0.8 的两处签到链修正：
  - 修复：新手礼包 / 活动补偿改走国内计费站 `https://www.codebuddy.cn`（上游把这两条定为跨站端点，不再跟账号历史 endpoint 走）；
  - 修复：国际版每日活跃在「活动未开启但活跃已点亮」时不再记成未完成 —— 活跃上报（`/v2/report`）或网页会话任一成功即算当日已完成（积分由上游异步发放）；只保活对话成功仍不算。

## v3.11（2026-10-16）

- 新增：**DeepSeek 服务异常补偿（500 积分）按状态领取**（社区贡献 PR #26，感谢 @coderDJing）：
  - 先查 `POST /billing/meter/compensation-status`（`active` / `claimed` / `credit_num` / `end_time`），只对**开放且未领取**的账号发起领取；
  - 领取后回查状态确认到账，失败不再静默忽略；本次补偿截止 **2026-10-22 23:59:59（北京时间）**，建议尽快升级。
- 一致性：两个新请求补上 `verify=False`，与脚本其余调用同口径。

## v3.10（2026-10-16）

- 修复：**企业版账号门控**（对齐上游 panel 仓库 2026-10-07 A/B 实测口径）：
  - 企业版账号**没有个人成长体系**：签到返回 `400 code 10001`「企业账号不支持该操作」，成长域端点一律 `403`「growth system is only available for personal users」；
  - 此前脚本会把成长 profile 的 403 误报成「凭据失效」并整号跳过；现在会正确识别企业版，打印「🏢 企业版账号：无个人成长体系」，**跳过签到与全部成长/互动任务**（不再对注定被拒的端点空发请求），令牌续期照常；
  - 企业额度不在个人资源包体系内（`get-user-resource*` 对企业号返回空），请在企业后台查询。
- 杂项：补上 v3.9 漏掉的脚本头版本号。

## v3.9（2026-10-16）

- 修复：**国际版活跃链对齐上游 3.0.4–3.0.7 的实测口径**：
  - 新增**活跃上报**（`POST /v2/report`，`chat_request_send` 事件）—— 点亮成长中心「连登」的只此一条通道；`userId` 必填（缺失会被上游静默丢弃），并回读 `GET /activity/growth/streak` 给出连登天数；
  - 新增**网页会话**（`POST /console/as/conversations/`）—— 上游「当日活跃」计数只认这一发（桌面端对话与 `/v2/report` 都不进该计数，issue #277）；只带 Bearer + X-User-Id，剥掉全部桌面端指纹；
  - 保活对话请求头按国际版客户端 5.8.4 抓包对齐（会话头族 + 链路头族，渠道校验 11-128 不再拦），去掉旧的 `X-Session-ID` 特征头；
  - `--no-keepalive` 现在仍会做活跃上报，只是不发保活对话与网页会话。
- 任一发失败只作附加说明，**不把签到判成失败**；三发结果分开报告。

## v3.8（2026-10-16）

- 新增：`credits_report.py` 积分余额与到期时间报告（国内 / 国际版）：
  - 余额组成 + 逐笔到期时间（月度套餐月底清零、活动赠送 30 天逐笔到期）；
  - 支持 Bark / WxPusher 推送与到期预警窗口。
- 新增：`daily_forward.py` 可选工具：读取 Actions 运行日志、重建签到报告并按需转发（社区贡献 #25，感谢 @wuge0）。
- 新增：WxPusher（微信）内置推送渠道，配置 `WXPUSHER_APP_TOKEN` + `WXPUSHER_UID` 即启用（社区贡献 #25）。
- 文档：README 环境变量表、目录结构与可选工具说明同步更新。

## v3.7（2026-10-11）

- 新增：国际版设备授权登录工具 `workbuddy_intl_login.py`（适配 Google / GitHub / X 注册的纯 OAuth 账号，免手机号）：
  申请授权会话 → 浏览器授权 → 轮询换令牌 → 取账号信息，输出可直接填入 `WORKBUDDY_INTL_TOKENS`；
  支持 `--refresh '备注|AT|uid|RT'` 直接续期已有令牌。
- 安全：`.gitignore` 补上 `wb_intl_login_result.json`、`wb_intl_tokens.json`（含令牌的本地结果文件不入库）。

## v3.6（2026-10-10）

- 新增：国际版（www.workbuddy.ai）每日活跃脚本 `workbuddy_intl_daily.py`
  - 活动探测 → 条件领取 → 免费模型活跃保活，三步一条链；
  - 活动未开放按正常状态处理，保活成功与领取成功分开报告；
  - 支持多账号与可选 refresh token 自动续期（写回 `wb_intl_tokens.json`）；
  - 可用 `--no-keepalive` 只探测与领取，`--only N` 单账号运行。

## v3.5（2026-10-08）

- 青龙默认通知兼容新版应用授权（OpenAPI /open/auth/token + PUT /open/system/notify）。
- 环境变量表与通知说明补齐青龙新版 / 旧版两种方式。

## v3.4（2026-10-07）

- 归档开学季活动与校园日实现快照（活动已结束）。
