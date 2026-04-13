#!/usr/bin/env python3
"""Seed 30 demo notes, generate insights, and share 2 to Ground.

Usage:
    python backend/scripts/seed_demo.py [--base-url http://localhost:8000]
"""
from __future__ import annotations

import argparse
import sys
import time

import httpx

BASE = "http://localhost:8000"
EMAIL = "demo@atelier.dev"
USERNAME = "demo"
PASSWORD = "Demo1234!"

# ---------------------------------------------------------------------------
# 30 notes across 6 categories with overlapping tags
# ---------------------------------------------------------------------------
NOTES: list[dict] = [
    # ── AI & Product (5) ──
    {
        "title": "LLM Routing Strategies for Multi-Model SaaS",
        "tags": ["ai", "product", "saas", "llm"],
        "markdown_content": (
            "## The Problem\n\n"
            "Running a single model per request is wasteful. A smarter approach routes "
            "queries by complexity — cheap models handle FAQ-style questions while frontier "
            "models tackle reasoning-heavy tasks.\n\n"
            "## Key Insight\n\n"
            "Latency-aware routing with a lightweight classifier (distilbert-base) achieves "
            "~92% accuracy on difficulty prediction. Combined with token-budget caps, this "
            "cuts inference cost by 40% without degrading user-perceived quality.\n\n"
            "Tags to revisit: cost optimization, model cascading, SLA tiers."
        ),
    },
    {
        "title": "Agent Tool-Use Patterns in Production",
        "tags": ["ai", "agent", "product", "llm"],
        "markdown_content": (
            "Observations from running tool-calling agents at scale:\n\n"
            "1. **Retry with back-off** — agents that retry failed tool calls with exponential "
            "back-off succeed 3x more often than single-shot.\n"
            "2. **Sandboxing** — every code-exec tool must run in a gVisor container; we learned "
            "this the hard way after a `rm -rf /` incident in staging.\n"
            "3. **Observation budget** — capping tool calls at 8 per turn prevents infinite loops "
            "while still allowing complex multi-step reasoning."
        ),
    },
    {
        "title": "Building a Feedback Flywheel for AI Products",
        "tags": ["ai", "product", "saas", "growth"],
        "markdown_content": (
            "The best AI products create a data flywheel:\n\n"
            "User action → model prediction → user correction → fine-tuning data → better model.\n\n"
            "Key metrics to track:\n"
            "- **Acceptance rate**: % of AI suggestions users keep as-is\n"
            "- **Edit distance**: how much users modify AI output\n"
            "- **Time-to-value**: seconds from request to useful output\n\n"
            "我们在 Atelier 的实践：每周从 rejection logs 中采样 500 条做 DPO 训练，"
            "acceptance rate 从 62% 提升到 78%。"
        ),
    },
    {
        "title": "Prompt Engineering as Product Design",
        "tags": ["ai", "product", "llm", "strategy"],
        "markdown_content": (
            "Prompt engineering is not a hack — it's the new UX copywriting.\n\n"
            "Principles:\n"
            "- **Persona framing** beats instruction-only prompts by ~15% on helpfulness\n"
            "- **Few-shot examples** should come from real user sessions, not synthetic data\n"
            "- **Chain-of-thought** is worth the extra tokens for any task requiring reasoning\n\n"
            "The prompt IS the product. Treat it like code: version it, A/B test it, review it."
        ),
    },
    {
        "title": "从 RAG 到 Agentic RAG：下一代知识系统",
        "tags": ["ai", "agent", "llm", "research"],
        "markdown_content": (
            "传统 RAG 的局限：\n"
            "- 单次检索，无法处理需要多跳推理的问题\n"
            "- 检索质量完全依赖 embedding 模型\n\n"
            "Agentic RAG 的改进：\n"
            "1. Agent 自主决定何时检索、检索什么\n"
            "2. 支持 iterative refinement — 根据初次检索结果调整 query\n"
            "3. 可以调用外部工具（计算器、代码执行）验证检索结果\n\n"
            "实测：在 HotpotQA 上，agentic RAG 比 naive RAG 高 18 个 F1 点。"
        ),
    },
    # ── Academic Physics (5) ──
    {
        "title": "Topological Photonics: Edge States in Coupled Resonators",
        "tags": ["physics", "optics", "quantum", "research"],
        "markdown_content": (
            "## Summary\n\n"
            "Coupled microring resonators arranged in a Harper-Hofstadter lattice exhibit "
            "topological edge states robust against fabrication disorder.\n\n"
            "Key results from our simulation:\n"
            "- Chern number C=1 confirmed via Berry phase integration\n"
            "- Edge state transmission >95% even with 10% random coupling variation\n"
            "- Q-factor of edge modes: ~2.4×10⁵\n\n"
            "Next: fabricate on SOI platform, measure with tunable laser."
        ),
    },
    {
        "title": "Quantum Error Correction with Surface Codes",
        "tags": ["physics", "quantum", "research"],
        "markdown_content": (
            "Reading notes on Fowler et al. (2012) surface code review.\n\n"
            "- Threshold error rate: ~1% per gate for distance-3 code\n"
            "- Logical error rate scales as p^(d/2) where d is code distance\n"
            "- Current best physical error rates (Google Willow): 0.1-0.3%\n\n"
            "Open question: can we use photonic qubits for surface codes? "
            "Loss tolerance is the main challenge — need >99% heralding efficiency."
        ),
    },
    {
        "title": "非线性光学中的孤子动力学",
        "tags": ["physics", "optics", "research", "simulation"],
        "markdown_content": (
            "## 研究笔记\n\n"
            "在 Kerr 非线性介质中，时间孤子的形成需要反常色散和自相位调制的平衡。\n\n"
            "数值模拟参数：\n"
            "- 非线性系数 γ = 1.2 W⁻¹km⁻¹\n"
            "- 色散 β₂ = -20 ps²/km\n"
            "- 输入功率 P₀ = 1 W\n\n"
            "Split-step Fourier 方法收敛良好，步长 Δz = 0.1 m 即可。"
        ),
    },
    {
        "title": "Metasurface Design for Orbital Angular Momentum",
        "tags": ["physics", "optics", "materials-science"],
        "markdown_content": (
            "Metasurfaces can generate OAM beams with high purity.\n\n"
            "Design approach:\n"
            "1. Pancharatnam-Berry phase elements (half-wave plates with varying orientation)\n"
            "2. Each element rotates by φ/2 where φ is the desired azimuthal phase\n"
            "3. Fabrication: e-beam lithography on TiO₂/glass\n\n"
            "Measured OAM purity: 94% for l=1, 87% for l=3. "
            "Higher-order modes need smaller feature sizes — pushing fab limits."
        ),
    },
    {
        "title": "Reading: Ashcroft & Mermin Ch.22 — Phonons",
        "tags": ["physics", "research", "materials-science"],
        "markdown_content": (
            "Key takeaways from phonon chapter:\n\n"
            "- Acoustic vs optical branches: acoustic → in-phase motion, optical → out-of-phase\n"
            "- Debye model works well for T << θ_D but fails near zone boundary\n"
            "- Phonon density of states: Van Hove singularities at critical points\n\n"
            "Connection to VASP: phonon calculations via DFPT (density functional perturbation theory) "
            "or finite displacement method. Need to check IBRION=7 vs 8."
        ),
    },
    # ── Scientific Computing (5) ──
    {
        "title": "VASP INCAR Settings for Hybrid Functional Calculations",
        "tags": ["vasp", "materials-science", "simulation", "hpc"],
        "markdown_content": (
            "## HSE06 Best Practices\n\n"
            "```\nALGO = All\nLHFCALC = .TRUE.\nHFSCREEN = 0.2\n"
            "PRECFOCK = Fast\nNKRED = 2\nTIME = 0.4\n```\n\n"
            "Memory optimization:\n"
            "- NCORE = sqrt(total_cores) works well on our cluster\n"
            "- KPAR should divide NKPTS evenly\n"
            "- For >100 atom cells, use LMAXFOCKAE = 4\n\n"
            "Typical wall time: 48h for 64-atom cell on 128 cores."
        ),
    },
    {
        "title": "Convergence Testing Protocol for DFT Calculations",
        "tags": ["vasp", "materials-science", "simulation"],
        "markdown_content": (
            "Standard convergence tests before production runs:\n\n"
            "1. **ENCUT**: scan 300-600 eV in 50 eV steps, converge total energy to <1 meV/atom\n"
            "2. **KPOINTS**: Monkhorst-Pack grid, increase until energy change <1 meV/atom\n"
            "3. **SIGMA**: for metals use ISMEAR=1, SIGMA=0.2; for insulators ISMEAR=0, SIGMA=0.05\n\n"
            "Automation script: `convergence_test.py` in our lab repo handles this. "
            "Submits SLURM jobs and plots convergence curves automatically."
        ),
    },
    {
        "title": "HPC Job Scheduling: SLURM Tips",
        "tags": ["hpc", "simulation", "productivity"],
        "markdown_content": (
            "Useful SLURM patterns I keep forgetting:\n\n"
            "```bash\n# Array jobs for parameter sweeps\n"
            "sbatch --array=1-20 sweep.sh\n\n"
            "# Dependency chains\n"
            "jid1=$(sbatch --parsable relax.sh)\n"
            "sbatch --dependency=afterok:$jid1 scf.sh\n```\n\n"
            "Memory gotcha: `--mem-per-cpu` vs `--mem` — the former is per core, "
            "the latter is per node. Always use `--mem-per-cpu` for VASP jobs.\n\n"
            "南开 HPC 特殊配置：partition=gpu_v100, gres=gpu:1"
        ),
    },
    {
        "title": "Machine Learning Interatomic Potentials",
        "tags": ["materials-science", "ai", "simulation", "research"],
        "markdown_content": (
            "ML potentials bridge the accuracy-speed gap:\n\n"
            "| Method | Accuracy | Speed | Training Data |\n"
            "|--------|----------|-------|---------------|\n"
            "| DFT | Reference | 1x | N/A |\n"
            "| GAP/SOAP | ~1 meV/atom | 100x | ~1000 configs |\n"
            "| NequIP | ~0.5 meV/atom | 500x | ~500 configs |\n"
            "| MACE | ~0.3 meV/atom | 300x | ~500 configs |\n\n"
            "For our perovskite project, MACE looks most promising. "
            "Need to generate training data with VASP MD at 300K, 600K, 900K."
        ),
    },
    {
        "title": "Wannier90 + VASP Workflow for Band Unfolding",
        "tags": ["vasp", "materials-science", "simulation", "physics"],
        "markdown_content": (
            "Band unfolding workflow for supercell calculations:\n\n"
            "1. Run VASP SCF on supercell with LORBIT=14\n"
            "2. Generate wannier90.win with appropriate projections\n"
            "3. Run VASP with LWANNIER90=.TRUE.\n"
            "4. Post-process with BandUP or fold2Bloch\n\n"
            "Gotcha: VASP 6.4+ changed the WAVECAR format for NCL calculations. "
            "Need to recompile Wannier90 with the updated interface.\n\n"
            "参考：PRB 89, 041407 (2014) — Popescu & Zunger unfolding method."
        ),
    },
    # ── Startup (5) ──
    {
        "title": "Zero-to-One: Finding Product-Market Fit",
        "tags": ["startup", "product", "strategy", "growth"],
        "markdown_content": (
            "Notes from talking to 12 early-stage founders:\n\n"
            "Common pattern: PMF feels like **pull**, not push. Signs:\n"
            "- Users complain when the product is down\n"
            "- Organic word-of-mouth > 40% of new signups\n"
            "- Usage frequency increases without prompting\n\n"
            "Anti-pattern: building features nobody asked for because 'the vision demands it'. "
            "Ship the smallest thing that solves a real pain, then iterate.\n\n"
            "Book rec: \"The Mom Test\" by Rob Fitzpatrick — best $15 I ever spent."
        ),
    },
    {
        "title": "Fundraising Deck Structure That Works",
        "tags": ["startup", "fundraising", "strategy"],
        "markdown_content": (
            "After reviewing 50+ successful seed decks:\n\n"
            "1. **Problem** (1 slide) — make it visceral\n"
            "2. **Solution** (1 slide) — demo screenshot > words\n"
            "3. **Market** (1 slide) — TAM/SAM/SOM, but focus on SAM\n"
            "4. **Traction** (1 slide) — the money slide\n"
            "5. **Business model** (1 slide)\n"
            "6. **Team** (1 slide) — why YOU\n"
            "7. **Ask** (1 slide) — specific amount + use of funds\n\n"
            "Total: 7-10 slides. If you need more, your story isn't clear enough."
        ),
    },
    {
        "title": "SaaS Metrics That Actually Matter",
        "tags": ["startup", "saas", "growth", "product"],
        "markdown_content": (
            "Forget vanity metrics. Track these:\n\n"
            "- **NDR** (Net Dollar Retention): >120% = world-class\n"
            "- **CAC Payback**: <12 months for seed, <18 for Series A\n"
            "- **Logo churn**: <3% monthly for SMB, <1% for enterprise\n"
            "- **Magic number**: >0.75 means efficient growth\n\n"
            "我们的数据：NDR 108%, CAC payback 9 months, logo churn 2.1%。"
            "需要把 NDR 提到 115%+ 才能拿到好的 Series A term。"
        ),
    },
    {
        "title": "Technical Co-founder Dynamics",
        "tags": ["startup", "strategy", "reflection"],
        "markdown_content": (
            "Reflections on the co-founder relationship after 18 months:\n\n"
            "What works:\n"
            "- Weekly 1:1 with no agenda — just vibes check\n"
            "- Clear ownership: I own product+growth, she owns eng+infra\n"
            "- Shared Notion doc for async disagreements\n\n"
            "What doesn't:\n"
            "- Making hiring decisions over Slack\n"
            "- Avoiding hard conversations about equity refresh\n\n"
            "Rule: if a topic makes you uncomfortable, that's exactly when you need to discuss it face-to-face."
        ),
    },
    {
        "title": "Growth Loops vs Growth Funnels",
        "tags": ["startup", "growth", "strategy", "product"],
        "markdown_content": (
            "Funnels are linear: acquire → activate → retain → monetize.\n"
            "Loops are circular: user action → output → new user input.\n\n"
            "Examples of loops:\n"
            "- **Content loop**: user creates content → SEO indexes it → new users find it\n"
            "- **Viral loop**: user invites friend → friend joins → friend invites\n"
            "- **Data loop**: user data → better model → better product → more users\n\n"
            "Our AI product has a natural data loop. Need to make it explicit in the product "
            "by showing users how their feedback improves results."
        ),
    },
    # ── Crypto & Long-term Thinking (5) ──
    {
        "title": "Bitcoin as Digital Gold: A 10-Year Thesis",
        "tags": ["crypto", "investment", "long-term-thinking"],
        "markdown_content": (
            "Core thesis: BTC is a non-sovereign store of value.\n\n"
            "Bull case (60% probability):\n"
            "- Captures 10% of gold's market cap → $500K/BTC\n"
            "- Nation-state adoption continues (El Salvador, UAE)\n"
            "- ETF inflows create sustained demand floor\n\n"
            "Bear case (25%):\n"
            "- Regulatory crackdown in major economies\n"
            "- Quantum computing threat (unlikely before 2035)\n\n"
            "Position sizing: 5% of liquid net worth, DCA monthly, never sell before 2030."
        ),
    },
    {
        "title": "DeFi Yield Farming: Risk Framework",
        "tags": ["crypto", "web3", "investment", "strategy"],
        "markdown_content": (
            "After losing money in Terra/Luna, built a risk framework:\n\n"
            "**Green** (low risk): Aave/Compound on ETH mainnet, <8% APY\n"
            "**Yellow** (medium): established L2 protocols, 8-20% APY\n"
            "**Red** (high): new chains, >20% APY — if yield seems too good, it IS too good\n\n"
            "Rules:\n"
            "1. Never put >10% of crypto portfolio in any single protocol\n"
            "2. Only use audited contracts (Trail of Bits, OpenZeppelin)\n"
            "3. Set stop-loss alerts for impermanent loss >5%"
        ),
    },
    {
        "title": "长期主义与复利思维",
        "tags": ["long-term-thinking", "investment", "reflection", "mindset"],
        "markdown_content": (
            "查理·芒格说：「所有聪明的投资都是价值投资。」\n\n"
            "复利的三个维度：\n"
            "1. **财务复利**：年化 15% 持续 20 年 = 16x\n"
            "2. **知识复利**：每天学 1 小时，10 年后你是领域专家\n"
            "3. **关系复利**：持续给予价值，人脉网络指数增长\n\n"
            "最大的敌人是短期思维。当所有人都在优化季度指标时，"
            "思考 10 年尺度的人拥有巨大的竞争优势。\n\n"
            "Bezos: 'If everything you do needs to work on a three-year time horizon, "
            "then you're competing against a lot of people.'"
        ),
    },
    {
        "title": "Web3 Infrastructure: What Survives the Hype Cycle",
        "tags": ["crypto", "web3", "long-term-thinking", "investment"],
        "markdown_content": (
            "After two crypto winters, pattern recognition:\n\n"
            "**Survives**: infrastructure layers (L1/L2, oracles, bridges)\n"
            "**Dies**: most application-layer tokens, NFT speculation\n\n"
            "Current bets:\n"
            "- Ethereum L2 ecosystem (Arbitrum, Base)\n"
            "- Cross-chain messaging (LayerZero, Wormhole)\n"
            "- Decentralized compute (Akash, Render)\n\n"
            "Thesis: crypto's killer app isn't finance — it's coordination. "
            "DAOs, prediction markets, and decentralized science (DeSci) are underrated."
        ),
    },
    {
        "title": "Lindy Effect and Technology Adoption",
        "tags": ["long-term-thinking", "strategy", "reflection"],
        "markdown_content": (
            "The Lindy Effect: the longer something has survived, the longer it will survive.\n\n"
            "Implications for tech investing:\n"
            "- TCP/IP (50 years) > blockchain (15 years) > LLMs (3 years)\n"
            "- SQL (50 years) will outlast most NoSQL databases\n"
            "- Email (50 years) will outlast Slack\n\n"
            "But: Lindy doesn't apply to things with natural lifespans (companies, people).\n"
            "It applies to ideas, technologies, and cultural artifacts.\n\n"
            "Practical rule: when choosing a technology stack, prefer boring technology. "
            "Innovation tokens are limited — spend them where they matter most."
        ),
    },
    # ── Personal Growth (5) ──
    {
        "title": "Morning Routine Experiment: Week 4 Results",
        "tags": ["productivity", "reflection", "focus", "mindset"],
        "markdown_content": (
            "4 weeks into the new morning routine:\n\n"
            "5:30 — Wake, no phone\n"
            "5:45 — 20 min meditation (Waking Up app)\n"
            "6:05 — Journal (3 pages, stream of consciousness)\n"
            "6:30 — Deep work block (most important task)\n"
            "8:00 — Exercise\n"
            "9:00 — Start regular workday\n\n"
            "Results: deep work output up ~40%, anxiety noticeably lower. "
            "The key insight: protecting the first 2.5 hours from inputs (email, Slack, news) "
            "is the highest-leverage habit change I've made."
        ),
    },
    {
        "title": "On Focus: Lessons from a Distracted Year",
        "tags": ["focus", "reflection", "productivity"],
        "markdown_content": (
            "2024 was scattered. Too many projects, too little depth.\n\n"
            "What I learned:\n"
            "- **Context switching** is the real productivity killer, not lack of hours\n"
            "- **Saying no** is a skill that atrophies without practice\n"
            "- **Depth > breadth** for career capital (Cal Newport was right)\n\n"
            "2025 commitment: maximum 3 active projects at any time. "
            "Everything else goes on a 'someday/maybe' list.\n\n"
            "「少即是多」不是口号，是生存策略。"
        ),
    },
    {
        "title": "Stoic Journaling: What I Control vs What I Don't",
        "tags": ["reflection", "mindset", "focus"],
        "markdown_content": (
            "Epictetus: 'It's not what happens to you, but how you react to it that matters.'\n\n"
            "**I control**: my effort, my attitude, my preparation, my response\n"
            "**I don't control**: outcomes, other people's opinions, market conditions, weather\n\n"
            "This week's application:\n"
            "- Paper rejected → I control: improve methodology, resubmit\n"
            "- Funding delayed → I control: extend runway, cut non-essential spend\n"
            "- Co-founder disagreement → I control: listen first, propose solutions\n\n"
            "The dichotomy of control is the most practical philosophical tool I know."
        ),
    },
    {
        "title": "读书笔记：《心流》米哈里·契克森米哈赖",
        "tags": ["focus", "productivity", "mindset", "reflection"],
        "markdown_content": (
            "心流的八个特征：\n"
            "1. 明确的目标\n"
            "2. 即时反馈\n"
            "3. 挑战与技能的平衡\n"
            "4. 行动与意识的融合\n"
            "5. 排除杂念\n"
            "6. 不担心失败\n"
            "7. 自我意识消失\n"
            "8. 时间感扭曲\n\n"
            "编程是最容易进入心流的活动之一——目标明确、反馈即时、挑战可调。\n"
            "关键：消除外部干扰（通知、会议）是进入心流的前提条件。"
        ),
    },
    {
        "title": "Energy Management > Time Management",
        "tags": ["productivity", "focus", "mindset", "strategy"],
        "markdown_content": (
            "Time is fixed. Energy is variable.\n\n"
            "Energy audit results:\n"
            "- **High energy** (9am-12pm): deep work, writing, coding\n"
            "- **Medium energy** (2pm-5pm): meetings, reviews, collaboration\n"
            "- **Low energy** (after 7pm): reading, planning, admin\n\n"
            "Mistake I kept making: scheduling creative work after 3pm meetings. "
            "Now I protect mornings ruthlessly.\n\n"
            "Sleep is the foundation. 7.5 hours minimum, no negotiation. "
            "One bad night costs two productive days."
        ),
    },
]

