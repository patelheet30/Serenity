import { goto } from '$app/navigation';
import { api, send } from '../../api';
import { save } from '../../autosave.svelte';
import type { Command } from '../../palette.svelte';
import { describe, parseTicketCommand, resolveTier, type TicketCommand } from '../../ticket-command';
import { toast } from '../../toast.svelte';
import type { TicketType, TicketUser } from './queue';
import type { TranscriptRow } from './transcripts';

type Detail = { number: number; type: TicketType; opener: TicketUser };

/** ⌘K ticket commands, through the same endpoints as the queue. New panels are for managers only. */
export function ticketCommands(guildId: string, manager: boolean): Command[] {
	const base = `/guilds/${guildId}/tickets`;
	const act = (c: { number: number }, path: string, body: unknown = {}) =>
		send<Detail>('POST', `${base}/${c.number}/${path}`, body);

	async function run(c: TicketCommand) {
		if (c.kind === 'panel') return goto(`/dashboard/${guildId}/tickets/panels?new=${c.pack}`);
		if (c.kind === 'transcript') {
			const r = await api<{ transcripts: TranscriptRow[] }>(`${base}/transcripts?before=${c.number + 1}`);
			const found = r.transcripts.find((t) => t.number === c.number);
			return found ? goto(`/transcripts/${found.id}`) : toast(`#${c.number} has no transcript you can see.`, { error: true });
		}
		const done = await save(async () => {
			if (c.kind === 'claim') return act(c, 'claim');
			if (c.kind === 'move') return act(c, 'stage', { stage: c.stage });
			if (c.kind === 'close') return act(c, 'close', { reason: c.reason });
			const ticket = await api<Detail>(`${base}/${c.number}`);
			if (c.kind === 'escalate') {
				const tier = c.tier && (resolveTier(c.tier, ticket.type.tiers) ?? c.tier); // the bot names a missing one
				return act(c, 'escalate', { tier, reason: c.reason });
			}
			return act(c, 'mod', { user_id: ticket.opener.id, action: c.action, reason: c.reason, duration: c.duration });
		});
		if (done) toast(`Done: ${describe(c)}`);
	}

	const entry = (kind: TicketCommand['kind'], title: string, template: string): Command => ({
		id: `tickets:${kind}`,
		group: 'Tickets',
		title,
		icon: 'tickets',
		keywords: 'ticket',
		template,
		parse: (q) => {
			const c = parseTicketCommand(q);
			return c?.kind === kind ? { label: describe(c), run: () => void run(c).catch((e) => toast(String(e?.message ?? e), { error: true })) } : null;
		}
	});

	return [
		entry('claim', 'claim #…', 'claim '),
		entry('escalate', 'escalate #… t2 "reason"', 'escalate '),
		entry('move', 'move #… to stage', 'move '),
		entry('mod', 'mod #… warn|timeout 1h|kick|ban reason', 'mod '),
		entry('close', 'close #… reason', 'close '),
		entry('transcript', 'open transcript #…', 'open transcript '),
		...(manager ? [entry('panel', 'new panel game|freelance|community|blank', 'new panel ')] : [])
	];
}
