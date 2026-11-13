# 门户 GraphQL 分段导出：边到边入仓（子树封存）设计

本轮只交付设计与可重跑观察证据，不修改 collector.py / schema.py / samples.py / probe.py，
不开发新采集器。文中所有"预测"均为对拟议状态机的推理结论，未冒充执行结果；
原实现的实际输出以 evidence.json 记录为准（`python3 checks.py` 会逐条重放核对）。
释放条件与崩溃论证按 CONTRACT.md 要求留待人审。

## 1. 目标

现行采集器把每个导出的全部行压到结束帧才入仓（整包封存）。主管要求：已经稳定的站点
不等整包收尾，按响应路径边到边入仓。新发布契约：

- 保留 `rows(capture)` 的完整导出结果（完成后内容不变，未完成仍为 None）。
- 新增 `certified(capture)`：返回已确定的 `[响应路径, 行]` 列表与是否已完整结束；
  响应路径用 Reading 的绝对位置 `["stations", s, "readings", j]`，不用 rid 充当身份。
- "已确定"是严格语义：对已连续消费前缀的每一种合法成功续帧，该路径都存在且行内容相同。
  每推进一个连续前缀，恰好释放全部满足该定义的行；已释放行不可撤回、不可替换。
- 入仓行与未完成子树有不同的持久归属；释放后输入原文与工作树中该子树载荷可删除。
- 全部状态仍在原 SQLite 库内，不另设消息系统。

## 2. 现状调用链与三个边界（collector.py 行号定位）

**接纳边界 `accept()`（L24–42）**：L25 用
`json.dumps(frame, sort_keys=True, separators=(",", ":"))` 得到帧体（注意：未带契约身份
公式要求的 `ensure_ascii=False, allow_nan=False`，非 ASCII 帧下与契约身份不一致，见 §5）；
L26 解出 capture/seq/wire；L27 `BEGIN IMMEDIATE`；L29–31 按 `(capture,seq)` 查 inbox 判重；
L32–36 命中时比对帧体，异内容抛 `FRAME_CONFLICT`（L33–34），同内容直接 COMMIT 返回（L35–36，
幂等重放）；L37 登记 inbox；L38 进入 `_apply`；L39 COMMIT；L40–42 任何异常 ROLLBACK，
保证同号冲突的原子拒绝（checks.py L21–28 的断言对象）。

**应用边界 `_apply()`（L44–90）**：L45–51 首帧 data 分支，L47–48 把整棵响应树 JSON 压入
slot（键为 wire），L49–51 对初始 readings 调 `_stage`；L52–54 非首帧按 wire 取回整树；
L55–57 登记 pending（`INSERT OR REPLACE`，主键只有 id，见 L14–15）；L58–80 逐补丁：
L59–61 用补丁 id 反查任务路径，L62–71 stations 流 items（L64–65 用 None 补洞，L66–68 写元素，
L69–70 暂存其 readings），L72–73 按 `path[1]` 定位站点，L74–76 readings 流 items 暂存，
L77–78 merge 把 code 并入树节点，L79–80 error 仅把树节点置 None；L81–82 按 completed
删除 pending；L83–84 整树写回 slot；L85–90 若 `hasNext=false` 则发布：L86–88 按 wire 收集
stage 全部行，L89–90 整体写入 published（主键 capture，L20–21）。

**发布边界 `rows()`（L98–101）**：只读 published；未发布返回 None。`_stage()`（L92–96）
按 `(wire, station_pos, reading_pos)` 覆盖写 stage（L16–19）。`state()`（L103–115）导出五表。

链路：`accept → inbox 判重(L29–37) → _apply(L38→L44) → slot/pending/stage(L47–84)
→ 结束帧发布(L85–90) → published → rows()`。

### 2.1 原实现的实际输出 vs 业务期望（证据锚点）

以下"实际"均为 evidence.json 中 probe.observe 对原实现的真实记录，"期望"为
samples.EXPECTED 的独立业务期望：

