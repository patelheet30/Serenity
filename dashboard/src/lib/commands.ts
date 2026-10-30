import { goto } from '$app/navigation';
import { inviteUrl, type Access, type Guild } from './api';
import type { IconName } from './icons';
import type { Command } from './palette.svelte';

export type Section = { k: IconName; name: string; key: string };

export const SECTIONS: Section[] = [
	{ k: 'overview', name: 'Overview', key: 'o' },
	{ k: 'slowmode', name: 'Slowmode', key: 's' },
	{ k: 'logging', name: 'Logging', key: 'l' },
	{ k: 'cases', name: 'Cases', key: 'c' },
	{ k: 'stats', name: 'Mod stats', key: 't' },
	{ k: 'audit', name: 'Audit log', key: 'a' },
	{ k: 'tickets', name: 'Tickets', key: 'k' },
	{ k: 'modules', name: 'Modules', key: 'm' }
];

/** The sections a user can open: ticket staff get only Tickets. */
export function visibleSections(access: Access | null): Section[] {
	if (access?.level === 'staff') return SECTIONS.filter((s) => s.k === 'tickets');
	return SECTIONS;
}

export function sectionCommands(guildId: string, sections = SECTIONS): Command[] {
	return sections.map((s) => ({
		id: `section:${s.k}`,
		group: 'Navigate',
		title: s.name,
		icon: s.k,
		shortcut: `g ${s.key}`,
		run: () => goto(`/dashboard/${guildId}/${s.k}`)
	}));
}

/** One palette entry per server: open it, or invite Serenity to it. */
export function serverCommands(guilds: Guild[]): Command[] {
	return guilds
		.filter((g) => g.manageable || g.staff)
		.map((g) => ({
			id: `server:${g.id}`,
			group: 'Servers',
			title: g.installed ? g.name : `Invite Serenity to ${g.name}`,
			icon: g.installed ? 'arrow' : 'ext',
			keywords: 'server switch',
			run: () => (g.installed ? goto(`/dashboard/${g.id}`) : window.open(inviteUrl(g.id), '_blank'))
		}));
}
