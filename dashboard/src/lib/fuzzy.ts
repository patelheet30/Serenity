/**
 * Palette ranking from the Calm prototype: every query token must appear in the text, either as a
 * substring (scores more at a word start) or as an in-order subsequence. 0 means no match.
 */
export function score(query: string, text: string): number {
	if (!query) return 1;
	const tl = text.toLowerCase();
	let s = 0;
	for (const tok of query.toLowerCase().split(/\s+/).filter(Boolean)) {
		const idx = tl.indexOf(tok);
		if (idx >= 0) {
			s += 10 + (idx === 0 || /[\s#@]/.test(tl[idx - 1]) ? 6 : 0) + tok.length;
			continue;
		}
		let j = 0;
		for (const ch of tl) {
			if (ch === tok[j]) j++;
			if (j === tok.length) break;
		}
		if (j === tok.length) s += 2;
		else return 0;
	}
	return s - text.length * 0.02;
}
