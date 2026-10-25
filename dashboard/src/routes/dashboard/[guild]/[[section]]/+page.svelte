<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { api, ApiError, inviteUrl } from '#lib/api.ts';
	import { SECTIONS } from '#lib/commands.ts';
	import AppShell from '#lib/components/AppShell.svelte';
	import DataTable from '#lib/components/DataTable.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import Icon from '#lib/components/Icon.svelte';
	import { handleUnauthorized, loadGuilds, requireMe, session } from '#lib/session.svelte.ts';

	type Channel = { id: string; name: string; type: string; parent_id: string | null; position: number };

	const guildId = $derived(page.params.guild!);
	const section = $derived(SECTIONS.find((s) => s.k === page.params.section) ?? SECTIONS[0]);
	const guild = $derived(
		session.guilds?.find((g) => g.id === guildId) ?? { id: guildId, name: 'Server', icon_url: null }
	);

	let channels: Channel[] | null = $state(null);
	let error: ApiError | null = $state(null);
	let loadError = $state('');

	onMount(() => {
		requireMe().then(
			(me) => me && loadGuilds().catch(() => {}),
			(e) => (loadError = e instanceof Error ? e.message : String(e))
		);
	});

	// Every guild route is authorised server-side; this request doubles as the access check.
	$effect(() => {
		if (!session.me) return; // wait for /me, so a failed login doesn't fire a doomed request
		const id = guildId;
		let stale = false; // a slower response for the previous server must not land here
		channels = null;
		error = null;
		api<Channel[]>(`/guilds/${id}/channels`).then(
			(c) => !stale && (channels = c),
			(e) => {
				if (stale || handleUnauthorized(e)) return;
				error = e instanceof ApiError ? e : new ApiError(0, 'network', String(e));
			}
		);
		return () => (stale = true);
	});
</script>

<svelte:head><title>{section.name} · {guild.name} · Serenity</title></svelte:head>

{#if session.me}
	<AppShell me={session.me} {guild} guilds={session.guilds ?? []} section={section.k}>
		<div class="phead">
			<div>
				<h1>{section.name}</h1>
				<p>{guild.name}</p>
			</div>
		</div>
		{#if error?.status === 403}
			<section class="pane">
				<EmptyState icon="lock">You need <span class="mono">Manage Server</span> in this server to configure it.</EmptyState>
			</section>
		{:else if error?.status === 404}
			<section class="pane">
				<EmptyState>
					Serenity isn’t in this server.
					<a class="btn sm pri" href={inviteUrl(guildId)} target="_blank" rel="noopener">Invite <Icon name="ext" /></a>
				</EmptyState>
			</section>
		{:else if error}
			<section class="pane"><EmptyState icon="warn">{error.message}</EmptyState></section>
		{:else if !channels}
			<section class="pane"><EmptyState>Loading…</EmptyState></section>
		{:else if section.k === 'overview'}
			<section class="pane">
				<div class="ph"><h2>Channels Serenity can see</h2><span class="sp"></span><span class="cnt">{channels.length}</span></div>
				<DataTable
					label="Channels"
					rows={channels}
					key={(c) => c.id}
					columns={[{ key: 'name', label: 'Channel' }, { key: 'type', label: 'Type' }, { key: 'position', label: 'Position', align: 'right' }]}
				/>
			</section>
		{:else}
			<section class="pane">
				<EmptyState icon={section.k}>{section.name} settings arrive with the Dashboard configuration milestone.</EmptyState>
			</section>
		{/if}
	</AppShell>
{:else if loadError}
	<main id="main" class="picker-wrap">
		<section class="pane">
			<EmptyState icon="warn">
				Couldn’t load your account: {loadError}
				<button class="linkish" onclick={() => location.reload()}>Try again</button>
			</EmptyState>
		</section>
	</main>
{/if}
