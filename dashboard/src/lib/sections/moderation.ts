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

export const ACTION_COLORS: Record<string, string> = {
	ban: '#ef8a78',
	softban: '#c96e60',
	kick: '#f0b46a',
	timeout: '#d9a05c',
	warn: '#e6d38c',
	unban: '#8fe3bf',
	untimeout: '#5fa98a'
};
