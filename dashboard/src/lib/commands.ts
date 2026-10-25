import { goto } from '$app/navigation';
import { inviteUrl, type Guild } from './api';
import type { IconName } from './icons';
import type { Command } from './palette.svelte';

export const SECTIONS: { k: IconName; name: string; key: string }[] = [
	{ k: 'overview', name: 'Overview', key: 'o' },
	{ k: 'slowmode', name: 'Slowmode', key: 's' },
	{ k: 'logging', name: 'Logging', key: 'l' },
	{ k: 'cases', name: 'Cases', key: 'c' },
	{ k: 'stats', name: 'Mod stats', key: 't' },
	{ k: 'modules', name: 'Modules', key: 'm' }
];

export function sectionCommands(guildId: string): Command[] {
	return SECTIONS.map((s) => ({
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
		.filter((g) => g.manageable)
		.map((g) => ({
			id: `server:${g.id}`,
			group: 'Servers',
			title: g.installed ? g.name : `Invite Serenity to ${g.name}`,
			icon: g.installed ? 'arrow' : 'ext',
			keywords: 'server switch',
			run: () => (g.installed ? goto(`/dashboard/${g.id}`) : window.open(inviteUrl(g.id), '_blank'))
		}));
}
