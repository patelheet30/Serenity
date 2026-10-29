/** Support hours as the API keeps them: seven days (Monday first) of [start, end] minutes. */
export type Schedule = number[][][];
/** The weekly grid's editable form: "HH:MM" pairs, where an end of "00:00" is midnight. */
export type Grid = [string, string][][];

export const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

const hm = (m: number) => `${String(Math.floor(m / 60) % 24).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;

export const toGrid = (s: Schedule | null): Grid =>
	DAYS.map((_, d) => (s?.[d] ?? []).map(([a, b]) => [hm(a), hm(b)] as [string, string]));

/** The text /ticket settings hours takes ("mon 09:00-17:00; sat 10:00-14:00"); '' for no hours.
 * The bot parses and checks it, so a bad range comes back as its own error. */
export const fromGrid = (g: Grid): string =>
	g
		.map((ranges, d) =>
			ranges.length ? `${DAYS[d].toLowerCase()} ${ranges.map(([a, b]) => `${a}-${b === '00:00' ? '24:00' : b}`).join(' ')}` : ''
		)
		.filter(Boolean)
		.join('; ');

export const scheduleText = (s: Schedule | null) => fromGrid(toGrid(s));

const esc = (t: string) => t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function relative(ts: number, now: number): string {
	const s = ts - now / 1000;
	const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });
	for (const [unit, size] of [['day', 86400], ['hour', 3600], ['minute', 60]] as const)
		if (Math.abs(s) >= size) return rtf.format(Math.round(s / size), unit);
	return rtf.format(Math.round(s), 'second');
}

/** A bot message as Discord shows it: mentions, <t:…> timestamps, **bold** and > quotes. HTML-safe. */
export function discordText(content: string, names: Record<string, string> = {}, now = Date.now()): string {
	const full = new Intl.DateTimeFormat(undefined, { dateStyle: 'full', timeStyle: 'short' });
	return content
		.split('\n')
		.map((line) => {
			const quote = line.startsWith('> ');
			const h = esc(quote ? line.slice(2) : line)
				.replace(/&lt;@!?(\d+)&gt;/g, (_, id) => `<span class="mention">@${esc(names[id] ?? 'member')}</span>`)
				.replace(/&lt;@&amp;(\d+)&gt;/g, '<span class="mention">@role</span>')
				.replace(/&lt;t:(\d+):([FR])&gt;/g, (_, ts, f) => (f === 'R' ? relative(+ts, now) : full.format(+ts * 1000)))
				.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
			return quote ? `<blockquote>${h}</blockquote>` : h;
		})
		.join('\n')
		.replace(/\n<blockquote>/g, '<blockquote>'); // a block already starts its own line
}