| 录制 | 实际（证据条目） | 期望 | 偏差根源（行号） |
|---|---|---|---|
| normal 全程 | A=两行 north（evidence 1） | 同 | 无 |
| normal 前2帧 | A=None（evidence 2） | 未完成 | 无 |
| delayed 全程 | A=两行但 code=null（evidence 11） | north 两行 | merge 只改树（L77–78），stage 行在 L75/L95 写入时 code 为 None 且永不回填 |
| late_null 全程 | A=两行 north（evidence 13） | A=[] | error 只置树节点 None（L79–80），已暂存行不失效，L86–90 照样发布 |
| gap 全程 | A=[]（evidence 14） | A=None | 无连续前沿，L38 对乱序帧立即执行，结束帧先到即发布空结果 |
| reused 全程 | B=两行 north（evidence 15） | B=[] | stage 以 wire 为键（L16–19）且从不清理，A 的暂存泄漏给复用同槽的 B |
| interleaved 全程 | 第3帧 IndexError、第5帧 TypeError，A=B=[]（evidence 16） | A=north 两行、B=east 两行 | pending 主键只有 id（L14–15），B 的登记覆盖 A 的同名任务，L59–61 反查到错捕获的路径 |
| cancelled 全程 | A=三行（含 null 站的 late 行）（evidence 17） | A=[] | 同 late_null，且 L74–76 继续接受已取消任务的迟到补丁并暂存 |
| holes / handoff / progress 全程 | 与期望一致（evidence 18/19/7） | 同 | 无 |

另有四个前缀级证据：progress 前4帧（evidence 4）r 已完成、s 未完结，两行 north 已在
stage 而 published 为空——原实现把已确定的行扣到第6帧；delayed 前2帧（evidence 8）暂存行
code 为 null（元数据未到）；late_null 前2帧（evidence 12）两行已暂存但任务仍开口；
progress 前4帧 reopen（evidence 9）证明逐帧 backup 重开后磁盘状态一致接续。

## 3. 可判定释放条件（对任意合法前缀成立，既不提前也不拖延）

### 3.1 子树归属与影响通道

行身份 = 捕获 c 内 Reading 的绝对位置 (s, j)。能改变 (c, s) 子树的通道只有四类，
且全部在创建帧封闭登记（契约：创建 Station 时其全部待补任务同帧登记，事后不能凭空加任务）：

1. 初始 data（创建帧本身，消费后即固定）；
2. stations 流：契约保证不改写旧元素、只在创建本次 items 时报告其错误——创建帧之后关闭；
3. 该站 readings 流任务：开口期间可写 items、可报 error；
4. 该站 code 延迟任务：开口期间可 merge 一次、可报 error。

### 3.2 释放谓词

对捕获 c、站点位置 s，定义 P(c, s) 同时成立：

- (a) s 的创建帧已进入连续前缀（station 行存在）；
- (b) 该站未被 null 覆盖；
- (c) (c, s) 名下无 open 任务（readings 任务已 completed；code 任务不存在或已 completed）；
- (d) station.code 已就位（合法输入下由 (c) 蕴含，作防御判据，防止把未到元数据猜成内容）。

释放动作：把 (c, s) 的全部 Reading 按 reading_pos 升序写入 certified，行内容
`[code, rid, value, quality]` 中 code 取释放时的 station.code（此时已一次写定）。

**不提前**：(c) 任一部件开口时，存在改变该行的合法续帧——readings 任务开口时，源可对其
尚未交付的叶报 error，经 schema.py 的 NON_NULL 链（value→Reading 非空→readings 非空→
Station 可空）整站置 null（late_null 第3帧正是如此使前两行失效）；code 任务开口时，源可
merge 改写 code（delayed 第3帧）或对 code 报 error 置 null。此时释放的行要么内容可变、
要么存在性可灭，违反严格定义。(d) 开口时行内容根本未成形（evidence 8 中 code=null）。

