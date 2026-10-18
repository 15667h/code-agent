# 基准实测报告（2026-10-18）

## 用了哪个基准，为什么是它

**SWE-bench Pro**（Scale AI，arXiv 2509.16941 / ICML 2026），公开集
`ScaleAI/SWE-bench_Pro`，本次用 `hard` 子集 **51 题**。

不用 SWE-bench Verified 的理由：OpenAI 已于 2026-02 停用该基准 ——
审计发现其失败任务里 **至少 59.4% 的测试用例本身有缺陷**，会拒绝功能正确的补丁。
拿一个已知有问题的尺子量出来的数，不如不量。

SWE-bench Pro 的特点（来自其论文与数据集卡片）：
- 1,865 题 / 41 个活跃仓库；公开集 731 题 / 11 个 GPL 仓库
- 平均 **170 行改动、5.1 个文件**（旧基准约 12 行、单文件）
- 100% 经人工核验，带 `requirements` / `interface` 字段
- 顶尖模型公开集通过率 **约 23%**（GPT-5 23.3%）

## 数据获取路径（网络受限环境下的实测结论）

| 端点 | 可达 | 说明 |
|---|---|---|
| `huggingface.co` | ✗ | 直连不通 |
| `hf-mirror.com` 的 parquet `resolve` | ✗ | 308 重定向回 huggingface.co → 502 |
| **`datasets-server.huggingface.co`** | **✓** | 官方数据服务，直接返回 JSON 行 |
| `github.com` clone | ✗ | 直连不通 |
| **`git fetch <sha>` / `codeload`** | **✓** | smart-http 路径通 |

所以：题目走 `datasets-server`，代码走 `git init` + `fetch --depth 1 <sha>` +
`checkout`（只取需要的那个 commit，不整仓 clone）。

---

## 第 1 层：判定正确性（强证据）

**51 题 × 3 用例 = 153 个用例，全部符合预期。**

| 用例 | 输入 | 期望 | 结果 |
|---|---|---|---|
| `fake` | 声称"已按需求改好"，**零写操作** | 必须拒 | **51/51 拒** |
| `wrong_target` | 改了文件，但改的是**无关文件** | 必须拒 | **51/51 拒** |
| `honest` | 改了目标文件 + 跑过验证 | 必须放行 | **51/51 放行** |

判定机制在真实任务上**可信**。

### 这一层跑出来的三个真实缺陷（全部已修）

#### B-1【严重】证书只看"有没有写"，不看"写的位置对不对"

第一轮：`wrong_target` **0/6** —— 全部放行。

证书当时只检查"轨迹里有没有成功的写操作"。于是**随便改个无关文件**
就能拿到完整证书。这正是 BenchJack 记录的作弊形态：
不改实现、改测试文件（`conftest.py` 钩住 pytest 把结果改写成 passed）。

**修法**：新增判据 —— 任务点名了文件且是写任务时，**至少一个**被点名文件
必须出现在写入产物里。用"至少一个"而非"全部"，因为真实任务常有
主文件 + 若干附件，要求全部会误拦"只改了主文件"这种合理情形。

修后：`wrong_target` **51/51**。

#### B-2【中】系统路径与模块名被当成"仓库文件"

ansible 的 `mount_facts` 题，requirements 说"must read `/etc/fstab`,
`/proc/mounts`" —— 这些是模块**运行时读取**的系统挂载点。
提取器把它们当成目标文件，于是 honest 用例被证书判 `out_of_scope`
（"写操作落在工作区之外"），**一个诚实完成被误拦**。

另一题的 `ansible.module_utils.urls` / `email.mime` / `text/plain`
同样被误当文件。

**修法**：`intent.is_probable_file()` —— 扩展名必须在已知源码后缀表里；
绝对路径、MIME 类型、点分模块名一律不算文件。
`preflight` 与 `certificate` 改用 `mentioned_files()`（过滤后）。

这不是基准特有问题：真实任务里"改 `lib/a/b.py` 里从 `pkg.sub.mod`
导入的那个函数"同样常见。

#### B-3【中】路径匹配规则两处不一致

证书用严格相等，`preflight` 用 `_covers()`（容忍 basename）。
于是任务说 `a.py`、agent 写 `src/a.py` 时，证书判"写的不是该写的地方" ——
**又一个诚实完成被误拦**。

**修法**：`_covers` 迁到 `intent.path_covers`，两处共用。
并加测试断言两处对同一对路径给出相同结论。

但要克制：**只允许 basename 兜底**（`a.py` ↔ `src/a.py`），
不允许模糊匹配（`user.py` 不该匹配 `user_repository.py`）——
后者会让 B-1 的检查失效。

### 另外两个"基准自己的缺陷"（不是 harness 的问题，但值得记）

构造测试输入时我犯了两次错，两次都被误读成"harness 有 bug"：

1. `requirements[:300]` 把路径**切断在中间**（`src/socket.io/user/pictu`），
   于是 prompt 里出现残缺路径，而 honest 写的是完整路径。
   → 修：按条目边界截断。
2. `touched[:3]` 取到的是 `.pcss` / `go.mod` 这类文件，
   prompt 里**一个可识别的源码文件都没有**，
   harness 的"写入必须触及点名文件"检查自然被跳过。
   → 修：构造 prompt 时优先挑可识别的源码文件。

这两条的教训是：**造测试输入时的 bug 会伪装成被测系统的 bug**。
所以每次失败都要先问"我的输入自洽吗"。

---

## 第 2 层：真实仓库行为观察（弱证据）

见下方"运行状态"一节。

这一层**不报正确性/Pass@1**。算它需要跑官方 `fail_to_pass` 测试套件，
而那需要每题一个 Docker 镜像（`ghcr.io/scaleapi/...`）。本机到 ghcr.io 不通，
且 SWE-bench Pro 的题平均 170 行改动、5.1 个文件，一次完整求解在本地要几十分钟。

**报一个"看起来像通过率"的数字比不报更糟** —— 那正是 BenchJack 里
"返回 `{}` 就过，验证管线从不检查实际输出"的形态。

这一层只记录行为指标：有没有读到目标文件、有没有写到该写的地方、
有没有跑过验证、闸门拦截是否合理。结论仅作观察，**不用于宣称强弱**。

---

## 复现方式

```bash
# 抓数据集（走 datasets-server，无需 HuggingFace 直连）
python harness/bench_swe_pro.py --fetch

# 看题目摘要
python harness/bench_swe_pro.py --list

# 第 1 层：判定正确性（强证据）
python harness/bench_swe_pro.py --layer1 --n 51

# 第 2 层：真实仓库行为观察（弱证据，需要真实模型）
python harness/bench_layer2.py --n 1
```
