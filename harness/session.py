"""会话与 rollout 落盘。

## rollout：可重放、可审计的会话记录

Codex 把每次编辑连同会话一起落盘（`rollout` crate 的 `recorder.rs` 与
`ordinal.rs` 保证事件顺序），因此**任何一次修改都能重放**。

书 §1 的「只增不改（Append-only）」设计模式指出它换来的是
**可缓存、可重放、可审计**；§5 又补充说，中立轨迹格式的价值不限于故障切换：

> 第七章的评估重放、第八章的训练样本构造、第九章的经验提取，
> 依赖的都是同一份产物。

本模块就负责产出并持久化这份产物。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .context import ContextBuilder
from .protocol import Event, EventBus, Message, Trajectory


@dataclass
class SessionMeta:
    session_id: str
    workspace: str
    model: str
    provider: str
    sandbox: str
    approval: str
    created_at: float = field(default_factory=time.time)
    prefix_hash: str = ""
    tool_order: List[str] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "workspace": self.workspace,
            "model": self.model,
            "provider": self.provider,
            "sandbox": self.sandbox,
            "approval": self.approval,
            "created_at": self.created_at,
            "prefix_hash": self.prefix_hash,
            "tool_order": self.tool_order,
        }


class Session:
    """一次会话。

    持有 ContextBuilder（前缀 + 轨迹）、EventBus（事件）、以及落盘路径。

    **落盘纪律**：只追加（打开文件用 "a"），从不重写已有记录。
    这样即使进程崩了，前面的事件也完好 —— 这正是 append-only 的工程价值。
    """

    def __init__(self, workspace: str, model: str, provider: str,
                 sandbox: str, approval: str,
                 run_dir: Optional[str] = None) -> None:
        self.meta = SessionMeta(
            session_id=uuid.uuid4().hex[:12],
            workspace=os.path.realpath(workspace),
            model=model, provider=provider,
            sandbox=sandbox, approval=approval,
        )
        self.bus = EventBus(turn_id=self.meta.session_id)
        self.context: Optional[ContextBuilder] = None
        self.run_dir = run_dir or os.path.join(self.meta.workspace, ".crucible", "runs", self.meta.session_id)
        self._events_path = os.path.join(self.run_dir, "events.jsonl")
        self._traj_path = os.path.join(self.run_dir, "trajectory.jsonl")
        self._meta_path = os.path.join(self.run_dir, "meta.json")
        os.makedirs(self.run_dir, exist_ok=True)

        # 事件一旦产生就落盘（append-only），不攒批 —— 崩溃也不丢
        self.bus.subscribe(self._persist_event)

    # ---------- 装配 ----------
    def attach_context(self, ctx: ContextBuilder, tool_order: List[str]) -> None:
        self.context = ctx
        self.meta.prefix_hash = ctx.prefix_hash()
        self.meta.tool_order = list(tool_order)
        self.write_meta()

    # ---------- 落盘 ----------
    def _persist_event(self, ev: Event) -> None:
        try:
            with open(self._events_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev.to_json(), ensure_ascii=False) + "\n")
        except OSError:
            # 落盘失败不能影响主循环（书 §5：错误路径上禁用副作用逻辑）。
            # 但它是一次真实的数据丢失风险，因此记录在内存里供上层检查。
            self._persist_failures = getattr(self, "_persist_failures", 0) + 1

    def persist_message(self, msg: Message) -> None:
        try:
            with open(self._traj_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg.to_json(), ensure_ascii=False) + "\n")
        except OSError:
            self._persist_failures = getattr(self, "_persist_failures", 0) + 1

    def write_meta(self) -> None:
        with open(self._meta_path, "w", encoding="utf-8") as f:
            json.dump(self.meta.to_json(), f, ensure_ascii=False, indent=2)

    # ---------- 读取（重放） ----------
    def read_events(self) -> List[Dict[str, Any]]:
        return _read_jsonl(self._events_path)

    def read_trajectory(self) -> Trajectory:
        rows = _read_jsonl(self._traj_path)
        return Trajectory.from_json(rows)

    def summary(self) -> Dict[str, Any]:
        evs = self.read_events()
        kinds: Dict[str, int] = {}
        for e in evs:
            kinds[e["type"]] = kinds.get(e["type"], 0) + 1
        return {
            "session_id": self.meta.session_id,
            "run_dir": self.run_dir,
            "events": len(evs),
            "by_type": kinds,
            "trajectory_len": len(self.read_trajectory()),
            "prefix_hash": self.meta.prefix_hash,
            "persist_failures": getattr(self, "_persist_failures", 0),
        }


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                # 损坏的行不抛错 —— 保留此前所有完好的记录，
                # 这正是不重写文件、只追加的收益。
                continue
    return out


def list_runs(workspace: str) -> List[Dict[str, Any]]:
    """列出工作区内的所有会话（供前端会话列表使用）。"""
    root = os.path.join(os.path.realpath(workspace), ".crucible", "runs")
    if not os.path.isdir(root):
        return []
    out: List[Dict[str, Any]] = []
    for name in sorted(os.listdir(root), reverse=True):
        d = os.path.join(root, name)
        mp = os.path.join(d, "meta.json")
        if not os.path.isfile(mp):
            continue
        try:
            with open(mp, "r", encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        m["run_dir"] = d
        out.append(m)
    return out