# Indices of content-rich notes to share to Ground
SHARE_INDICES = [0, 19]  # "LLM Routing Strategies" and "长期主义与复利思维"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth(client: httpx.Client) -> str:
    """Register (or login if exists) and return Bearer token."""
    r = client.post("/api/v1/auth/register", json={
        "email": EMAIL, "username": USERNAME, "password": PASSWORD,
    })
    if r.status_code == 201:
        print(f"  Registered user {EMAIL}")
    elif r.status_code == 400:
        print(f"  User {EMAIL} already exists, logging in")
    else:
        r.raise_for_status()

    r = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    r.raise_for_status()
    token = r.json()["access_token"]
    print(f"  Got access token: {token[:20]}...")
    return token


def _seed_notes(client: httpx.Client, headers: dict) -> list[str]:
    """Create 30 notes, return list of note IDs."""
    note_ids: list[str] = []
    for i, note in enumerate(NOTES):
        r = client.post("/api/v1/notes", json=note, headers=headers)
        if r.status_code == 201:
            nid = r.json()["id"]
            note_ids.append(nid)
            print(f"  [{i+1:2d}/30] Created: {note['title'][:50]}")
        elif r.status_code == 400 and "DUPLICATE" in r.text:
            print(f"  [{i+1:2d}/30] Skipped (exists): {note['title'][:50]}")
            note_ids.append("")
        else:
            print(f"  [{i+1:2d}/30] FAILED ({r.status_code}): {r.text[:100]}")
            note_ids.append("")
    return note_ids