**不拖延**：P 成立时逐条核对 §3.1 的通道——创建帧已消费；stations 流对旧元素关闭；
readings 任务 completed，契约保证不再有合法补丁改变已完成任务，而 error 只能落在任务
尚未成功交付的叶，completed 后不存在这样的叶；code 任务 completed（merge 一次写定）或
根本不存在。于是每一种合法续帧下 (s, j) 行都存在且内容相同，继续扣留即违反"恰好释放"。
P 在每帧应用后对受影响站点重估，满足即释放，无需等 hasNext=false。

### 3.3 三个必须判对的录制点

- **progress 第4帧（应释放）**：第2帧创建站0（code=north 随帧就位，无 code 任务）并登记 r；
  第3帧 r 交付两行；第4帧 `completed=["r"]`。此刻 (c) 成立：r 已完结，stations 任务 s 虽开口
  但契约保证它不改写旧元素、也不事后报旧元素错误。任何合法续帧都含这两行 → 必须释放
  `[["stations",0,"readings",0],["north","same",10,"ok"]]` 与
  `[["stations",0,"readings",1],["north","same",0,"ok"]]`，complete=false。
  原实现实际行为（evidence 4）：published 为空、两行压在 stage，直到第6帧才放行——这正是
  主管要消除的"等整包收尾"。（预测：拟议方案在第4帧所在事务释放这两行。）
- **delayed 第2帧（不得释放）**：code 任务 m 与 readings 任务 r 双开口，行内容未成形
  （evidence 8 实际暂存 code=null）。第3帧 merge 落地、m 完结后 r 仍开口（evidence 9），
  仍不得释放；到第4帧 `completed=["r"]` 才释放。（预测：释放行为 north 两行。）
- **late_null 第2帧（不得释放）**：r 开口（evidence 12），第3帧的 error 使整站置 null、
  前两行失效。若第2帧释放则必须撤回，违反不可撤回。（预测：certified 始终为空，
  完成后 rows("A")=[]，与期望一致；原实现实际却发布了这两行，见 evidence 13。）

## 4. 持久状态与索引（同一 SQLite 库）

```sql
CREATE TABLE export(            -- 每捕获一行：输入账的连续前沿
  capture TEXT PRIMARY KEY,
  wire TEXT NOT NULL,           -- 仅记录，不作键（连接槽可复用）
  frontier INTEGER NOT NULL DEFAULT 0,   -- 已连续应用的前缀 [1..frontier]
  final_seq INTEGER,                     -- 见过的 hasNext=false 帧号
  done INTEGER NOT NULL DEFAULT 0);
CREATE TABLE inbox(             -- 输入账：摘要判重，原文可清
  capture TEXT NOT NULL, seq INTEGER NOT NULL,
  digest TEXT NOT NULL,         -- 契约身份：json.dumps(frame, sort_keys=True,
                                --   separators=(',',':'), ensure_ascii=False,
                                --   allow_nan=False) 的 UTF-8 SHA-256
  body TEXT,                    -- 未应用的停放帧保留原文；应用后同事务置 NULL
  PRIMARY KEY(capture, seq));
CREATE TABLE task(              -- 工作态：任务按捕获归属
  capture TEXT NOT NULL, id TEXT NOT NULL,
  kind TEXT NOT NULL,           -- stations | readings | code（由登记路径形状判定）
  station_pos INTEGER,          -- stations 流为 NULL
  state TEXT NOT NULL,          -- open | completed | cancelled（取消即墓碑）
  PRIMARY KEY(capture, id));
CREATE INDEX task_by_station ON task(capture, station_pos);
CREATE TABLE station(           -- 工作态：未释放子树的根
  capture TEXT NOT NULL, pos INTEGER NOT NULL,
  code TEXT,                    -- NULL = 未到（code 任务开口），非 null 即一次写定
  nulled INTEGER NOT NULL DEFAULT 0,
  released INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(capture, pos));
CREATE TABLE reading(           -- 工作态：未释放、仍可被 null 覆盖的暂存行
  capture TEXT NOT NULL, station_pos INTEGER NOT NULL, reading_pos INTEGER NOT NULL,
  rid TEXT NOT NULL, value INTEGER NOT NULL, quality TEXT,   -- 显式 null 原样保存
  PRIMARY KEY(capture, station_pos, reading_pos));
CREATE TABLE certified(         -- 入仓结果：只追加，不更新，不删除
  capture TEXT NOT NULL, station_pos INTEGER NOT NULL, reading_pos INTEGER NOT NULL,
  code TEXT NOT NULL, rid TEXT NOT NULL, value INTEGER NOT NULL, quality TEXT,
  PRIMARY KEY(capture, station_pos, reading_pos));
```

