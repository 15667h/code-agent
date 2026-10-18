"""待验证清单 —— docs/harness-architecture.md 第十一节的 11 项。

每一项都对应书里的一条具体纪律，测试名直接写明出处。
用标准库 unittest，`python -m unittest discover` 或直接跑本文件都行。

跑法：
    python -m harness.test_harness
    python -m unittest discover -s harness -p "test_*.py" -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.context import ContextBuilder, StaticPrefix, build_tool_spec
from harness.correct import (
    CircuitBreaker, Corrector, FaultClass, LoopDetector, Watchdog, classify,
)
from harness.loop import AgentLoop, LoopConfig, build_default_registry
from harness.model import (ModelReply, ScriptedModel, error_reply, final_reply,
                           tool_call_reply)
from harness.policy import (Action, ActionKind, ApprovalPolicy, Decision, Policy,
                           SandboxMode, infer_network_needed, infer_side_effect)
from harness.protocol import (EventBus, EventType, Message, ToolCall, ToolResult,
                             Trajectory, truncate_output)
from harness.session import Session
from harness.verify import Verifier
from harness.tools.patch import apply_patch, parse_patch


# ══════════════════════════════════════════════════════════════════
class TempWorkspace:
    def __init__(self):
        self.path = tempfile.mkdtemp(prefix="crucible-test-")

    def write(self, rel: str, content: str) -> str:
        full = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return full

    def read(self, rel: str) -> str:
        with open(os.path.join(self.path, rel), "r", encoding="utf-8") as f:
            return f.read()

    def cleanup(self):
        shutil.rmtree(self.path, ignore_errors=True)


def make_prefix():
    reg, shell = build_default_registry_tmp()
    return StaticPrefix("你是测试 agent。", reg.specs()), reg, shell


def build_default_registry_tmp():
    ws = tempfile.mkdtemp(prefix="crucible-reg-")
    reg, shell = build_default_registry(ws)
    return reg, shell


# ══════════════════════════════════════════════════════════════════
# 1. 前缀稳定性（书 §2 铁律 1）
# ══════════════════════════════════════════════════════════════════
class TestPrefixStability(unittest.TestCase):
    """[ ] 前缀稳定性：连续 N 轮后 prefix_hash() 不变。"""

    def setUp(self):
        self.ws = TempWorkspace()
        self.reg, self.shell = build_default_registry(self.ws.path)
        self.prefix = StaticPrefix("系统提示词 v1", self.reg.specs())
        self.ctx = ContextBuilder(self.prefix)

    def tearDown(self):
        self.ws.cleanup()
        self.shell.reset()

    def test_prefix_hash_unchanged_after_many_appends(self):
        h0 = self.ctx.prefix_hash()
        for i in range(50):
            self.ctx.append_user(f"第 {i} 轮用户消息")
            self.ctx.append_assistant(f"第 {i} 轮回复")
            self.ctx.inject_status({"iter": i, "time": f"t{i}"})
        self.assertEqual(h0, self.ctx.prefix_hash(),
                         "追加 50 轮后前缀指纹必须不变（书 §2 铁律 1）")

    def test_assert_prefix_stable_passes(self):
        self.ctx.append_user("hi")
        self.ctx.assert_prefix_stable()      # 不应抛错

    def test_manual_prefix_mutation_is_caught(self):
        """有人偷偷改前缀（比如注入当前时间）时，断言必须炸。"""
        self.ctx.append_user("hi")
        # 模拟「把动态时间戳塞进系统提示词」这个书里点名的错误
        self.prefix._system = "系统提示词 v1\nCurrent time: 2026-10-18T07:00:00"
        with self.assertRaises(AssertionError) as cm:
            self.ctx.assert_prefix_stable()
        self.assertIn("静态前缀被修改", str(cm.exception))

    def test_dynamic_status_does_not_touch_prefix(self):
        """动态信息只追加到末尾（书 §2 铁律 2）。"""
        h0 = self.ctx.prefix_hash()
        n0 = len(self.ctx.trajectory())
        self.ctx.inject_status({"cwd": "/x", "now": "12:00"})
        self.assertEqual(h0, self.ctx.prefix_hash())
        self.assertEqual(n0 + 1, len(self.ctx.trajectory()))
        self.assertEqual(self.ctx.trajectory()[-1].meta.get("kind"), "status")


# ══════════════════════════════════════════════════════════════════
# 2. append-only（书 §2 纪律 / §1 设计模式之三）
# ══════════════════════════════════════════════════════════════════
class TestAppendOnly(unittest.TestCase):
    """[ ] append-only：轨迹只增不改。"""

    def setUp(self):
        self.ws = TempWorkspace()
        self.reg, self.shell = build_default_registry(self.ws.path)
        self.ctx = ContextBuilder(StaticPrefix("sys", self.reg.specs()))

    def tearDown(self):
        self.ws.cleanup()
        self.shell.reset()

    def test_no_mutating_api_exposed(self):
        """接口层面就不该有原地修改的办法。"""
        for bad in ("set", "update", "insert", "remove", "pop", "clear", "replace"):
            self.assertFalse(hasattr(self.ctx, bad),
                             f"ContextBuilder 不应该暴露 {bad}() —— 那会破坏 append-only")

    def test_trajectory_only_grows(self):
        sizes = []
        for i in range(10):
            self.ctx.append_user(f"u{i}")
            sizes.append(len(self.ctx.trajectory()))
        self.assertEqual(sizes, list(range(1, 11)), "轨迹长度必须单调不减")

    def test_compaction_appends_not_rewrites(self):
        """压缩只能追加摘要，绝不删改旧消息（书 §2 点名「滑动窗口」是错误做法）。"""
        for i in range(20):
            self.ctx.append_user(f"消息 {i}")
        before = [m.to_json() for m in self.ctx.trajectory()]
        msg = self.ctx.compact(lambda middle: f"覆盖 {len(middle)} 条", keep_recent=5)
        self.assertIsNotNone(msg)
        after = [m.to_json() for m in self.ctx.trajectory()]
        self.assertEqual(before, after[:len(before)],
                         "压缩不得改动任何已有消息")
        self.assertEqual(msg.meta.get("kind"), "compaction")


# ══════════════════════════════════════════════════════════════════
# 3. 工具顺序固定（书 §2 错误模式之三）
# ══════════════════════════════════════════════════════════════════
class TestToolOrderFixed(unittest.TestCase):
    """[ ] 工具顺序固定：多次构建上下文，工具定义序列字节级一致。"""

    def setUp(self):
        self.ws = TempWorkspace()
        self.reg, self.shell = build_default_registry(self.ws.path)

    def tearDown(self):
        self.ws.cleanup()
        self.shell.reset()

    def test_specs_byte_identical_across_calls(self):
        a = json.dumps(self.reg.specs(), ensure_ascii=False, sort_keys=False)
        for _ in range(5):
            b = json.dumps(self.reg.specs(), ensure_ascii=False, sort_keys=False)
            self.assertEqual(a, b, "工具定义序列必须字节级一致")

    def test_order_is_declared_order(self):
        names = self.reg.names()
        self.assertEqual(names, [t["function"]["name"] for t in self.reg.specs()],
                         "specs() 的输出顺序必须等于注册顺序")

    def test_assert_order_stable_detects_change(self):
        good = self.reg.names()
        self.reg.assert_order_stable(good)          # 不应抛错
        with self.assertRaises(AssertionError):
            self.reg.assert_order_stable(list(reversed(good)))

    def test_duplicate_registration_refused(self):
        from harness.tools.fs import register_fs_tools
        with self.assertRaises(ValueError):
            register_fs_tools(self.reg, self.ws.path)

    def test_prefix_carries_tools_in_order(self):
        prefix = StaticPrefix("sys", self.reg.specs())
        self.assertEqual(prefix.tool_names_in_order(), self.reg.names())


# ══════════════════════════════════════════════════════════════════
# 4. 策略判定表（书 §1 故障安全默认值 + §5 过程约束）
# ══════════════════════════════════════════════════════════════════
class TestPolicyMatrix(unittest.TestCase):
    """[ ] 策略判定表：9 个格子（3 沙箱 × 3 审批）逐个断言。"""

    def setUp(self):
        self.ws = TempWorkspace()
        self.inside = os.path.join(self.ws.path, "a.py")
        self.outside = os.path.join(tempfile.gettempdir(), "outside-target.py")

    def tearDown(self):
        self.ws.cleanup()

    def p(self, sbx, ap):
        return Policy(sbx, ap, self.ws.path)

    # ---------- read-only ----------
    def test_read_only_read_runs(self):
        for ap in ApprovalPolicy:
            r = self.p( SandboxMode.READ_ONLY, ap).decide(
                Action(ActionKind.READ))
            self.assertIs(r.decision, Decision.RUN, f"read-only + {ap.value} 读应放行")

    def test_read_only_write_refused(self):
        for ap in ApprovalPolicy:
            r = self.p(SandboxMode.READ_ONLY, ap).decide(
                Action(ActionKind.WRITE, paths=[self.inside], side_effect=True))
            self.assertIs(r.decision, Decision.REFUSE,
                          f"read-only + {ap.value} 写必须拒绝")

    def test_read_only_side_effect_shell_refused(self):
        for ap in ApprovalPolicy:
            r = self.p(SandboxMode.READ_ONLY, ap).decide(
                Action(ActionKind.SHELL, command="rm x", side_effect=True))
            self.assertIs(r.decision, Decision.REFUSE)

    def test_read_only_readonly_shell_asks_even_never(self):
        """★3 格子：只读模式下即使策略 never，运行命令仍需确认。"""
        r = self.p(SandboxMode.READ_ONLY, ApprovalPolicy.NEVER).decide(
            Action(ActionKind.SHELL, command="ls", side_effect=False))
        self.assertIs(r.decision, Decision.ASK,
                      "never 不等于无限制；沙箱优先")

    # ---------- workspace-write ----------
    def test_ws_write_inside_untrusted_asks(self):
        r = self.p(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.UNTRUSTED).decide(
            Action(ActionKind.WRITE, paths=[self.inside], side_effect=True))
        self.assertIs(r.decision, Decision.ASK)

    def test_ws_write_inside_onrequest_runs(self):
        for ap in (ApprovalPolicy.ON_REQUEST, ApprovalPolicy.NEVER):
            r = self.p(SandboxMode.WORKSPACE_WRITE, ap).decide(
                Action(ActionKind.WRITE, paths=[self.inside], side_effect=True))
            self.assertIs(r.decision, Decision.RUN, f"{ap.value} 工作区内写应放行")

    def test_ws_write_outside(self):
        """★2 格子：越界写 —— on-request 问，never 拒绝。"""
        r = self.p(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_REQUEST).decide(
            Action(ActionKind.WRITE, paths=[self.outside], side_effect=True))
        self.assertIs(r.decision, Decision.ASK)
        r = self.p(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.NEVER).decide(
            Action(ActionKind.WRITE, paths=[self.outside], side_effect=True))
        self.assertIs(r.decision, Decision.REFUSE,
                      "never 下越界写必须拒绝（never ≠ 无限制）")

    def test_ws_network(self):
        r = self.p(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_REQUEST).decide(
            Action(ActionKind.NETWORK, needs_network=True))
        self.assertIs(r.decision, Decision.ASK)
        r = self.p(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.NEVER).decide(
            Action(ActionKind.NETWORK, needs_network=True))
        self.assertIs(r.decision, Decision.REFUSE)

    def test_ws_readonly_shell_runs(self):
        r = self.p(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_REQUEST).decide(
            Action(ActionKind.SHELL, command="ls", side_effect=False))
        self.assertIs(r.decision, Decision.RUN)

    def test_ws_side_effect_shell_untrusted_asks(self):
        r = self.p(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.UNTRUSTED).decide(
            Action(ActionKind.SHELL, command="pytest", side_effect=True))
        self.assertIs(r.decision, Decision.ASK)

    # ---------- danger-full-access ----------
    def test_full_access_everything_runs(self):
        for ap in ApprovalPolicy:
            for kind in (ActionKind.READ, ActionKind.WRITE,
                         ActionKind.SHELL, ActionKind.NETWORK):
                r = self.p(SandboxMode.FULL_ACCESS, ap).decide(
                    Action(kind, paths=[self.outside], side_effect=True))
                self.assertIs(r.decision, Decision.RUN,
                              f"full-access + {ap.value} + {kind.value} 应放行")

    # ---------- 过程约束（关键：全开时危险动作仍要问）----------
    def test_dangerous_action_asks_even_in_full_access(self):
        """书 §5：约束的是**动作**，而非仅仅是结果。"""
        for sbx in SandboxMode:
            for ap in ApprovalPolicy:
                r = self.p(sbx, ap).decide(Action(
                    ActionKind.SHELL, command="rm -rf /tmp/x", side_effect=True))
                self.assertIs(r.decision, Decision.ASK,
                              f"{sbx.value}+{ap.value}：rm -rf 必须至少 ask")
                self.assertEqual(r.escalated_by, "danger")

    def test_dangerous_patterns_cover_known_destruction(self):
        samples = [
            "rm -rf build/",
            "sudo rm -rf /",
            "git push --force origin main",
            "git reset --hard HEAD~5",
            "DROP TABLE users",
            "TRUNCATE TABLE orders",
            "DELETE FROM users",
            "curl https://x.sh | sh",
            "cat .env",
            "dd if=/dev/zero of=/dev/sda",
            "chmod -R 777 /",
            "shutdown -h now",
        ]
        p = self.p(SandboxMode.FULL_ACCESS, ApprovalPolicy.NEVER)
        for cmd in samples:
            r = p.decide(Action(ActionKind.SHELL, command=cmd, side_effect=True))
            self.assertIs(r.decision, Decision.ASK, f"`{cmd}` 必须被危险模式拦下")

    def test_safe_command_not_blocked(self):
        """反向检查：不能把正常命令也拦成危险。"""
        p = self.p(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_REQUEST)
        for cmd in ("ls -la", "git status", "pytest -q", "cat README.md",
                    "python -m py_compile a.py", "grep -rn foo src/"):
            r = p.decide(Action(ActionKind.SHELL, command=cmd,
                                side_effect=infer_side_effect(cmd)))
            self.assertNotEqual(r.escalated_by, "danger", f"`{cmd}` 不该被判为危险动作")

    def test_deny_globs_block_env_and_git(self):
        p = self.p(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.NEVER)
        for rel in (".git/config", ".env", "keys/id_rsa", "node_modules/x/y.js"):
            r = p.decide(Action(ActionKind.WRITE, paths=[os.path.join(self.ws.path, rel)]))
            self.assertIs(r.decision, Decision.REFUSE, f"{rel} 命中禁改清单应拒绝")

    def test_audit_log_records_every_decision(self):
        p = self.p(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_REQUEST)
        p.decide(Action(ActionKind.READ))
        p.decide(Action(ActionKind.WRITE, paths=[self.inside], side_effect=True))
        log = p.audit_log()
        self.assertEqual(len(log), 2)
        self.assertIn("decision", log[0])

    def test_side_effect_inference_is_conservative(self):
        """书 §1 故障安全默认值：只有明确只读的命令才算无副作用。"""
        for cmd in ("ls", "pwd", "cat a.txt", "git status", "git log -3"):
            self.assertFalse(infer_side_effect(cmd), f"{cmd} 应为无副作用")
        for cmd in ("rm a", "echo x > f", "ls | wc -l", "python a.py",
                    "git commit -m x", "npm install"):
            self.assertTrue(infer_side_effect(cmd), f"{cmd} 应判为有副作用")

    def test_network_inference(self):
        self.assertTrue(infer_network_needed("curl https://x.com"))
        self.assertTrue(infer_network_needed("pip install requests"))
        self.assertTrue(infer_network_needed("git push origin main"))
        self.assertFalse(infer_network_needed("ls -la"))


# ══════════════════════════════════════════════════════════════════
# 5. apply_patch：唯一性 + 越界（书 §4 保真性 / §5 最小 diff）
# ══════════════════════════════════════════════════════════════════
class TestApplyPatch(unittest.TestCase):
    """[ ] 补丁唯一性 / [ ] 补丁越界拒绝。"""

    def setUp(self):
        self.ws = TempWorkspace()

    def tearDown(self):
        self.ws.cleanup()

    def test_add_file(self):
        r = apply_patch(self.ws.path, {"patch":
            "*** Begin Patch\n*** Add File: a.py\n+x = 1\n+y = 2\n*** End Patch"})
        self.assertTrue(r.ok, r.text)
        self.assertEqual(self.ws.read("a.py"), "x = 1\ny = 2")
        self.assertIn("a.py", r.files_changed)

    def test_update_file(self):
        self.ws.write("a.py", "x = 1\ny = 2\n")
        r = apply_patch(self.ws.path, {"patch":
            "*** Begin Patch\n*** Update File: a.py\n@@ x = 1\n-x = 1\n+x = 42\n*** End Patch"})
        self.assertTrue(r.ok, r.text)
        self.assertEqual(self.ws.read("a.py"), "x = 42\ny = 2\n")

    def test_delete_file(self):
        self.ws.write("a.py", "x")
        r = apply_patch(self.ws.path, {"patch":
            "*** Begin Patch\n*** Delete File: a.py\n*** End Patch"})
        self.assertTrue(r.ok, r.text)
        self.assertFalse(os.path.exists(os.path.join(self.ws.path, "a.py")))

    # ---------- 唯一性：本实现的安全核心 ----------
    def test_ambiguous_match_refused(self):
        """**关键**：同一原文出现多处时必须拒绝，绝不猜位置。"""
        self.ws.write("a.py", "x = 1\nmid\nx = 1\nend\n")
        r = apply_patch(self.ws.path, {"patch":
            "*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+x = 2\n*** End Patch"})
        self.assertFalse(r.ok, "歧义匹配必须被拒绝")
        self.assertEqual(r.meta.get("kind"), "no_unique_match")
        self.assertIn("出现了 2 次", r.text)
        self.assertIn("不会替你猜位置", r.text)
        # 文件未被改动
        self.assertEqual(self.ws.read("a.py"), "x = 1\nmid\nx = 1\nend\n")

    def test_not_found_gives_actionable_feedback(self):
        self.ws.write("a.py", "x = 1\n")
        r = apply_patch(self.ws.path, {"patch":
            "*** Begin Patch\n*** Update File: a.py\n@@\n-nope = 9\n+nope = 8\n*** End Patch"})
        self.assertFalse(r.ok)
        self.assertEqual(r.meta.get("kind"), "no_unique_match")
        self.assertIn("read_file", r.text, "错误信息应指引模型如何修正")

    def test_unique_match_with_context_succeeds(self):
        self.ws.write("a.py", "x = 1\nmid\nx = 1\nend\n")
        r = apply_patch(self.ws.path, {"patch":
            "*** Begin Patch\n*** Update File: a.py\n@@ end\n-x = 1\n+x = 2\n end\n*** End Patch"})
        self.assertTrue(r.ok, r.text)
        self.assertEqual(self.ws.read("a.py"), "x = 1\nmid\nx = 2\nend\n")

    def test_adding_more_context_resolves_ambiguity(self):
        """歧义时补上下文应能成功 —— 这是给模型的出路。"""
        self.ws.write("a.py", "x = 1\nA\nx = 1\nB\n")
        bad = apply_patch(self.ws.path, {"patch":
            "*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+x = 9\n*** End Patch"})
        self.assertFalse(bad.ok)
        good = apply_patch(self.ws.path, {"patch":
            "*** Begin Patch\n*** Update File: a.py\n@@ B\n-x = 1\n+x = 9\n B\n*** End Patch"})
        self.assertTrue(good.ok, good.text)

    # ---------- 越界 ----------
    def test_path_escape_refused(self):
        r = apply_patch(self.ws.path, {"patch":
            "*** Begin Patch\n*** Add File: ../escaped.py\n+x\n*** End Patch"})
        self.assertFalse(r.ok)
        self.assertIn("越出工作区", r.text)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.ws.path), "escaped.py")))

    def test_absolute_path_refused(self):
        r = apply_patch(self.ws.path, {"patch":
            f"*** Begin Patch\n*** Add File: {os.path.join(tempfile.gettempdir(), 'abs.py')}\n+x\n*** End Patch"})
        self.assertFalse(r.ok)
        self.assertIn("绝对路径", r.text)

    # ---------- 原子性 ----------
    def test_atomic_all_or_nothing(self):
        """任一文件无法应用 → 整个补丁不落盘（书 §5 最小 diff + 可回滚）。"""
        self.ws.write("exists.py", "keep\n")
        r = apply_patch(self.ws.path, {"patch":
            "*** Begin Patch\n"
            "*** Add File: new.py\n+fresh\n"
            "*** Update File: exists.py\n@@\n-nothere\n+x\n"
            "*** End Patch"})
        self.assertFalse(r.ok)
        self.assertFalse(os.path.exists(os.path.join(self.ws.path, "new.py")),
                         "失败时不得留下部分写入")

    def test_add_existing_refused(self):
        self.ws.write("a.py", "x")
        r = apply_patch(self.ws.path, {"patch":
            "*** Begin Patch\n*** Add File: a.py\n+y\n*** End Patch"})
        self.assertFalse(r.ok)
        self.assertIn("已存在", r.text)

    # ---------- 格式错误信息要可纠正 ----------
    def test_parse_errors_are_actionable(self):
        cases = [
            ("*** Add File: a.py\n+x\n", "缺少"),                          # 无 Begin
            ("*** Begin Patch\n+x\n*** End Patch", "任何文件段之前"),       # 内容先于段落
            ("*** Begin Patch\n*** Add File: a.py\n-x\n*** End Patch", "只有 Update"),  # Delete 段用 -
        ]
        for patch, expect in cases:
            r = apply_patch(self.ws.path, {"patch": patch})
            self.assertFalse(r.ok, f"`{patch}` 应解析失败")
            self.assertIn(expect, r.text, f"错误信息应含「{expect}」，实际：{r.text}")

    def test_dry_run_does_not_write(self):
        r = apply_patch(self.ws.path, {"patch":
            "*** Begin Patch\n*** Add File: d.py\n+x\n*** End Patch"}, dry_run=True)
        self.assertTrue(r.ok)
        self.assertEqual(r.files_changed, [])
        self.assertFalse(os.path.exists(os.path.join(self.ws.path, "d.py")))

    def test_structure_of_parsed_ops(self):
        ops = parse_patch(
            "*** Begin Patch\n"
            "*** Add File: a.py\n+x\n"
            "*** Update File: b.py\n@@ anchor\n-old\n+new\n"
            "*** Delete File: c.py\n"
            "*** End Patch")
        self.assertEqual([o.kind for o in ops], ["add", "update", "delete"])
        self.assertEqual(ops[1].hunks[0].anchor, "anchor")


# ══════════════════════════════════════════════════════════════════
# 6. 重复调用指纹熔断（书 §5 检测「模式」）
# ══════════════════════════════════════════════════════════════════
class TestLoopDetection(unittest.TestCase):
    """[ ] 重复调用指纹：同指纹重复 N 次触发熔断。"""

    def test_same_call_same_result_trips(self):
        d = LoopDetector(threshold=3)
        call = ToolCall("read_file", {"path": "a.py"})
        res = ToolResult(ok=False, text="文件不存在：a.py")
        self.assertIsNone(d.observe(call, res), "第 1 次不应触发")
        self.assertIsNone(d.observe(call, res), "第 2 次不应触发")
        hit = d.observe(call, res)
        self.assertIsNotNone(hit, "第 3 次必须触发")
        self.assertIn("无进展循环", hit)
        self.assertIn("read_file", hit)

    def test_same_call_different_result_does_not_trip(self):
        """重试后结果变了，说明有进展，不该误报。"""
        d = LoopDetector(threshold=3)
        call = ToolCall("shell", {"command": "pytest"})
        d.observe(call, ToolResult(ok=False, text="failed: 2"))
        d.observe(call, ToolResult(ok=False, text="failed: 1"))
        self.assertIsNone(d.observe(call, ToolResult(ok=True, text="passed")))

    def test_different_args_not_confused(self):
        d = LoopDetector(threshold=2)
        d.observe(ToolCall("read_file", {"path": "a.py"}), ToolResult(ok=False, text="e"))
        self.assertIsNone(
            d.observe(ToolCall("read_file", {"path": "b.py"}), ToolResult(ok=False, text="e")))

    def test_fingerprint_is_arg_order_independent(self):
        a = ToolCall("f", {"x": 1, "y": 2})
        b = ToolCall("f", {"y": 2, "x": 1})
        self.assertEqual(a.fingerprint(), b.fingerprint(),
                         "参数顺序不同但语义相同，指纹必须一致")

    def test_reset_clears_counters(self):
        d = LoopDetector(threshold=2)
        call = ToolCall("f", {})
        res = ToolResult(ok=False, text="e")
        d.observe(call, res)
        d.reset()
        self.assertIsNone(d.observe(call, res), "reset 后计数应清零")


# ══════════════════════════════════════════════════════════════════
# 7. 工具输出上限 + 落盘（书 §5 上下文管理）
# ══════════════════════════════════════════════════════════════════
class TestOutputCap(unittest.TestCase):
    """[ ] 工具输出上限：超限截断 + 全文落盘 + 返回可查看路径。"""

    def test_truncate_keeps_head_and_tail(self):
        """书 §5：保留前若干行（错误上下文）与后若干行（错误总结）。"""
        lines = [f"line-{i}" for i in range(1000)]
        text = "\n".join(lines)
        out, was = truncate_output(text, limit=500)
        self.assertTrue(was)
        self.assertIn("line-0", out, "必须保留开头")
        self.assertIn("line-999", out, "必须保留结尾")
        self.assertIn("省略中间", out)
        self.assertNotIn("line-500", out, "中段应被省略")

    def test_no_truncation_under_limit(self):
        out, was = truncate_output("short", limit=1000)
        self.assertFalse(was)
        self.assertEqual(out, "short")

    def test_router_caps_and_persists(self):
        from harness.tools.registry import ToolRegistry, Tool
        from harness.tools.router import ToolRouter
        from harness.policy import Policy

        ws = TempWorkspace()
        try:
            bus = EventBus("t")
            reg = ToolRegistry()

            def big(args):
                return ToolResult(ok=True, text="\n".join(f"r{i}" for i in range(5000)),
                                  side_effect=False)

            def desc(args):
                return Action(ActionKind.READ)

            reg.register(Tool("big", "输出很大的测试工具", {}, big, desc))
            pol = Policy(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_REQUEST, ws.path)
            router = ToolRouter(reg, pol, bus, ws.path, output_limit=800)

            res = router.route(ToolCall("big", {}))
            self.assertTrue(res.ok)
            self.assertTrue(res.truncated, "超限必须标记 truncated")
            self.assertIsNotNone(res.full_output_path)
            self.assertTrue(os.path.isfile(res.full_output_path), "全文必须落盘")
            self.assertLessEqual(len(res.text), 800 + 200, "截断后长度应在上限附近")
            self.assertIn("完整输出已保存到", res.text)
            # 落盘内容应是完整的
            with open(res.full_output_path, "r", encoding="utf-8") as f:
                self.assertEqual(len(f.read().splitlines()), 5000)
        finally:
            ws.cleanup()


# ══════════════════════════════════════════════════════════════════
# 8. 配对完整性（书 §5 完整性监控）
# ══════════════════════════════════════════════════════════════════
class TestPairingIntegrity(unittest.TestCase):
    """[ ] 配对完整性：缺 tool 结果时自动补配对而不是抛异常。"""

    def test_detects_missing_pair(self):
        t = Trajectory()
        t.append(Message.user("hi"))
        t.append(Message.assistant(tool_calls=[ToolCall("a", {}), ToolCall("b", {})]))
        t.append(Message.tool_result("", "ok", "a"))
        self.assertEqual(t.unpaired_tool_calls(), ["2"], "应检出缺少 1 条配对")

    def test_repair_fills_placeholders(self):
        t = Trajectory()
        t.append(Message.assistant(tool_calls=[ToolCall("a", {}), ToolCall("b", {})]))
        t.append(Message.tool_result("", "ok", "a"))
        n = t.repair_pairing("[缺失]")
        self.assertEqual(n, 1)
        self.assertEqual(t.unpaired_tool_calls(), [])

    def test_repair_is_append_not_rewrite(self):
        """修补也只能追加占位，不能改动已有消息。"""
        t = Trajectory()
        t.append(Message.assistant(tool_calls=[ToolCall("a", {})]))
        before = [m.to_json() for m in t]
        t.repair_pairing("[缺失]")
        after = [m.to_json() for m in t]
        self.assertEqual(before, after[:len(before)], "修补不得改写已有消息")

    def test_strict_mode_refuses_repair(self):
        """书 §5：训练数据收集模式**拒绝修复**，因为合成占位符会污染训练数据。"""
        t = Trajectory()
        t.append(Message.assistant(tool_calls=[ToolCall("a", {})]))
        with self.assertRaises(AssertionError):
            t.repair_pairing_strict()

    def test_no_repair_needed_when_intact(self):
        t = Trajectory()
        t.append(Message.assistant(tool_calls=[ToolCall("a", {})]))
        t.append(Message.tool_result("", "ok", "a"))
        self.assertEqual(t.repair_pairing(), 0)


# ══════════════════════════════════════════════════════════════════
# 9. 事件 seq 单调（协议不变式）
# ══════════════════════════════════════════════════════════════════
class TestEventSeq(unittest.TestCase):
    """[ ] 事件 seq 单调：无空洞、无回退。"""

    def test_seq_monotonic(self):
        bus = EventBus("t")
        for i in range(20):
            bus.emit(EventType.REASONING, text=f"e{i}")
        bus.assert_monotonic()
        evs = bus.events()
        self.assertEqual([e.seq for e in evs], list(range(1, 21)))

    def test_assert_catches_corruption(self):
        bus = EventBus("t")
        bus.emit(EventType.MESSAGE, text="a")
        bus.emit(EventType.MESSAGE, text="b")
        bus._log[1].seq = 5      # 人为破坏
        with self.assertRaises(AssertionError):
            bus.assert_monotonic()

    def test_subscriber_exception_does_not_break_loop(self):
        """书 §5：错误路径上禁止一切会再次出错的副作用逻辑。"""
        bus = EventBus("t")
        def bad(ev):
            raise RuntimeError("订阅者挂了")
        bus.subscribe(bad)
        bus.emit(EventType.MESSAGE, text="还应继续")
        self.assertEqual(len(bus.events()), 1)

    def test_jsonl_roundtrip(self):
        bus = EventBus("t")
        bus.emit(EventType.TOOL_REQUESTED, tool="read_file", args={"path": "a"})
        lines = bus.to_jsonl().splitlines()
        self.assertEqual(len(lines), 1)
        d = json.loads(lines[0])
        self.assertEqual(d["type"], "tool.requested")
        self.assertEqual(d["data"]["tool"], "read_file")


# ══════════════════════════════════════════════════════════════════
# 10. 审批闸门（挂起 → 批准 → 继续；拒绝 → 不执行）
# ══════════════════════════════════════════════════════════════════
class TestApprovalGate(unittest.TestCase):
    """[ ] 审批闸门：ask 时事件流挂起，批准后继续，拒绝后不执行。"""

    def setUp(self):
        self.ws = TempWorkspace()

    def tearDown(self):
        self.ws.cleanup()

    def _router(self, handler):
        from harness.tools.registry import ToolRegistry, Tool
        from harness.tools.router import ToolRouter
        from harness.policy import Policy

        reg = ToolRegistry()
        self.executed = []

        def h(args):
            self.executed.append(args)
            return ToolResult(ok=True, text="done", side_effect=True,
                              files_changed=["out.txt"])

        def d(args):
            return Action(ActionKind.SHELL, command="deploy.sh --prod",
                          side_effect=True, summary="deploy.sh --prod")

        reg.register(Tool("deploy", "危险部署工具", {}, h, d))
        # untrusted 策略 → 有副作用的命令要问
        pol = Policy(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.UNTRUSTED, self.ws.path)
        bus = EventBus("t")
        return ToolRouter(reg, pol, bus, self.ws.path, approval_handler=handler), bus

    def test_rejected_does_not_execute(self):
        r, bus = self._router(lambda req: "rejected")
        res = r.route(ToolCall("deploy", {}))
        self.assertFalse(res.ok)
        self.assertEqual(self.executed, [], "被拒绝后绝不能执行")
        self.assertIn("拒绝", res.text)
        types = [e.type for e in bus.events()]
        self.assertIn(EventType.APPROVAL_REQUIRED, types)
        self.assertIn(EventType.TOOL_OUTPUT, types)

    def test_approved_executes(self):
        r, bus = self._router(lambda req: "approved")
        res = r.route(ToolCall("deploy", {}))
        self.assertTrue(res.ok)
        self.assertEqual(len(self.executed), 1, "批准后必须执行")
        self.assertEqual(res.files_changed, ["out.txt"])
        types = [e.type for e in bus.events()]
        self.assertIn(EventType.FILE_CHANGED, types, "改文件必须发 file.changed")

    def test_suspended_when_handler_returns_none(self):
        """处理器返回 None 表示**挂起**，等待外部决定。"""
        r, bus = self._router(lambda req: None)
        r.route(ToolCall("deploy", {}))
        self.assertEqual(len(r.pending), 1, "应留下一个待决审批")
        self.assertEqual(self.executed, [], "挂起期间不得执行")
        gate = [e for e in bus.events() if e.type == EventType.APPROVAL_REQUIRED]
        self.assertTrue(any(g.data.get("state") == "suspended" for g in gate))

    def test_no_handler_defaults_to_refuse(self):
        """无人处理时的默认行为必须是拒绝 —— 故障安全，绝不默认放行。"""
        r, _ = self._router(None)
        res = r.route(ToolCall("deploy", {}))
        self.assertFalse(res.ok)
        self.assertEqual(self.executed, [])

    def test_danger_is_never_remembered(self):
        """**关键**：危险动作不参与「批准并记住」—— 每次都必须重新征询。

        这是书 §5「约束的是动作，而非仅仅是结果」的直接检验：
        如果危险动作能被一次性记住放行，那条纪律就形同虚设。

        用**挂起式**处理器（返回 None）来观察：危险动作必须真的走闸门，
        而不是被会话记忆短路掉。
        """
        prompts = []
        r, _ = self._router(lambda req: (prompts.append(req), None)[1])
        # 先塞进一条"已记住"的批准，模拟用户之前批准过同名同类动作
        r._session_allow.add(("deploy", "shell", tuple()))
        self.executed.clear()

        # 把该工具的行为换成危险命令
        r.registry.tool("deploy").describe_action = lambda a: Action(
            ActionKind.SHELL, command="rm -rf /tmp/x", side_effect=True,
            summary="rm -rf /tmp/x")

        r.route(ToolCall("deploy", {}))

        # 1) 必须真的问了人（说明没吃已记住的批准）
        self.assertTrue(prompts, "危险动作必须重新征询，不能吃已记住的批准")
        self.assertEqual(prompts[0].result.escalated_by, "danger")
        self.assertFalse(prompts[0].to_json()["rememberable"],
                         "危险动作在闸门数据里必须标记为不可记住")

        # 2) 闸门事件带上了 danger 升格来源（前端据此改语气、去掉"记住"按钮）
        gate = [e for e in r.bus.events()
                if e.type == EventType.APPROVAL_REQUIRED
                and e.data.get("escalated_by") == "danger"]
        self.assertTrue(gate, "闸门事件必须带 escalated_by=danger")

        # 3) 挂起期间没有执行
        self.assertEqual(self.executed, [], "未经重新批准前不得执行危险动作")
        self.assertTrue(r.pending, "危险动作必须留下待决审批")

    def test_remember_is_ignored_for_danger_via_api(self):
        """即使走 API 传 remember=True，危险动作也不该被记住（第二道防线）。"""
        r, _ = self._router(lambda req: None)
        r.registry.tool("deploy").describe_action = lambda a: Action(
            ActionKind.SHELL, command="rm -rf /tmp/x", side_effect=True,
            summary="rm -rf /tmp/x")
        r.route(ToolCall("deploy", {}))
        cid = next(iter(r.pending))
        before = set(r._session_allow)
        self.assertTrue(r.resolve_approval(cid, "approved", remember=True))
        self.assertEqual(r._session_allow, before,
                         "危险动作的 remember=True 必须被忽略")

    def test_ordinary_action_can_be_remembered(self):
        """反向检查：普通动作的「批准并记住」必须真的生效，否则该功能是坏的。"""
        r, _ = self._router(lambda req: None)
        # deploy 的默认 describe_action 是普通的有副作用命令（非危险）
        r.route(ToolCall("deploy", {}))
        cid = next(iter(r.pending))
        self.assertTrue(r.resolve_approval(cid, "approved", remember=True))
        self.assertTrue(r._session_allow, "普通动作应能被记住")

    def test_resolve_approval_from_outside(self):
        r, _ = self._router(lambda req: None)
        r.route(ToolCall("deploy", {}))
        cid = next(iter(r.pending))
        self.assertTrue(r.resolve_approval(cid, "approved"))
        self.assertFalse(r.pending[cid].decided is None)
        self.assertFalse(r.resolve_approval("call_nonexistent", "approved"))


# ══════════════════════════════════════════════════════════════════
# 11. 端到端：循环 + 终止条件 + 离线可跑
# ══════════════════════════════════════════════════════════════════
class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.ws = TempWorkspace()
        self.ws.write("src/a.py", "x = 1\n")
        self.reg, self.shell = build_default_registry(self.ws.path)

    def tearDown(self):
        self.shell.reset()
        self.ws.cleanup()

    def _loop(self, script, **cfg):
        prefix = StaticPrefix("你是测试 agent。", self.reg.specs())
        sess = Session(self.ws.path, "scripted", "stub", "workspace-write", "on-request")
        pol = Policy(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_REQUEST, self.ws.path)
        model = ScriptedModel(script)
        return AgentLoop(model, self.reg, prefix, pol, sess,
                         LoopConfig(**cfg) if cfg else None), sess, model

    def test_read_then_final(self):
        loop, sess, model = self._loop([
            tool_call_reply("read_file", {"path": "src/a.py"}, "先看文件。"),
            final_reply("文件内容是 x = 1。"),
        ])
        res = loop.run("a.py 里有什么？")
        self.assertEqual(res.status, "completed")
        self.assertEqual(res.tool_calls, 1)
        self.assertIn("x = 1", res.answer)
        # 轨迹里应有一条 tool 结果，且带行号
        traj = loop.context.trajectory()
        tool_msgs = [m for m in traj if m.role == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertIn("1| x = 1", tool_msgs[0].content, "读取结果必须带行号前缀")

    def test_prefix_stable_through_full_run(self):
        """跑完整循环后前缀仍不能变。"""
        loop, _, _ = self._loop([
            tool_call_reply("list_dir", {"path": "."}),
            tool_call_reply("search_grep" if False else "grep", {"pattern": "x"}),
            final_reply("done"),
        ])
        h0 = loop.context.prefix_hash()
        loop.run("调查这个项目")
        self.assertEqual(h0, loop.context.prefix_hash())

    def test_tool_order_stable_through_run(self):
        loop, sess, _ = self._loop([final_reply("done")])
        loop.run("hi")
        self.assertEqual(sess.meta.tool_order, self.reg.names())

    def test_max_iterations_terminates(self):
        """书 §5：没有上限的循环本身就是最典型的控制流故障。

        注意：这里刻意用**每次结果都不同**的调用，否则会先被循环指纹检测拦下
        （那是更早、更精确的拦截）。本测试要验证的是「迭代上限」这条兜底。
        """
        # 读不同文件 → 结果各不相同 → 不会触发指纹检测
        script = [tool_call_reply("read_file", {"path": f"f{i}.py"}) for i in range(50)]
        loop, _, _ = self._loop(script, max_iterations=4)
        res = loop.run("一个个读文件")
        self.assertEqual(res.status, "max_iterations")
        self.assertEqual(res.iterations, 4)
        self.assertIn("最大迭代轮数", res.reason)

    def test_loop_detection_fires_before_iteration_cap(self):
        """同样的调用+同样的结果，会被**更早、更精确**的指纹检测拦下。"""
        script = [tool_call_reply("list_dir", {"path": "."}) for _ in range(50)]
        loop, _, _ = self._loop(script, max_iterations=20)
        res = loop.run("反复列目录")
        self.assertEqual(res.status, "escalated")
        self.assertIn("无进展循环", res.reason)
        self.assertLess(res.iterations, 20, "应在耗尽迭代上限之前就被拦下")

    def test_loop_detection_escalates(self):
        """同一个调用 + 同一个结果重复 3 次 → 升级人工。"""
        # read 一个不存在的文件，结果每次一样
        script = [tool_call_reply("read_file", {"path": "missing.py"}) for _ in range(10)]
        loop, _, _ = self._loop(script)
        res = loop.run("读一个不存在的文件")
        self.assertEqual(res.status, "escalated")
        self.assertIn("无进展循环", res.reason)
        self.assertTrue(res.attempted_recoveries is not None)

    def test_api_error_retry_then_escalate(self):
        """可重试的 API 错误会被重试，耗尽后升级。"""
        calls = {"n": 0}
        def inject(i, req):
            calls["n"] += 1
            if calls["n"] <= 10:
                return error_reply(429, "rate limited, retry-after: 0")
            return final_reply("终于成功")
        prefix = StaticPrefix("sys", self.reg.specs())
        sess = Session(self.ws.path, "m", "stub", "workspace-write", "on-request")
        pol = Policy(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_REQUEST, self.ws.path)
        model = ScriptedModel([], on_call=inject)
        loop = AgentLoop(model, self.reg, prefix, pol, sess)
        res = loop.run("hi")
        self.assertEqual(res.status, "escalated")
        self.assertGreater(calls["n"], 1, "429 应当被重试若干次")
        types = [e.type for e in sess.bus.events()]
        self.assertIn(EventType.RETRY, types, "重试必须发 retry 事件")

    def test_unretryable_api_error_stops_immediately(self):
        prefix = StaticPrefix("sys", self.reg.specs())
        sess = Session(self.ws.path, "m", "stub", "workspace-write", "on-request")
        pol = Policy(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_REQUEST, self.ws.path)
        model = ScriptedModel([], on_call=lambda i, r: error_reply(401, "invalid api key"))
        loop = AgentLoop(model, self.reg, prefix, pol, sess)
        res = loop.run("hi")
        self.assertEqual(res.status, "escalated")
        self.assertEqual(len(model.calls), 1, "401 不该被重试")
        self.assertIn("不可重试", res.reason)

    def test_trajectory_roundtrip_from_disk(self):
        """rollout 可重放（书 §1：可缓存、可重放、可审计）。"""
        loop, sess, _ = self._loop([
            tool_call_reply("read_file", {"path": "src/a.py"}),
            final_reply("ok"),
        ])
        loop.run("读文件")
        traj = sess.read_trajectory()
        self.assertGreater(len(traj), 2)
        roles = [m.role for m in traj]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)
        self.assertIn("tool", roles)
        # 事件也落盘了
        evs = sess.read_events()
        self.assertGreater(len(evs), 5)
        self.assertEqual([e["seq"] for e in evs], list(range(1, len(evs) + 1)),
                         "落盘的事件 seq 也必须连续")

    def test_tool_error_becomes_model_input(self):
        """书 §5：工具层错误不终止会话，把错误变成模型的输入。"""
        loop, _, _ = self._loop([
            tool_call_reply("read_file", {"path": "nope.py"}),
            final_reply("那个文件不存在，我换个方式。"),
        ])
        res = loop.run("读不存在的文件")
        self.assertEqual(res.status, "completed", "单次工具错误不该终止会话")
        traj = loop.context.trajectory()
        tool_msgs = [m for m in traj if m.role == "tool"]
        self.assertTrue(any("失败" in m.content for m in tool_msgs))
        self.assertTrue(any(not m.meta.get("ok", True) for m in tool_msgs))

    def test_unknown_tool_error_is_fed_back(self):
        loop, _, _ = self._loop([
            ModelReply(tool_calls=[ToolCall("nonexistent_tool", {})]),
            final_reply("改用可用工具。"),
        ])
        res = loop.run("试着调用一个不存在的工具")
        self.assertEqual(res.status, "completed")
        traj = loop.context.trajectory()
        tool_msgs = [m for m in traj if m.role == "tool"]
        self.assertTrue(any("不存在" in m.content for m in tool_msgs))
        self.assertTrue(any("可用工具" in m.content for m in tool_msgs),
                        "错误信息应列出可用工具，帮助模型自我纠正")

    def test_lint_feedback_on_syntax_error(self):
        """书 §5「即时语法反馈机制」：写完立刻检查并反馈。"""
        loop, _, _ = self._loop([
            ModelReply(
                tool_calls=[ToolCall("apply_patch", {"patch":
                    "*** Begin Patch\n*** Add File: src/broken.py\n"
                    "+def f(:\n+    pass\n*** End Patch"})],
                reasoning="写一个文件。"),
            final_reply("我修好了。"),
        ])
        loop.run("创建文件")
        traj = loop.context.trajectory()
        tool_msgs = [m for m in traj if m.role == "tool"]
        self.assertTrue(any("即时检查" in m.content for m in tool_msgs),
                        "编辑后应自动跑 lint 并把结果作为工具返回值的一部分")

    def test_status_bar_injected_and_appended(self):
        loop, _, _ = self._loop([final_reply("done")], inject_status=True)
        h0 = loop.context.prefix_hash()
        loop.run("hi")
        self.assertEqual(h0, loop.context.prefix_hash(), "状态栏不得改前缀")
        traj = loop.context.trajectory()
        self.assertTrue(any(m.meta.get("kind") == "status" for m in traj),
                        "应注入过状态栏消息")

    def test_tool_output_event_seq_monotonic(self):
        loop, sess, _ = self._loop([
            tool_call_reply("list_dir", {"path": "."}),
            tool_call_reply("read_file", {"path": "src/a.py"}),
            final_reply("done"),
        ])
        loop.run("做点事")
        sess.bus.assert_monotonic()

    def test_shell_persists_cwd(self):
        """书 §5：持久化终端会话，`cd` 之后下一条命令应停在新目录。"""
        loop, _, _ = self._loop([
            tool_call_reply("shell", {"command": "cd src"}),
            tool_call_reply("shell", {"command": "pwd"}),
            final_reply("done"),
        ])
        loop.run("切换到 src")
        traj = loop.context.trajectory()
        tool_msgs = [m for m in traj if m.role == "tool"]
        joined = "\n".join(m.content for m in tool_msgs)
        self.assertIn("src", joined, "持久化会话应保留 cd 之后的工作目录")

    def test_dangerous_shell_asks_and_blocks_without_handler(self):
        """无审批处理器时，危险命令必须被拒绝而不是执行。"""
        loop, _, _ = self._loop([
            tool_call_reply("shell", {"command": "rm -rf /tmp/should-not-run"}),
            final_reply("被拦住了。"),
        ])
        res = loop.run("删除一些东西")
        self.assertEqual(res.status, "completed")
        traj = loop.context.trajectory()
        joined = "\n".join(m.content for m in traj if m.role == "tool")
        self.assertIn("拒绝", joined, "危险命令应被安全地拒绝")


# ══════════════════════════════════════════════════════════════════
# 12. 编译目录结构（书 §5 故障分类 / §1 中立格式）
# ══════════════════════════════════════════════════════════════════
class TestCorrectLayer(unittest.TestCase):
    def test_classify_retryable_vs_not(self):
        self.assertIs(classify(ToolResult(ok=False, text="HTTP 429 too many requests",
                                         error_layer="api")).cls, FaultClass.RETRYABLE)
        self.assertIs(classify(ToolResult(ok=False, text="invalid_request",
                                         error_layer="api")).cls,
                      FaultClass.NEEDS_INPUT_CHANGE)
        # 工具层默认不可重试（书 §5：原样重试多少次都是同样的结果）
        self.assertIs(classify(ToolResult(ok=False, text="参数不合法",
                                         error_layer="tool")).cls,
                      FaultClass.NEEDS_INPUT_CHANGE)

    def test_parse_retry_after(self):
        from harness.correct import _parse_retry_after
        self.assertEqual(_parse_retry_after("retry-after: 12"), 12.0)
        self.assertEqual(_parse_retry_after("Please try again in 3s"), 3.0)
        self.assertIsNone(_parse_retry_after("no hint here"))

    def test_backoff_respects_server_hint(self):
        c = Corrector()
        self.assertEqual(c.backoff(0, retry_after_s=7.5), 7.5,
                         "存在服务端提示时必须尊重它")

    def test_backoff_jitter_on_delay_not_attempt(self):
        """抖动作用在已算出的延迟上 —— 否则并发会话会同步重试。"""
        c = Corrector()
        vals = {round(c.backoff(2), 6) for _ in range(40)}
        self.assertGreater(len(vals), 1, "退避必须带抖动")

    def test_breaker_opens_at_limit_and_resets_on_success(self):
        b = CircuitBreaker("retry", 3)
        self.assertFalse(b.record_failure("e1"))
        self.assertFalse(b.record_failure("e2"))
        self.assertTrue(b.record_failure("e3"), "第 3 次连续失败应打开熔断")
        self.assertTrue(b.is_open)
        b.record_success()
        self.assertFalse(b.is_open, "成功一次应复位（连续计数，不是累计）")

    def test_corrector_background_call_dropped(self):
        """书 §5：后台调用失败直接放弃，避免"重试放大"挤占主链路配额。"""
        c = Corrector()
        f = classify(ToolResult(ok=False, text="429", error_layer="api"))
        plan = c.plan(f, attempt=0, foreground=False)
        self.assertEqual(plan["action"], "drop")

    def test_recovery_depth_guard(self):
        c = Corrector()
        for _ in range(Corrector.MAX_RECOVERY_DEPTH + 1):
            c.enter_recovery()
        self.assertTrue(c.depth_exceeded(), "递归深度超限必须被检出")

    def test_error_path_disables_side_effects(self):
        """书 §5：错误路径上禁用一切会再次调用模型的副作用逻辑。"""
        c = Corrector()
        self.assertTrue(c.safe_on_error_path())
        c.mark_error_path(True)
        self.assertFalse(c.safe_on_error_path())

    def test_watchdog_detects_stall(self):
        w = Watchdog(idle_timeout_s=0.01)
        self.assertFalse(w.is_stalled())
        import time as _t
        _t.sleep(0.02)
        self.assertTrue(w.is_stalled(), "空闲超时必须被检出（长连接需要活性信号）")
        w.beat()
        self.assertFalse(w.is_stalled(), "活性信号应重置计时")


class TestVerifier(unittest.TestCase):
    def setUp(self):
        self.ws = TempWorkspace()
        self.v = Verifier(self.ws.path)

    def tearDown(self):
        self.ws.cleanup()

    def test_only_reads_structured_fields(self):
        """书 §1：**不看模型自由生成的文本**。"""
        res = ToolResult(ok=True, text="一切正常，我保证没问题", files_changed=[])
        ctx = self.v.prepare("read_file", {}, res)
        r = self.v.run(ctx)
        self.assertTrue(r.ok)
        # 反向：文本说没问题、但结构化字段矛盾，必须判失败
        bad = ToolResult(ok=True, text="成功", exit_code=1)
        r2 = self.v.run(self.v.prepare("shell", {}, bad))
        self.assertFalse(r2.ok, "结构化字段矛盾时，文本说得再好也不能通过")
        self.assertTrue(any("矛盾" in i["detail"] for i in r2.items))

    def test_missing_file_fails_verification(self):
        res = ToolResult(ok=True, text="改好了", files_changed=["not_there.py"])
        r = self.v.run(self.v.prepare("apply_patch", {}, res))
        self.assertFalse(r.ok)
        self.assertTrue(any("不存在" in i["detail"] for i in r.items if not i["ok"]))

    def test_escape_fails_verification(self):
        outside = os.path.join(os.path.dirname(self.ws.path), "evil.py")
        res = ToolResult(ok=True, text="", files_changed=[outside])
        r = self.v.run(self.v.prepare("apply_patch", {}, res))
        self.assertFalse(r.ok, "声明的越界改动必须判失败")

    def test_error_without_layer_fails(self):
        res = ToolResult(ok=False, text="失败了但没说哪层")
        r = self.v.run(self.v.prepare("shell", {}, res))
        self.assertFalse(r.ok)


class TestNeutralTrajectory(unittest.TestCase):
    """书 §5：中立格式 —— 凭证不可移植，文字可移植。"""

    def test_portabilize_drops_signature_keeps_text(self):
        m = Message.assistant(content="答案", reasoning="我的推理",
                              signature="anthropic-sig-abc123",
                              origin_model="claude-x")
        p = m.portabilize()
        self.assertEqual(p.reasoning, "我的推理", "思考文字必须保留")
        self.assertIsNone(p.reasoning_signature, "凭证必须丢弃")
        self.assertIsNone(p.origin_model)

    def test_render_regenerates_call_ids(self):
        """标识符在渲染时按目标厂商重新生成（书 §5）。"""
        m = Message.assistant(tool_calls=[ToolCall("read_file", {"path": "a"})])
        r1 = m.render_for("openai")
        r2 = m.render_for("deepseek")
        id1 = r1["tool_calls"][0]["id"]
        id2 = r2["tool_calls"][0]["id"]
        self.assertTrue(id1.startswith("call_"))
        self.assertTrue(id2.startswith("call_"))
        self.assertNotEqual(id1, id2, "每次渲染都应重新生成标识符")

    def test_no_vendor_private_fields_persisted(self):
        m = Message.assistant(reasoning="推理", signature="sig", tool_calls=[ToolCall("f", {})])
        d = m.to_json()
        # 中立格式里 tool_calls 只记名称与参数
        self.assertEqual(set(d["tool_calls"][0].keys()), {"name", "args"})
        self.assertNotIn("id", d["tool_calls"][0])

    def test_reasoning_becomes_plain_content_on_port(self):
        """书 §5：文字以普通内容的身份带入，不塞回目标厂商放思考的位置。"""
        m = Message.assistant(content="", reasoning="只有推理没有正文")
        rendered = m.portabilize().render_for("other-vendor")
        self.assertEqual(rendered["content"], "只有推理没有正文")
        self.assertNotIn("reasoning", rendered)


class TestToolSpecQuality(unittest.TestCase):
    """书 §4：工具描述的三条硬纪律。"""

    def test_spec_has_examples_when_given(self):
        spec = build_tool_spec("t", "做什么", {"a": {"type": "string"}},
                               ["a"], [{"a": "示例值"}])
        desc = spec["function"]["description"]
        self.assertIn("调用示例", desc)
        self.assertIn("示例值", desc)

    def test_registered_tools_describe_boundaries(self):
        """书 §4：工具描述必须写清**边界（做不到什么）**。"""
        ws = TempWorkdirOnce.get()
        reg, shell = build_default_registry(ws)
        try:
            for name in reg.names():
                d = reg.tool(name).spec["function"]["description"]
                self.assertIn("边界", d,
                              f"工具 {name} 的描述缺少「边界」段 —— "
                              "书 §4：大多数调用失败的根因不是不知道能做什么，"
                              "而是不知道不能做什么")
                self.assertIn("什么时候用", d,
                              f"工具 {name} 的描述缺少「什么时候用」段")
        finally:
            shell.reset()

    def test_registered_tools_have_examples(self):
        ws = TempWorkdirOnce.get()
        reg, shell = build_default_registry(ws)
        try:
            for name in reg.names():
                self.assertTrue(reg.tool(name).examples,
                                f"工具 {name} 缺少调用示例（书 §4：示例能显著提升准确率）")
        finally:
            shell.reset()

    def test_parallel_default_is_false(self):
        """书 §5：并发声明默认为否 —— 故障安全。"""
        from harness.tools.registry import Tool
        t = Tool("x", "d", {}, lambda a: ToolResult(ok=True), lambda a: Action(ActionKind.READ))
        self.assertFalse(t.supports_parallel, "默认必须不支持并发")

    def test_mutating_tools_not_parallel(self):
        ws = TempWorkdirOnce.get()
        reg, shell = build_default_registry(ws)
        try:
            for n in ("apply_patch", "shell"):
                self.assertFalse(reg.tool(n).supports_parallel,
                                 f"{n} 会改动状态，不得声明并发")
            for n in ("read_file", "grep", "glob", "list_dir"):
                self.assertTrue(reg.tool(n).supports_parallel,
                                f"{n} 是纯读，可以并发")
        finally:
            shell.reset()


class TestBlockingApprovalGate(unittest.TestCase):
    """服务端的**阻塞式**审批闸门：工具线程原地等待，外部回灌后继续。

    这一组测试锁住一条容易退化的性质：
    审批不能变成「先拒绝、事后再补执行」——那样同一次调用就有两条执行路径，
    重复副作用的窗口就打开了。正确做法是让它在原地等（书 §5：避免重复副作用）。
    """

    def setUp(self):
        self.ws = TempWorkspace()

    def tearDown(self):
        self.ws.cleanup()

    def test_suspend_then_approve_executes_once(self):
        """挂起 → 外部批准 → 只执行一次。"""
        from harness.server import RunState
        from harness.tools.registry import ToolRegistry, Tool
        from harness.tools.router import ToolRouter
        from harness.policy import Policy

        executed = []
        reg = ToolRegistry()

        def h(args):
            executed.append(args)
            return ToolResult(ok=True, text="deployed", side_effect=True)

        def d(args):
            return Action(ActionKind.SHELL, command="deploy.sh --prod",
                          side_effect=True, summary="deploy.sh --prod")

        reg.register(Tool("deploy", "危险部署工具", {}, h, d))
        pol = Policy(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.UNTRUSTED, self.ws.path)
        bus = EventBus("t")

        st = RunState("run-test", self.ws.path)
        router = ToolRouter(reg, pol, bus, self.ws.path,
                            approval_handler=lambda req: st.await_approval(req, timeout_s=10.0))

        # 在后台线程里调用工具：它应该在闸门处阻塞
        out = {}
        def call():
            out["res"] = router.route(ToolCall("deploy", {}))

        t = threading.Thread(target=call, daemon=True)
        t.start()

        # 等到确实出现了待决审批
        deadline = time.time() + 5.0
        while time.time() < deadline and not router.pending:
            time.sleep(0.02)
        self.assertTrue(router.pending, "工具调用应停在闸门处等待审批")
        self.assertEqual(executed, [], "审批未回灌前不得执行")

        # 回灌批准
        call_id = list(router.pending.keys())[0]
        router.resolve_approval(call_id, "approved")
        st.deliver_approval("approved")

        t.join(timeout=5.0)
        self.assertFalse(t.is_alive(), "回灌后工具线程应被唤醒并结束")
        self.assertTrue(out["res"].ok)
        self.assertEqual(len(executed), 1, "**只能执行一次** —— 不得重复副作用")

    def test_suspend_then_reject_does_not_execute(self):
        """挂起 → 外部拒绝 → 绝不执行。"""
        from harness.server import RunState
        from harness.tools.registry import ToolRegistry, Tool
        from harness.tools.router import ToolRouter
        from harness.policy import Policy

        executed = []
        reg = ToolRegistry()
        reg.register(
            Tool("deploy", "危险部署工具", {},
                 lambda a: (executed.append(a), ToolResult(ok=True, side_effect=True))[1],
                 lambda a: Action(ActionKind.SHELL, command="deploy.sh --prod",
                                  side_effect=True, summary="deploy.sh --prod")))
        pol = Policy(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.UNTRUSTED, self.ws.path)
        st = RunState("run-test2", self.ws.path)
        router = ToolRouter(reg, pol, EventBus("t2"), self.ws.path,
                            approval_handler=lambda req: st.await_approval(req, timeout_s=10.0))

        out = {}
        t = threading.Thread(target=lambda: out.update(
            res=router.route(ToolCall("deploy", {}))), daemon=True)
        t.start()

        deadline = time.time() + 5.0
        while time.time() < deadline and not router.pending:
            time.sleep(0.02)
        self.assertTrue(router.pending)
        call_id = list(router.pending.keys())[0]
        router.resolve_approval(call_id, "rejected")
        st.deliver_approval("rejected")

        t.join(timeout=5.0)
        self.assertFalse(t.is_alive())
        self.assertFalse(out["res"].ok)
        self.assertEqual(executed, [], "拒绝后绝不能执行")

    def test_timeout_fails_safe_to_reject(self):
        """闸门超时必须按**拒绝**处理，而不是静默卡死或默认放行。

        注意语义：阻塞式处理器超时后直接返回 "rejected"，
        而不是返回 None —— None 是"挂起，等外部后续决定"的意思，
        那是给**非阻塞**处理器用的。阻塞处理器等到超时就必须给出终态，
        否则运行会永久挂着。
        """
        from harness.server import RunState

        st = RunState("run-test3", self.ws.path)
        st.approval_timeout_s = 0.6
        t0 = time.time()
        dec = st.await_approval(None)
        elapsed = time.time() - t0
        self.assertEqual(dec, "rejected", "超时必须给出终态且方向是拒绝（故障安全）")
        self.assertGreaterEqual(elapsed, 0.5, "应真的等到超时")
        self.assertLess(elapsed, 3.0, "超时不该无限拖长")

    def test_approve_endpoint_rejects_ghost_call_id(self):
        """回灌一个不存在的 call_id 必须被拒绝 —— 否则是幽灵审批。

        这里验证 RunState 的等待语义：无人回灌时必须在超时后给出终态。
        serve 层的 pending 校验拒绝不存在的 call_id（见 gate_e2e 的 HTTP 验收）。
        """
        from harness.server import RunState

        st = RunState("run-test4", self.ws.path)
        st.approval_timeout_s = 0.3
        self.assertEqual(st.await_approval(None), "rejected")


class TestShellPlatformBehavior(unittest.TestCase):
    """shell 工具在**两种平台**上都必须给出干净、可判定的结果。

    这组测试来自实测踩到的真实缺陷：
      Windows 上 cmd.exe 用 UTF-8 解码会全量乱码；
      启动版权横幅与提示符回显会混进命令输出；
      `%ERRORLEVEL%` 同行展开会取到上一行的值。
    这些如果不锁住，跨平台行为会悄悄退化 —— 而且只在 Windows 上退化，
     在 CI(Linux) 上永远看不到。
    """

    def setUp(self):
        self.ws = TempWorkspace()

    def tearDown(self):
        try:
            self.shell.kill()
        except Exception:
            pass
        self.ws.cleanup()

    def _shell(self):
        from harness.tools.shell import PersistentShell
        self.shell = PersistentShell(self.ws.path)
        return self.shell

    def test_success_returns_zero_and_clean_output(self):
        sh = self._shell()
        cmd = "echo hello" if os.name == "nt" else "echo hello"
        r = sh.run(cmd)
        self.assertTrue(r.ok, "echo 应成功；实际 rc=%s text=%r" % (r.exit_code, r.text))
        self.assertEqual(r.exit_code, 0)
        self.assertIn("hello", r.text)
        # 输出必须干净：不含启动横幅、不含提示符标记
        from harness.tools.shell import PROMPT_MARK
        self.assertNotIn(PROMPT_MARK, r.text, "提示符标记不得出现在输出里")
        self.assertNotIn("Microsoft Corporation", r.text, "启动横幅不得出现在输出里")

    def test_failure_returns_nonzero(self):
        sh = self._shell()
        # 用能**确定**返回非零码、且不会退出会话的命令。
        # 不用 `exit 3`（会杀掉常驻 shell），也不用 `cd 不存在的目录`
        # （cmd 的行为在各版本间不一致）。python 显式退出码最可靠。
        cmd = 'python -c "import sys; sys.exit(3)"'
        r = sh.run(cmd)
        self.assertFalse(r.ok, "非零退出码必须被判为失败；text=%r" % r.text)
        self.assertEqual(r.exit_code, 3,
                         "退出码必须被如实取回（实测同行展开会取到上一行的值）")

    def test_cwd_persists_across_commands(self):
        """cwd 在命令之间必须保持 —— 这是书里明确要求的默认行为。"""
        sh = self._shell()
        sub = os.path.join(self.ws.path, "sub")
        os.makedirs(sub, exist_ok=True)
        # 切目录（用相对路径，避免平台差异）
        cd = "cd sub" if os.name == "nt" else "cd sub"
        sh.run(cd)
        r = sh.run("dir" if os.name == "nt" else "ls")
        self.assertTrue(r.ok)
        # 第二条命令应在新目录里执行（cwd 保持）
        self.assertTrue(
            os.path.samefile(sh.cwd, sub) or os.path.realpath(sh.cwd) == os.path.realpath(sub),
            "cwd 应保持为 sub，实际 %s" % sh.cwd)

    def test_timeout_is_reported_not_hung(self):
        """超时必须返回 timeout 错误，而不是把调用方挂死。"""
        sh = self._shell()
        if os.name == "nt":
            cmd = "ping -n 10 127.0.0.1 > NUL"
        else:
            cmd = "sleep 10"
        r = sh.run(cmd, timeout_ms=800)
        self.assertFalse(r.ok)
        self.assertEqual(r.meta.get("kind"), "timeout",
                         "超时应带 kind=timeout，实际 %r" % r.meta.get("kind"))

    def test_console_encoding_is_real_codec(self):
        """编码探测必须返回 Python 能识别的编码名。"""
        from harness.tools.shell import PersistentShell
        enc = PersistentShell._console_encoding()
        if os.name == "nt":
            self.assertIn(enc, ("utf-8", "gbk", "big5", "cp936"))
        else:
            self.assertEqual(enc, "utf-8")
        # 必须能被 Python 真的用起来
        "测试".encode(enc)


class TestToolResultEventUniqueness(unittest.TestCase):
    """**一次执行 = 一条 tool.output 事件**。

    这组测试来自实测抓到的真 bug：loop 曾在 router 之外**又发了一遍**
    tool.output（`persisted=True`，无 call_id）。前端因为无法按 call_id 合并，
    把同一次结果渲染成两张卡片，看起来像"执行了两次"。
    事件唯一性必须靠测试锁住 —— 光靠代码注释提醒不住。
    """

    def setUp(self):
        self.ws = TempWorkspace()

    def tearDown(self):
        self.ws.cleanup()

    def test_one_output_event_per_execution(self):
        """调用一次工具 → 恰好一条 tool.output 事件。"""
        from harness.loop import AgentLoop
        from harness.model import ScriptedModel, tool_call_reply, final_reply

        reg, shell = build_default_registry(self.ws.path)
        try:
            prefix = StaticPrefix("sys", reg.specs())
            sess = Session(self.ws.path, "m", "stub", "workspace-write", "on-request")
            pol = Policy(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_REQUEST, self.ws.path)
            model = ScriptedModel([
                tool_call_reply("list_dir", {"path": "."}, "看看目录"),
                final_reply("看完了"),
            ])
            loop = AgentLoop(model, reg, prefix, pol, sess)

            # 单独统计 bus 上 TOOL_OUTPUT 的条数，只看这一次调用
            got = []
            orig = loop.bus.emit

            def spy(etype, **data):
                if etype == EventType.TOOL_OUTPUT:
                    got.append(data)
                return orig(etype, **data)

            loop.bus.emit = spy
            try:
                loop.run("列一下目录")
            finally:
                loop.bus.emit = orig
                shell.reset()

            self.assertEqual(len(got), 1,
                             "一次执行只能广播一条 tool.output；实际 %d 条：%r"
                             % (len(got), [g.get("text", "")[:40] for g in got]))
            self.assertEqual(got[0].get("tool"), "list_dir")
        finally:
            shell.reset()

    def test_output_event_carries_call_id(self):
        """结果事件必须带 call_id —— 前端靠它把结果并回对应的请求卡片。

        没有 call_id 的结果事件会让前端新起一张卡片（实测就是这样变成两张的）。
        """
        from harness.loop import AgentLoop
        from harness.model import ScriptedModel, tool_call_reply, final_reply

        reg, shell = build_default_registry(self.ws.path)
        try:
            prefix = StaticPrefix("sys", reg.specs())
            sess = Session(self.ws.path, "m", "stub", "workspace-write", "on-request")
            pol = Policy(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_REQUEST, self.ws.path)
            model = ScriptedModel([
                tool_call_reply("list_dir", {"path": "."}, "看看目录"),
                final_reply("看完了"),
            ])
            loop = AgentLoop(model, reg, prefix, pol, sess)
            loop.run("列一下目录")

            outs = [e for e in loop.bus.events()
                    if e.type == EventType.TOOL_OUTPUT and e.data.get("ok") is not None]
            self.assertTrue(outs, "应有至少一条结果事件")
            # 至少有一条带 call_id 的（router 发的那条）
            self.assertTrue(any(e.data.get("call_id") for e in outs),
                            "结果事件必须携带 call_id，否则前端无法合并到请求卡片")
        finally:
            shell.reset()


class TestModelEndpoint(unittest.TestCase):
    """模型接入：**只有三个字段**，走 OpenAI 兼容端点。

    这组测试锁住三件容易退化的事：
      1. 地址归一化（中转站的四种写法）—— 写错地址是这类配置最常见的失败
      2. api_key 绝不回传明文（对外只有掩码）
      3. 配置落盘的合并语义（key 留空 = 沿用，而不是被清空）
    """

    def setUp(self):
        self.ws = TempWorkspace()

    def tearDown(self):
        self.ws.cleanup()

    # ---------- 地址归一化 ----------
    def test_normalize_common_middleman_shapes(self):
        from harness.model import normalize_base_url
        cases = [
            # 用户填法                                 期望
            ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1/chat/completions"),
            ("https://api.deepseek.com/v1/", "https://api.deepseek.com/v1/chat/completions"),
            ("https://x.com/v1/chat/completions", "https://x.com/v1/chat/completions"),
            ("https://x.com/v1/chat/completions/", "https://x.com/v1/chat/completions"),
            ("  https://x.com/v1  ", "https://x.com/v1/chat/completions"),
            ("", ""),
        ]
        for raw, want in cases:
            self.assertEqual(normalize_base_url(raw), want,
                             "地址归一化错了：%r" % raw)

    def test_normalize_does_not_guess_v1(self):
        """**刻意不自动补 /v1** —— 猜错会制造"模型不存在"的误导性报错。

        这个测试是在**锁住一个设计决定**，而不是锁住一个实现细节：
        将来若有人"热心地"加上自动补 /v1 的逻辑，这里会红。
        """
        from harness.model import normalize_base_url
        got = normalize_base_url("https://x.com")
        self.assertEqual(got, "https://x.com/chat/completions")
        self.assertNotIn("/v1/", got)

    # ---------- key 掩码 ----------
    def test_key_is_masked_never_returned_raw(self):
        from harness.model import EndpointConfig, mask_key
        key = "sk-abcdefghijklmnop1234"
        cfg = EndpointConfig(base_url="https://x.com/v1", api_key=key, model="m")
        pub = cfg.to_public()
        self.assertNotIn(key, json.dumps(pub), "公开形态绝不能含明文 key")
        self.assertTrue(pub["has_key"])
        self.assertIn("••", pub["api_key_masked"])
        # 掩码要足够用于"核对是哪一个"，但不能反推出原文
        self.assertTrue(mask_key(key).startswith("sk-a"))
        self.assertTrue(mask_key(key).endswith("1234"))

    def test_mask_short_key_still_checkable(self):
        """短 key 也要能核对 —— 全部遮死会让用户认不出自己存的是哪个 key，
        于是每次换 key 都得重新粘一遍。掩码的价值就在"能认出"。"""
        from harness.model import mask_key
        self.assertEqual(mask_key("abc"), "a••")        # ≤4 位：留第一位
        self.assertEqual(mask_key("abcd"), "a•••")
        self.assertEqual(mask_key("abcde"), "a•••e")    # 5–8 位：留首尾
        self.assertEqual(mask_key(""), "")
        # 无论长短，都不能回传出完整的原始 key
        for k in ("abc", "abcd", "abcde", "good-key", "sk-abcdefghijklmnop1234"):
            m = mask_key(k)
            self.assertNotEqual(m, k, "掩码不能等于原文：%r" % k)
            self.assertIn("•", m)

    # ---------- 三字段齐备判定 ----------
    def test_configured_requires_all_three(self):
        from harness.model import EndpointConfig
        good = EndpointConfig(base_url="https://x.com/v1", api_key="k", model="m")
        self.assertTrue(good.configured)
        for bad in (
            EndpointConfig(base_url="", api_key="k", model="m"),
            EndpointConfig(base_url="https://x.com/v1", api_key="", model="m"),
            EndpointConfig(base_url="https://x.com/v1", api_key="k", model=" "),
        ):
            self.assertFalse(bad.configured, "缺任一字段都不算配置完整")

    def test_provider_name_derived_from_host(self):
        """provider 标识从地址取主机名 —— 这样三个字段就真的只有三个
        （不需要用户额外填一个"厂家名"）。"""
        from harness.model import EndpointConfig
        cfg = EndpointConfig(base_url="https://api.deepseek.com/v1",
                             api_key="k", model="m")
        self.assertEqual(cfg.name, "api.deepseek.com")

    # ---------- 落盘与合并 ----------
    def test_save_and_load_roundtrip(self):
        from harness.model import EndpointConfig, load_config, save_config
        cfg = EndpointConfig(base_url="https://x.com/v1", api_key="sk-secret-123",
                             model="my-model")
        p = save_config(self.ws.path, cfg)
        self.assertTrue(os.path.isfile(p))
        back = load_config(self.ws.path)
        self.assertEqual(back.base_url, cfg.base_url)
        self.assertEqual(back.api_key, cfg.api_key)
        self.assertEqual(back.model, cfg.model)

    def test_config_file_is_not_world_readable(self):
        """配置含明文 key，权限必须收紧（POSIX 上生效）。"""
        from harness.model import EndpointConfig, save_config
        p = save_config(self.ws.path,
                        EndpointConfig(base_url="https://x.com/v1",
                                       api_key="sk-x", model="m"))
        if os.name != "nt":
            mode = os.stat(p).st_mode & 0o777
            self.assertEqual(mode, 0o600, "配置文件应是 0600，实际 %o" % mode)

    def test_clear_removes_file(self):
        from harness.model import (EndpointConfig, clear_config, load_config,
                                   save_config)
        save_config(self.ws.path,
                    EndpointConfig(base_url="https://x.com/v1", api_key="k", model="m"))
        self.assertTrue(clear_config(self.ws.path))
        self.assertFalse(load_config(self.ws.path).configured)
        self.assertFalse(clear_config(self.ws.path), "重复清除应返回 False")

    def test_corrupt_config_falls_back_without_crashing(self):
        """配置损坏不该炸掉 harness —— 退回"未配置"即可（于是走离线脚本）。"""
        from harness.model import config_path, load_config
        p = config_path(self.ws.path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ 这不是合法 JSON")
        cfg = load_config(self.ws.path)      # 不应抛异常
        self.assertFalse(cfg.configured)

    def test_resolve_endpoint_merges_partial_override(self):
        """请求只带部分字段时，未带的沿用落盘值。

        这条语义很重要：否则前端每次保存模型名都要重填 key。
        """
        from harness.model import (EndpointConfig, resolve_endpoint, save_config)
        save_config(self.ws.path, EndpointConfig(
            base_url="https://x.com/v1", api_key="sk-old", model="old-model"))
        # 只改模型名
        ep = resolve_endpoint(self.ws.path, {"model": "new-model"})
        self.assertEqual(ep.model, "new-model")
        self.assertEqual(ep.api_key, "sk-old", "未提供的字段必须沿用")
        self.assertEqual(ep.base_url, "https://x.com/v1")

    # ---------- 未配置时的行为 ----------
    def test_unconfigured_model_returns_actionable_error(self):
        """三个字段没填全时，错误必须**指名缺什么**，而不是笼统的 401。"""
        from harness.model import EndpointConfig, HttpModel
        m = HttpModel(EndpointConfig(base_url="", api_key="", model=""))
        reply = m.complete({"messages": [{"role": "user", "content": "hi"}]})
        self.assertIsNotNone(reply.error_layer)
        self.assertFalse(reply.retryable, "缺配置是不可重试的 —— 重试没有意义")
        for field in ("接口地址", "API Key", "模型名称"):
            self.assertIn(field, reply.raw_error)

    # ---------- HTTP 错误翻译 ----------
    def test_404_distinguishes_url_from_model(self):
        """**404 有两种完全不同的含义**，必须靠正文区分。

        指错方向比不给提示更糟 —— 用户会去改一个本来正确的字段。
        这条测试就是为这个场景写的（实测时真的踩到了：
        模型名不存在时，最初的实现报"地址可能不对"）。
        """
        from harness.model import HttpModel
        model_missing = HttpModel._explain_http(
            404, '{"error":{"message":"model not found"}}')
        self.assertIn("模型名称", model_missing,
                      "模型名不存在时应指向模型名：%s" % model_missing)
        self.assertNotIn("接口地址", model_missing)

        path_wrong = HttpModel._explain_http(
            404, '{"error":{"message":"unknown path /v1/chat/completions"}}')
        self.assertIn("接口地址", path_wrong,
                      "地址错时应指向地址：%s" % path_wrong)

        unclear = HttpModel._explain_http(404, "Not Found")
        self.assertIn("或", unclear, "判不出来时要说清两种可能，而不是乱指一个")

    def test_http_errors_get_actionable_hints(self):
        from harness.model import HttpModel
        cases = [
            (401, "API Key"),
            (403, "权限"),
            (429, "限流"),
        ]
        for code, needle in cases:
            msg = HttpModel._explain_http(code, "")
            self.assertIn(needle, msg,
                          "%d 的提示应指向真正的原因，实际：%s" % (code, msg))

    def test_only_gateway_errors_are_retryable(self):
        """401/403/404 是配置问题，**不能标成可重试** ——
        否则用户会白等若干轮退避，最后还是同一个错。"""
        from harness.model import error_reply
        self.assertFalse(error_reply(401, "x", retryable=False).retryable)
        self.assertFalse(error_reply(404, "x", retryable=False).retryable)
        self.assertTrue(error_reply(429, "x").retryable)
        self.assertTrue(error_reply(503, "x").retryable)

    def test_parse_detects_error_payload_in_200(self):
        """有的中转在出错时返回 200 + {"error": {...}}。

        必须识别出来，否则会被当成"模型没说话"，变成一次**静默的失败** ——
        正是书 §1 警告的那种"看上去毫无破绽"。
        """
        from harness.model import EndpointConfig, HttpModel
        m = HttpModel(EndpointConfig(base_url="https://x.com/v1", api_key="k", model="m"))
        reply = m._parse({"error": {"message": "quota exceeded"}})
        self.assertIsNotNone(reply.error_layer)
        self.assertIn("quota exceeded", reply.raw_error)

    def test_parse_tool_calls_from_openai_shape(self):
        from harness.model import EndpointConfig, HttpModel
        m = HttpModel(EndpointConfig(base_url="https://x.com/v1", api_key="k", model="m"))
        reply = m._parse({"choices": [{"message": {
            "content": "",
            "tool_calls": [{"function": {"name": "read_file",
                                         "arguments": '{"path": "a.py"}'}}],
        }, "finish_reason": "tool_calls"}]})
        self.assertEqual(len(reply.tool_calls), 1)
        self.assertEqual(reply.tool_calls[0].name, "read_file")
        self.assertEqual(reply.tool_calls[0].args, {"path": "a.py"})

    def test_malformed_tool_args_kept_for_model_to_fix(self):
        """参数不是合法 JSON 时保留原文，由 router 报结构化错误让模型自纠
        （书 §5：工具层错误不终止会话，而是变成模型的输入）。"""
        from harness.model import EndpointConfig, HttpModel
        m = HttpModel(EndpointConfig(base_url="https://x.com/v1", api_key="k", model="m"))
        reply = m._parse({"choices": [{"message": {
            "tool_calls": [{"function": {"name": "read_file",
                                         "arguments": "{not json"}}],
        }, "finish_reason": "tool_calls"}]})
        self.assertEqual(reply.tool_calls[0].args.get("__malformed__"), "{not json")


class TestTextToolCallFallback(unittest.TestCase):
    """**不支持原生 function calling 的端点**要用文本协议。

    这组测试来自实测：`deepseek-v4.1-flash` 端点即使 `tool_choice="required"`
    也**不返回 tool_calls**，工具意图被塞进 reasoning_content、content 为空。
    若不处理，循环看到「没有 tool_calls」就判定为最终回答，
    任务在第一轮就"成功完成"——事件流看起来毫无破绽。
    正是书 §1 警告的那种"看上去正常的失败"。
    """

    def test_extract_single_call(self):
        from harness.model import extract_text_tool_calls
        calls, stripped = extract_text_tool_calls(
            '<tool_call>{"name": "read_file", "arguments": {"path": "a.py"}}</tool_call>')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "read_file")
        self.assertEqual(calls[0].args, {"path": "a.py"})
        self.assertEqual(stripped, "", "剥掉块后正文应为空")

    def test_extract_multiple_calls_in_order(self):
        from harness.model import extract_text_tool_calls
        text = ('先读再搜。\n'
                '<tool_call>{"name": "read_file", "arguments": {"path": "a.py"}}</tool_call>\n'
                '<tool_call>{"name": "grep", "arguments": {"pattern": "foo"}}</tool_call>')
        calls, stripped = extract_text_tool_calls(text)
        self.assertEqual([c.name for c in calls], ["read_file", "grep"])
        self.assertIn("先读再搜。", stripped)
        self.assertNotIn("<tool_call>", stripped)

    def test_prose_without_blocks_is_not_a_call(self):
        """**描述意图 ≠ 调用工具**。

        这是实测踩到的确切形态：模型回了
        "I'll start by reading the README" —— 纯文本，没有 tool_calls。
        必须判为"没有工具调用"，而不是去猜它想调用什么。
        """
        from harness.model import extract_text_tool_calls
        calls, stripped = extract_text_tool_calls("I'll start by reading the README.")
        self.assertEqual(calls, [], "纯描述不应被当成工具调用")
        self.assertEqual(stripped, "I'll start by reading the README.")

    def test_malformed_block_stays_in_text_for_model_to_fix(self):
        """坏块**不清除** —— 让模型看见自己的原文，下一轮才有机会纠正。"""
        from harness.model import extract_text_tool_calls
        text = '<tool_call>{"name": "read_file", "arguments": {not json}}</tool_call>'
        calls, stripped = extract_text_tool_calls(text)
        self.assertEqual(calls, [])
        self.assertIn("not json", stripped, "坏块应保留在正文里")

    def test_alternate_key_names_accepted(self):
        """有的模型会写 `tool` / `args` 而不是 `name` / `arguments`。"""
        from harness.model import extract_text_tool_calls
        calls, _ = extract_text_tool_calls('<tool_call>{"tool": "list_dir", "args": {"path": "."}}</tool_call>')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "list_dir")

    def test_loose_block_without_closing_tag(self):
        """模型偶尔漏掉闭合标签；不因此丢掉整条调用。"""
        from harness.model import extract_text_tool_calls
        calls, _ = extract_text_tool_calls('<tool_call>{"name": "list_dir", "arguments": {"path": "."}}')
        self.assertEqual(len(calls), 1)

    def test_parse_uses_text_fallback_and_fixes_finish_reason(self):
        """`_parse` 要能走通降级路径，并把 finish_reason 修正为 tool_calls。

        不修正的话轨迹里会留下自相矛盾的记录（说 stop 却带着工具调用）。
        """
        from harness.model import EndpointConfig, HttpModel
        m = HttpModel(EndpointConfig(base_url="https://x.com/v1", api_key="k", model="m"))
        reply = m._parse({"choices": [{
            "message": {"content": '<tool_call>{"name": "list_dir", "arguments": {"path": "."}}</tool_call>'},
            "finish_reason": "stop",
        }]})
        self.assertEqual(len(reply.tool_calls), 1)
        self.assertEqual(reply.tool_calls[0].name, "list_dir")
        self.assertEqual(reply.finish_reason, "tool_calls")
        self.assertEqual(reply.content, "", "块应从正文里剥掉，避免污染下一轮上下文")

    def test_native_tool_calls_take_priority(self):
        """接口支持原生 function calling 时，**不应**再走文本解析。"""
        from harness.model import EndpointConfig, HttpModel
        m = HttpModel(EndpointConfig(base_url="https://x.com/v1", api_key="k", model="m"))
        reply = m._parse({"choices": [{
            "message": {
                "content": '<tool_call>{"name": "should_be_ignored", "arguments": {}}</tool_call>',
                "tool_calls": [{"function": {"name": "native_call", "arguments": "{}"}}],
            },
            "finish_reason": "tool_calls",
        }]})
        self.assertEqual(len(reply.tool_calls), 1)
        self.assertEqual(reply.tool_calls[0].name, "native_call")


    def test_text_protocol_calls_replay_as_text_not_native(self):
        """**文本协议的调用必须原样回放成文本，不能伪装成原生 tool_calls。**

        这是实测踩到的硬 bug：第一轮文本调用被当成原生 tool_calls 存进轨迹，
        第二轮请求时上游看到「有 role=tool 的结果消息，但 assistant 里
        没有对应的 tool_calls」，直接 400：
            `tool calls and tool results do not match`
        于是整个任务在第二轮就死了，而第一轮看起来完全正常。
        """
        from harness.protocol import Message, ROLE_ASSISTANT, ROLE_TOOL, ToolCall
        # 文本协议来的调用
        m = Message.assistant(content="", tool_calls=[ToolCall("read_file", {"path": "a.py"})])
        m.meta["tool_calls_were_text"] = True
        rendered = m.render_for("openai")
        self.assertNotIn("tool_calls", rendered,
                         "文本协议的调用不能渲染成原生 tool_calls")
        self.assertIn("<tool_call>", rendered["content"],
                      "必须还原成文本块，否则上游会 400")

        # 对应的结果也必须以普通用户消息回灌
        r = Message.tool_result("", "# a.py", "read_file")
        r.meta["text_protocol_result"] = True
        r.meta["tool_name"] = "read_file"
        rr = r.render_for("openai")
        self.assertEqual(rr["role"], "user", "不能回灌成 role=tool")
        self.assertNotIn("tool_call_id", rr, "文本协议下没有 id 可以对上")
        self.assertIn("read_file", rr["content"])

    def test_native_calls_still_replay_natively(self):
        """原生调用**不受影响** —— 仍按标准 OpenAI 形状回放。"""
        from harness.protocol import Message, ToolCall
        m = Message.assistant(content="", tool_calls=[ToolCall("read_file", {"path": "a.py"})])
        rendered = m.render_for("openai")
        self.assertIn("tool_calls", rendered)
        self.assertEqual(rendered["tool_calls"][0]["function"]["name"], "read_file")
        self.assertNotIn("<tool_call>", rendered.get("content") or "")


class TestTypedRetention(unittest.TestCase):
    """类型化上下文保留 —— 防「压缩悬崖」。

    arXiv 2608.22752（CIKM 2026）实测：不加区分的摘要会把安全规则
    保留率打到 1 轮 53%、5 轮 10%。原因是规则需要精确措辞才可执行，
    而情节日志只需要大意，两者同速率摘要时先死的必然是规则。

    这组测试锁住三条不变量：
      1. 硬约束**永不进摘要器**
      2. 摘要条目**必须标来源**（环境事实 vs 模型推断）
      3. 压缩必须是**追加**，不改已有消息
    """

    def _ctx(self, constraint_in_prompt: bool = True):
        from harness.context import ContextBuilder, StaticPrefix
        sys_prompt = "你是助手。"
        if constraint_in_prompt:
            sys_prompt += "\n不要修改工作区以外的任何路径。危险命令必须审批。"
        return ContextBuilder(StaticPrefix(sys_prompt, []))

    def _fill(self, ctx, n: int = 10):
        from harness.protocol import Message, ToolCall
        ctx.append_user("重构 a.py。不要修改工作区以外的任何路径。")
        for i in range(n):
            ctx.append_assistant(content="", tool_calls=[ToolCall("read_file", {"path": f"f{i}.py"})])
            ctx.append(Message.tool_result("", f"file {i} " + "x" * 150, "read_file"))
            ctx.append_assistant(content=f"第 {i} 步：claude 我认为这看起来像个模块。")

    def test_constraint_never_enters_summarizer(self):
        """**核心不变量**：约束消息不得进入摘要器。

        用一个会记录收到什么的假摘要器直接验证 ——
        如果约束进去了，测试就红。
        """
        from harness.retention import classify, plan_compaction, Kind
        from harness.protocol import Message
        ctx = self._ctx()
        self._fill(ctx)
        traj = ctx.trajectory()
        plan = plan_compaction(traj)

        for m in plan.to_summarize:
            self.assertNotEqual(classify(m), Kind.CONSTRAINT,
                                "约束消息 seq=%s 不该出现在待摘要列表里" % m.seq)
        self.assertTrue(plan.verbatim, "至少应有一条约束被原样保留")

    def test_compaction_block_keeps_constraints_verbatim(self):
        """压缩块里必须原样出现约束原文，且标为不可改写。"""
        from harness.retention import _fallback_summary
        ctx = self._ctx()
        self._fill(ctx)
        blk = ctx.compact(lambda ms: _fallback_summary(ms, 120), keep_recent=6)
        self.assertIsNotNone(blk)
        self.assertIn("不要修改工作区以外的任何路径", blk.content,
                      "硬约束原文必须原样出现在压缩块里")
        self.assertIn("[硬约束", blk.content, "必须有显式的约束区块标记")
        self.assertTrue(blk.meta["audit"]["has_constraint_block"])

    def test_summary_labels_sources(self):
        """摘要条目要区分 `[环境]` 与推断，不能让推断以事实语气进入上下文。"""
        from harness.retention import _fallback_summary
        ctx = self._ctx()
        self._fill(ctx)
        blk = ctx.compact(lambda ms: _fallback_summary(ms, 120), keep_recent=6)
        self.assertIn("[环境]", blk.content, "工具结果必须标为环境事实")
        self.assertIn("[推断]", blk.content,
                      "必须有对推断的标注说明（即便当前没有推断条目）")

    def test_compaction_only_appends(self):
        """压缩**只追加**，绝不修改或删除已有消息（append-only 纪律）。"""
        ctx = self._ctx()
        self._fill(ctx, 8)
        before = [(m.seq, m.role, m.content) for m in ctx.trajectory()]
        ctx.compact(lambda ms: "", keep_recent=4)
        after = [(m.seq, m.role, m.content) for m in ctx.trajectory()]
        self.assertEqual(after[:len(before)], before, "已有消息必须逐字节不变")
        self.assertEqual(len(after), len(before) + 1, "只应多出一条压缩块")

    def test_planned_drops_are_recorded(self):
        """被丢弃/被摘要的范围必须落进 meta，让「东西什么时候没的」可追查。"""
        from harness.retention import _fallback_summary
        ctx = self._ctx()
        self._fill(ctx)
        blk = ctx.compact(lambda ms: _fallback_summary(ms, 80), keep_recent=6)
        plan = blk.meta["plan"]
        self.assertIn("verbatim", plan)
        self.assertIn("to_summarize", plan)
        self.assertIn("dropped", plan)
        self.assertIn("by_kind", plan)
        # 被压缩的原文仍留在 trajectory 里，只是不再进后续 prompt
        self.assertGreater(len(ctx.trajectory()), len(plan["to_summarize"]))

    def test_classification_of_each_role(self):
        from harness.protocol import Message, ToolCall
        from harness.retention import classify, Kind
        self.assertEqual(classify(Message.system("规则")), Kind.CONSTRAINT)
        self.assertEqual(classify(Message.tool_result("", "输出", "read_file")), Kind.FACT)
        self.assertEqual(classify(Message.assistant(content="", tool_calls=[ToolCall("x", {})])),
                         Kind.DECISION)
        self.assertEqual(classify(Message.assistant(content="我在想")), Kind.REASONING)
        m = Message.user("[环境状态] {}"); m.meta["kind"] = "status"
        self.assertEqual(classify(m), Kind.EPISODE)

    def test_constraint_detection_covers_chinese_and_english(self):
        from harness.retention import is_constraint
        for t in ("不要修改工作区以外的路径", "危险命令必须审批",
                  "Do not write outside the workspace", "requires approval"):
            self.assertTrue(is_constraint(t), "应识别为硬约束：%r" % t)
        for t in ("我读完了这个文件", "the file has 30 lines"):
            self.assertFalse(is_constraint(t), "不应误判为约束：%r" % t)

    def test_hedge_detection(self):
        from harness.retention import is_hedged
        self.assertTrue(is_hedged("我认为这个函数是入口"))
        self.assertTrue(is_hedged("it probably returns None"))
        self.assertFalse(is_hedged("文件共 34 行"))


class TestCompactionTrigger(unittest.TestCase):
    """压缩**必须真的被触发** —— 这是之前的核心缺陷。

    诊断发现 `context.compact()` 从未被调用（grep 无结果），
    长会话根本不压缩。这组测试锁住"循环里真的会压"。
    """

    def setUp(self):
        self.ws = TempWorkspace()

    def tearDown(self):
        self.ws.cleanup()

    def test_loop_compacts_when_ratio_reached(self):
        from harness.loop import AgentLoop, LoopConfig
        from harness.model import ScriptedModel, tool_call_reply, final_reply
        from harness.context import StaticPrefix
        from harness.policy import Policy, SandboxMode, ApprovalPolicy
        from harness.session import Session

        reg, shell = build_default_registry(self.ws.path)
        try:
            prefix = StaticPrefix("sys", reg.specs())
            sess = Session(self.ws.path, "m", "stub", "workspace-write", "on-request")
            pol = Policy(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_REQUEST, self.ws.path)

            # 每轮换一个不同的 path —— 相同的调用会被**指纹检测**当成无进展循环
            # 提前熔断（那是正确行为，但会让这条测试测不到压缩）。
            for i in range(12):
                self.ws.write("f%d.txt" % i, "content %d\n" % i)
            script = [tool_call_reply("read_file", {"path": "f%d.txt" % i}) for i in range(12)]
            script.append(final_reply("好了"))
            # 把预算压到很小 + 间隔设 1，让比例闸快速命中
            cfg = LoopConfig(max_iterations=20, max_tokens_budget=600,
                             compact_at_ratio=0.05, compact_min_interval=1,
                             compact_keep_recent=4, max_compactions=3)
            loop = AgentLoop(ScriptedModel(script), reg, prefix, pol, sess, cfg)
            loop.run("读这些文件")

            compactions = [e for e in loop.bus.events() if e.type == EventType.COMPACTED]
            self.assertTrue(compactions, "达到比例后必须真的触发压缩")
            self.assertLessEqual(len(compactions), 3, "压缩次数必须有上限")
            # 每次压缩都要带计划与审计，不能是黑盒
            for e in compactions:
                self.assertIn("plan", e.data)
                self.assertIn("audit", e.data)
        finally:
            shell.reset()

    def test_compaction_disabled_when_max_zero(self):
        from harness.loop import AgentLoop, LoopConfig
        from harness.model import ScriptedModel, tool_call_reply, final_reply
        from harness.context import StaticPrefix
        from harness.policy import Policy, SandboxMode, ApprovalPolicy
        from harness.session import Session

        reg, shell = build_default_registry(self.ws.path)
        try:
            prefix = StaticPrefix("sys", reg.specs())
            sess = Session(self.ws.path, "m", "stub", "workspace-write", "on-request")
            pol = Policy(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_REQUEST, self.ws.path)
            for i in range(6):
                self.ws.write("g%d.txt" % i, "c%d\n" % i)
            script = [tool_call_reply("read_file", {"path": "g%d.txt" % i}) for i in range(6)]
            script.append(final_reply("好了"))
            cfg = LoopConfig(max_iterations=12, max_tokens_budget=100,
                             compact_at_ratio=0.01, max_compactions=0)
            loop = AgentLoop(ScriptedModel(script), reg, prefix, pol, sess, cfg)
            loop.run("读文件")
            self.assertFalse([e for e in loop.bus.events() if e.type == EventType.COMPACTED],
                             "max_compactions=0 时必须完全禁用压缩")
        finally:
            shell.reset()

    def test_prefix_hash_stable_across_compaction(self):
        """压缩绝不能动静态前缀 —— 否则击穿 KV-cache。"""
        from harness.context import StaticPrefix, ContextBuilder
        from harness.retention import _fallback_summary
        from harness.protocol import Message, ToolCall
        prefix = StaticPrefix("系统提示\n不要越界。", [])
        ctx = ContextBuilder(prefix)
        h0 = prefix.hash()
        ctx.append_user("任务")
        for i in range(8):
            ctx.append_assistant(content="", tool_calls=[ToolCall("read_file", {"path": str(i)})])
            ctx.append(Message.tool_result("", "x" * 100, "read_file"))
        ctx.compact(lambda ms: _fallback_summary(ms, 50), keep_recent=3)
        ctx.assert_prefix_stable()          # 不抛错即证明前缀未变
        self.assertEqual(prefix.hash(), h0, "压缩后前缀指纹必须不变")


class TempWorkdirOnce:
    _p = None

    @classmethod
    def get(cls):
        if cls._p is None:
            cls._p = tempfile.mkdtemp(prefix="crucible-reg-")
        return cls._p


# ══════════════════════════════════════════════════════════════════
# 12. 目标证据绑定（修 GoalVerifier 空壳）
# ══════════════════════════════════════════════════════════════════
class TestGoalEvidence(unittest.TestCase):
    """`GoalVerifier` 曾经是空壳：`record()` 全项目无人调用，
    `_results` 恒空 → `all_met` **恒 False**。

    后果不是"少报"，而是**每次声明目标条件都必然报"未达成"** ——
    一个恒假的闸门比没有闸门更坏：它把未完成包装成一条看起来很严谨的
    失败理由，人反而更容易接受。

    修法是把判定改成**确定性证据绑定**：条件文本翻译成可观测信号，
    信号在结构化工具结果里被命中才算 satisfied。全程不读模型文本。
    """

    def setUp(self):
        self.ws = TempWorkspace()

    def tearDown(self):
        self.ws.cleanup()

    # ---------- 信号提取 ----------
    def test_signal_extraction(self):
        from harness.verify import _extract_signals

        s = _extract_signals("创建 snake/index.html")
        self.assertEqual(s.tool, "apply_patch")
        self.assertIn("snake/index.html", s.paths)

        s = _extract_signals("运行 pytest 并让它通过")
        self.assertEqual(s.tool, "shell")
        self.assertEqual(s.command, "pytest")

        s = _extract_signals("读取 README.md")
        self.assertEqual(s.tool, "read_file")

        # 无信号的条件下不能瞎猜
        s = _extract_signals("代码质量要好")
        self.assertFalse(s.has_signal)

    def test_explicit_tool_name_wins(self):
        """条件里显式写出工具名是最可靠的信号，优先级最高。"""
        from harness.verify import _extract_signals
        s = _extract_signals("用 grep 找到那个函数")
        self.assertEqual(s.tool, "grep")

    # ---------- 绑定 ----------
    def test_path_evidence_binds_goal(self):
        """apply_patch 真的落盘了这个文件 → 条件才满足。

        这里是最关键的语义：**证据来自结构化字段 `files_changed`**，
        而不是去解析补丁正文。
        """
        from harness.verify import GoalVerifier
        gv = GoalVerifier(["创建 snake/index.html"])
        self.assertEqual(gv.report()["satisfied"], 0)
        self.assertFalse(gv.report()["all_met"])

        newly = gv.observe("apply_patch", {"patch": "*** Begin Patch"},
                           ToolResult(ok=True, side_effect=True,
                                      files_changed=["snake/index.html"]))
        self.assertEqual(newly, ["创建 snake/index.html"])
        rep = gv.report()
        self.assertEqual(rep["satisfied"], 1)
        self.assertTrue(rep["all_met"])
        self.assertIn("snake/index.html", rep["items"][0]["evidence"])

    def test_failed_tool_output_is_not_evidence(self):
        """**失败的工具输出不构成证据。**

        否则"我试过了"会被当成"我做成了" —— 这是最容易被忽略的漏洞。
        """
        from harness.verify import GoalVerifier
        gv = GoalVerifier(["创建 snake/index.html"])
        gv.observe("apply_patch", {},
                   ToolResult(ok=False, error_layer="tool",
                              files_changed=["snake/index.html"]))
        self.assertEqual(gv.report()["satisfied"], 0,
                         "ok=False 的结果即使声明了文件改动也不能算证据")

    def test_path_match_tolerates_basename(self):
        """条件里写 `index.html`、结果是 `snake/index.html` 应视为同一文件。"""
        from harness.verify import GoalVerifier
        gv = GoalVerifier(["写出 index.html"])
        gv.observe("apply_patch", {},
                   ToolResult(ok=True, side_effect=True,
                              files_changed=["snake/index.html"]))
        self.assertTrue(gv.report()["all_met"])

    def test_shell_command_evidence(self):
        from harness.verify import GoalVerifier
        gv = GoalVerifier(["运行 pytest 通过"])
        gv.observe("shell", {"command": "python -m pytest -q"},
                   ToolResult(ok=True, exit_code=0, side_effect=True))
        rep = gv.report()
        self.assertTrue(rep["all_met"])
        self.assertIn("退出码 0", rep["items"][0]["evidence"])

        # 换个不相关的命令不能蒙混过关
        gv2 = GoalVerifier(["运行 pytest 通过"])
        gv2.observe("shell", {"command": "ls -la"},
                    ToolResult(ok=True, exit_code=0, side_effect=True))
        self.assertEqual(gv2.report()["satisfied"], 0)

    def test_unmatched_condition_is_reported_not_guessed(self):
        """认不出信号的条件必须**如实暴露**，既不算满足也不算未满足。

        沉默是最坏的选择：它会让人把"没有报未达成"读成"已达成"。
        """
        from harness.verify import GoalVerifier
        gv = GoalVerifier(["代码质量要好", "创建 a.txt"])
        gv.observe("apply_patch", {}, ToolResult(ok=True, side_effect=True,
                                                 files_changed=["a.txt"]))
        rep = gv.report()
        self.assertEqual(rep["satisfied"], 1)
        self.assertEqual(rep["unmatched"], 1)
        self.assertFalse(rep["all_met"], "有无法判定的条件时不能声称全部达成")
        self.assertEqual(gv.unmatched(), ["代码质量要好"])

    def test_multiple_paths_all_required(self):
        """一条条件里提到多个文件 → 必须全部出现才算满足。"""
        from harness.verify import GoalVerifier
        gv = GoalVerifier(["创建 a.txt 和 b.txt"])
        gv.observe("apply_patch", {}, ToolResult(ok=True, side_effect=True,
                                                 files_changed=["a.txt"]))
        self.assertFalse(gv.report()["all_met"])
        gv.observe("apply_patch", {}, ToolResult(ok=True, side_effect=True,
                                                 files_changed=["b.txt"]))
        self.assertTrue(gv.report()["all_met"])

    def test_read_does_not_satisfy_write_goal(self):
        """**读文件不能冒充"创建了文件"。**

        这是修复前真实存在的假阳性通道：`read_file a.txt` 也会把
        `a.txt` 放进 actual_paths，于是条件"创建 a.txt"被误判为满足。
        所以写意图的条件必须要求证据来自写类工具。
        """
        from harness.verify import GoalVerifier
        gv = GoalVerifier(["创建 a.txt"])
        gv.observe("read_file", {"path": "a.txt"},
                   ToolResult(ok=True, side_effect=False,
                              meta={"path": "a.txt"}))
        self.assertEqual(gv.report()["satisfied"], 0,
                         "读取不是创建，不能作为创建类目标的证据")

    def test_shell_created_file_satisfies_create_goal_unit(self):
        """单元级：shell 命中的路径也算"创建"的证据（不要求 apply_patch）。"""
        from harness.verify import GoalVerifier
        gv = GoalVerifier(["创建 notes/hello.txt"])
        gv.observe("shell", {"command": "echo hi > notes/hello.txt"},
                   ToolResult(ok=True, exit_code=0, side_effect=True,
                              files_changed=["notes/hello.txt"]))
        self.assertTrue(gv.report()["all_met"],
                        "shell 落盘同样是写动作，闸门不能死认 apply_patch")

    def test_read_goal_accepts_read_tool(self):
        """条件是"读取 X"时，读工具本身就是正确证据。"""
        from harness.verify import GoalVerifier
        gv = GoalVerifier(["读取 README.md"])
        gv.observe("read_file", {"path": "README.md"},
                   ToolResult(ok=True, meta={"path": "README.md"}))
        self.assertTrue(gv.report()["all_met"])

    def test_report_basis_declares_limits(self):
        """报告必须自述判据边界 —— 证据只说明动作发生了，不说明内容正确。"""
        from harness.verify import GoalVerifier
        gv = GoalVerifier(["创建 a.txt"])
        basis = gv.report()["basis"]
        self.assertIn("结构化字段", basis)
        self.assertIn("不读模型文本", basis)
        self.assertIn("不构成对内容正确性的判断", basis)

    def test_manual_record_still_works(self):
        """逃生口：外部显式登记仍然生效（供评审 UI / 测试夹具用）。"""
        from harness.verify import GoalVerifier
        gv = GoalVerifier(["主观条件：文案读起来自然"])
        self.assertFalse(gv.report()["all_met"])
        gv.record("主观条件：文案读起来自然", True, "人工评审通过", seq=42)
        rep = gv.report()
        self.assertTrue(rep["all_met"])
        self.assertEqual(rep["items"][0]["evidence"], "人工评审通过")


class TestGoalGateInLoop(unittest.TestCase):
    """目标闸门接进主循环后的行为。"""

    def setUp(self):
        self.ws = TempWorkspace()
        self.ws.write("src/a.py", "x = 1\n")
        self.reg, self.shell = build_default_registry(self.ws.path)

    def tearDown(self):
        self.shell.reset()
        self.ws.cleanup()

    def _loop(self, script, goals, **cfg):
        from harness.loop import AgentLoop, LoopConfig
        prefix = StaticPrefix("你是测试 agent。", self.reg.specs())
        sess = Session(self.ws.path, "scripted", "stub", "workspace-write", "on-request")
        pol = Policy(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_REQUEST, self.ws.path)
        return AgentLoop(ScriptedModel(script), self.reg, prefix, pol, sess,
                         LoopConfig(**cfg) if cfg else None,
                         goal_conditions=goals), sess

    def test_goal_met_reports_all_met(self):
        """模型真的落盘了这个文件 → 闸门应放行，而不是无脑报未达成。"""
        loop, _ = self._loop([
            tool_call_reply("apply_patch", {"patch":
                "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x = 1\n+x = 2\n*** End Patch"}),
            final_reply("改好了。"),
        ], ["修改 src/a.py"])
        res = loop.run("把 a.py 改成 x = 2")
        self.assertEqual(res.status, "completed")
        self.assertTrue(res.goal["all_met"], "有结构化证据时必须判为达成")
        self.assertIn("任务完成", res.reason)

    def test_goal_unmet_feeds_back_then_completes(self):
        """未达成 → 先回灌纠正（不是直接终止），补齐后正常收尾。

        早期实现在这里**直接收尾**，闸门只会报失败而从不尝试补救。
        """
        loop, _ = self._loop([
            final_reply("我觉得已经好了。"),          # 空口无凭
            tool_call_reply("apply_patch", {"patch":
                "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x = 1\n+x = 2\n*** End Patch"}),
            final_reply("现在真的改好了。"),
        ], ["修改 src/a.py"])
        res = loop.run("把 a.py 改成 x = 2")
        self.assertEqual(res.status, "completed")
        self.assertTrue(res.goal["all_met"])
        # 中间那轮纠正必须留下结构化事件（可追查"为什么多跑了一轮"）
        errs = [e for e in loop.bus.events()
                if e.type == EventType.ERROR and e.data.get("cls") == "goal_unmet"]
        self.assertEqual(len(errs), 1, "应恰好回灌一次纠正")

    def test_correction_when_model_had_no_tool_calls(self):
        """**harness 的纠正不能伪装成工具结果。**

        实测踩到的真实故障：模型"空口说完成"（这条 assistant 消息里
        **没有 tool_calls**），闸门回灌时若追加 role=tool 的消息，
        历史里就出现一条**孤立的工具结果**，上游立刻 400：
        `tool calls and tool results do not match`。

        该 bug 第一轮不炸 —— 崩溃点落在第二次请求上，而且报 400
        （被归类为"不可重试"），看起来完全像配置错误。所以必须用
        结构断言把它钉死：任何 tool 消息都必须紧跟它自己的 tool_calls。
        """
        loop, _ = self._loop([
            final_reply("我觉得已经好了。"),            # 没有 tool_calls
            final_reply("真的好了。"),
            final_reply("确实好了。"),
        ], ["修改 src/a.py"])
        loop.run("改 a.py")

        traj = loop.context.trajectory()
        for i, m in enumerate(traj):
            if m.role != "tool":
                continue
            # 向前找最近的一条 assistant
            prev_assistant = None
            for k in range(i - 1, -1, -1):
                if traj[k].role == "assistant":
                    prev_assistant = traj[k]
                    break
            self.assertIsNotNone(prev_assistant, "tool 消息之前必须存在 assistant 消息")
            self.assertTrue(
                prev_assistant.tool_calls,
                f"第 {i} 条 tool 消息是孤立的：它前面的 assistant 没有 tool_calls。"
                "这会让上游返回 400 `tool calls and tool results do not match`。",
            )

    def test_correction_message_is_plain_user(self):
        """纠正必须落成普通用户消息，且带可识别的 meta 供审计。"""
        loop, _ = self._loop([
            final_reply("好了。"), final_reply("好了。"), final_reply("好了。"),
        ], ["修改 src/a.py"])
        loop.run("改 a.py")
        corr = [m for m in loop.context.trajectory()
                if m.meta.get("kind") == "correction"]
        self.assertTrue(corr, "闸门回灌纠正时必须在轨迹里留下记录")
        for m in corr:
            self.assertEqual(m.role, "user", "纠正必须是普通用户消息，不能是 role=tool")
            self.assertEqual(m.meta.get("from"), "harness")

    def test_goal_retry_has_a_cap(self):
        """纠正必须有上限 —— 否则闸门变成死循环。"""
        loop, _ = self._loop([
            final_reply("好了。"), final_reply("真的好了。"),
            final_reply("确实好了。"), final_reply("反正好了。"),
        ], ["修改 src/a.py"])
        res = loop.run("改 a.py")
        self.assertEqual(res.status, "completed")
        self.assertFalse(res.goal["all_met"])
        self.assertIn("尚未满足", res.reason)
        errs = [e for e in loop.bus.events()
                if e.type == EventType.ERROR and e.data.get("cls") == "goal_unmet"]
        self.assertLessEqual(len(errs), 2, "纠正次数必须有上限")
        self.assertLess(res.iterations, 8, "闸门不得变成无限循环")

    def test_unmatched_warning_emitted_at_start(self):
        """无法自动判定的条件必须在**开场**就告知，不能静默。"""
        loop, _ = self._loop([final_reply("好了。")], ["代码质量要好"])
        loop.run("写点东西")
        warns = [e for e in loop.bus.events()
                 if e.type == EventType.CHECK
                 and e.data.get("warning") == "unmatched_conditions"]
        self.assertEqual(len(warns), 1)
        self.assertIn("无法自动判定", warns[0].data["reason"])

    def test_no_goal_conditions_means_no_gate(self):
        """没声明条件时闸门完全透明 —— 不能凭空给普通任务加一轮。"""
        loop, _ = self._loop([final_reply("好了。")], [])
        res = loop.run("随便聊聊")
        self.assertEqual(res.status, "completed")
        self.assertIsNone(res.goal)
        self.assertEqual(res.iterations, 1)

    def test_evidence_event_emitted_on_first_bind(self):
        """条件首次挂上证据时要发事件 —— 闸门状态变化必须可观察。"""
        loop, _ = self._loop([
            tool_call_reply("read_file", {"path": "src/a.py"}),
            tool_call_reply("apply_patch", {"patch":
                "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x = 1\n+x = 2\n*** End Patch"}),
            final_reply("改好了。"),
        ], ["修改 src/a.py"])
        loop.run("改 a.py")
        evs = [e for e in loop.bus.events()
               if e.type == EventType.CHECK and e.data.get("scope") == "goal"
               and e.data.get("newly_met")]
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].data["newly_met"], "修改 src/a.py")

    def test_shell_write_satisfies_create_goal(self):
        """**实测场景**：模型用 `shell` 而不是 apply_patch 建文件。

        真实运行里 deepseek-v4.1-flash 直接发了
        `mkdir -p notes && printf 'hello world\\n' > notes/hello.txt`，
        文件确实落盘了，但早期闸门死认 `apply_patch`，于是报"未满足" ——
        **闸门在说假话**，比没有闸门更坏（它把已完成包装成一条严谨的失败理由）。

        修法：shell 用文件系统事实（mtime 前后对比）上报 files_changed，
        闸门按"写类动作 + 路径命中"判定，而不是死认某个工具名。
        """
        loop, _ = self._loop([
            tool_call_reply("shell", {"command": "mkdir -p notes && echo hello world > notes/hello.txt"}),
            final_reply("建好了。"),
        ], ["创建 notes/hello.txt"])
        res = loop.run("新建 notes/hello.txt")
        self.assertEqual(res.status, "completed")
        self.assertTrue(res.goal["all_met"],
                        f"shell 已落盘该文件，闸门必须认可。实际报告：{res.goal}")

    def test_shell_reports_changed_files_structurally(self):
        """shell 必须用**结构化字段**上报改动，不能靠解析命令文本。"""
        sh = self.shell.shell            # ShellTool 内部持有的持久会话
        r = sh.run("echo hi > probe.txt")
        self.assertTrue(r.ok)
        self.assertIn("probe.txt", r.files_changed,
                      "shell 写了文件就必须在结构化字段里报出来")
        self.assertIn("changed_probe", r.meta)

        r_read = sh.run("echo just-listing")
        self.assertTrue(r_read.ok)
        self.assertEqual(r_read.files_changed, [],
                         f"纯读命令不该报改动，实际报了 {r_read.files_changed}")

    def test_glob_meta_exposes_matches(self):
        """glob 必须把命中清单放进结构化 meta —— 证据绑定不能靠解析文本。"""
        from harness.tools.fs import glob_files
        self.ws.write("snake/index.html", "<html></html>\n")
        r = glob_files(self.ws.path, {"pattern": "**/*.html"})
        self.assertTrue(r.ok)
        self.assertIn("snake/index.html", r.meta.get("matches") or [])

    def test_grep_meta_exposes_matches(self):
        from harness.tools.search import grep
        self.ws.write("snake/index.html", "<html>needle</html>\n")
        r = grep(self.ws.path, {"pattern": "needle"})
        self.assertTrue(r.ok)
        self.assertIn("snake/index.html", r.meta.get("matches") or [])


# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    unittest.main(verbosity=2)
