export type CaseUser = { id: string; name: string | null; avatar_url: string | null };
export type Case = {
	number: number;
	action: string;
	target: CaseUser;
	moderator: CaseUser;
	reason: string | null;
	duration: number | null;
	created_at: number;
	expires_at: number | null;
	/** Warnings and timeouts only; null for actions without a state. */
	active: boolean | null;
};

/** Case durations (seconds) as the bot's embeds write them: 45s, 10m, 1h 30m, 7d. */
export function duration(seconds: number | null): string {
	if (!seconds) return '-';
	if (seconds < 60) return `${seconds}s`;
	if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
	if (seconds < 86400) {
		const m = Math.floor((seconds % 3600) / 60);
		return `${Math.floor(seconds / 3600)}h${m ? ` ${m}m` : ''}`;
	}
	return `${Math.floor(seconds / 86400)}d`;
}

/** Every case action: its colour, and which group the "Actions over time" chart stacks it in. */
export const ACTIONS: Record<string, { color: string; group: 'Removals' | 'Restrictions' | 'Reversals' }> = {
	ban: { color: '#ef8a78', group: 'Removals' },
	softban: { color: '#c96e60', group: 'Removals' },
	kick: { color: '#f0b46a', group: 'Removals' },
	timeout: { color: '#d9a05c', group: 'Restrictions' },
	warn: { color: '#e6d38c', group: 'Restrictions' },
	untimeout: { color: '#5fa98a', group: 'Reversals' },
	unban: { color: '#8fe3bf', group: 'Reversals' }
};

export const GROUPS = [
	{ n: 'Removals', c: 'var(--red)' },
	{ n: 'Restrictions', c: 'var(--amber)' },
	{ n: 'Reversals', c: 'var(--acc)' }
].map((g) => ({ ...g, a: Object.keys(ACTIONS).filter((a) => ACTIONS[a].group === g.n) }));
