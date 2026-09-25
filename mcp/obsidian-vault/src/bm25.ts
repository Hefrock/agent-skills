// Hand-rolled BM25F-style scoring. No search library — this is small enough
// (~100 lines) to own outright and keep the repo's zero-extra-runtime-deps rule.
//
// This is an APPROXIMATION of textbook BM25F (Robertson & Zaragoza), not the
// literal formula from the paper. What it does: per-field term frequency is
// length-normalized per field (using that field's own average length across
// the index), then combined into one weighted pseudo-frequency per term
// BEFORE a single k1 saturation and a single global IDF are applied. What it
// does NOT do: the paper's more general per-stream k1/b tuning, or any
// treatment of field-specific term statistics beyond length normalization.
// Called out explicitly because an imprecise BM25F implementation produces
// rankings that are subtly wrong in ways easy to miss without deliberately
// testing documents of very different lengths and field distributions — see
// the length-normalization and field-weight tests in test/server.test.mjs.

export const K1 = 1.2;
export const B = 0.75;

export type FieldWeights = Record<string, number>;
export type FieldTokens = Record<string, string[]>;

// Single tokenizer used for BOTH indexing (document fields) and querying, so
// a term is identified the same way on both sides of the match. Lowercase +
// NFKC normalize (so visually-identical characters with different code
// points, e.g. combining vs. precomposed accents, tokenize the same way),
// then split on runs of Unicode letters/numbers. No stemming, no stopwords —
// deliberately out of scope for this phase.
export function tokenize(text: string): string[] {
  return text.normalize("NFKC").toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
}

interface DocEntry {
  fieldLengths: Record<string, number>;
  termFreqs: Record<string, Map<string, number>>;
  vocab: Set<string>; // union of terms across all fields, for df bookkeeping
}

// A live, incrementally-updatable BM25F index over documents with named,
// weighted fields (e.g. { title: 5, tags: 3, body: 1 } for vault notes, or
// { title: 3, body: 1 } for warehouse passages). IDF and average field
// lengths are always computed over the WHOLE index — callers that want to
// restrict which documents are considered (e.g. a folder-scoped vault search)
// filter which ids they score, not the index's own statistics, so scores
// stay comparable between a scoped and unscoped query.
export class BM25Index {
  private weights: FieldWeights;
  private docs = new Map<string, DocEntry>();
  private df = new Map<string, number>();
  private fieldLenSums: Record<string, number> = {};

  constructor(weights: FieldWeights) {
    this.weights = weights;
    for (const field of Object.keys(weights)) this.fieldLenSums[field] = 0;
  }

  get size(): number {
    return this.docs.size;
  }

  ids(): IterableIterator<string> {
    return this.docs.keys();
  }

  has(id: string): boolean {
    return this.docs.has(id);
  }

  // Insert or replace a document's fields wholesale. Safe to call for an id
  // already in the index (removes the stale entry's df/length contribution
  // first) or a brand-new one.
  upsert(id: string, fields: FieldTokens): void {
    this.remove(id);
    const fieldLengths: Record<string, number> = {};
    const termFreqs: Record<string, Map<string, number>> = {};
    const vocab = new Set<string>();
    for (const field of Object.keys(this.weights)) {
      const tokens = fields[field] ?? [];
      fieldLengths[field] = tokens.length;
      const tf = new Map<string, number>();
      for (const t of tokens) {
        tf.set(t, (tf.get(t) ?? 0) + 1);
        vocab.add(t);
      }
      termFreqs[field] = tf;
      this.fieldLenSums[field] = (this.fieldLenSums[field] ?? 0) + tokens.length;
    }
    for (const term of vocab) this.df.set(term, (this.df.get(term) ?? 0) + 1);
    this.docs.set(id, { fieldLengths, termFreqs, vocab });
  }

  // Remove a document. A no-op if the id isn't indexed — callers invalidate
  // unconditionally on writes/deletes without checking whether a search ever
  // actually indexed the path first.
  remove(id: string): void {
    const entry = this.docs.get(id);
    if (!entry) return;
    for (const term of entry.vocab) {
      const c = this.df.get(term) ?? 0;
      if (c <= 1) this.df.delete(term);
      else this.df.set(term, c - 1);
    }
    for (const field of Object.keys(this.weights)) {
      this.fieldLenSums[field] = (this.fieldLenSums[field] ?? 0) - (entry.fieldLengths[field] ?? 0);
    }
    this.docs.delete(id);
  }

  private avgFieldLength(field: string): number {
    return this.docs.size > 0 ? (this.fieldLenSums[field] ?? 0) / this.docs.size : 0;
  }

  // Always non-negative: N - df + 0.5 >= 0.5 since df <= N, and df + 0.5 > 0,
  // so the ratio inside ln(1 + …) is always > 0.
  private idf(term: string): number {
    const df = this.df.get(term) ?? 0;
    const N = this.docs.size;
    return Math.log(1 + (N - df + 0.5) / (df + 0.5));
  }

  // Score one document against already-tokenized query terms. 0 if the id
  // isn't indexed or no term matches any field.
  score(id: string, queryTerms: string[]): number {
    const entry = this.docs.get(id);
    if (!entry) return 0;
    let score = 0;
    for (const term of queryTerms) {
      let weightedTf = 0;
      for (const field of Object.keys(this.weights)) {
        const tf = entry.termFreqs[field]?.get(term) ?? 0;
        if (tf === 0) continue;
        const avgLen = this.avgFieldLength(field);
        const norm = avgLen > 0 ? 1 - B + B * (entry.fieldLengths[field] / avgLen) : 1;
        weightedTf += this.weights[field] * (tf / norm);
      }
      if (weightedTf === 0) continue;
      score += this.idf(term) * (weightedTf / (K1 + weightedTf));
    }
    return score;
  }
}
