<script lang="ts">
	import AppShell from '#lib/components/AppShell.svelte';
	import AutosaveIndicator from '#lib/components/AutosaveIndicator.svelte';
	import Avatar from '#lib/components/Avatar.svelte';
	import Chip from '#lib/components/Chip.svelte';
	import DataTable from '#lib/components/DataTable.svelte';
	import Drawer from '#lib/components/Drawer.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import InlineEdit from '#lib/components/InlineEdit.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Segmented from '#lib/components/Segmented.svelte';
	import Select from '#lib/components/Select.svelte';
	import Sparkline from '#lib/components/Sparkline.svelte';
	import Toggle from '#lib/components/Toggle.svelte';
	import { registerCommands } from '#lib/palette.svelte.ts';
	import { toast } from '#lib/toast.svelte.ts';

	// Every component in its states, with mock data. Not linked from the app.
	const me = { id: '2287401193', username: 'heet', avatar_url: null };
	const guild = { id: '1', name: 'Lumen Collective', icon_url: null };

	let on = $state(true);
	let off = $state(false);
	let interval = $state(2);
	let channel: string | null = $state(null);
	let drawer = $state(false);
	let modal = $state(false);
	let selected: string | null = $state(null);
	let saveStatus: 'idle' | 'saving' | 'saved' | 'error' = $state('idle');
	let savedAt = $state(0);

	let rows = $state([
		{ name: 'general', threshold: '', rate: 12.4, hist: [8, 9, 12, 11, 14, 12, 10, 12] },
		{ name: 'memes', threshold: '15', rate: 19.1, hist: [12, 15, 22, 30, 26, 19, 18, 19] },
		{ name: 'art-share', threshold: '', rate: 4.2, hist: [3, 4, 5, 4, 3, 4, 5, 4] },
		{ name: 'help', threshold: '8', rate: 7.0, hist: [6, 7, 6, 8, 7, 7, 6, 7] }
	]);

	function fakeSave() {
		saveStatus = 'saving';
		setTimeout(() => ((saveStatus = 'saved'), (savedAt = Date.now())), 600);
	}

	$effect(() =>
		registerCommands([
			{
				id: 'gallery:threshold',
				group: 'Templates',
				title: 'set #channel threshold …',
				icon: 'slowmode',
				template: 'set #',
				parse: (q) => {
					const m = /^set #?([\w-]+) threshold (\d+)$/.exec(q);
					const row = m && rows.find((r) => r.name.startsWith(m[1]));
					return row
						? { label: `Set #${row.name} threshold to ${m![2]}`, run: () => ((row.threshold = m![2]), toast(`#${row.name} threshold → ${m![2]}`)) }
						: null;
				}
			},
			...rows.map((r) => ({ id: `gallery:ch:${r.name}`, group: 'Channels', title: `#${r.name}`, icon: 'hash' as const, run: () => (selected = r.name) }))
		])
	);
</script>

<svelte:head><title>Components · Serenity</title><meta name="robots" content="noindex" /></svelte:head>

