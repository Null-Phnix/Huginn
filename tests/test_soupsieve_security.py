"""Bounded regressions for CVE-2026-85999 and CVE-2026-86000."""

import subprocess
import sys

import pytest

_SELECTOR_PROBE = r'''
import sys
from soupsieve import SelectorSyntaxError
from huginn.models import OutputFormat
from huginn.starsearch_scrape import _html_to_scrapedata

kind = sys.argv[1]
html = '<main><section class="keep"><span data-code="one two">kept</span><p>other</p></section></main>'
selector = {
    'control': '.keep > span[data-code="one two"]',
    'value': '[a=' + 'a' * 100000,
    'identifier': 'a' * 100000 + '!',
    'whitespace': 'section' + ' ' * 100000 + 'span',
    'comments': 'section ' + '/*x*/' * 20000 + ' span',
}[kind]

for option in ('include_tags', 'exclude_tags'):
    try:
        data = _html_to_scrapedata(
            html, 'https://example.test', [OutputFormat.HTML], True,
            **{option: [selector]},
        )
    except SelectorSyntaxError:
        assert kind in ('identifier', 'value'), 'A valid selector was rejected'
    else:
        assert kind not in ('identifier', 'value'), 'A malformed selector was accepted'
        if option == 'include_tags':
            assert 'kept' in data.html and 'other' not in data.html
        else:
            assert 'kept' not in data.html and 'other' in data.html
'''


@pytest.mark.parametrize("kind", ["control", "identifier", "value", "whitespace", "comments"])
def test_html_selector_processing_is_bounded(kind):
    # A subprocess can be killed even while the regex engine holds the GIL.
    # Ten seconds allows cold imports on CI; patched parsing is normally fast.
    result = subprocess.run(
        [sys.executable, "-c", _SELECTOR_PROBE, kind],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
