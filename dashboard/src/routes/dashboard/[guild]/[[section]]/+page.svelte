<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { api, ApiError, errorMessage, inviteUrl, type Channel } from '#lib/api.ts';
	import { autosave } from '#lib/autosave.svelte.ts';
	import { SECTIONS } from '#lib/commands.ts';
	import AppShell from '#lib/components/AppShell.svelte';
	import AutosaveIndicator from '#lib/components/AutosaveIndicator.svelte';
	import DataTable from '#lib/components/DataTable.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import Icon from '#lib/components/Icon.svelte';
	import Logging from '#lib/sections/Logging.svelte';
	import Modules from '#lib/sections/Modules.svelte';
	import Slowmode from '#lib/sections/Slowmode.svelte';
	import { handleUnauthorized, loadGuilds, requireMe, session } from '#lib/session.svelte.ts';

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
			(e) => (loadError = errorMessage(e))
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
			(e) => !stale && fail(e)
		);
		return () => (stale = true);
	});

	/** After the bot creates channels (logging setup), so pickers show them by name. */
	async function reloadChannels() {
		const id = guildId;
		try {
			const c = await api<Channel[]>(`/guilds/${id}/channels`);
			if (id === guildId) channels = c;
		} catch (e) {
			fail(e);
		}
	}

	/** A section's own load failed: same states as the access check above. */
	function fail(e: unknown) {
		if (handleUnauthorized(e)) return;
		error = e instanceof ApiError ? e : new ApiError(0, 'network', errorMessage(e));
	}
</script>

<svelte:head><title>{section.name} · {guild.name} · Serenity</title></svelte:head>

{#if session.me}
	<AppShell me={session.me} {guild} guilds={session.guilds ?? []} section={section.k}>
		{#snippet status()}<AutosaveIndicator status={autosave.status} savedAt={autosave.savedAt} />{/snippet}
		{#if error || !channels || section.k === 'overview' || section.k === 'cases' || section.k === 'stats'}
			<div class="phead">
				<div>
					<h1>{section.name}</h1>
					<p>{guild.name}</p>
				</div>
			</div>
		{/if}
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
		{:else if section.k === 'slowmode'}
			<Slowmode {guildId} {channels} onerror={fail} />
		{:else if section.k === 'logging'}
			<Logging {guildId} {channels} onerror={fail} onchannels={reloadChannels} />
		{:else if section.k === 'modules'}
			<Modules {guildId} onerror={fail} />
		{:else}
			<section class="pane">
				<EmptyState icon={section.k}>{section.name} arrives later in the Dashboard configuration milestone.</EmptyState>
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
