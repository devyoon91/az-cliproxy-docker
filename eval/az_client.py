"""Agent Zero 클라이언트 — 케이스 실행을 위한 최소 인터페이스.

설계 의도:

- runner 는 `AZClient` Protocol 만 알고, 구체 구현은 둘:
  - `HTTPAZClient` — 실제 AZ 컨테이너에 메시지를 보내고 task_report
    JSON 이 디스크에 떨어지는 것을 기다린다.
  - `FakeAZClient` — 테스트용. 미리 준비한 task_report 를 즉시 떨어뜨려
    runner 의 트레이스 변환·가드 검사 로직만 단위 테스트한다.

task_report JSON 의 생성 자체는 AZ 내부 확장 (`monologue_end/_50_task_report_finish.py`)
이 책임지므로, 클라이언트는 "메시지 전달 → 완료된 JSON 등장 대기" 만 한다.
별도 토큰 트래킹 코드를 만들지 않는 게 핵심: 기존 task_report 시스템이
이미 입출력/캐시/비용을 정확히 기록하므로 그걸 재사용.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunResult:
    """클라이언트가 한 케이스 실행 후 반환하는 원시 결과.

    - `task_report`: AZ task_report JSON 의 파싱된 dict. 완료된 경우 항상 채워짐.
    - `started_at_iso` / `ended_at_iso`: 메시지 송신 ~ 완료 감지 사이의 UTC 시각.
    - `error`: 클라이언트 측 오류 (timeout, 네트워크, 파싱 등). task_report
      가 채워졌으면 None.
    """

    task_report: dict[str, Any] | None
    started_at_iso: str
    ended_at_iso: str
    error: str | None = None


class AZClient(Protocol):
    """Eval runner 가 의존하는 최소 인터페이스."""

    async def send_and_wait(self, task: str, *, timeout_sec: int) -> RunResult:
        ...


# ── HTTP 구현 ───────────────────────────────────────────────────────────


class HTTPAZClient:
    """실제 AZ 컨테이너에 붙어 케이스를 실행.

    동작:
    1. `/api/message_async` 로 task 송신.
    2. `tasks_dir` (= host 의 `agent-zero/logs/tasks/`) 에서 `started_at`
       이 송신 시각 이후이고 `ended_reason == "completed"` 인 JSON 을
       polling.
    3. 그 JSON 을 task_report 로 반환.

    aiohttp 는 옵션 의존성으로 두고 실제 import 는 메서드 안에서만 한다 —
    테스트는 FakeAZClient 로 돌리므로 aiohttp 미설치 환경에서도 import 가능.
    """

    def __init__(
        self,
        *,
        az_url: str,
        az_prefix: str = "/api",
        tasks_dir: Path | str,
        poll_interval_sec: float = 0.5,
    ) -> None:
        self.az_url = az_url.rstrip("/")
        self.az_prefix = az_prefix
        self.tasks_dir = Path(tasks_dir)
        self.poll_interval_sec = poll_interval_sec

    async def send_and_wait(
        self, task: str, *, timeout_sec: int
    ) -> RunResult:
        import aiohttp  # 지연 import — 테스트 환경 의존성 회피

        from .trace import now_iso

        started_iso = now_iso()
        started_mono = time.monotonic()

        # 1. 메시지 송신.
        try:
            async with aiohttp.ClientSession() as session:
                # CSRF 토큰 획득 (v1.8+ 보호).
                csrf = ""
                try:
                    async with session.get(
                        f"{self.az_url}{self.az_prefix}/csrf_token",
                        headers={"Origin": "http://localhost:50001"},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status == 200:
                            csrf = (await resp.json()).get("token", "")
                except Exception as e:
                    logger.warning(f"csrf_token fetch failed: {e}")

                headers = {"Origin": "http://localhost:50001"}
                if csrf:
                    headers["X-CSRF-Token"] = csrf

                async with session.post(
                    f"{self.az_url}{self.az_prefix}/message_async",
                    json={"text": task, "context": ""},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        return RunResult(
                            task_report=None,
                            started_at_iso=started_iso,
                            ended_at_iso=now_iso(),
                            error=(
                                f"message_async failed: {resp.status} {body[:200]}"
                            ),
                        )
        except Exception as e:
            return RunResult(
                task_report=None,
                started_at_iso=started_iso,
                ended_at_iso=now_iso(),
                error=f"send failed: {e}",
            )

        # 2. 완료된 task_report JSON 을 디스크에서 polling.
        report = await self._wait_for_report(
            send_mono=started_mono, timeout_sec=timeout_sec
        )
        ended_iso = now_iso()

        if report is None:
            return RunResult(
                task_report=None,
                started_at_iso=started_iso,
                ended_at_iso=ended_iso,
                error=f"timed out waiting for task_report ({timeout_sec}s)",
            )

        return RunResult(
            task_report=report,
            started_at_iso=started_iso,
            ended_at_iso=ended_iso,
        )

    async def _wait_for_report(
        self, *, send_mono: float, timeout_sec: int
    ) -> dict[str, Any] | None:
        """`tasks_dir` 에서 송신 이후 생성된 completed task_report 를 찾을 때까지 대기.

        조건:
        - JSON 파일의 mtime 이 송신 시각 이후 (간단·신뢰가능한 1차 필터)
        - `ended_reason == "completed"` (orphaned 는 실패로 간주)

        여러 후보가 있으면 mtime 이 가장 빠른 것을 고른다 — 같은 디렉토리를
        쓰는 평행 작업이 없다는 단일 테넌트 가정 (eval 운영 시 유지).
        """
        send_wall = time.time() - (time.monotonic() - send_mono)
        deadline = send_mono + timeout_sec

        while time.monotonic() < deadline:
            if self.tasks_dir.exists():
                candidates: list[tuple[float, Path]] = []
                for p in self.tasks_dir.glob("*.json"):
                    if p.name.endswith(".tmp"):
                        continue
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    if st.st_mtime < send_wall:
                        continue
                    candidates.append((st.st_mtime, p))

                candidates.sort(key=lambda x: x[0])
                for _, path in candidates:
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    if data.get("ended_reason") == "completed":
                        return data

            await asyncio.sleep(self.poll_interval_sec)

        return None


# ── 테스트용 가짜 클라이언트 ────────────────────────────────────────────


class FakeAZClient:
    """캔드 응답을 즉시 반환하는 테스트 더블.

    `responses` 큐에 RunResult 를 넣어두면 호출 순서대로 반환한다.
    큐가 비면 마지막 응답을 반복한다 — 한 가지 결과만 흉내내고 싶을 때 편의.
    """

    def __init__(self, responses: list[RunResult]) -> None:
        if not responses:
            raise ValueError("FakeAZClient requires at least one response")
        self._responses = list(responses)
        self._idx = 0
        self.received_tasks: list[str] = []
        self.received_timeouts: list[int] = []

    async def send_and_wait(
        self, task: str, *, timeout_sec: int
    ) -> RunResult:
        self.received_tasks.append(task)
        self.received_timeouts.append(timeout_sec)
        if self._idx < len(self._responses):
            resp = self._responses[self._idx]
            self._idx += 1
            return resp
        return self._responses[-1]