与旧五表对应：inbox 增加 digest、body 可空；slot 的整树 JSON（L12–13）拆解为
station/reading 规范化行，消除"释放后子树仍占暂存"的结构根源；pending 主键改为
(capture, id)；stage 按捕获归属并随释放删除；published 由 certified + export.done 取代。
四态区分落在：code 未到（station.code IS NULL 且 code 任务 open）、列表空洞（reading 中
该位置无行）、显式 null（quality 列存 NULL、行存在）、空列表（该站 reading 零行）。

## 5. 事务内语句级伪代码

每次 accept 一个事务（沿用 L27/L39/L40–42 的 BEGIN IMMEDIATE / COMMIT / ROLLBACK 骨架）。

```
accept(frame):
  body = canonical(frame)                 # 契约公式序列化
  digest = sha256(body.utf8)
  BEGIN IMMEDIATE
  old = SELECT digest FROM inbox WHERE capture=? AND seq=?          -- PK 命中
  if old:
      if old.digest != digest: ROLLBACK; raise FRAME_CONFLICT       -- 同号冲突，原子拒绝
      COMMIT; return                       -- 幂等重放（已应用或仍停放），不重复执行
  INSERT INTO inbox VALUES (capture, seq, digest, body)
  INSERT OR IGNORE INTO export(capture, wire, frontier) VALUES (?, ?, 0)
  if not frame.hasNext:
      UPDATE export SET final_seq=? WHERE capture=?                  -- 只记账，不执行
  drain(capture)                           -- 连续前沿推进，见下
  COMMIT

drain(capture):
  loop:
      f = SELECT seq, body FROM inbox
          WHERE capture=? AND seq = frontier+1 AND body IS NOT NULL  -- PK 命中，不扫历史
      if none: break
      affected = apply_frame(f)            -- 返回本帧触及的站点集合
      UPDATE inbox SET body=NULL WHERE capture=? AND seq=f.seq       -- 原文清除
      UPDATE export SET frontier=frontier+1 WHERE capture=?
      release_eval(capture, affected)      -- 安全发布 + 载荷释放 + 任务退场，同事务
      if frontier == final_seq:            -- 终态转移
          UPDATE export SET done=1 WHERE capture=?
          DELETE FROM task WHERE capture=?           -- 剩余墓碑与 stations 流退场
          DELETE FROM station WHERE capture=?        -- reading 此时必空（已释放或已失效）

apply_frame(f):                              -- 整帧解释，completed 不作发布屏障
  if f.data:  for i, st in enumerate(f.data.stations) where st is not None:
      INSERT INTO station VALUES (capture, i, st.get('code'), 0, 0)
      for j, r in enumerate(st.readings): INSERT INTO reading VALUES (...)
  for e in f.pending:  INSERT INTO task VALUES (capture, e.id, kind(e.path),
                                                station_of(e.path), 'open')
  for p in non_error_patches(f):             -- 先数据后错误，null 优先
      t = SELECT * FROM task WHERE capture=? AND id=p.id             -- PK 命中
      if t.state == 'cancelled': continue    -- 迟到取消补丁：接受并记账，不复活子树
      if t.kind == 'stations':
          for off, item in enumerate(p.items, start=p.at) where item is not None:
              INSERT INTO station VALUES (capture, p.at+off, item.get('code'), 0, 0)
              暂存 item.readings 到 reading
      elif t.kind == 'readings':
          if station(t.station_pos).nulled: continue                 -- 同帧已置 null
          INSERT INTO reading VALUES (capture, t.station_pos, p.at+off, ...)
      elif t.kind == 'code':                 -- merge，一次写定
          UPDATE station SET code=p.data.code
          WHERE capture=? AND pos=t.station_pos AND nulled=0
  for p in error_patches(f):                 -- p.path 是出错叶全路径，不按任务登记路径找
      i = p.path[1]                          -- code/value 经 NULL_CHAIN 都冒泡到 Station
      UPDATE station SET nulled=1 WHERE capture=? AND pos=i
      DELETE FROM reading WHERE capture=? AND station_pos=i          -- 已暂存行失效
      UPDATE task SET state='cancelled'
      WHERE capture=? AND station_pos=i AND state='open'             -- 含同帧新登记任务
  for token in f.completed:
      UPDATE task SET state='completed'
      WHERE capture=? AND id=token AND state='open'                  -- 取消墓碑吸收 token
  return affected                            -- 创建/补丁/完结/置 null 触及的站点

release_eval(capture, affected):             -- §3.2 谓词 P 的逐站点判定
  for pos in affected:
      st = SELECT code, nulled, released FROM station WHERE capture=? AND pos=?
      if st missing or st.nulled or st.released: continue
      open_cnt = SELECT COUNT(*) FROM task
                 WHERE capture=? AND station_pos=? AND state='open'  -- 走 task_by_station
      if open_cnt > 0 or st.code IS NULL: continue
      for row in SELECT reading_pos, rid, value, quality FROM reading
                 WHERE capture=? AND station_pos=? ORDER BY reading_pos:
          INSERT INTO certified VALUES (capture, pos, row.reading_pos,
                                        st.code, row.rid, row.value, row.quality)
      DELETE FROM reading WHERE capture=? AND station_pos=?          -- 暂存载荷释放
      DELETE FROM task WHERE capture=? AND station_pos=?             -- 任务退场
      UPDATE station SET released=1 WHERE capture=? AND pos=?
```

