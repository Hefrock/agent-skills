// Paragraph-aware chunking of warehouse document text into ~1500-code-point
// passages with ~200-code-point overlap, reporting offsets in Unicode CODE
// POINTS (not UTF-16 code units, which is what JS string indices/.length/
// .slice() natively use, and not bytes). This distinction is not
// hypothetical: a non-BMP character (an emoji) is confirmed present in at
// least one already-ingested warehouse document's extracted text (issue
// recon, Tier 0), and Python's char_count (the manifest field this is meant
// to agree with) is computed in code points. Getting this conversion wrong
// would silently misalign every char_start/char_end after the first non-BMP
// character in a document — exactly the kind of bug this module exists to
// make impossible by construction rather than by careful callers.
//
// Approach: do all boundary-finding (paragraphs, form feeds, whitespace) on
// the raw string using ordinary UTF-16 string operations — safe here because
// every delimiter involved (\n, \f, space) is itself a single BMP character,
// so UTF-16-vs-code-point never matters for FINDING a boundary, only for
// REPORTING its position. A single offset-map pass converts between the two
// only at the point offsets are read out or a caller-supplied offset is
// resolved back to a UTF-16 slice.

export interface OffsetMaps {
  // cpToU16[i] = the UTF-16 offset of the i-th code point; cpToU16[N] (N =
  // total code point count) = text.length, a sentinel for "end of text".
  cpToU16: number[];
  // u16ToCp[u] = the code-point index that UTF-16 offset u falls within
  // (both halves of a surrogate pair map to the same code-point index, so a
  // caller can never slice INTO a surrogate pair); u16ToCp[text.length] = N.
  u16ToCp: Int32Array;
}

export function buildOffsetMaps(text: string): OffsetMaps {
  const cpToU16: number[] = [];
  const u16ToCp = new Int32Array(text.length + 1);
  let cp = 0;
  let i = 0;
  while (i < text.length) {
    cpToU16.push(i);
    const codePoint = text.codePointAt(i)!;
    const width = codePoint > 0xffff ? 2 : 1;
    u16ToCp[i] = cp;
    if (width === 2) u16ToCp[i + 1] = cp;
    i += width;
    cp += 1;
  }
  cpToU16.push(text.length);
  u16ToCp[text.length] = cp;
  return { cpToU16, u16ToCp };
}

export const CHUNK_TARGET = 1500; // code points
export const CHUNK_OVERLAP = 200; // code points

export interface Chunk {
  text: string;
  charStart: number; // code points
  charEnd: number; // code points
}

interface Paragraph {
  u16Start: number;
  u16End: number;
  hardBreakAfter: boolean;
}

// Splits on runs of blank lines. When `formFeedIsHardBoundary` is true (only
// for extraction_method text-layer:pdftotext — confirmed to emit real \f
// page separators), a \f is ALSO a boundary, and the paragraph ending there
// is marked so packIntoChunks never merges across it or overlaps into it.
// For every other extraction method, \f is left embedded as an ordinary
// character — the spec is explicit that other extractors don't reliably
// contain it, so it isn't safe to treat it as meaningful there.
function splitParagraphs(text: string, formFeedIsHardBoundary: boolean): Paragraph[] {
  const breakRe = /\n[ \t]*\n+|\f/g;
  const paragraphs: Paragraph[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = breakRe.exec(text))) {
    const isFormFeed = m[0] === "\f";
    if (isFormFeed && !formFeedIsHardBoundary) continue; // not a boundary here; leave it embedded
    if (m.index > last) {
      paragraphs.push({ u16Start: last, u16End: m.index, hardBreakAfter: isFormFeed });
    } else if (isFormFeed && paragraphs.length > 0) {
      // Form feed immediately adjacent to an already-found boundary (e.g. a
      // blank line right before it) — upgrade the previous paragraph's break
      // to hard rather than emit a zero-length paragraph.
      paragraphs[paragraphs.length - 1].hardBreakAfter = true;
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) paragraphs.push({ u16Start: last, u16End: text.length, hardBreakAfter: false });
  return paragraphs;
}

