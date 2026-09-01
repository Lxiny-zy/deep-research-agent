"""Chaos 恢复演示：kill -9 强杀研究进程，重启后自动接管断点续跑（完全离线、可复现）。

流程：
  1. 起 API 子进程（uvicorn + 独立 SQLite 临时库 + DR_DEMO_FAKE_BACKENDS=1 离线假后端，
     每次 LLM/检索调用 sleep 数秒并计入模拟 token）；
  2. 先跑一次「对照 run」（不杀进程）拿到完整跑一遍的 token 基线；
  3. 提交 chaos run（deep 工作流：planner → researcher → reflect_loop → synthesizer），
     监听 SSE 事件流，跑到中间层（默认 step-3 反思补洞）开始时硬杀子进程（等价 kill -9）；
  4. 等崩溃 worker 的执行租约 TTL 过期（fencing 安全窗），重启 API；
  5. 启动恢复扫描自动接管：跳过已完成节点、只重跑断点之后的节点，轮询到终态；
  6. 输出报告：kill 时刻、接管耗时（重启 → 首个 workflow.resumed 事件）、
     各节点续跑跳过/重执行、断点续跑节省 token 占比。

用法：
    python scripts/chaos_demo.py                # 默认参数即可复现
    python scripts/chaos_demo.py --delay 1.5    # 调整每次后端调用的人为延迟（秒）

依赖：仓库已装依赖（httpx / uvicorn / fastapi）。全程无需任何真实 API key 与网络。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
LEASE_TTL_SECONDS = 120.0  # 与 sql_repository.acquire_lease 默认 TTL 一致
LEASE_MARGIN_SECONDS = 3.0  # 时钟/落库偏差余量；周期恢复扫描（30s）兜底
HEALTH_TIMEOUT = 60.0
RUN_TIMEOUT = 180.0


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class RunWatch:
    """SSE 观测到的单个 run 的关键量。"""

    done_tokens: int = 0
    done_elapsed: float = 0.0
    last_checkpoint_tokens: int = 0
    completed_nodes: list[str] = field(default_factory=list)
    resumed_at: float | None = None  # perf_counter 时刻
    killed_at_node: str | None = None
    finished: bool = False
    error: str | None = None


class ApiServer:
    """API 子进程管理（跨平台：Popen + proc.kill()，Windows 上即 TerminateProcess）。"""

    def __init__(self, port: int, env: dict[str, str], log_path: Path) -> None:
        self.port = port
        self.env = env
        self.log_path = log_path
        self.proc: subprocess.Popen[bytes] | None = None
        self.started_at: float = 0.0

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        log = open(self.log_path, "ab")  # noqa: SIM115 子进程持有到退出
        self.started_at = time.perf_counter()
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "deep_research.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "warning",
            ],
            cwd=str(REPO_ROOT),
            env=self.env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    def wait_health(self, client: httpx.Client) -> float:
        deadline = time.monotonic() + HEALTH_TIMEOUT
        while time.monotonic() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError(f"API 子进程提前退出，日志见 {self.log_path}")
            try:
                if client.get(f"{self.base}/healthz", timeout=2.0).status_code == 200:
                    return time.perf_counter()
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise RuntimeError(f"API 在 {HEALTH_TIMEOUT}s 内未就绪，日志见 {self.log_path}")

    def kill_hard(self) -> None:
        """等价 kill -9：Windows 上 Popen.kill() 即 TerminateProcess，无清理机会。"""
        assert self.proc is not None
        self.proc.kill()
        self.proc.wait(timeout=30)

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=15)


class WorkerProcess:
    """执行 worker 子进程（``--target worker``）。

    与 ``ApiServer`` 的关键区别：杀掉它**不影响 API**——SSE 连接不断、历史可读、
    新请求照常入队。恢复也不需要重启任何东西：另一个 worker 的领取循环在租约
    过期后自然接管。这正是 API/执行分离要证明的性质。
    """

    def __init__(self, env: dict[str, str], log_path: Path, name: str) -> None:
        self.env = env
        self.log_path = log_path
        self.name = name
        self.proc: subprocess.Popen[bytes] | None = None
        self.started_at: float = 0.0

    def start(self) -> None:
        log = open(self.log_path, "ab")  # noqa: SIM115 子进程持有到退出
        self.started_at = time.perf_counter()
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "deep_research.worker", "--name", self.name],
            cwd=str(REPO_ROOT),
            env=self.env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    def kill_hard(self) -> None:
        assert self.proc is not None
        self.proc.kill()
        self.proc.wait(timeout=30)

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=15)


def sse_events(client: httpx.Client, url: str, *, timeout: float):
    """迭代 SSE 事件（dict）。忽略心跳注释行；读超时由调用方兜底。"""
    with client.stream("GET", url, timeout=httpx.Timeout(10.0, read=timeout)) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            yield json.loads(line[len("data: ") :])


def create_run(client: httpx.Client, base: str, query: str, workflow: str) -> str:
    resp = client.post(
        f"{base}/api/runs", json={"query": query, "workflow": workflow}, timeout=10.0
    )
    resp.raise_for_status()
    return resp.json()["run_id"]


def watch_until_done(client: httpx.Client, base: str, run_id: str) -> RunWatch:
    """跟随事件流直到终态（对照 run / 恢复后的 chaos run 共用）。"""
    watch = RunWatch()
    for ev in sse_events(client, f"{base}/api/runs/{run_id}/stream", timeout=RUN_TIMEOUT):
        data = ev.get("data") or {}
        name = data.get("event_name")
        if name == "workflow.resumed" and watch.resumed_at is None:
            watch.resumed_at = time.perf_counter()
            # 恢复接管前收到的 step.completed 是**历史事件重放**（EventHub 会把
            # 上一 attempt 的进度重新推给订阅者），不是本次真正执行的节点。
            # 不清空的话「重执行」列表会把跳过的节点也算进去，与 token 账目矛盾。
            watch.completed_nodes.clear()
        elif name == "step.completed":
            watch.completed_nodes.append(data.get("node_id", "?"))
        elif name == "checkpoint.saved":
            watch.last_checkpoint_tokens = data.get("total_tokens", ev.get("tokens", 0))
        if ev.get("stage") == "ORCHESTRATOR" and ev.get("type") in ("done", "error"):
            watch.finished = True
            if ev.get("type") == "error":
                watch.error = ev.get("message", "unknown")
            watch.done_tokens = data.get("total_tokens", ev.get("tokens", 0))
            watch.done_elapsed = data.get("elapsed", ev.get("elapsed", 0.0))
            break
    return watch


def watch_until_kill(
    client: httpx.Client,
    base: str,
    run_id: str,
    kill_node: str,
    victim: ApiServer | WorkerProcess,
) -> tuple[RunWatch, float]:
    """监听 chaos run；目标中间层节点 step.started 出现的瞬间硬杀执行进程。"""
    watch = RunWatch()
    kill_at = 0.0
    victim_label = "API" if isinstance(victim, ApiServer) else "worker"
    try:
        for ev in sse_events(client, f"{base}/api/runs/{run_id}/stream", timeout=RUN_TIMEOUT):
            data = ev.get("data") or {}
            name = data.get("event_name")
            if name == "step.completed":
                watch.completed_nodes.append(data.get("node_id", "?"))
            elif name == "checkpoint.saved":
                # 优先读 data.total_tokens：顶层 tokens 不落库，worker 模式下
                # SSE 从仓储回放，只有结构化 data 里的数字是可信的。
                watch.last_checkpoint_tokens = data.get("total_tokens", ev.get("tokens", 0))
            elif name == "step.started" and data.get("node_id") == kill_node:
                watch.killed_at_node = kill_node
                kill_at = time.perf_counter()
                _log(
                    f"节点 {kill_node}（{data.get('label', '')}）开始执行 → "
                    f"硬杀 {victim_label} 进程（等价 kill -9）"
                )
                victim.kill_hard()
                break
    except httpx.HTTPError:
        pass  # 进程被杀导致的断流是预期结局
    if watch.killed_at_node is None:
        raise RuntimeError(f"事件流结束仍未见到目标节点 {kill_node}，无法注入故障")
    return watch, kill_at


def get_run_detail(client: httpx.Client, base: str, run_id: str) -> dict:
    resp = client.get(f"{base}/api/runs/{run_id}", timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def main() -> int:
    parser = argparse.ArgumentParser(description="kill -9 故障恢复演示（离线可复现）")
    parser.add_argument("--delay", type=float, default=2.0, help="每次假 LLM/检索调用的延迟秒数")
    parser.add_argument(
        "--tokens-per-call", type=int, default=1000, help="每次 LLM 调用计入的模拟 token"
    )
    parser.add_argument("--workflow", default="deep", help="演示用工作流（默认 deep，多层结构）")
    parser.add_argument(
        "--kill-node", default="step-3", help="在该节点开始时杀进程（默认第 3 层反思补洞）"
    )
    parser.add_argument(
        "--query", default="量子计算的商业化现状", help="研究问题（离线假数据，仅作标识）"
    )
    parser.add_argument("--port", type=int, default=0, help="API 端口（默认自动挑选空闲端口）")
    parser.add_argument(
        "--target",
        choices=("api", "worker"),
        default="api",
        help=(
            "杀哪个进程：api＝inline 模式，杀 API 后重启并由启动恢复扫描接管（默认）；"
            "worker＝API/执行分离，杀 worker，API 全程存活，由另一个 worker 领取接管"
        ),
    )
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

    tmp = Path(tempfile.mkdtemp(prefix="chaos-demo-"))
    db_path = tmp / "chaos_demo.db"
    port = args.port or _free_port()
    env = {k: v for k, v in os.environ.items() if k not in ("API_KEY",)}
    env.update(
        {
            "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
            "RUNTIME_CONFIG_PATH": str(tmp / "runtime_config.json"),  # 隔离全局持久化配置
            "DR_DEMO_FAKE_BACKENDS": "1",  # 离线假后端注入钩子（api.py，仅供演示/测试）
            "DR_DEMO_STEP_DELAY": str(args.delay),
            "DR_DEMO_TOKENS_PER_CALL": str(args.tokens_per_call),
            "PYTHONUNBUFFERED": "1",
        }
    )
    worker_mode = args.target == "worker"
    if worker_mode:
        # API 只入队；执行与租约归 worker 进程。
        env["DR_EXECUTION_MODE"] = "worker"
        env["DR_WORKER_POLL_SECONDS"] = "0.5"
    server = ApiServer(port, env, tmp / "api.log")
    worker: WorkerProcess | None = None
    standby: WorkerProcess | None = None
    client = httpx.Client()
    try:
        _log(f"临时 SQLite 库：{db_path}")
        _log(f"启动 API 子进程（端口 {port}，离线假后端，每次调用延迟 {args.delay}s）…")
        server.start()
        server.wait_health(client)
        if worker_mode:
            _log("启动执行 worker 子进程（execution_mode=worker，API 只入队）…")
            worker = WorkerProcess(env, tmp / "worker-1.log", "chaos-worker-1")
            worker.start()

        victim: ApiServer | WorkerProcess = worker if worker is not None else server

        # ---- 阶段 1：对照 run（不杀进程），得到完整跑一遍的 token/耗时基线 ----
        _log("阶段 1/4：跑对照 run（不注入故障）…")
        control_id = create_run(client, server.base, args.query, args.workflow)
        control = watch_until_done(client, server.base, control_id)
        if not control.finished or control.error:
            raise RuntimeError(f"对照 run 未正常完成：{control.error}")
        _log(
            f"对照 run 完成：token {control.done_tokens}，耗时 {control.done_elapsed:.1f}s，"
            f"完成节点 {control.completed_nodes}"
        )

        # ---- 阶段 2：chaos run，跑到中间层时 kill -9 ----
        _log(f"阶段 2/4：提交 chaos run，将在节点 {args.kill_node} 开始时硬杀进程…")
        t_created = time.perf_counter()
        chaos_id = create_run(client, server.base, args.query, args.workflow)
        pre_kill, kill_at = watch_until_kill(client, server.base, chaos_id, args.kill_node, victim)
        _log(
            f"已 kill（run 启动后 {kill_at - t_created:.1f}s）；断点前已完成节点："
            f"{pre_kill.completed_nodes}，checkpoint 已保留 token {pre_kill.last_checkpoint_tokens}"
        )
        if worker_mode:
            # 这一条是 worker 模式的核心断言：执行进程死了，API 依然在服务。
            health = client.get(f"{server.base}/healthz", timeout=5.0)
            if health.status_code != 200:
                raise RuntimeError("worker 崩溃不应影响 API 可用性")
            _log("API 仍然健康（worker 崩溃未波及服务面）：/healthz 200")

        # ---- 阶段 3：等租约 TTL 过期（fencing 安全窗）后由新执行者接管 ----
        lease_deadline = t_created + LEASE_TTL_SECONDS + LEASE_MARGIN_SECONDS
        wait_s = max(0.0, lease_deadline - time.perf_counter())
        _log(
            f"阶段 3/4：等待崩溃 worker 的执行租约过期（TTL {LEASE_TTL_SECONDS:.0f}s，"
            f"还需 {wait_s:.0f}s）——这是防脑裂的 fencing 安全窗，而非恢复本身的开销…"
        )
        time.sleep(wait_s)
        if worker_mode:
            _log("启动第二个 worker 子进程接管（API 未重启）…")
            standby = WorkerProcess(env, tmp / "worker-2.log", "chaos-worker-2")
            standby.start()
            t_restart = standby.started_at
            t_health = standby.started_at  # worker 无健康端点：启动即开始领取
        else:
            _log("重启 API 子进程…")
            server.start()
            t_restart = server.started_at
            t_health = server.wait_health(client)

        # ---- 阶段 4：跟随恢复后的事件流直到终态 ----
        _log("阶段 4/4：等待启动恢复扫描自动接管并续跑到终态…")
        resumed = watch_until_done(client, server.base, chaos_id)
        if not resumed.finished or resumed.error:
            raise RuntimeError(f"恢复后的 run 未正常完成：{resumed.error}")
        if resumed.resumed_at is None:
            raise RuntimeError("未观测到 workflow.resumed 事件——不是恢复接管路径")
        takeover_from_start = resumed.resumed_at - t_restart
        takeover_from_health = max(0.0, resumed.resumed_at - t_health)

        detail = get_run_detail(client, server.base, chaos_id)
        steps = (detail.get("orchestration") or {}).get("steps", [])
        # 跳过的节点＝崩溃前已完成并进了 checkpoint 的那些；其余节点都在恢复后重跑。
        # 从持久化的节点记录反推，而不是靠事件到达顺序——后者会漏掉恰好在
        # workflow.resumed 之前完成的断点节点。
        skipped = list(dict.fromkeys(pre_kill.completed_nodes))
        reexecuted = {str(s.get("node_id")) for s in steps if str(s.get("node_id")) not in skipped}

        attempt2_tokens = resumed.done_tokens - pre_kill.last_checkpoint_tokens
        saved_tokens = control.done_tokens - attempt2_tokens
        saved_pct = 100.0 * saved_tokens / control.done_tokens if control.done_tokens else 0.0

        print()
        print("=" * 72)
        topology = (
            "API/执行分离（杀 worker，API 全程存活）"
            if worker_mode
            else "inline（杀 API，重启后启动恢复扫描接管）"
        )
        print("Chaos 恢复演示报告（全程离线假后端，token 为按调用计量的模拟值）")
        print("=" * 72)
        print(
            f"run_id            : {chaos_id}（工作流 {args.workflow}，共 {len(steps)} 个节点记录）"
        )
        print(f"执行拓扑           : {topology}")
        print(
            f"kill 时刻          : run 启动后 {kill_at - t_created:.1f}s，"
            f"节点 {args.kill_node} 刚开始执行（硬杀，等价 kill -9）"
        )
        print(f"租约 fencing 窗    : {LEASE_TTL_SECONDS:.0f}s TTL（崩溃检测窗口，非恢复耗时）")
        if worker_mode:
            print(
                f"恢复接管耗时       : 新 worker 启动 → workflow.resumed 事件 "
                f"{takeover_from_start:.1f}s（API 未重启，服务面零中断）"
            )
        else:
            print(
                f"恢复接管耗时       : 重启进程 → workflow.resumed 事件 {takeover_from_start:.1f}s"
                f"（其中服务就绪后 {takeover_from_health:.1f}s）"
            )
        print(f"续跑节点           : 跳过 {skipped}（断点前已完成，直接复用 checkpoint）")
        print(f"                    重执行 {sorted(reexecuted)}（断点处及其后节点）")
        print("节点明细：")
        for s in steps:
            mark = "断点续跑跳过" if s.get("node_id") in skipped else "恢复后重执行"
            print(
                f"  - {s.get('node_id'):8s} {s.get('label', ''):8s} "
                f"status={s.get('status'):9s} attempt={s.get('attempt')}  [{mark}]"
            )
        print(
            f"token 账目         : 完整跑一遍（对照）= {control.done_tokens}；"
            f"断点 checkpoint 保留 = {pre_kill.last_checkpoint_tokens}；"
            f"恢复后新增 = {attempt2_tokens}；最终累计 = {resumed.done_tokens}"
        )
        print(f"断点续跑节省       : {saved_tokens} token（占完整重跑的 {saved_pct:.1f}%）")
        print(
            f"最终状态           : {detail.get('status')}"
            "（报告已生成，前端 RunPage 可回放事件时间线）"
        )
        print("=" * 72)
        return 0
    finally:
        if standby is not None:
            standby.stop()
        if worker is not None:
            worker.stop()
        server.stop()
        client.close()
        # SQLite 文件在进程退出后才可删；失败不影响演示结果
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