读取接口：

```
certified(capture):
  rows = SELECT station_pos, reading_pos, code, rid, value, quality
         FROM certified WHERE capture=? ORDER BY station_pos, reading_pos
  return {"complete": export.done == 1,
          "rows": [[["stations", s, "readings", j], [code, rid, value, quality]], ...]}

rows(capture):                                -- 保留旧契约的完整导出结果
  if export.done != 1: return None            -- 中断保持 incomplete，不伪造完整回执
  return [[code, rid, value, quality], ...]   -- 同 certified 顺序，即响应路径顺序
```

## 6. 重开恢复顺序与持久化中断

1. 打开同一 SQLite 库，`CREATE TABLE IF NOT EXISTS` 幂等建表（对应 L8–22）。
2. 无需重放/重做：每个 accept 单事务提交，SQLite 回滚日志在 open 时自动收拾未提交事务，
   状态恒等于最后一个已提交 accept。frontier、final_seq、done、certified 全是持久事实，
   不从内存重建，也不扫全库。
3. 崩溃点分类：提交前崩溃 → 帧未入账，重收时正常走接纳；提交后崩溃 → 重放该帧时
   digest 命中，幂等返回。原文清除、任务退场、安全发布在同一事务，不存在"已发布但
   暂存未删"或"已删暂存但未发布"的中间态；已释放行不会因重开丢失或重复释放
   （certified 主键 + released 标记双保险）。
