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
	/** The ticket it was made from (a ticket's Mod action). */
	ticket: number | null;
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
	ban: { color: '#f0567a', group: 'Removals' },
	softban: { color: '#c2456a', group: 'Removals' },
	kick: { color: '#ff7f6b', group: 'Removals' },
	timeout: { color: '#ff9a2e', group: 'Restrictions' },
	warn: { color: '#ffd38e', group: 'Restrictions' },
	untimeout: { color: '#7e78d6', group: 'Reversals' },
	unban: { color: '#a9a3f0', group: 'Reversals' }
};

export const GROUPS = [
	{ n: 'Removals', c: 'var(--red)' },
	{ n: 'Restrictions', c: 'var(--acc)' },
	{ n: 'Reversals', c: 'var(--lilac)' }
].map((g) => ({ ...g, a: Object.keys(ACTIONS).filter((a) => ACTIONS[a].group === g.n) }));
