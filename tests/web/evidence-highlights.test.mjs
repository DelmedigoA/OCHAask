import assert from 'node:assert/strict';
import test from 'node:test';
import { findEvidenceRanges } from '../../src/ochask/web/static/evidence-highlights.js';

test('matches evidence across PDF fragments, typography, and line hyphenation', () => {
  const result = findEvidenceRanges(['The doc', 'tors’ assess-', 'ment was ', '39', '% with ﬁve cases.'], ["The doctors' assessment was 39% with five cases."]);
  assert.equal(result.matchedQuotes, 1);
  assert.deepEqual(result.ranges[1], [[0, 12]]);
  assert.deepEqual(result.ranges[4], [[0, 17]]);
});

test('maps ligature expansion back to the original character', () => {
  assert.deepEqual(findEvidenceRanges(['A ﬁnding here'], ['finding']).ranges, [[[2, 8]]]);
});

test('does not highlight paraphrases, missing anchors, or empty quotes', () => {
  assert.equal(findEvidenceRanges(['Doctors treated patients.'], ['Doctors refused patients.']).matchedQuotes, 0);
  assert.deepEqual(findEvidenceRanges(['Doctors treated patients.'], []).ranges, [[]]);
  assert.equal(findEvidenceRanges(['Doctors treated patients.'], ['  ']).matchedQuotes, 0);
});

test('merges overlapping evidence into one range', () => {
  assert.deepEqual(findEvidenceRanges(['One supporting sentence.'], ['supporting sentence', 'One supporting']).ranges, [[[0, 23]]]);
});

test('quotation marks added around an evidence anchor do not prevent a match', () => {
  assert.equal(findEvidenceRanges(['The ratio increased in May to 39%.'], ['“The ratio increased in May to 39%”']).matchedQuotes, 1);
});

test('ellipsis anchors highlight only exact fragments in their original order', () => {
  const texts = ['The report says that five people left before noon.'];
  const result = findEvidenceRanges(texts, ['“The report says … five people left”']);
  assert.equal(result.matchedQuotes, 1);
  assert.deepEqual(result.ranges, [[[0, 15], [21, 37]]]);
  assert.equal(findEvidenceRanges(texts, ['five people left … The report says']).matchedQuotes, 0);
  assert.equal(findEvidenceRanges(texts, ['The report says … invented words']).matchedQuotes, 0);
});
