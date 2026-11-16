// Match verbatim evidence while ignoring PDF layout and typographic differences.
// Keep offsets into the original spans so highlights wrap the actual PDF text.
function normalizeWithMap(value) {
  let text = '';
  const map = [];
  const ends = [];
  let offset = 0;
  for (const character of String(value || '')) {
    const normalized = character.normalize('NFKC')
      .replace(/[‘’]/g, "'").replace(/[“”]/g, '"')
      .replace(/[\s\u00ad\u200b\ufeff\-‐‑]/g, '');
    for (let i = 0; i < normalized.length; i += 1) {
      text += normalized[i];
      map.push(offset);
      ends.push(offset + character.length);
    }
    offset += character.length;
  }
  return { text, map, ends };
}

function mergeRanges(ranges) {
  return ranges.sort((a, b) => a[0] - b[0]).reduce((merged, range) => {
    const previous = merged.at(-1);
    if (previous && range[0] <= previous[1]) previous[1] = Math.max(previous[1], range[1]);
    else merged.push([...range]);
    return merged;
  }, []);
}

function findEvidenceRanges(texts, quotes) {
  let offset = 0;
  const records = texts.map(value => {
    const normalized = normalizeWithMap(value);
    const record = { ...normalized, start: offset, end: offset + normalized.text.length };
    offset = record.end;
    return record;
  });
  const normalizedPage = records.map(record => record.text).join('');
  const ranges = texts.map(() => []);
  let matchedQuotes = 0;
  for (const quote of quotes) {
    // Models sometimes add quote delimiters or explicit omissions. Only match
    // the supplied fragments, in order; never highlight the omitted passage.
    let value = String(quote || '').trim();
    if (/^(?:“[\s\S]*”|"[\s\S]*"|‘[\s\S]*’|'[\s\S]*')$/.test(value)) value = value.slice(1, -1);
    const fragments = value.split(/…|\.{3}/).map(part => normalizeWithMap(part).text);
    const matches = [];
    let cursor = 0;
    for (const normalizedQuote of fragments) {
      const start = normalizedPage.indexOf(normalizedQuote, cursor);
      if (!normalizedQuote || start < 0) break;
      cursor = start + normalizedQuote.length;
      matches.push([start, cursor]);
    }
    if (matches.length !== fragments.length) continue;
    matchedQuotes += 1;
    for (const [start, end] of matches) {
      records.forEach((record, index) => {
        const localStart = Math.max(start, record.start) - record.start;
        const localEnd = Math.min(end, record.end) - record.start;
        if (localStart < localEnd) ranges[index].push([record.map[localStart], record.ends[localEnd - 1]]);
      });
    }
  }
  return { ranges: ranges.map(mergeRanges), matchedQuotes };
}

export { findEvidenceRanges };
