import assert from 'node:assert/strict';
import test from 'node:test';
import { renderMarkdown, shortReportTitle } from '../../src/ochask/web/static/formatting.js';

test('renders headings, lists, emphasis and safe links', () => {
  const result = renderMarkdown('# Evidence\n\n- **Important**\n- [Source](https://example.com)');
  assert.match(result, /<h1>Evidence<\/h1>/);
  assert.match(result, /<ul>\s*<li><strong>Important<\/strong><\/li>/);
  assert.match(result, /href="https:\/\/example\.com\/"/);
});

test('escapes untrusted HTML and does not create javascript links', () => {
  const result = renderMarkdown('<img src=x onerror=alert(1)> [bad](javascript:alert)');
  assert.ok(!result.includes('<img'));
  assert.ok(!result.includes('href="javascript:'));
});

test('shortens labels without language tags', () => {
  assert.equal(shortReportTitle('Humanitarian Situation Update #300 | Gaza Strip [EN/AR/HE]'), 'Situation Update #300');
  assert.equal(shortReportTitle('Hostilities | Flash Update #61 [EN/AR/HE]'), 'Flash Update #61');
  assert.equal(shortReportTitle('Gaza Humanitarian Response | Situation Report No. 36'), 'Situation Report #36');
});


test('renders tables and fenced code blocks', () => {
  const result = renderMarkdown('| Report | Page |\n|---|---|\n| Test | 5 |\n\n```python\nprint(1)\n```');
  assert.match(result, /<table>/);
  assert.match(result, /<pre><code class="language-python">/);
});