// A single paragraph longer than CHUNK_TARGET on its own: cut at whitespace
// nearest the target size, with CHUNK_OVERLAP-worth of trailing overlap
// carried into the next piece, same as the paragraph-level packer.
function hardSplitAtWhitespace(text: string, u16Start: number, u16End: number, maps: OffsetMaps): Chunk[] {
  const chunks: Chunk[] = [];
  let curStart = u16Start;
  while (maps.u16ToCp[u16End] - maps.u16ToCp[curStart] > CHUNK_TARGET) {
    const targetCp = maps.u16ToCp[curStart] + CHUNK_TARGET;
    const targetU16 = maps.cpToU16[targetCp];
    let splitAt = targetU16;
    while (splitAt > curStart + 1 && !/\s/.test(text[splitAt - 1])) splitAt--;
    if (splitAt <= curStart) splitAt = targetU16; // no whitespace in range; hard-cut mid-word rather than loop
    chunks.push({ text: text.slice(curStart, splitAt), charStart: maps.u16ToCp[curStart], charEnd: maps.u16ToCp[splitAt] });
    const overlapCp = Math.max(maps.u16ToCp[curStart], maps.u16ToCp[splitAt] - CHUNK_OVERLAP);
    const nextStart = maps.cpToU16[overlapCp];
    curStart = nextStart > curStart ? nextStart : splitAt; // guarantee forward progress
  }
  if (curStart < u16End) {
    chunks.push({ text: text.slice(curStart, u16End), charStart: maps.u16ToCp[curStart], charEnd: maps.u16ToCp[u16End] });
  }
  return chunks;
}

function packIntoChunks(text: string, paragraphs: Paragraph[], maps: OffsetMaps): Chunk[] {
  const chunks: Chunk[] = [];
  const cpLen = (a: number, b: number) => maps.u16ToCp[b] - maps.u16ToCp[a];

  let i = 0;
  while (i < paragraphs.length) {
    if (cpLen(paragraphs[i].u16Start, paragraphs[i].u16End) > CHUNK_TARGET) {
      chunks.push(...hardSplitAtWhitespace(text, paragraphs[i].u16Start, paragraphs[i].u16End, maps));
      i++;
      continue;
    }

    let j = i;
    while (
      j + 1 < paragraphs.length &&
      !paragraphs[j].hardBreakAfter &&
      cpLen(paragraphs[j + 1].u16Start, paragraphs[j + 1].u16End) <= CHUNK_TARGET &&
      cpLen(paragraphs[i].u16Start, paragraphs[j + 1].u16End) <= CHUNK_TARGET
    ) {
      j++;
    }

    const chunkStart = paragraphs[i].u16Start;
    const chunkEnd = paragraphs[j].u16End;
    chunks.push({ text: text.slice(chunkStart, chunkEnd), charStart: maps.u16ToCp[chunkStart], charEnd: maps.u16ToCp[chunkEnd] });

    if (paragraphs[j].hardBreakAfter || j === paragraphs.length - 1) {
      i = j + 1;
      continue;
    }

    // Overlap: walk backward from j while the tail [k-1.start, chunkEnd] is
    // still within CHUNK_OVERLAP code points, giving the earliest whole
    // paragraph the next chunk can resume from. Never resume before i, and
    // always resume strictly after i (i + 1 at minimum) so the loop is
    // guaranteed to terminate even when the whole chunk is <= CHUNK_OVERLAP
    // long (otherwise the backward scan could resolve to i itself and
    // reproduce the same chunk forever).
    let k = j;
    while (k > i && !paragraphs[k - 1].hardBreakAfter && cpLen(paragraphs[k - 1].u16Start, chunkEnd) <= CHUNK_OVERLAP) {
      k--;
    }
    i = Math.max(k, i + 1);
  }
  return chunks;
}

export function chunkText(text: string, extractionMethod: string): Chunk[] {
  if (text.length === 0) return [];
  const maps = buildOffsetMaps(text);
  const formFeedIsHardBoundary = extractionMethod === "text-layer:pdftotext";
  const paragraphs = splitParagraphs(text, formFeedIsHardBoundary);
  if (paragraphs.length === 0) return [];
  return packIntoChunks(text, paragraphs, maps);
}
