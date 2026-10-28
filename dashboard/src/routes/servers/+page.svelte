<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { inviteUrl, type Guild } from '#lib/api.ts';
	import { serverCommands } from '#lib/commands.ts';
	import Avatar from '#lib/components/Avatar.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import Icon from '#lib/components/Icon.svelte';
	import Mark from '#lib/components/Mark.svelte';
	import UserMenu from '#lib/components/UserMenu.svelte';
	import { registerCommands } from '#lib/palette.svelte.ts';
	import { handleUnauthorized, loadGuilds, requireMe, session } from '#lib/session.svelte.ts';
	import { MOD } from '#lib/util.ts';

	let error = $state('');
	let q = $state('');
	let sel = $state(0);

	async function refresh() {
		try {
			if (!(await requireMe())) return;
			await loadGuilds(true);
			error = '';
		} catch (e) {
			if (!handleUnauthorized(e)) error = e instanceof Error ? e.message : String(e);
		}
	}

	onMount(refresh);
	$effect(() => registerCommands(serverCommands(session.guilds ?? [])));

	const all = $derived(session.guilds ?? []);
	const match = (g: Guild) => g.name.toLowerCase().includes(q.trim().toLowerCase());
	const ready = $derived(all.filter((g) => (g.manageable || g.staff) && g.installed && match(g)));
	const invite = $derived(all.filter((g) => g.manageable && !g.installed && match(g)));
	const noPerm = $derived(all.filter((g) => !g.manageable && !g.staff && match(g)));
	const rows = $derived([...ready, ...invite]);
	$effect(() => {
		sel = Math.min(sel, Math.max(0, rows.length - 1));
	});

	function open(g: Guild) {
		if (g.installed) goto(`/dashboard/${g.id}`);
		// Discord preselects the server; coming back to this tab refreshes the list.
		else window.open(inviteUrl(g.id), '_blank', 'noopener');
	}

	function onkeydown(e: KeyboardEvent) {
		const n = Math.max(1, rows.length);
		if (e.key === 'ArrowDown') sel = (sel + 1) % n;
		else if (e.key === 'ArrowUp') sel = (sel - 1 + n) % n;
		else if (e.key === 'Enter' && rows[sel]) open(rows[sel]);
		else return;
		e.preventDefault();
	}

	$effect(() => {
		document.getElementById(`pr-${rows[sel]?.id}`)?.scrollIntoView({ block: 'nearest' });
	});
</script>

<svelte:head><title>Pick a server · Serenity</title></svelte:head>
<svelte:window onfocus={() => session.me && refresh()} />

{#snippet row(g: Guild, i: number)}
	<button class="prow" role="option" id="pr-{g.id}" aria-selected={i === sel} tabindex="-1" onclick={() => open(g)} onmousemove={() => (sel = i)}>
		<Avatar kind="guild" name={g.name} src={g.icon_url} />
		<div>
			<div class="nm">{g.name}{#if g.staff}<span class="sbadge">Staff</span>{/if}</div>
			<div class="sub">{g.staff ? 'Tickets only' : g.installed ? 'Serenity active' : 'Serenity isn’t here yet'}</div>
		</div>
		<span class="go">
			{#if g.installed}Open <Icon name="arrow" />{:else}<span class="btn sm">Invite <Icon name="ext" /></span>{/if}
		</span>
	</button>
{/snippet}

<header class="picker-top">
	<a class="brand" href="/" aria-label="Serenity home"><Mark /><span class="wm">Serenity</span></a>
	<span class="sp"></span>
	{#if session.me}<UserMenu me={session.me} />{/if}
</header>

<main id="main" class="picker-wrap">
	<div class="picker">
		<h1>Pick a server</h1>
		<p>
			{#if session.me}Signed in as <b>{session.me.username}</b>.{/if}
			Showing servers where you have <span class="mono">Manage Server</span> or handle tickets.
		</p>
		<div class="plist">
			<div class="pin">
				<Icon name="search" />
				<!-- svelte-ignore a11y_autofocus -->
				<input
					type="text"
					aria-label="Filter servers"
					placeholder="Type to filter…"
					autocomplete="off"
					autofocus
					data-search
					role="combobox"
					aria-expanded="true"
					aria-controls="plistbox"
					aria-activedescendant={rows[sel] ? `pr-${rows[sel].id}` : undefined}
					bind:value={q}
					oninput={() => (sel = 0)}
					{onkeydown}
				/>
				<kbd>↑↓</kbd><kbd>↵</kbd>
			</div>
			<div id="plistbox" role="listbox" aria-label="Servers">
				{#if error}
					<EmptyState icon="warn">
						Couldn’t load your servers: {error}
						<button class="linkish" onclick={refresh}>Try again</button>
					</EmptyState>
				{:else if !session.guilds}
					<EmptyState>Loading your servers…</EmptyState>
				{:else if !rows.length}
					<EmptyState>
						{q ? `No servers you can configure match “${q}”.` : 'You don’t manage any servers yet.'}
					</EmptyState>
				{/if}
				{#if ready.length}
					<div class="grp">Ready</div>
					{#each ready as g, i (g.id)}{@render row(g, i)}{/each}
				{/if}
				{#if invite.length}
					<div class="grp">Invite Serenity</div>
					{#each invite as g, i (g.id)}{@render row(g, ready.length + i)}{/each}
				{/if}
			</div>
			{#if noPerm.length}
				<details class="noperm">
					<summary>
						<Icon name="lock" />{noPerm.length} {noPerm.length === 1 ? 'server' : 'servers'} without Manage Server permission <Icon name="chev" />
					</summary>
					{#each noPerm as g (g.id)}
						<div class="prow off" aria-disabled="true">
							<Avatar kind="guild" name={g.name} src={g.icon_url} />
							<div><div class="nm">{g.name}</div><div class="sub">not configurable</div></div>
						</div>
					{/each}
				</details>
			{/if}
			<div class="pfoot">
				<span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>↵</kbd> open or invite</span><span><kbd>{MOD}K</kbd> palette</span>
			</div>
		</div>
	</div>
</main>
