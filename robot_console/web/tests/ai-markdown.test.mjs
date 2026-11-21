import assert from 'node:assert/strict'
import test from 'node:test'
import { loadSource } from './load-source.mjs'

// The AI assistant renders model answers with v-html: everything must be escaped except the tags it makes itself
const { renderMarkdown } = loadSource('../../plugins/ai_assistant/web/src/markdown.ts')

test('markdown answers are escaped and only safe tags are produced', () => {
  const html = renderMarkdown('<img src=x onerror=alert(1)> **bold** `<b>` [ok](https://example.com/a?b=1&c=2) [bad](javascript:alert(1))')
  assert.ok(!html.includes('<img'))
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'))
  assert.ok(html.includes('<strong>bold</strong>'))
  assert.ok(html.includes('<code>&lt;b&gt;</code>'))
  assert.ok(html.includes('<a href="https://example.com/a?b=1&amp;c=2" target="_blank" rel="noopener noreferrer">ok</a>'))
  assert.ok(!html.includes('href="javascript'))
})

test('tables, lists, headings and code blocks', () => {
  const html = renderMarkdown([
    '## Result', '', '| Run | ATE |', '|---|---:|', '| a | **0.031 m** |', '| b | 0.035 m |', '',
    '- one', '- two', '', '1. first', '2. second', '', '```', 'x < y', '```', 'line one', 'line two',
  ].join('\n'))
  assert.match(html, /<h4>Result<\/h4>/)
  assert.match(html, /<table><thead><tr><th>Run<\/th><th>ATE<\/th><\/tr><\/thead><tbody><tr><td>a<\/td><td><strong>0.031 m<\/strong><\/td><\/tr>/)
  assert.match(html, /<ul><li>one<\/li><li>two<\/li><\/ul><ol><li>first<\/li><li>second<\/li><\/ol>/)
  assert.match(html, /<pre><code>x &lt; y<\/code><\/pre>/)
  assert.match(html, /<p>line one<br>line two<\/p>$/)
})