def _generate_insights(client: httpx.Client, headers: dict) -> None:
    """Trigger insight generation and wait for completion."""
    print("\n  Triggering insight generation...")
    r = client.post("/api/v1/insights/generate", headers=headers)
    if r.status_code not in (200, 202):
        print(f"  WARNING: generate returned {r.status_code}: {r.text[:200]}")
        return

    gen = r.json()
    gen_status = gen.get("status", "unknown")
    print(f"  Generation started: id={gen.get('id', '?')}, status={gen_status}")

    if gen_status in ("completed", "error"):
        print(f"  Generation already {gen_status}")
        return

    # Poll for completion
    for attempt in range(60):
        time.sleep(5)
        r = client.get("/api/v1/insights/generations/latest", headers=headers)
        if r.status_code != 200:
            continue
        latest = r.json()
        if latest is None:
            continue
        st = latest.get("status", "unknown")
        total = latest.get("total_reports", 0)
        print(f"  Poll [{attempt+1}]: status={st}, reports={total}")
        if st == "completed":
            print("  Insight generation completed!")
            return
        if st == "error":
            print(f"  Insight generation failed: {latest.get('error', 'unknown')}")
            return

    print("  WARNING: timed out waiting for insight generation (5 min)")


def _share_to_ground(client: httpx.Client, headers: dict, note_ids: list[str]) -> None:
    """Share selected notes to Ground."""
    for idx in SHARE_INDICES:
        nid = note_ids[idx] if idx < len(note_ids) else ""
        if not nid:
            print(f"  Skipping share for index {idx} (no note ID)")
            continue
        r = client.post(f"/api/v1/ground/notes/{nid}/share", headers=headers)
        if r.status_code == 200:
            print(f"  Shared to Ground: {NOTES[idx]['title'][:50]}")
        else:
            print(f"  Share failed ({r.status_code}): {r.text[:100]}")


