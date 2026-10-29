import type { Case, CaseUser } from '../moderation';

export type TicketUser = CaseUser;
export type TicketField = {
	key: string;
	label: string;
	type: string;
	required: boolean;
	staff_only: boolean;
	/** A dropdown's choices. */
	options: string[];
};
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

/** Another ticket, in the detail's context panel. */
export type Brief = {
	number: number;
	type_id: number;
	status: 'open' | 'closed';
	opener: TicketUser;
	opened_at: number;
	closed_at: number | null;
	linked_by?: string[];
};
export type TicketEvent = { kind: string; actor: TicketUser | null; data: Record<string, unknown>; created_at: number };
/** GET /tickets/{number}, and what every action answers with. */
export type Detail = Ticket & {
	closed_at: number | null;
	close_reason: string | null;
	type: TicketType;
	transcripts: boolean;
	handlers: { user: TicketUser; role: 'claimed' | 'assisted'; messages: number }[];
	notes: { author: TicketUser; body: string; created_at: number }[];
	events: TicketEvent[];
	previous: Brief[];
	linked: Brief[];
	/** null while Moderation is off. */
	moderation: { user: TicketUser; warnings: number; cases: Case[] }[] | null;
	can: { unclaim: boolean; reassign: boolean; mod: string[] };
	/** After a mod action: its case number. */
	case?: number;
	/** After a priority change still waiting on Discord's rename limit. */
	renaming?: boolean;
};
export type Message = {
	id: string;
	author: { id: string; name: string; avatar: string; color: number | null; bot: boolean };
	content: string;
	timestamp: number;
	edited: boolean;
	embeds: { title: string | null; description: string | null; fields: { name: string; value: string }[] }[];
	attachments: { filename: string; size: number; url: string; content_type: string | null }[];
};

/** A ticket event as one line of its history. */
export function eventText(e: TicketEvent, type: TicketType): string {
	const tier = (id: unknown) => type.tiers.find((t) => t.id === id)?.name ?? 'a removed tier';
	const d = e.data;
	const why = d.reason ? `: ${d.reason}` : '';
	switch (e.kind) {
		case 'opened': return 'opened the ticket';
		case 'claimed': return 'claimed it';
		case 'assigned': return 'reassigned it';
		case 'unclaimed': return 'unassigned it';
		case 'escalated': return `escalated ${tier(d.from)} → ${tier(d.to)}${why}`;
		case 'deescalated': return `handed it back ${tier(d.from)} → ${tier(d.to)}${why}`;
		case 'stage': return `moved it to ${d.to}`;
		case 'priority': return `set priority ${d.from} → ${d.to}`;
		case 'fields_edited': return 'edited the answers';
		case 'mod_action': return `${d.action} · case #${d.case}`;
		case 'closed': return 'closed it';
		case 'auto_closed': return 'closed it for inactivity';
		default: return e.kind.replace(/_/g, ' ');
	}
}
