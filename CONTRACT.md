# 门户分段导出：本题冻结边界

本仓库是门户采集接入的可执行参考基线，只有 Python 标准库和 SQLite。Collector.accept(frame) 是传输适配器调用入口；probe.py 从 samples.py 读取已经完成 JSON 解码的录制帧，不涉及 HTTP 分包解析。collector.py 中 inbox 是输入账，slot/pending/stage 是工作态，published 是本地入仓结果，全部位于同一 SQLite 数据库。本轮不增加服务、远端确认、跨库投递或旧库在线迁移，也不实现新采集器。

## 响应语义

这是门户网关冻结的 GraphQL 分段配置，不是通用 GraphQL 客户端。schema.py 的 NULL_CHAIN 是网关提供的可空信息。响应树为 stations:[Station]!，Station.code:String!，Station.readings:[Reading!]!；Reading.rid:String!、value:Int!、quality:String。stations 的元素可为 null。code 可以由稍后补丁提供；quality 在 Reading 创建时即存在，允许显式 null。未到达的 code、尚未填的列表位置、显式 null、空列表是四种不同状态。

每帧含 capture、seq、wire、hasNext，其他键见 samples.py。capture 是全局唯一的一次导出，seq 从 1 连续编号；传输可能乱序、重复或迟到，wire 是可以复用的连接槽号。不同 capture 可以并行，各自的 pending.id 可相同，同一 capture 内不重用 id。一个 capture 的 wire 固定，结束后同号 wire 可以服务下一导出，前导出的重传仍可能随后到达。首帧含 data；最后一帧 hasNext=false，其 seq 是该导出的最后编号。它先到不代表前面的缺帧已经收到。正常完整输入最终会补齐全部缺帧。

pending 是本帧新登记的待补任务，path 为绝对响应路径。路径含字段名和从 0 起的列表位置。只有三类任务：stations 的流、某站 readings 的流、某站 code 的延迟任务。创建一个新 Station 时，其所需的全部待补任务必须同帧登记；后续不能凭空给一个已有站点添加任务。创建 Station 的帧也可以同时含它的补丁。一个任务可以在发出最后一段数据的同帧 completed；该帧还可能引入新 Station 及其任务。必须解释整帧，不能把 completed 单独当作发布屏障。

patches 中 items 对流任务写入从 at 起的连续位置；位置可以跳跃后补洞，但同一流不覆盖已写位置。stations 流创建完整或带待补字段的 Station；readings 流创建 Reading。初始 data 中的列表内容也占用位置。流完成时，从 0 到最大已写位置必须无洞；无数据即空列表。stations 流创建的旧元素以后不会再由该流改写，对旧元素的后续影响只能来自创建时登记的任务。merge 属于 code 延迟任务，把 code 写入该 Station，一次写定。

error 的 field 为 code 或 value，path 是实际出错叶的完整路径；不能只按任务登记路径找失效位置。按 schema.py 的 NON_NULL 链向上冒泡，在最近可空处写 null；因此 code 错误使该 Station 为 null，任意 value 错误经过非空 Reading、非空 readings 也使所属 Station 为 null。错误只能发生在该任务尚未成功交付的叶；late_null 是后续第 3 个 Reading 出错，继而使先前已交付的两行也失效，并非已交付的 value 被改写。stations 流只会在创建本次 items 的 Station 时报告其错误，不会事后报旧元素的错误。错误传播可以覆盖同帧数据及新 pending，null 优先。已被 null 覆盖的任务仍可能收到先前排队的补丁或 completed：接受并记账，但这些数据不能复活子树。源端保证除已取消任务外，所有任务在最终帧前或最终帧内 completed；不存在新的合法补丁再改变已完成任务。协议保证源帧合法，采集器仍须拒绝同号异内容的重放；不要求设计通用坏协议校验器。

