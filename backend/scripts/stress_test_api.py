"""
UsingAll API 压力测试脚本
测试 subapi.usingall.com/v1 的 chat completions 和 embeddings 端点

用法:
    python backend/scripts/stress_test_api.py
    python backend/scripts/stress_test_api.py --concurrency 20 --requests 100
    python backend/scripts/stress_test_api.py --endpoint embeddings --concurrency 10
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field

import httpx

# ── Config ──────────────────────────────────────────────────────────────
BASE_URL = "https://subapi.usingall.com/v1"
API_KEY = "sk-8039812dc33be5c4f789ebc50ab724c844dc5a364a21ad824fe623906a4b9715"
CHAT_MODEL = "claude-haiku-4-5-20251001"
AGENT_MODEL = "claude-sonnet-4-6"
EMBEDDING_MODEL = "openai/text-embedding-3-small"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

# ── Test payloads ───────────────────────────────────────────────────────
CHAT_PAYLOADS = [
    {"role": "user", "content": "用一句话解释量子计算"},
    {"role": "user", "content": "What is the capital of France?"},
    {"role": "user", "content": "写一首关于春天的五言绝句"},
    {"role": "user", "content": "Explain recursion in 20 words"},
    {"role": "user", "content": "列出三种常见的排序算法"},
]

EMBEDDING_TEXTS = [
    "今天天气真好，适合出去散步",
    "Machine learning is a subset of artificial intelligence",
    "量子计算利用量子力学原理进行信息处理",
    "The quick brown fox jumps over the lazy dog",
    "深度学习在自然语言处理领域取得了巨大突破",
]


@dataclass
class RequestResult:
    success: bool
    latency_ms: float
    status_code: int = 0
    error: str = ""
    tokens_used: int = 0
    first_token_ms: float = 0  # TTFT for streaming


@dataclass
class StressTestReport:
    endpoint: str
    model: str
    total_requests: int
    concurrency: int
    streaming: bool = False
    results: list[RequestResult] = field(default_factory=list)

    @property
    def successes(self) -> list[RequestResult]:
        return [r for r in self.results if r.success]

    @property
    def failures(self) -> list[RequestResult]:
        return [r for r in self.results if not r.success]

    @property
    def latencies(self) -> list[float]:
        return [r.latency_ms for r in self.successes]

    def summary(self) -> str:
        lines = []
        lines.append(f"\n{'='*60}")
        lines.append(f"  端点: {self.endpoint}")
        lines.append(f"  模型: {self.model}")
        lines.append(f"  流式: {'是' if self.streaming else '否'}")
        lines.append(f"{'='*60}")

        total = len(self.results)
        ok = len(self.successes)
        fail = len(self.failures)
        lines.append(f"  总请求数:  {total}")
        lines.append(f"  并发数:    {self.concurrency}")
        lines.append(f"  成功:      {ok}  ({ok/total*100:.1f}%)")
        lines.append(f"  失败:      {fail}  ({fail/total*100:.1f}%)")

        if self.latencies:
            lats = sorted(self.latencies)
            lines.append(f"\n  延迟统计 (ms):")
            lines.append(f"    最小:   {min(lats):.0f}")
            lines.append(f"    最大:   {max(lats):.0f}")
            lines.append(f"    平均:   {statistics.mean(lats):.0f}")
            lines.append(f"    中位数: {statistics.median(lats):.0f}")
            lines.append(f"    P90:    {lats[int(len(lats)*0.9)]:.0f}")
            lines.append(f"    P95:    {lats[int(len(lats)*0.95)]:.0f}")
            if len(lats) >= 10:
                lines.append(f"    P99:    {lats[int(len(lats)*0.99)]:.0f}")

            total_time_s = sum(lats) / 1000
            lines.append(f"\n  吞吐量:    {ok / (total_time_s / self.concurrency):.2f} req/s (估算)")

            total_tokens = sum(r.tokens_used for r in self.successes)
            if total_tokens:
                lines.append(f"  总 tokens: {total_tokens}")

        if self.streaming and self.successes:
            ttfts = [r.first_token_ms for r in self.successes if r.first_token_ms > 0]
            if ttfts:
                ttfts.sort()
                lines.append(f"\n  首 Token 延迟 (TTFT, ms):")
                lines.append(f"    最小:   {min(ttfts):.0f}")
                lines.append(f"    平均:   {statistics.mean(ttfts):.0f}")
                lines.append(f"    P90:    {ttfts[int(len(ttfts)*0.9)]:.0f}")
                lines.append(f"    P95:    {ttfts[int(len(ttfts)*0.95)]:.0f}")

        if self.failures:
            error_counts: dict[str, int] = {}
            for r in self.failures:
                key = f"HTTP {r.status_code}" if r.status_code else r.error[:60]
                error_counts[key] = error_counts.get(key, 0) + 1
            lines.append(f"\n  错误分布:")
            for err, cnt in sorted(error_counts.items(), key=lambda x: -x[1]):
                lines.append(f"    {err}: {cnt}次")

        lines.append(f"{'='*60}\n")
        return "\n".join(lines)


# ── Request functions ───────────────────────────────────────────────────

async def do_chat_request(client: httpx.AsyncClient, idx: int) -> RequestResult:
    msg = CHAT_PAYLOADS[idx % len(CHAT_PAYLOADS)]
    payload = {
        "model": CHAT_MODEL,
        "max_tokens": 100,
        "temperature": 0.7,
        "messages": [msg],
    }
    t0 = time.perf_counter()
    try:
        resp = await client.post(f"{BASE_URL}/chat/completions", json=payload, headers=HEADERS)
        latency = (time.perf_counter() - t0) * 1000
        if resp.status_code == 200:
            data = resp.json()
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return RequestResult(True, latency, 200, tokens_used=tokens)
        return RequestResult(False, latency, resp.status_code, error=resp.text[:200])
    except Exception as e:
        latency = (time.perf_counter() - t0) * 1000
        return RequestResult(False, latency, error=str(e)[:200])


async def do_chat_stream_request(client: httpx.AsyncClient, idx: int) -> RequestResult:
    msg = CHAT_PAYLOADS[idx % len(CHAT_PAYLOADS)]
    payload = {
        "model": CHAT_MODEL,
        "max_tokens": 100,
        "temperature": 0.7,
        "messages": [msg],
        "stream": True,
    }
    t0 = time.perf_counter()
    first_token_ms = 0
    try:
        async with client.stream("POST", f"{BASE_URL}/chat/completions",
                                  json=payload, headers=HEADERS) as resp:
            if resp.status_code != 200:
                await resp.aread()
                latency = (time.perf_counter() - t0) * 1000
                return RequestResult(False, latency, resp.status_code, error="stream error")

            chunk_count = 0
            async for line in resp.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    chunk_count += 1
                    if chunk_count == 1:
                        first_token_ms = (time.perf_counter() - t0) * 1000

            latency = (time.perf_counter() - t0) * 1000
            return RequestResult(True, latency, 200, first_token_ms=first_token_ms)
    except Exception as e:
        latency = (time.perf_counter() - t0) * 1000
        return RequestResult(False, latency, error=str(e)[:200])


async def do_embedding_request(client: httpx.AsyncClient, idx: int) -> RequestResult:
    text = EMBEDDING_TEXTS[idx % len(EMBEDDING_TEXTS)]
    payload = {
        "model": EMBEDDING_MODEL,
        "input": text,
    }
    t0 = time.perf_counter()
    try:
        resp = await client.post(f"{BASE_URL}/embeddings", json=payload, headers=HEADERS)
        latency = (time.perf_counter() - t0) * 1000
        if resp.status_code == 200:
            data = resp.json()
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return RequestResult(True, latency, 200, tokens_used=tokens)
        return RequestResult(False, latency, resp.status_code, error=resp.text[:200])
    except Exception as e:
        latency = (time.perf_counter() - t0) * 1000
        return RequestResult(False, latency, error=str(e)[:200])


async def do_agent_model_request(client: httpx.AsyncClient, idx: int) -> RequestResult:
    """Test with the heavier agent model (sonnet)."""
    msg = CHAT_PAYLOADS[idx % len(CHAT_PAYLOADS)]
    payload = {
        "model": AGENT_MODEL,
        "max_tokens": 100,
        "temperature": 0.7,
        "messages": [msg],
    }
    t0 = time.perf_counter()
    try:
        resp = await client.post(f"{BASE_URL}/chat/completions", json=payload, headers=HEADERS)
        latency = (time.perf_counter() - t0) * 1000
        if resp.status_code == 200:
            data = resp.json()
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return RequestResult(True, latency, 200, tokens_used=tokens)
        return RequestResult(False, latency, resp.status_code, error=resp.text[:200])
    except Exception as e:
        latency = (time.perf_counter() - t0) * 1000
        return RequestResult(False, latency, error=str(e)[:200])


# ── Runner ──────────────────────────────────────────────────────────────

async def run_test(
    name: str,
    model: str,
    request_fn,
    total: int,
    concurrency: int,
    streaming: bool = False,
) -> StressTestReport:
    report = StressTestReport(
        endpoint=name, model=model,
        total_requests=total, concurrency=concurrency, streaming=streaming,
    )
    sem = asyncio.Semaphore(concurrency)

    async def bounded(idx: int):
        async with sem:
            return await request_fn(client, idx)

    print(f"\n▶ 开始测试: {name} (模型: {model}, 并发: {concurrency}, 总数: {total})")

    async with httpx.AsyncClient(timeout=120, verify=False, limits=httpx.Limits(
        max_connections=concurrency + 5,
        max_keepalive_connections=concurrency,
    )) as client:
        t0 = time.perf_counter()
        tasks = [bounded(i) for i in range(total)]
        results = await asyncio.gather(*tasks)
        wall_time = time.perf_counter() - t0

    report.results = list(results)
    print(f"  完成! 耗时 {wall_time:.1f}s")
    return report


# ── Main ────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="UsingAll API 压力测试")
    parser.add_argument("--concurrency", "-c", type=int, default=10, help="并发数 (default: 10)")
    parser.add_argument("--requests", "-n", type=int, default=30, help="每个端点的请求总数 (default: 30)")
    parser.add_argument("--endpoint", "-e", choices=["all", "chat", "chat-stream", "embeddings", "agent"],
                        default="all", help="测试哪个端点 (default: all)")
    args = parser.parse_args()

    reports: list[StressTestReport] = []

    if args.endpoint in ("all", "chat"):
        r = await run_test("Chat Completions (非流式)", CHAT_MODEL,
                           do_chat_request, args.requests, args.concurrency)
        reports.append(r)

    if args.endpoint in ("all", "chat-stream"):
        r = await run_test("Chat Completions (流式)", CHAT_MODEL,
                           do_chat_stream_request, args.requests, args.concurrency, streaming=True)
        reports.append(r)

    if args.endpoint in ("all", "embeddings"):
        r = await run_test("Embeddings", EMBEDDING_MODEL,
                           do_embedding_request, args.requests, args.concurrency)
        reports.append(r)

    if args.endpoint in ("all", "agent"):
        r = await run_test("Agent Model Chat", AGENT_MODEL,
                           do_agent_model_request, args.requests, args.concurrency)
        reports.append(r)

    # ── Print report ────────────────────────────────────────────────────
    print("\n" + "█" * 60)
    print("  UsingAll API 压力测试报告")
    print(f"  Base URL: {BASE_URL}")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("█" * 60)

    for r in reports:
        print(r.summary())

    # Save JSON report
    report_path = f"backend/scripts/stress_test_report_{int(time.time())}.json"
    json_data = []
    for r in reports:
        entry = {
            "endpoint": r.endpoint,
            "model": r.model,
            "streaming": r.streaming,
            "total": r.total_requests,
            "concurrency": r.concurrency,
            "success_count": len(r.successes),
            "failure_count": len(r.failures),
            "success_rate": f"{len(r.successes)/r.total_requests*100:.1f}%",
        }
        if r.latencies:
            lats = sorted(r.latencies)
            entry["latency_ms"] = {
                "min": round(min(lats)),
                "max": round(max(lats)),
                "mean": round(statistics.mean(lats)),
                "median": round(statistics.median(lats)),
                "p90": round(lats[int(len(lats)*0.9)]),
                "p95": round(lats[int(len(lats)*0.95)]),
            }
        if r.streaming:
            ttfts = [x.first_token_ms for x in r.successes if x.first_token_ms > 0]
            if ttfts:
                ttfts.sort()
                entry["ttft_ms"] = {
                    "min": round(min(ttfts)),
                    "mean": round(statistics.mean(ttfts)),
                    "p90": round(ttfts[int(len(ttfts)*0.9)]),
                    "p95": round(ttfts[int(len(ttfts)*0.95)]),
                }
        if r.failures:
            entry["errors"] = [{"status": f.status_code, "error": f.error[:100]} for f in r.failures[:5]]
        json_data.append(entry)

    with open(report_path, "w") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    print(f"JSON 报告已保存: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
