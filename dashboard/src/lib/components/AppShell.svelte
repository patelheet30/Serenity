<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { Guild, Me } from '../api';
	import { SECTIONS, sectionCommands, serverCommands, type Section } from '../commands';
	import { openPalette, registerCommands } from '../palette.svelte';
	import { MOD } from '../util';
	import Avatar from './Avatar.svelte';
	import Icon from './Icon.svelte';
	import Mark from './Mark.svelte';
	import UserMenu from './UserMenu.svelte';

	// Top command bar: brand, server switcher, section tabs (second row below 1240px), ⌘K, account.
	// Sections with a parent share its tab and get a sub-nav above the page.
	let {
		me,
		guild,
		guilds = [],
		sections = SECTIONS,
		section,
		status,
		children
	}: {
		me: Me;
		guild: Pick<Guild, 'id' | 'name' | 'icon_url'>;
		guilds?: Guild[];
		sections?: Section[];
		section: string;
		status?: Snippet;
		children: Snippet;
	} = $props();

	const parentOf = (k: string) => sections.find((s) => s.k === k)?.parent ?? k;
	const top = $derived(sections.filter((s) => !s.parent));
	const group = $derived(sections.filter((s) => parentOf(s.k) === parentOf(section)));

	$effect(() => registerCommands([...sectionCommands(guild.id, sections), ...serverCommands(guilds)]));
</script>

<header class="top">
	<div class="top-in">
		<a class="brand" href="/servers" aria-label="Serenity, all servers"><Mark /><span class="wm">Serenity</span></a>
		<button class="gsw" aria-label="Switch server, current: {guild.name}" onclick={() => openPalette('')}>
			<Avatar kind="guild" name={guild.name} src={guild.icon_url} />
			<span class="t">{guild.name}</span>
			<Icon name="chev" />
		</button>
		<nav class="tabs" aria-label="Sections">
			{#each top as s (s.k)}
				<a
					class="tab"
					title="g then {s.key}"
					href="/dashboard/{guild.id}/{s.k}"
					aria-current={s.k === section ? 'page' : s.k === parentOf(section) ? 'true' : undefined}><Icon name={s.k} />{s.name}</a
				>
			{/each}
		</nav>
		<button class="cmdk" aria-label="Open command palette ({MOD} K)" onclick={() => openPalette()}>
			<Icon name="search" /><span class="t">Search or run…</span><kbd>{MOD}K</kbd>
		</button>
		<div class="user" style="gap:10px">
			{@render status?.()}
			<UserMenu {me} />
		</div>
	</div>
</header>
<main class="dash" id="main">
	{#if group.length > 1}
		<nav class="seg subnav" aria-label="{group[0].name} pages">
			{#each group as s (s.k)}
				<a href="/dashboard/{guild.id}/{s.k}" title="g then {s.key}" aria-current={s.k === section ? 'page' : undefined}>{s.name}</a>
			{/each}
		</nav>
	{/if}
	{@render children()}
</main>
