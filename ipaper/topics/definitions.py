"""Versioned research directions, not a list of paper-specific keywords.

A direction requires both a research object and a method/task/context. Common
ambiguous terms are deliberately absent from the sufficient evidence groups.
Public labels are separate from definition identity and matching vocabulary.
"""

from dataclasses import dataclass
import re
import unicodedata

VERSION = "research-directions-1"


@dataclass(frozen=True)
class Direction:
    id: str
    name: str
    parent: str | None
    objects: tuple[str, ...]
    contexts: tuple[str, ...]


DEFINITIONS = (
    Direction(
        "embodied",
        "动作理解与机器人",
        None,
        (
            "robot",
            "robotic",
            "机器人",
            "action",
            "动作",
            "physical reasoning",
            "物理推理",
            "kinematic",
            "运动学",
        ),
        (
            "segmentation",
            "分割",
            "boundary detection",
            "边界检测",
            "control",
            "控制",
            "trajectory",
            "轨迹",
            "policy",
            "策略",
            "representation",
            "表征",
            "primitive",
            "基元",
            "reasoning",
            "推理",
        ),
    ),
    Direction(
        "action-understanding",
        "动作理解",
        "embodied",
        ("action", "动作", "trajectory", "轨迹", "kinematic", "运动学"),
        (
            "segmentation",
            "分割",
            "boundary",
            "边界",
            "primitive",
            "基元",
            "recognition",
            "识别",
        ),
    ),
    Direction(
        "agents",
        "智能体研究",
        None,
        ("agent", "agentic", "智能体", "autonomous", "自主"),
        (
            "reasoning",
            "推理",
            "planning",
            "规划",
            "tool",
            "工具",
            "scientific",
            "科学",
            "discovery",
            "发现",
            "policy",
            "策略",
            "evaluation",
            "evaluating",
            "评估",
            "benchmark",
            "基准",
            "optimization",
            "优化",
            "representation",
            "表征",
        ),
    ),
    Direction(
        "systems",
        "计算机系统",
        None,
        (
            "compiler",
            "编译器",
            "microservice",
            "微服务",
            "operating system",
            "操作系统",
            "sandbox",
            "沙箱",
            "distributed system",
            "分布式系统",
            "edge computing",
            "边缘计算",
            "cloud computing",
            "云计算",
            "database",
            "数据库",
        ),
        (
            "optimization",
            "优化",
            "placement",
            "部署",
            "scheduling",
            "调度",
            "compression",
            "压缩",
            "latency",
            "延迟",
            "throughput",
            "吞吐",
            "memory",
            "内存",
            "fault",
            "容错",
            "consistency",
            "一致性",
        ),
    ),
    Direction(
        "vision",
        "计算机视觉",
        None,
        ("image", "图像", "video", "视频", "point cloud", "点云", "visual", "视觉"),
        (
            "recognition",
            "识别",
            "detection",
            "检测",
            "segmentation",
            "分割",
            "reconstruction",
            "重建",
            "tracking",
            "跟踪",
            "representation",
            "表征",
        ),
    ),
    Direction(
        "language",
        "语言与文本计算",
        None,
        (
            "language model",
            "语言模型",
            "text",
            "文本",
            "machine translation",
            "机器翻译",
            "language understanding",
            "语言理解",
        ),
        (
            "pretraining",
            "预训练",
            "tokenization",
            "分词",
            "alignment",
            "对齐",
            "translation",
            "翻译",
            "generation",
            "生成",
            "retrieval",
            "检索",
        ),
    ),
    Direction(
        "security",
        "安全与隐私",
        None,
        (
            "privacy",
            "隐私",
            "cryptograph",
            "密码学",
            "cybersecurity",
            "网络安全",
            "vulnerability",
            "漏洞",
            "adversarial attack",
            "对抗攻击",
        ),
        (
            "protection",
            "保护",
            "detection",
            "检测",
            "attack",
            "攻击",
            "defense",
            "防御",
            "verification",
            "验证",
            "encryption",
            "加密",
            "learning",
            "学习",
        ),
    ),
    Direction(
        "health",
        "生物医学计算",
        None,
        (
            "medical",
            "医学",
            "clinical",
            "临床",
            "protein",
            "蛋白质",
            "genome",
            "基因组",
            "drug",
            "药物",
        ),
        (
            "prediction",
            "预测",
            "diagnosis",
            "诊断",
            "discovery",
            "发现",
            "analysis",
            "分析",
            "learning",
            "学习",
            "design",
            "设计",
        ),
    ),
)
BY_ID = {d.id: d for d in DEFINITIONS}


def normalized(text):
    return re.sub(r"[^\S\n]+", " ", unicodedata.normalize("NFKC", text).casefold())


def contains(text, phrase):
    if phrase.isascii():
        # English inflections are permitted only at a word end. "agent" must
        # not match "reagent"; "cloud" alone is never systems evidence.
        return bool(
            re.search(
                r"(?<![a-z])" + re.escape(phrase) + r"(?:s|es|ing|ic)?(?![a-z])", text
            )
        )
    return phrase in text


def classify(sections, allowed=None, check=lambda: None):
    """Bounded lexical overlap gated by co-occurring object and task evidence.

    Scores are internal ranking only. A title gets extra weight, but an isolated
    hit is never sufficient, and evidence cannot cross unrelated paragraphs.
    """
    allowed = set(BY_ID) if allowed is None else set(allowed)
    scores = {}
    consumed = 0
    for section in sections[:1000]:
        check()
        raw = str(section.get("text") or "")[: max(0, 200000 - consumed)]
        consumed += len(raw)
        text = normalized(raw)
        weight = 2 if section.get("kind") == "title" else 1
        for paragraph in re.split(r"\n|(?<=[.!?。！？])\s+", text):
            # A bounded local window prevents a vocabulary hit in the beginning
            # and an unrelated discussion at the end from creating a category.
            for start in range(0, len(paragraph), 600):
                window = paragraph[start : start + 900]
                for direction in DEFINITIONS:
                    if direction.id not in allowed:
                        continue
                    objects = sum(contains(window, term) for term in direction.objects)
                    contexts = sum(
                        contains(window, term) for term in direction.contexts
                    )
                    if objects and contexts:
                        overlap = (objects + contexts) / (
                            len(direction.objects) + len(direction.contexts)
                        )
                        scores[direction.id] = max(
                            scores.get(direction.id, 0), weight * overlap
                        )
        if consumed >= 200000:
            break
    # Persist the most specific supported node per branch, infer ancestors in
    # queries. Stable tie breaking makes repeated local tasks deterministic.
    for direction in DEFINITIONS:
        if direction.id in scores and direction.parent in scores:
            del scores[direction.parent]
    return sorted(scores, key=lambda key: (-scores[key], key))[:3]


def proposed_directions(papers, existing=()):
    """Bootstrap broad directions; subdivisions need two distinct papers."""
    support = {}
    for paper_id, sections in papers:
        for key in classify(sections):
            support.setdefault(key, set()).add(paper_id)
            if BY_ID[key].parent:
                support.setdefault(BY_ID[key].parent, set()).add(paper_id)
    roots = sorted(
        (key for key in support if BY_ID[key].parent is None),
        key=lambda key: (-len(support[key]), key),
    )[:6]
    chosen = set(roots)
    chosen.update(
        key for key in support if BY_ID[key].parent in chosen and len(support[key]) >= 2
    )
    return [d for d in DEFINITIONS if d.id in chosen and d.id not in set(existing)]
