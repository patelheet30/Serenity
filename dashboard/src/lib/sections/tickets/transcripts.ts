/** Shared by the staff transcript list and the transcript viewer (#78). */

export type TicketUser = { id: string; name: string | null; avatar_url: string | null };
export type Retention = { expires_at: number | null; held_by_case: number | null; deletion_requested_at: number | null };

export type TranscriptRow = Retention & {
	id: string;
	number: number;
	type: { id: number; name: string; emoji: string | null };
	opener: TicketUser;
	staff: TicketUser[];
	outcome: 'staff' | 'opener' | 'auto';
	closed_at: number;
	reason: string | null;
	rating: number | null;
};

export const OUTCOMES = { staff: 'Closed by staff', opener: 'Closed by opener', auto: 'Auto-closed' } as const;

/** "deletes in 12d", "held as evidence for Case #4" or "kept until deleted". */
export function expiry(r: Retention, now = Date.now()): string {
	if (r.held_by_case) return `held as evidence for Case #${r.held_by_case}`;
	if (!r.expires_at) return 'kept until deleted';
	const days = Math.ceil((r.expires_at * 1000 - now) / 86_400_000);
	return days > 0 ? `deletes in ${days}d` : 'deletes today';
}

export const who = (u: TicketUser) => u.name ?? u.id;