4. 恢复后第一帧的处理与正常路径完全一致：digest 判重 → 缺口停放 → drain 续推进。
   evidence 3、9（reopen=True 逐帧 backup 重开）表明原实现已具备磁盘状态一致接续的
   观察基础，拟议方案把这一性质收敛为"单事务 + 幂等重放"两条规则。

## 7. 联合判据：补丁归属、可空传播、可恢复发布、载荷释放

四者是 frontier 推进这一**同一个状态转移**的不同结果，在同一事务内提交，不做事后
垃圾回收服务：

- **补丁归属**：补丁先按 `(capture, id)` 归到任务（跨导出同名不串），再按任务登记的
  绝对路径归到子树；items 写绝对位置不覆盖已写位置；merge 一次写定；归属到 cancelled
  墓碑的补丁接受入账但不产生任何效果（不复活子树）。
- **可空传播**：error 以叶全路径定位站点，按 schema.py 的 NULL_CHAIN 冒泡到最近可空点
  （两类 field 都止于 Station），同事务内：置 nulled → 删该站 reading 暂存 → 其 open
  任务转 cancelled；帧内先应用数据补丁再应用 error，保证 null 覆盖同帧数据与新 pending。
  已释放行永不参与传播——P 保证释放后不存在合法 error 通道（§3.2），故无需撤回机制。
- **可恢复发布**：certified 追加与上述转移同事务提交；certified() 随时可读已确定前缀，
  complete 由 export.done 独立给出，中断时不伪造完整回执。
- **载荷释放**：释放即 `DELETE reading` + `DELETE task` + `inbox.body=NULL`（应用帧时），
  与 certified 追加同生同事务；已释放子树在暂存区零残留（对照：原实现 stage 从不删除，
  reused 录制中 A 的暂存被 B 原样发布，evidence 15）。

## 8. 六类乱象下的成立性

1. **乱序重放**：digest 命中即幂等返回，无论该帧已应用还是仍停放；未命中且 seq 越过
   缺口则只登记停放。drain 严格按 frontier+1 取帧，缺帧不越过后帧（gap 的反例固化）。
2. **同号冲突**：同 `(capture, seq)` 异 digest → ROLLBACK 抛 FRAME_CONFLICT，库态零变化
   （沿用 L33–34/L40–42 语义，checks.py 的原子断言继续成立）。清掉原文后凭 digest 仍
   可判定旧重放与冲突，不必永久保存全部原文。
3. **结束帧先到**：hasNext=false 帧在接纳时只登记 final_seq，body 停放在 inbox；
   frontier 未推进到它之前不执行、不发布。gap 录制（evidence 14：原实现实际输出 []，
   期望 None）在拟议方案下预测为 rows("A")=None、certified complete=false；
   第2帧补齐后同一 drain 事务连续应用两帧并收尾。
4. **任务同名**：task 主键 (capture, id)，A、B 各自的 "r" 互不覆盖（interleaved 的
   IndexError/TypeError 根源消除，预测 A=north 两行、B=east 两行）。
5. **连接复用**：全部工作态按 capture 归属，wire 仅是记录字段；旧导出的迟到重传由
   (capture, seq) 的 digest 账去重，够不到新导出的子树；新导出复用同号 wire 不继承任何
   暂存（reused 下预测 B=[]）。
6. **持久化中断**：见 §6；inbox 摘要、export 前沿、certified 行三者足以无歧义续跑，
   中断可保持 incomplete，不伪造完整回执。

## 9. 六类状态去向

- **未到元数据**：code 未到 = station.code 为 NULL 且 code 任务 open，阻塞 P（§3.2(d)）；
  不把未到猜成内容，也不把列表空洞当 null。merge 落地后 code 一次写定。（delayed）
- **任务转交**：handoff 第2帧同帧交付 stations 流最后 items、登记 r、completed s——
  整帧在同一事务解释：s 退场，站0的命运转交 r；发布评估在整帧应用之后进行，completed
  不单独充当发布屏障。