完整导出的目标是按响应路径依次展开所有非 null Station 的 readings，输出 [code,rid,value,quality]。相同 rid 甚至完全相同行仍保留各自位置；不能按业务字段去重。空 Station 不输出行。samples.EXPECTED 是各完整录制的独立业务期望，gap 表示目前缺少第 2 帧，所以仍未完成。

## 要讨论的新发布契约

旧 rows(capture) 只在整包结束后给出行。方案要保留其完整导出结果，并提出 certified(capture) 的持久化设计：返回已经确定的 [响应路径,行]，以及是否已完整结束；响应路径使用 Reading 的绝对位置，不使用 rid 充当身份。所有本地发布仍在原 SQLite 库内，不另设消息系统。

“已经确定”是严格语义：对已连续消费前缀的每一种合法成功续帧，该路径都存在且行内容相同。每推进一个连续前缀，都必须释放恰好全部满足此定义的行；不能为了省事等 hasNext=false，不能撤回或替换已经释放的行。遇到缺帧不能越过缺口执行后帧。导出中断可以保持 incomplete，不能伪造完整回执；本题只承诺对最终补齐的合法输入完成采集。

入仓行和未完成子树必须有不同的持久归属。释放后，输入原文及工作树中的该子树载荷应可删除；正式入仓行属于必要结果，可以继续保存。还在补洞、尚可能被 null 覆盖的载荷允许保留。帧身份按 json.dumps(frame,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False) 的 UTF-8 字节之 SHA-256 判定，本题将摘要相等视为同一身份；清掉原文后仍须判定旧重放及冲突，不得永久保存全部原文充当恢复方案。

方案不能每来一帧扫描所有历史 pending、所有已入仓行或整棵响应树。首次登记允许建立索引；后续只访问该帧的路径、其必要祖先、受影响任务和真正需要释放或丢弃的载荷。说明每帧工作量、保留状态及不可避免的大失效范围，不要求虚构固定内存上限。这里的原文删除、任务退场和安全发布是同一个状态转移的不同结果，必须联合考虑，不是另做垃圾回收服务。

DESIGN.md 要逐段引用 collector.py 的真实行号，解释接纳、应用、发布三个边界，提出必要表/索引、事务内语句级伪代码和重开恢复顺序，并比较整包封存与子树封存的取舍。给出对任意合法前缀成立的可判定释放条件，论证既不提前也不拖延。用 progress 第 4 帧说明为什么全局任务未结束仍应释放 north 的两行；delayed 第 2 帧和 late_null 第 2 帧均不得释放。对未到元数据、任务转交、空洞补齐、迟到取消补丁、跨导出任务同名和连接复用分别说明状态去向，不需要编写实现或形式化证明工具。

## 可重跑观察

python3 checks.py 核对正常完整输出、未结束不可读、逐帧备份重开、同帧重放和冲突原子拒绝。它不是新发布契约的正确性证明。
python3 probe.py CASE [N] 输出前 N 个录制帧调用原 accept 的实际结果、完整业务期望和五类表内容；省略 N 则处理全部。CASE 是 samples.cases() 的键，stdout 是一个 JSON 对象。probe.observe(CASE,N,reopen=True) 每帧后用 SQLite backup 复制到新连接并关闭旧连接，仅用于确定性磁盘状态接续观察，不冒充真实杀进程试验。

讨论证据放 evidence.json，形状为非空列表；每项含 case（录制名）、stop（整数或 null）、reopen（布尔）和 observed（对应 probe.observe 返回的完整对象）。它只记录原实现，不把拟议协议的预测冒充执行结果。可增加只调用现有入口的观察脚本，不得改 collector.py、schema.py、samples.py、probe.py 或删除 checks.py 的业务断言。DESIGN.md 存在时 checks.py 会重放 evidence.json 逐对象核对，方案的释放条件与崩溃论证仍需人审。引用原始序号说明预期与实际即可，不要求开发参考采集器、跑浏览器或搭建通用协议引擎。
