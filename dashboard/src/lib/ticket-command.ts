/** ⌘K ticket commands (#79), parsed here and run by sections/tickets/commands.ts. */

export const PACKS = ['game', 'freelance', 'community', 'blank'] as const;
export const MOD_ACTIONS = ['warn', 'timeout', 'kick', 'ban'] as const;

export type TicketCommand =
	| { kind: 'claim'; number: number }
	| { kind: 'escalate'; number: number; tier: string | null; reason: string | null }
	| { kind: 'move'; number: number; stage: string }
	| { kind: 'mod'; number: number; action: (typeof MOD_ACTIONS)[number]; duration: string | null; reason: string | null }
	| { kind: 'close'; number: number; reason: string | null }
	| { kind: 'transcript'; number: number }
	| { kind: 'panel'; pack: (typeof PACKS)[number] };

const N = '#?(\\d{1,9})';
const rest = (s: string | undefined) => s?.trim().replace(/^"(.*)"$/, '$1').trim() || null;

/**
 * `claim 142`, `escalate 141 t2 "reason"`, `move 142 to quoted`, `mod 139 timeout 1h spam`,
 * `close 142 resolved`, `open transcript 131`, `new panel game`; null for anything else.
 */
export function parseTicketCommand(raw: string): TicketCommand | null {
	const q = raw.trim().replace(/\s+/g, ' ');
	let m: RegExpExecArray | null;
	if ((m = new RegExp(`^claim ${N}$`, 'i').exec(q))) return { kind: 'claim', number: +m[1] };
	if ((m = new RegExp(`^escalate ${N}(?: ([^\\s"]+))?(?: (.+))?$`, 'i').exec(q)))
		return { kind: 'escalate', number: +m[1], tier: m[2] ?? null, reason: rest(m[3]) };
	if ((m = new RegExp(`^move ${N} to (.+)$`, 'i').exec(q)))
		return { kind: 'move', number: +m[1], stage: rest(m[2])! };
	if ((m = new RegExp(`^mod ${N} (warn|timeout|kick|ban)(?: (\\d{1,4}[mhd]))?(?: (.+))?$`, 'i').exec(q))) {
		const action = m[2].toLowerCase() as (typeof MOD_ACTIONS)[number];
		if ((action === 'timeout') !== !!m[3]) return null; // a timeout needs its length, nothing else takes one
		return { kind: 'mod', number: +m[1], action, duration: m[3]?.toLowerCase() ?? null, reason: rest(m[4]) };
	}
	if ((m = new RegExp(`^close ${N}(?: (.+))?$`, 'i').exec(q))) return { kind: 'close', number: +m[1], reason: rest(m[2]) };
	if ((m = new RegExp(`^(?:open )?transcript ${N}$`, 'i').exec(q))) return { kind: 'transcript', number: +m[1] };
	if ((m = /^new panel (\w+)$/i.exec(q))) {
		const pack = m[1].toLowerCase() as (typeof PACKS)[number];
		return PACKS.includes(pack) ? { kind: 'panel', pack } : null;
	}
	return null;
}

/** A typed tier: `t2` is the second rung, otherwise its name (any case); null if the type has none such. */
export function resolveTier(token: string, tiers: { name: string }[]): string | null {
	const m = /^t(\d+)$/i.exec(token);
	const byName = tiers.find((t) => t.name.toLowerCase() === token.toLowerCase());
	return byName?.name ?? (m ? (tiers[+m[1] - 1]?.name ?? null) : null);
}

/** What the palette shows for a parsed command. */
export function describe(c: TicketCommand): string {
	const why = (r: string | null) => (r ? `: ${r}` : '');
	switch (c.kind) {
		case 'claim':
			return `Claim ticket #${c.number}`;
		case 'escalate':
			return `Escalate #${c.number} to ${c.tier ?? 'the next tier'}${why(c.reason)}`;
		case 'move':
			return `Move #${c.number} to ${c.stage}`;
		case 'mod':
			return `${c.action[0].toUpperCase()}${c.action.slice(1)} #${c.number}’s opener${c.duration ? ` for ${c.duration}` : ''}${why(c.reason)}`;
		case 'close':
			return `Close #${c.number}${why(c.reason)}`;
		case 'transcript':
			return `Open the transcript of #${c.number}`;
		case 'panel':
			return `New ${c.pack} panel`;
	}
}
