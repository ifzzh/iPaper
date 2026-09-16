"""Generic direction development cases. Independent acceptance is separate."""

from ipaper.topics.definitions import classify, proposed_directions
import pytest


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Temporal action segmentation and boundary detection", "action-understanding"),
        ("机器人轨迹分割与运动学表示", "action-understanding"),
        ("Autonomous scientific discovery with agentic planning", "agents"),
        ("Compiler optimization using distributed search", "systems"),
        ("Microservice placement to reduce latency", "systems"),
        ("Point cloud reconstruction with image segmentation", "vision"),
        ("语言模型的预训练和对齐", "language"),
        ("Privacy protection in federated learning", "security"),
        ("Protein design and drug discovery", "health"),
    ],
)
def test_supported_directions(text, expected):
    assert expected in classify([{"kind": "title", "text": text}])


@pytest.mark.parametrize(
    "text",
    [
        "Cloud patterns over the mountain",
        "Memory and childhood recollection",
        "Chemical reagent design",
        "A new method",
        "",
        "References: memory cloud action agent",
    ],
)
def test_isolated_ambiguous_words_do_not_force_a_topic(text):
    assert classify([{"text": text}]) == []


def test_bootstrap_requires_two_papers_for_subdivision():
    paper = [("one", [{"text": "Temporal action segmentation"}])]
    assert [d.id for d in proposed_directions(paper)] == ["embodied"]
    paper.append(("two", [{"text": "机器人轨迹分割"}]))
    assert {d.id for d in proposed_directions(paper)} == {
        "embodied",
        "action-understanding",
    }
    assert proposed_directions([]) == []
