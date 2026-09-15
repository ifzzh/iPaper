"""Bounded, extractive phrase ranking; no network, model or PDF parsing."""

import math
import re
import time
from collections import Counter
from .common import label, name_key, KeywordError

# This vocabulary normalizes known equivalents; candidates are independently
# extracted from arbitrary text. It is not a topic classifier or a recall gate.
CONCEPTS = [
    ("强化学习", ["reinforcement learning", "RL"]),
    ("离线强化学习", ["offline reinforcement learning", "offline RL"]),
    ("模仿学习", ["imitation learning"]),
    ("机器人学习", ["robot learning"]),
    ("动作分词", ["action tokenization"]),
    ("数据筛选", ["data filtering"]),
    ("数据选择", ["data selection"]),
    ("KV cache", ["key-value cache", "key value cache", "kv cache"]),
    (
        "SLO",
        [
            "service level objective",
            "service-level objective",
            "service level objectives",
        ],
    ),
    ("尾延迟", ["tail latency"]),
    ("资源调度", ["resource scheduling"]),
    (
        "检索增强生成",
        ["retrieval augmented generation", "retrieval-augmented generation", "RAG"],
    ),
    ("大语言模型", ["large language model", "large language models", "LLM", "LLMs"]),
    (
        "视觉语言模型",
        ["vision language model", "vision-language model", "vision-language models"],
    ),
    ("世界模型", ["world model", "world models"]),
    ("长期记忆", ["long-term memory", "long term memory"]),
    ("科学发现", ["scientific discovery"]),
    ("科学研究", ["scientific research"]),
    ("图神经网络", ["graph neural network", "graph neural networks"]),
    ("联邦学习", ["federated learning"]),
    ("对比学习", ["contrastive learning"]),
    ("扩散模型", ["diffusion model", "diffusion models"]),
    ("测试时适应", ["test-time adaptation"]),
    ("语音识别", ["speech recognition"]),
    ("目标检测", ["object detection"]),
    ("语义分割", ["semantic segmentation"]),
    ("时间序列", ["time series", "time-series"]),
]
ALIASES = {
    name_key(v): canonical
    for canonical, variants in CONCEPTS
    for v in [canonical, *variants]
}
STOP = set(
    "a an the and or of to in on for from by with without at as is are was were be been being this that these those it its we our us they their you your can could may might will would should must do does did have has had not no but if then than through into over under between across during using used use based proposed propose present presents paper papers study studies research method methods approach approaches framework frameworks model models result results show shows shown new novel existing different also such more most many much each which where when what how who both all other provides provide achieve achieves achieved improve improved improves outperform outperforms demonstrate demonstrates compared demonstrate evaluation evaluate evaluated effective efficient comprehensive various including specifically introduce introduces introduced designed enables enable employing within towards via against whether further significant significantly work works system systems task tasks performance experimental experiments experiment extensive key main first second third several address addresses investigating investigate investigation state art problem problems challenges challenge".split()
)
STOP_ZH = set(
    "本文 论文 研究 方法 结果 模型 实验 系统 提出 我们 基于 通过 使用 实现 一个 一种 进行 可以 有效 显著 性能 提升 任务 问题 框架 以及 对于 相关 不同 现有 工作 分析 表明 验证 具有 能够 因此 其中".split()
)
# Keep nouns inside phrases (e.g. scientific research, world models). Generic
# standalone words are rejected separately; removing them before phrase assembly
# would destroy precisely the technical expressions we want to retain.
PHRASE_NOUNS = set(
    "research method methods model models system systems task tasks performance experimental experiments experiment evaluation framework frameworks learning policy policies".split()
)
STOP_BOUNDARY = (STOP - PHRASE_NOUNS) | set(
    "because since while although however therefore thus whose whom thereof herein thereby rather instead even already yet still often typically particularly mainly primarily generally recently previously now well successfully successfully enables enabling enabled allowing allow allows allowed leveraging leverage leverages leveraged alleviating detecting evaluating learning promoting balancing improving optimizing optimized introduce introducing emerging achieves achieving optimizing combining combined combine combines unlike beyond toward along about upon out up down only just full complete comprehensive extensive novel new diverse varied robust".split()
)
STOP_BOUNDARY |= set(
    "own itself themselves ourselves respectively lack lacks guide guides guiding reduce reduces reducing reduced increase increases increasing support supports supporting capture captures capturing help helps helping makes make making allows allow allowing discover discovering discover discovers explores explore exploring identifies identify identifying offers offer offering producing produces produce treated treat treats returns return returning designs design demonstrates demonstrated demonstrating proposes proposing existing modern important essential promising high low entire human designed large scale wide broad better best strong capable necessary need needs needed require requires requiring required remains remain remaining open-ended appropriate novel general substantial significant wide-ranging".split()
)
STOP_BOUNDARY.discard("learning")
STOP_BOUNDARY.update(
    {
        "surpass",
        "surpasses",
        "baselines",
        "baseline",
        "expert",
        "experts",
        "matches",
        "surpassed",
    }
)
STOP_ZH.update({"文献", "合成", "验证", "探讨", "介绍", "综述"})
ZH_BOUNDARY = set(
    "和 与 或 及 而 但 并 且 因为 所以 如果 虽然 例如 然而 就 了 的 地 得 将 把 对 从 中 上 下 会 使 用 为 以".split()
)
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[+#]+|(?:[-.'][A-Za-z0-9]+)*)")
SENTENCES = re.compile(r"[\n.!?。！？;；:：]+")


def normalize_phrase(value):
    value = label(value, 48)
    return ALIASES.get(name_key(value), value)


def extract(source, check=lambda: None):
    start = time.monotonic()
    candidates = {}
    frequency = Counter()
    words = Counter()
    degree = Counter()
    title_phrases = set()
    author_names = {str(x).casefold() for x in source.get("excludedNames", []) if x}
    sections = source.get("sections", [])
    total_chars = 0
    scanned = 0
    truncated = False
    zh = None

    def add(value, weight, section, quote, explicit=False):
        original = value.strip() if isinstance(value, str) else ""
        try:
            value = label(value, 48)
        except KeywordError:
            return
        k = name_key(value)
        if (
            k in author_names
            or k in STOP
            or value in STOP_ZH
            or len(value) < 2
            or value.isnumeric()
        ):
            return
        if len(k) > 3 and any(
            re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", n) for n in author_names
        ):
            return
        tokens = TOKEN.findall(value)
        if tokens and not re.search(r"[\u3400-\u9fff]", value):
            if (
                tokens[0].casefold() in STOP_BOUNDARY
                or tokens[-1].casefold() in STOP_BOUNDARY
            ):
                return
            if len(tokens) == 1 and (len(value) < 4 and not value.isupper()):
                return
        if len(candidates) >= 12000 and k not in candidates:
            return
        at = max(0, quote.casefold().find(original.casefold()) - 60)
        rec = candidates.setdefault(
            k,
            {
                "name": value,
                "weight": 0.0,
                "section": section,
                "quote": quote[at : at + 240],
                "explicit": False,
            },
        )
        rec["weight"] += weight
        rec["explicit"] |= explicit
        frequency[k] += 1
        for word in tokens:
            word = word.casefold()
            words[word] += 1
            degree[word] += len(tokens)

    for term in source.get("terms", []):
        add(
            term["text"],
            12 if term["source"] == "source" else 4,
            "keywords",
            term["text"],
            True,
        )
    for section in sections:
        check()
        if time.monotonic() - start > 15 or total_chars >= 200000:
            truncated = True
            break
        text = section["text"][: 200000 - total_chars]
        total_chars += len(text)
        scanned += 1
        weight = (
            4.0
            if section["kind"] == "title"
            else 2.0 if section["kind"] == "abstract" else 0.6
        )
        for sentence in SENTENCES.split(text):
            check()
            if time.monotonic() - start > 15:
                truncated = True
                break
            if not sentence.strip():
                continue
            if len(sentence) > 4000:
                sentence = sentence[:4000]
                truncated = True
            if re.search(r"[\u3400-\u9fff]", sentence):
                if zh is None:
                    import jieba
                    import jieba.posseg

                    jieba.setLogLevel(40)
                    tokenizer = jieba.Tokenizer()
                    # No caller-supplied dictionaries/cache paths or runtime downloads.
                    zh = jieba.posseg.POSTokenizer(tokenizer)
                runs = []
                run = []
                for word, flag in zh.cut(sentence, HMM=True):
                    if (
                        word in STOP_ZH
                        or word in ZH_BOUNDARY
                        or flag in {"nr", "ns", "nt", "w", "r", "p", "x"}
                        or flag.startswith("u")
                        or (flag == "m" and word not in {"多", "双", "单"})
                    ):
                        if run:
                            runs.append(run)
                            run = []
                    elif word.strip():
                        run.append(word.strip())
                if run:
                    runs.append(run)
                for run in runs:
                    for length in range(1, min(3, len(run)) + 1):
                        for i in range(len(run) - length + 1):
                            add(
                                "".join(run[i : i + length]),
                                weight,
                                section["kind"],
                                sentence,
                            )
            # Preserve original spans, including technical hyphens and punctuation.
            matches = list(TOKEN.finditer(sentence))
            runs = []
            run = []
            for match in matches:
                token = match.group()
                gap = sentence[run[-1].end() : match.start()] if run else ""
                if token.casefold() in STOP_BOUNDARY or (
                    run and (re.search(r"[^\s-]", gap) or len(gap) > 5)
                ):
                    if run:
                        runs.append(run)
                        run = []
                if token.casefold() not in STOP_BOUNDARY:
                    run.append(match)
            if run:
                runs.append(run)
            for run in runs:
                if section["kind"] == "title" and 2 <= len(run) <= 4:
                    title_phrases.add(
                        name_key(sentence[run[0].start() : run[-1].end()])
                    )
                for length in range(1, min(4, len(run)) + 1):
                    for i in range(len(run) - length + 1):
                        value = sentence[run[i].start() : run[i + length - 1].end()]
                        # Longer phrases need repeated evidence, title prominence,
                        # or an explicit source keyword to beat their shorter core.
                        add(value, weight, section["kind"], sentence)
        if scanned % 4 == 0:
            check()
    # Precompute proper phrase extensions in linear time, rather than comparing
    # every candidate with the entire candidate set on long papers.
    extension_frequency = Counter()
    for key in candidates:
        parts = key.split(" ")
        for length in range(1, len(parts)):
            for fragment in (" ".join(parts[:length]), " ".join(parts[-length:])):
                extension_frequency[fragment] = max(
                    extension_frequency[fragment], frequency[key]
                )
    ranked = []
    for key, r in candidates.items():
        toks = [x.casefold() for x in TOKEN.findall(r["name"])]
        chinese = bool(re.search(r"[\u3400-\u9fff]", r["name"]))
        if (
            not r["explicit"]
            and r["section"] != "title"
            and frequency[key] < 2
            and (
                (len(toks) == 1 and key not in ALIASES)
                or (chinese and len(r["name"]) < 4)
            )
        ):
            continue
        score = r["weight"] * (1 + math.log1p(frequency[key]))
        if toks:
            score *= min(
                2.4, sum(degree[t] / max(1, words[t]) for t in toks) / len(toks)
            )
        length = len(toks) if toks else max(1, len(r["name"]) / 3)
        score *= min(1.8, 0.75 + length * 0.3)
        if length > 3 and frequency[key] == 1 and not r["explicit"]:
            score *= 0.55
        if len(toks) == 1 and not chinese and not r["explicit"]:
            acronym = bool(re.fullmatch(r"[A-Z]{2,}[sS]?", r["name"]))
            score *= 0.45 if acronym else 0.12
        if chinese and len(r["name"]) == 2 and not r["explicit"] and key not in ALIASES:
            score *= 0.45
        if len(toks) >= 2 and not r["explicit"]:
            # Penalize partial windows cut out of longer, one-off noun chains.
            if extension_frequency[key] >= frequency[key]:
                score *= 0.7
        if key in ALIASES:
            score *= 1.4
        if key in title_phrases:
            score *= 1.6
        ranked.append((score, key, r))
    chosen = []
    keys = set()
    original_keys = set()
    ranked.sort(key=lambda x: (-x[0], x[1]))
    floor = ranked[0][0] * 0.4 if ranked else 0
    for score, key, r in ranked:
        threshold = floor * (0.4 if key in title_phrases or key in ALIASES else 1)
        if score < threshold and not r["explicit"]:
            continue
        canonical = normalize_phrase(r["name"])
        canonkey = name_key(canonical)
        if canonkey in keys:
            continue
        # Avoid adjacent fragments of the same phrase filling all eight slots.
        words_here = set(TOKEN.findall(key))
        if any(
            (key in k or k in key)
            and min(len(key), len(k))
            >= (2 if re.search(r"[\u3400-\u9fff]", key + k) else 4)
            or (
                words_here
                and len(words_here & set(TOKEN.findall(k)))
                / max(1, min(len(words_here), len(set(TOKEN.findall(k)))))
                >= 0.5
            )
            for k in original_keys
        ):
            continue
        chosen.append(
            {
                "name": canonical,
                "original": r["name"],
                "section": r["section"],
                "quote": r["quote"],
            }
        )
        keys.add(canonkey)
        original_keys.add(key)
        if len(chosen) == 8:
            break
    return chosen, {
        "sectionsScanned": scanned,
        "charactersScanned": total_chars,
        "truncated": truncated,
        "candidateCount": len(candidates),
    }
