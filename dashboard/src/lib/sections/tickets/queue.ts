import type { CaseUser } from '../moderation';

export type TicketUser = CaseUser;
export type TicketField = { key: string; label: string; type: string; staff_only: boolean };
export type Stage = { name: string; color: number | null; terminal: boolean };
export type TicketType = {
	id: number;
	name: string;
	emoji: string | null;
	fields: TicketField[];
	stages: Stage[];
	/** Lowest first. */
	tiers: { id: number; name: string }[];
};
export type Ticket = {
	number: number;
	type_id: number;
	status: 'open' | 'closed';
	channel_id: string | null;
	opener: TicketUser;
	assignee: TicketUser | null;
	stage: string | null;
	tier_id: number | null;
	/** 'staff': the member spoke last. */
	waiting_on: 'staff' | 'opener';
	priority: Priority;
	opened_at: number;
	last_activity_at: number;
	fields: Record<string, string>;
	off_hours: boolean;
};
export type Queue = {
	tickets: Ticket[];
	/** Open tickets the user can see, before filters. */
	total: number;
	types: TicketType[];
	facets: {
		type: Record<string, number>;
		stage: Record<string, number>;
		tier: Record<string, number>;
		status: Record<string, number>;
		mine: number;
		unclaimed: number;
	};
};

export const PRIORITIES = ['low', 'normal', 'high', 'urgent'] as const;
export type Priority = (typeof PRIORITIES)[number];

export const who = (u: TicketUser) => u.name ?? u.id;
export const typeLabel = (t: TicketType) => (t.emoji ? `${t.emoji} ${t.name}` : t.name);
export const stageColor = (s: Stage | undefined) =>
	s?.color != null ? `#${s.color.toString(16).padStart(6, '0')}` : 'var(--fg2)';
export const channelUrl = (guildId: string, t: Ticket) =>
	t.channel_id ? `https://discord.com/channels/${guildId}/${t.channel_id}` : null;