<AppShell {me} {guild} section="overview">
	{#snippet status()}<AutosaveIndicator status={saveStatus} {savedAt} />{/snippet}

	<div class="phead"><div><h1>Component gallery</h1><p>Every Calm component in its states. Try ⌘K → “set #memes threshold 15”.</p></div></div>

	<div class="grid">
		<section class="pane s6">
			<div class="ph"><h2>Buttons</h2></div>
			<div class="pb" style="display:flex;gap:8px;flex-wrap:wrap">
				<button class="btn pri">Primary</button><button class="btn">Default</button>
				<button class="btn ghost">Ghost</button><button class="btn danger">Danger</button>
				<button class="btn sm">Small</button><button class="btn" disabled>Disabled</button>
			</div>
		</section>

		<section class="pane s6">
			<div class="ph"><h2>Toggles, segmented, chips</h2></div>
			<div class="pb stack">
				<div class="rowline"><span>On</span><Toggle label="On" bind:checked={on} /></div>
				<div class="rowline"><span>Off</span><Toggle label="Off" bind:checked={off} /></div>
				<div class="rowline"><span>Blocked</span><Toggle label="Blocked" disabled onchange={() => {}} /></div>
				<Segmented label="Update interval" bind:value={interval} options={[1, 2, 3, 4, 5].map((v) => ({ value: v, label: `${v}m` }))} />
				<div class="facet"><Chip count={12}>ban</Chip><Chip pressed count={4}>kick</Chip><Chip>warn</Chip></div>
			</div>
		</section>

		<section class="pane s6">
			<div class="ph"><h2>Select / picker</h2></div>
			<div class="pb stack">
				<Select label="Log channel" bind:value={channel} options={rows.map((r) => ({ value: r.name, label: `#${r.name}` }))} />
				<input class="inp" placeholder="Text input" aria-label="Text input" />
			</div>
		</section>

		<section class="pane s6">
			<div class="ph"><h2>Drawer, modal, toast, autosave</h2></div>
			<div class="pb" style="display:flex;gap:8px;flex-wrap:wrap">
				<button class="btn" onclick={() => (drawer = true)}>Open drawer</button>
				<button class="btn" onclick={() => (modal = true)}>Open modal</button>
				<button class="btn" onclick={() => toast('Logging turned off', { undo: () => toast('Logging back on') })}>Toast with Undo</button>
				<button class="btn" onclick={() => toast('Couldn’t save', { error: true })}>Error toast</button>
				<button class="btn" onclick={fakeSave}>Save something</button>
				<button class="btn" onclick={() => (saveStatus = 'error')}>Fail a save</button>
			</div>
		</section>

		<section class="pane s12">
			<div class="ph"><h2>Data table</h2><span class="sp"></span><span class="hint">j/k to move, ↵ to open, click a threshold to edit</span></div>
			<DataTable
				label="Channels"
				{rows}
				key={(r) => r.name}
				{selected}
				onopen={(r) => (selected = r.name)}
				columns={[{ key: 'name', label: 'Channel' }, { key: 'threshold', label: 'Threshold' }, { key: 'rate', label: 'Msgs/min', align: 'right' }, { key: 'hist', label: 'Last 8 min' }]}
			>
				{#snippet cell(r, c)}
					{#if c.key === 'name'}<span class="chname">#{r.name}</span>
					{:else if c.key === 'threshold'}<InlineEdit label="#{r.name} threshold" value={r.threshold} onsave={(v) => ((r.threshold = v), fakeSave())} />
					{:else if c.key === 'rate'}<span class="num">{r.rate.toFixed(1)}</span>
					{:else}<div style="width:120px"><Sparkline values={r.hist} label="#{r.name} rate" /></div>{/if}
				{/snippet}
			</DataTable>
		</section>

		<section class="pane s6">
			<div class="ph"><h2>Avatars</h2></div>
			<div class="pb" style="display:flex;gap:10px;align-items:center">
				<Avatar name="vapor.wav" /><Avatar name="mothlight" size="lg" /><Avatar name="kiri_nyx" size="xs" />
				<Avatar kind="guild" name="Lumen Collective" />
			</div>
		</section>

		<section class="pane s6">
			<div class="ph"><h2>Empty state</h2></div>
			<EmptyState icon="cases">No cases yet. They appear here as moderators act.</EmptyState>
		</section>
	</div>
</AppShell>

<Drawer bind:open={drawer} title="Case #1042">
	<p class="reason-t">Drawers hold detail views. On phones they become a bottom sheet.</p>
</Drawer>

<Modal bind:open={modal} title="Clear 3 warnings?">
	<p>vapor.wav’s active warnings will be cleared. Cases stay in their history.</p>
	{#snippet actions()}
		<button class="btn" onclick={() => (modal = false)}>Cancel</button>
		<button class="btn pri" onclick={() => ((modal = false), toast('Warnings cleared'))}>Clear warnings</button>
	{/snippet}
</Modal>
