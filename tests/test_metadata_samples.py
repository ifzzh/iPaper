import json
from pathlib import Path
import pytest
from ipaper.metadata.providers import clues, match

SAMPLES=json.loads((Path(__file__).parent/'fixtures/metadata/samples.json').read_text())

@pytest.mark.parametrize('sample',SAMPLES,ids=[s['name'] for s in SAMPLES])
def test_identification_ground_truth(sample):
    clue=clues(sample['fields'],{'first_page_text':sample['header']})
    assert [match(record,clue) for record in sample['records']]==sample['expected']