- **空洞补齐**：reading 允许位置空洞（holes 第2帧先写位置2）；空洞中的行是"仍可能被
  null 覆盖的载荷"，允许保留但不得释放；任务 completed 时源保证 0..max 无洞，整批释放。
- **迟到取消补丁**：任务取消后保留墓碑，迟到补丁/迟到 completed 接受并记账、效果为空；
  墓碑随释放或终态转移退场。（cancelled，预测 rows("A")=[]）
- **跨导出任务同名**：见 §8.4，任务、站点、暂存、入仓全部按 capture 隔离。
- **连接复用**：见 §8.5，wire 不作任何持久键；export.wire 仅供诊断。

## 10. 整包封存 vs 子树封存

| 维度 | 整包封存（原实现） | 子树封存（拟议） |
|---|---|---|
| 释放时机 | hasNext=false 单点（L85–90） | 每个连续前缀推进后按 P 逐站判定 |
| 稳定站点等待 | 等最慢任务/整包收尾（progress 第4帧证据） | 任务闭环即入仓 |
| 撤回风险 | 无（但以扣留全部行为代价） | 无（P 的充分性论证，§3.2） |
| 暂存占用 | 整包生命周期内单调增长，且发布后不清（reused 泄漏） | 以开口子树为界，释放即删 |
| 崩溃恢复 | 结束前无可入仓结果 | 已释放行持久，重开续跑 §6 |
| 判定成本 | 每帧 O(1) 但语义错误集中在边界（gap/interleaved/cancelled 实证） | 每帧 O(帧大小+受影响站点+释放/失效行) |
| 正确性风险 | 边界语义错（实证六例） | 风险集中于 P 的判定，需人审 |

取舍结论：子树封存用"每帧一次受限判定"换掉"整包等待 + 暂存泄漏"，其正确性依赖契约的
两条保证——创建帧封闭登记任务、completed 任务不再有合法变更；这两条正是 P 的支柱。

## 11. 每帧工作量与保留状态

每帧只访问：inbox 一行（PK）、export 一行（PK）、本帧 pending/patches/completed 涉及的
任务行（PK）、受影响站点行（PK）、释放或失效涉及的 reading 行（task_by_station 与
reading 主键前缀）。不扫全部历史 pending、不扫已入仓行、不扫整棵响应树。索引在建表时
一次建立。不可避免的大失效范围：某站 readings 已大量暂存时来 error，需删该站全部暂存行，
规模以该子树为界，属帧内容固有成本。保留状态：export 前沿、inbox 摘要（仅停放帧带原文）、
open 任务、未释放站点与暂存行、certified 入仓行；终态后仅剩 export 行、摘要账与 certified。

## 12. 拟议方案预测汇总（非执行结果）

对全部完整录制，预测 rows(capture) == samples.EXPECTED（含 delayed=north 两行、
late_null=[]、cancelled=[]、reused B=[]、interleaved A/B 各两行、gap 补齐前 None）；
progress 前4帧预测 certified 放出 north 两行且 complete=false；delayed/late_null 前2帧
预测 certified 为空。以上与 evidence.json 记录的原实现实际输出分列，互不冒充。

## 13. 证据与重跑

- `python3 observe_cases.py`：只调用 probe.observe 重新生成 evidence.json（19 条，
  覆盖 §2.1 全部锚点：正常/前缀/重开/四个关键前缀点/六个偏差录制/空洞/转交）。
- `python3 checks.py`：重放 evidence.json 逐对象核对（DESIGN.md 存在时启用），
  并保留原业务断言。
- `python3 probe.py CASE [N]`：单点复查任一录制前缀的五表内容。

## 14. 范围外

不实现新采集器、不做形式化证明工具、不设计通用坏协议校验器；释放谓词 P 的充分性论证、
崩溃恢复论证与 evidence 覆盖度按契约留待人审。