def _verify(client: httpx.Client, headers: dict) -> None:
    """Verify seed results."""
    print("\n── Verification ──")

    # Notes
    r = client.get("/api/v1/notes?page_size=50", headers=headers)
    notes = r.json() if r.status_code == 200 else {}
    total = notes.get("total", 0)
    print(f"  Notes: {total} (expected ≥30)")

    # Mind graph
    r = client.get("/api/v1/mind/graph", headers=headers)
    if r.status_code == 200:
        g = r.json()
        print(f"  Mind graph: {len(g.get('nodes', []))} nodes, {len(g.get('edges', []))} edges")
    else:
        print(f"  Mind graph: FAILED ({r.status_code})")

    # Insights
    r = client.get("/api/v1/insights", headers=headers)
    insights = r.json() if r.status_code == 200 else []
    print(f"  Insights: {len(insights)} reports")

    # Ground
    r = client.get("/api/v1/ground/feed", headers=headers)
    feed = r.json() if r.status_code == 200 else []
    print(f"  Ground feed: {len(feed)} shared notes")

    ok = total >= 30
    print(f"\n  {'✓ All checks passed' if ok else '✗ Some checks failed'}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Seed demo data for note-app")
    parser.add_argument("--base-url", default=BASE, help="FastAPI base URL")
    parser.add_argument("--skip-insights", action="store_true", help="Skip insight generation")
    args = parser.parse_args()

    client = httpx.Client(base_url=args.base_url, timeout=120)

    print("═══ Note App Seed Script ═══\n")

    print("1. Auth")
    token = _auth(client)
    headers = {"Authorization": f"Bearer {token}"}

    print("\n2. Creating 30 notes")
    note_ids = _seed_notes(client, headers)

    if not args.skip_insights:
        print("\n3. Generating insights")
        _generate_insights(client, headers)
    else:
        print("\n3. Skipping insight generation")

    print("\n4. Sharing to Ground")
    _share_to_ground(client, headers, note_ids)

    _verify(client, headers)

    print("\n═══ Done ═══")


if __name__ == "__main__":
    main()

